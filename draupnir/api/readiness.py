"""What `/readyz` probes, and how. RF-17.

Readiness reported one dependency, `database`, and built a connection pool to
ask about it: `create_engine()` and `engine.dispose()` on every probe, so an
orchestrator checking every few seconds constructed and tore down a pool at
that rate while the pooled engine the lifespan already owns sat unused.

The bigger defect is what it did not report. SAD 11.2 has nine degraded modes
and `docs/runbook.md` sends an operator to `/readyz` for them; the object
store, the HODD vault, the scheduler and the federation link were all
invisible to it. An operator following the runbook for "runs stuck in QUEUED"
reached a probe that could not tell them whether REGIN was answering.

**A failed check degrades, it does not raise.** SAD 11.2 requires degraded
modes to be visible rather than fatal, and readiness that 500s tells an
orchestrator to take the process out of service for a dependency the process
is designed to survive without. Every check below returns a boolean, and an
exception inside one is that check's `False` and nothing else's.

**A dependency that is not configured is not checked.** Reporting `false` for
something this deployment does not use would leave every forge permanently
degraded, which is the readiness probe that teaches people to ignore readiness
probes. So the checks that appear are the dependencies that exist, and the
object store appears only where it is the store in use -- `hodd.stores.store_for`
picks the vault when one is configured and never opens a bucket, so a bucket
check would be reporting on something that cannot affect service.

**Reachability, not capability.** The scheduler and federation checks accept
any HTTP answer below 500 as reachable, including 401 and 404. `slurmrestd`
answering "unauthorised" is `slurmrestd` answering, and the runbook rows these
map to -- "Slurm controller unavailable", "wide-area network to MEGINGJORD
lost" -- are about the link rather than about the API version behind it. The
alternative is naming an endpoint path here, which duplicates the driver's
`API_VERSION` in a second place and makes a probe fail on a version bump that
broke nothing.

**The names are an operator's index into the runbook.** AC-D3 asks for a
section per row of SAD 11.2 and `docs/runbook.md` has one; `RUNBOOK_SECTIONS`
below is the join, and a test asserts every name it holds points at a section
that exists. A probe that says something is wrong and nothing about where to
look sends an operator to the logs, which is where they were going anyway.

**Every check has its own timeout and they run concurrently**, so the probe
costs one timeout rather than the sum of them. A readiness check that blocks
on a hung NFS mount for thirty seconds is a readiness check that the
orchestrator times out, which reports the whole process dead over one degraded
dependency.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import text

# At import, not inside the checks (RF-46). An import inside a check runs on the
# first probe of every process, and an import is the one piece of work a check's
# timeout cannot bound.
from draupnir.hodd.reconcile import require_vault
from draupnir.hodd.stores import PosixStoreDriver

logger = structlog.get_logger(__name__)

#: How long any one dependency has to answer. Short: this is a liveness-adjacent
#: probe an orchestrator runs every few seconds, and a check that takes longer
#: than the interval is a check that is always in flight.
CHECK_TIMEOUT_SECONDS = 2.0

#: An HTTP answer at or above this is the far end failing rather than answering.
#: Below it -- including 401 and 404 -- something is there.
SERVER_ERROR = 500

#: The names, and the section of `docs/runbook.md` each one sends an operator
#: to. Written down here so the mapping is a fact in the code rather than a
#: convention two documents happen to share (RF-17).
RUNBOOK_SECTIONS: dict[str, int] = {
    "database": 5,
    "vault": 4,
    "object_store": 4,
    "scheduler": 2,
    "federation": 7,
}


@dataclass
class Dependencies:
    """The things `/readyz` can reach, wired once by the application's lifespan.

    Holds the engine rather than making one, which was the defect: an engine is
    a connection pool, and a pool built and disposed per probe is a pool that
    never pools anything.

    Everything is optional and the default reaches nothing, so an application
    built without a lifespan -- which is what the contract tests do -- answers
    a well formed probe with no checks rather than raising.
    """

    #: The lifespan's async engine. `Any` rather than `AsyncEngine` so this
    #: module does not import SQLAlchemy for a type it only calls one method on.
    engine: Any = None
    #: Where the HODD vault is mounted, empty where this forge has none.
    vault_root: str = ""
    #: The site's identifier, which the POSIX driver needs to resolve authority.
    site_id: str = "sindri"
    #: A callable that answers whether the object store holds the bucket. Given
    #: rather than built so a test can supply one that opens no socket.
    object_store: Callable[[], bool] | None = None
    #: Where `slurmrestd` answers, empty where this installation has no scheduler.
    scheduler_url: str = ""
    #: Where MEGINGJORD answers, empty where this forge is not federated.
    registry_url: str = ""
    #: The brokered clients the two link checks go through -- one each, because
    #: the broker approves a destination under a policy and REGIN's is not
    #: MEGINGJORD's. A single client able to reach both is precisely what the
    #: allow list's two separate entries exist to prevent: a caller holding the
    #: scheduling approval must not be able to spend it on the federation link.
    #:
    #: Brokered at all because a readiness probe is outbound traffic like any
    #: other, and threat T11 is egress with no allow list decided -- a probe
    #: holding a raw client would be the one call in the process that went
    #: round the decision.
    scheduler_client: Any = None
    federation_client: Any = None
    timeout: float = CHECK_TIMEOUT_SECONDS

    async def checks(self) -> dict[str, bool]:
        """Probe every configured dependency, concurrently, and report each."""
        probes: list[tuple[str, Callable[[], Awaitable[bool]]]] = []
        if self.engine is not None:
            probes.append(("database", self._database))
        if self.vault_root:
            probes.append(("vault", self._vault))
        elif self.object_store is not None:
            probes.append(("object_store", self._object_store))
        if self.scheduler_url:
            probes.append(("scheduler", self._scheduler))
        if self.registry_url:
            probes.append(("federation", self._federation))

        answers = await asyncio.gather(*(self._answer(name, run) for name, run in probes))
        return dict(answers)

    async def _answer(self, name: str, run: Callable[[], Awaitable[bool]]) -> tuple[str, bool]:
        """One check, bounded and total.

        Its own timeout, because the alternative is one hung dependency
        deciding how long the whole probe takes. Its own `except`, because a
        check that raised would take the other checks' results with it through
        `gather` -- and the operator would lose the four that answered in order
        to be told about the one that did not.
        """
        try:
            async with asyncio.timeout(self.timeout):
                return name, await run()
        except TimeoutError:
            logger.warning("readiness.check.timeout", check=name, seconds=self.timeout)
            return name, False
        except Exception as failed:
            logger.warning("readiness.check.failed", check=name, reason=str(failed))
            return name, False

    async def _database(self) -> bool:
        """PostgreSQL answers. Runbook 5."""
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True

    async def _vault(self) -> bool:
        """The vault is mounted, and it is the vault. Runbook 4.

        Through `require_vault` rather than a bare `is_dir()`, because the two
        failures need different actions and the one a bare check misses is the
        dangerous one: a directory somebody created on the mount point looks
        exactly like a mounted vault and is empty. `require_vault` distinguishes
        them; this only needs to know that neither happened, and the exception
        it raises carries which for the log line above.
        """
        driver = PosixStoreDriver(root=Path(self.vault_root), local_site=self.site_id)
        # In a thread: `is_dir()` on a hung NFS mount blocks uninterruptibly,
        # and blocking the event loop would stop the other checks and every
        # request in flight -- turning one degraded dependency into an outage.
        await asyncio.to_thread(require_vault, driver)
        return True

    async def _object_store(self) -> bool:
        """The bucket answers. Runbook 4."""
        if self.object_store is None:
            return False
        return bool(await asyncio.to_thread(self.object_store))

    async def _scheduler(self) -> bool:
        """`slurmrestd` on REGIN answers. Runbook 2."""
        return await self._reaches(self.scheduler_client, self.scheduler_url)

    async def _federation(self) -> bool:
        """MEGINGJORD answers over the wide-area link. Runbook 7."""
        return await self._reaches(self.federation_client, self.registry_url)

    @staticmethod
    async def _reaches(client: Any, url: str) -> bool:
        """Whether something answers at `url` without failing.

        The clients are synchronous -- they are the same shape as the ones the
        JWKS fetch and the anchor submission hold -- so the call goes to a
        thread rather than blocking the loop.
        """
        if client is None:
            return False
        response = await asyncio.to_thread(client.get, url)
        return bool(getattr(response, "status_code", SERVER_ERROR) < SERVER_ERROR)


#: The process-wide dependencies. Reached through the functions below rather
#: than imported by name, for the reason `deps.STORE` and `deps.READER` are:
#: a router that captured this at import time would keep probing the empty one
#: after the lifespan installed the real one, and the symptom is a readiness
#: endpoint that reports nothing at all while every dependency is fine.
DEPENDENCIES = Dependencies()


def dependencies() -> Dependencies:
    """The current dependencies, resolved at call time."""
    return DEPENDENCIES


def set_dependencies(chosen: Dependencies) -> None:
    """Install what readiness probes. Called by the lifespan, and by tests."""
    global DEPENDENCIES
    DEPENDENCIES = chosen


def object_store_probe(settings: Any) -> Callable[[], bool] | None:
    """A callable that asks the object store whether its bucket is there.

    `None` where a vault is configured, because `hodd.stores.store_for` uses
    the vault and never opens a bucket -- so a bucket check would report on a
    dependency that cannot affect service, and a forge whose MinIO is down but
    unused would sit permanently degraded.

    **The client is built here, once, when the lifespan makes the probe** -- and
    not inside the probe (RF-46). This imported `minio` and built a client on
    every call, so the first `/readyz` of every process paid for an import on
    top of the check, and nothing else in the application had loaded `minio` by
    then. An import is the one piece of work a check's timeout cannot bound, and
    the first probe is the one an orchestrator sends as the process comes up.
    Startup has no deadline, so the work belongs there.

    A client that cannot be built is not a process that cannot start. The probe
    left behind reports the object store unreachable, and the reason is logged
    once, because readiness degrades rather than refusing (SAD 11.2).
    """
    if settings.vault_root:
        return None

    try:
        from minio import Minio

        client = Minio(
            settings.object_store_endpoint,
            access_key=settings.object_store_access_key,
            secret_key=settings.object_store_secret_key,
            secure=settings.object_store_secure,
        )
    except Exception as unusable:
        logger.warning("readiness.object_store.unconfigurable", reason=str(unusable))

        def unreachable() -> bool:
            return False

        return unreachable

    bucket = settings.object_store_bucket

    def probe() -> bool:
        return bool(client.bucket_exists(bucket))

    return probe


__all__ = [
    "CHECK_TIMEOUT_SECONDS",
    "RUNBOOK_SECTIONS",
    "SERVER_ERROR",
    "Dependencies",
    "dependencies",
    "object_store_probe",
    "set_dependencies",
]
