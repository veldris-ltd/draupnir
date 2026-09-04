"""The loop itself: one tick, and the process that repeats it.

A tick is short by construction. It reads the runs this site holds, does at
most one thing to each, performs whatever periodic duty has come due, and
returns. Nothing is carried to the next tick except when a duty was last done,
and losing even that only causes a duty to run early.

**One transaction per run, not one per tick.** A tick that wrote every run's
progress in a single transaction would lose all of it when one run's stage
raised, and would hold the site's advisory lock for the length of the whole
tick. Each run gets its own transaction, so a failure costs that run's tick and
nothing else, and the lock is held for one append at a time.

**Observe before dispatch.** The order in `ORDER` puts QUEUED last, so a job
that finished during the previous tick is recorded, its allocation released,
and the capacity it freed is used by a queued run in the same tick rather than
the next one.

**The supply gates dispatch and nothing else.** SAD 11.2's last row: on a
transfer to battery, "training continues, release does not" and running work is
checkpointed. The monitor is asked once per tick, its answer sets
`Context.may_dispatch`, and the transfer itself is recorded -- a power cut is an
event an operator will later need to place against a run that failed.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

import structlog
from sqlalchemy import Connection, Engine, create_engine

from draupnir.core.application.orchestrator import (
    ConcurrentTransitionError,
    OrchestrationError,
    Orchestrator,
    RunFacts,
)
from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import (
    GuardRefusedError,
    IllegalTransitionError,
    RunState,
)
from draupnir.core.infrastructure.config import get_settings
from draupnir.core.infrastructure.orchestration import for_connection
from draupnir.core.infrastructure.repositories import LedgerRepository, SiteRepository
from draupnir.hodd.reconcile import require_vault
from draupnir.hodd.stores import PosixStoreDriver
from draupnir.motsognir import execution
from draupnir.motsognir.placement import Estate
from draupnir.motsognir.supply import Action, SupplyMonitor, read_status_file
from draupnir.worker import duties, stages
from draupnir.worker.duties import Duty, Finding, Timetable
from draupnir.worker.stages import Context, Outcome, Result

logger = structlog.get_logger(__name__)

#: What one committed piece of work returns. Named so that `_commit` hands the
#: caller back its own type rather than `Any`.
_T = TypeVar("_T")

#: The defaults `WorkerSettings` carries, as module constants rather than as
#: class attributes: the settings are a slotted dataclass, so `cls.interval` is
#: not a readable default and the environment reader needs one.
DEFAULT_ACTOR = "worker@veldris.internal"
DEFAULT_INTERVAL = 5.0
DEFAULT_SCRATCH = Path("build") / "worker"
DEFAULT_FABRIC_BASELINE_GBPS = 0.0

#: The order runs are worked in. Later states first, so that finishing work
#: frees capacity before queued work asks for it, within the same tick.
ORDER: tuple[RunState, ...] = (
    RunState.TRAINING,
    RunState.TRAINED,
    RunState.EVALUATING,
    RunState.MERGED,
    RunState.QUANTISED,
    RunState.QUEUED,
)

#: The transition string a supply transfer is recorded under.
SUPPLY_TRANSFER = "supply.transfer"


def _now() -> datetime:
    """The current instant, with an explicit offset. SAD 11E.2."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    """What one worker process needs to know.

    A frozen record read once at start rather than consulted per tick: a
    dispatcher whose timeout changed underneath it would be a dispatcher whose
    behaviour cannot be reproduced from its logs.
    """

    site_id: str = "sindri"
    actor: str = DEFAULT_ACTOR
    database_url: str = ""
    #: Seconds between ticks. Tens of jobs a day (SAD 11.4) does not need a
    #: broker, and it does not need a fast poll either.
    interval: float = DEFAULT_INTERVAL
    scratch: Path = DEFAULT_SCRATCH
    timeout: float = execution.DEFAULT_TIMEOUT_SECONDS
    #: The commissioned fabric bandwidth in GB/s. Zero means none is recorded,
    #: and the probe then reports a reading without an alarm.
    fabric_baseline_gbps: float = DEFAULT_FABRIC_BASELINE_GBPS
    #: Where the supply daemon writes its status block, if one is fitted.
    supply_status: Path | None = None
    #: Where the HODD vault is mounted. None means this installation has none,
    #: and the capacity duty is skipped rather than alarming every quarter hour
    #: about an NFS export that was never there.
    vault_root: Path | None = None
    #: Set false to run the runs and skip the periodic duties, which is what a
    #: second worker on the same site should do: one of them verifies.
    perform_duties: bool = True

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> WorkerSettings:
        """Read the settings, deferring to the process-wide configuration.

        The site and the database come from `core.infrastructure.config`, so a
        worker and the API it shares a chain with cannot be pointed at
        different databases by configuring one of them.
        """
        source = os.environ if environ is None else environ
        shared = get_settings()
        supply = source.get("DRAUPNIR_WORKER_SUPPLY_STATUS", "").strip()
        vault = source.get("DRAUPNIR_VAULT_ROOT", shared.vault_root).strip()
        return cls(
            site_id=source.get("DRAUPNIR_SITE_ID", shared.site_id),
            actor=source.get("DRAUPNIR_WORKER_ACTOR", DEFAULT_ACTOR),
            database_url=source.get("DRAUPNIR_DATABASE_URL_SYNC", shared.database_url_sync),
            interval=float(source.get("DRAUPNIR_WORKER_INTERVAL", str(DEFAULT_INTERVAL))),
            scratch=Path(source.get("DRAUPNIR_WORKER_SCRATCH", str(DEFAULT_SCRATCH))),
            timeout=float(
                source.get("DRAUPNIR_WORKER_TIMEOUT", str(execution.DEFAULT_TIMEOUT_SECONDS))
            ),
            fabric_baseline_gbps=float(
                source.get(
                    "DRAUPNIR_WORKER_FABRIC_BASELINE_GBPS", str(DEFAULT_FABRIC_BASELINE_GBPS)
                )
            ),
            supply_status=Path(supply) if supply else None,
            vault_root=Path(vault) if vault else None,
            perform_duties=source.get("DRAUPNIR_WORKER_DUTIES", "1").strip()
            not in {"0", "false", "FALSE", "no"},
        )


@dataclass(frozen=True, slots=True)
class TickReport:
    """What one tick did. The unit of the worker's log."""

    at: datetime
    outcomes: tuple[Outcome, ...] = ()
    findings: tuple[Finding, ...] = ()
    supply: tuple[Action, ...] = ()
    dispatching: bool = True

    @property
    def moved(self) -> tuple[Outcome, ...]:
        """Runs whose state changed. What an operator watching wants to see."""
        return tuple(item for item in self.outcomes if item.result is Result.MOVED)

    @property
    def alarms(self) -> tuple[Finding, ...]:
        """Duty findings that alarm."""
        return duties.alarms(self.findings)

    @property
    def idle(self) -> bool:
        """Whether the tick found nothing to do. True most of the time."""
        return not self.moved and not self.alarms and not self.supply

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, for a log line and for `draupnirctl`."""
        return {
            "at": self.at.isoformat(),
            "dispatching": self.dispatching,
            "outcomes": [item.as_payload() for item in self.outcomes],
            "findings": [item.as_payload() for item in self.findings],
            "supply": [item.as_payload() for item in self.supply],
        }


def ordered(facts: Iterable[RunFacts]) -> tuple[RunFacts, ...]:
    """The runs the worker acts on, in the order it acts on them.

    Any actionable state missing from `ORDER` is worked last rather than
    dropped. A stage added to the table and forgotten here should run late, not
    never: a run that silently stopped moving is the harder failure to find.
    """
    rank = {state: index for index, state in enumerate(ORDER)}
    actionable = stages.actionable()
    return tuple(
        sorted(
            (item for item in facts if item.state in actionable),
            key=lambda item: (rank.get(item.state, len(ORDER)), item.run_id.hex),
        )
    )


def tick(orchestrator: Orchestrator, context: Context) -> tuple[Outcome, ...]:
    """Do one thing to each run this site holds that needs something done.

    Returns every outcome, including the ones that did nothing, because "this
    run was looked at and there was nothing to do" and "this run was not looked
    at" are different facts and only one of them is fine.
    """
    outcomes: list[Outcome] = []
    for facts in ordered(orchestrator.facts_of(run_id) for run_id in orchestrator.runs()):
        outcomes.append(stages.advance(context, facts))
    return tuple(outcomes)


# ---------------------------------------------------------------------------
# The periodic duties
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckedVault:
    """A vault that establishes it is the vault before reporting capacity.

    The store driver refuses when its root is absent, which catches a dropped
    mount. It cannot catch the other one: a directory an operator created on
    the mount point answers every capacity question with the control plane's
    own local disk. `require_vault` reads the marker, so the duty alarms on
    both rather than on the loud one.
    """

    store: PosixStoreDriver

    def free_bytes(self) -> int:
        """Bytes available, once the vault has been established as the vault."""
        require_vault(self.store)
        return self.store.free_bytes()

    def total_bytes(self) -> int:
        """Total capacity."""
        return self.store.total_bytes()


@dataclass
class Maintenance:
    """What the periodic duties of SAD 11.3 are performed against.

    Every field is optional, and a duty whose subject is absent is skipped
    rather than failed: a development worker has no vault and no fabric, and a
    worker that alarmed about their absence would train an operator to ignore
    its alarms.
    """

    chain: duties.Chain | None = None
    vault: duties.Vault | None = None
    scheduler: Any = None
    workdir: Path = field(default_factory=lambda: Path("build") / "worker")
    last_anchored_at: datetime | None = None
    fabric_baseline_gbps: float = 0.0

    def perform(
        self, duty: Duty, *, now: datetime
    ) -> tuple[Finding | None, tuple[duties.Due, ...]]:
        """Do one duty, or return None where its subject is not present."""
        if duty is Duty.CHAIN and self.chain is not None:
            return duties.verify(self.chain), ()
        if duty is Duty.VAULT and self.vault is not None:
            return duties.capacity(self.vault), ()
        if duty is Duty.ANCHOR and self.last_anchored_at is not None:
            return duties.freshness(self.last_anchored_at, now=now), ()
        if duty is Duty.FABRIC and self.scheduler is not None:
            self.workdir.mkdir(parents=True, exist_ok=True)
            return (
                duties.probe(
                    self.scheduler,
                    workdir=self.workdir,
                    baseline_gbps=self.fabric_baseline_gbps,
                ),
                (),
            )
        if duty is Duty.RETENTION and self.chain is not None:
            return duties.sweep(self.chain, now=now)
        return None, ()


def maintain(
    orchestrator: Orchestrator,
    maintenance: Maintenance,
    timetable: Timetable,
    *,
    now: datetime,
) -> tuple[Finding, ...]:
    """Perform every duty that is due, and record what has to be recorded.

    An alarm becomes a ledger entry against the site; a retention proposal
    becomes one against the corpus. Everything else is a log line. See the
    `duties` module for why that line is drawn there.
    """
    found: list[Finding] = []
    for duty in timetable.outstanding(now):
        finding, due = maintenance.perform(duty, now=now)
        timetable.mark(duty, now)
        if finding is None:
            continue
        found.append(finding)

        if finding.alarm:
            orchestrator.record(
                subject_type=duties.SITE_SUBJECT,
                subject_id=orchestrator.site_id,
                transition=duties.ALARM_RAISED,
                payload=finding.as_payload(),
            )
        for item in due:
            orchestrator.record(
                subject_type=duties.CORPUS_SUBJECT,
                subject_id=item.corpus_sha256,
                transition=duties.RETENTION_PROPOSED,
                payload=item.as_payload(),
            )
    return tuple(found)


# ---------------------------------------------------------------------------
# The process
# ---------------------------------------------------------------------------


class Worker:
    """One worker process against one site's chain.

    Safe to run twice. Every append serialises on the site's advisory lock and
    every stage is guarded by SAD 6.1, so two workers that both notice a queued
    run place it once between them: the second one's transition is refused
    because the run is no longer QUEUED.
    """

    def __init__(
        self,
        settings: WorkerSettings,
        *,
        scheduler: Any = None,
        engine: Engine | None = None,
        estate: Estate | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        """Build a worker. Nothing is connected and nothing is dispatched yet."""
        self.settings = settings
        self.timetable = Timetable()
        self.monitor = SupplyMonitor()
        self._scheduler = scheduler
        self._engine = engine
        self._owns_engine = engine is None
        self._estate = estate or Estate(site=settings.site_id)
        self._clock = clock
        self._stopped = False

    # -- resources ----------------------------------------------------------

    @property
    def engine(self) -> Engine:
        """The database engine, created on first use."""
        if self._engine is None:
            url = self.settings.database_url or get_settings().database_url_sync
            self._engine = create_engine(url, future=True)
        return self._engine

    @property
    def scheduler(self) -> Any:
        """The schedule driver work is placed through.

        Resolved on first use rather than at construction, so that a worker can
        be built in a test that never dispatches anything without the driver
        being installed.
        """
        if self._scheduler is None:
            # Imported here rather than at the top of the module: the driver is
            # a plug-in, and a control plane that could not start without one
            # installed would have the dependency the entry point group exists
            # to avoid (SAD 8.2).
            import draupnir_local_subprocess

            self._scheduler = draupnir_local_subprocess.driver
        return self._scheduler

    def close(self) -> None:
        """Release the engine, if this worker made one."""
        if self._engine is not None and self._owns_engine:
            self._engine.dispose()
            self._engine = None

    def stop(self) -> None:
        """Ask `serve` to return after the tick it is in. Idempotent."""
        self._stopped = True

    # -- one tick -----------------------------------------------------------

    def run_once(self) -> TickReport:
        """One tick: supply, then runs, then duties. Each in its own transaction."""
        now = self._clock()
        outcomes: list[Outcome] = []
        findings: tuple[Finding, ...] = ()

        with self.engine.connect() as connection:
            work, placements = self._survey(connection)
            actions = self._observe_supply(now, placements)
            dispatching = self.monitor.may_dispatch()

            if actions:
                self._record_transfer(connection, actions)

            for facts in work:
                outcomes.append(self._advance(connection, facts, dispatching=dispatching))

            if self.settings.perform_duties:
                findings = self._maintain(connection, now=now)

        report = TickReport(
            at=now,
            outcomes=tuple(outcomes),
            findings=findings,
            supply=actions,
            dispatching=dispatching,
        )
        self._log(report)
        return report

    def serve(self, *, iterations: int | None = None) -> tuple[TickReport, ...]:
        """Tick until asked to stop, or for a fixed number of ticks.

        A tick that raises is logged and the loop continues. SAD 11.2 row 1
        expects a control plane that comes back rather than one that stays down,
        and a transient database error is not a reason to stop moving every run
        on the estate.
        """
        reports: list[TickReport] = []
        count = 0
        while not self._stopped and (iterations is None or count < iterations):
            count += 1
            try:
                reports.append(self.run_once())
            except Exception:
                logger.exception("worker.tick.failed", site=self.settings.site_id)
            if self._stopped or (iterations is not None and count >= iterations):
                break
            time.sleep(self.settings.interval)
        return tuple(reports)

    # -- internals ----------------------------------------------------------

    def _context(self, orchestrator: Orchestrator, may_dispatch: bool) -> Context:
        return Context(
            orchestrator=orchestrator,
            scheduler=self.scheduler,
            scratch=self.settings.scratch,
            estate=self._estate,
            may_dispatch=may_dispatch,
            timeout=self.settings.timeout,
        )

    def _orchestrator(self, connection: Connection) -> Orchestrator:
        """One orchestrator over this connection, scoped to this worker's site."""
        return for_connection(
            connection, SiteScope(self.settings.site_id), actor=self.settings.actor
        )

    def _survey(
        self, connection: Connection
    ) -> tuple[tuple[RunFacts, ...], dict[UUID, dict[str, Any] | None]]:
        """Read what there is to do, in a transaction that writes nothing.

        The placements come back with it because the supply monitor needs the
        scheduler's names for the running jobs before anything is dispatched:
        SAD 11.2's last row checkpoints running work by name on a transfer.
        """
        transaction = connection.begin()
        try:
            orchestrator = self._orchestrator(connection)
            context = self._context(orchestrator, True)
            work = ordered(orchestrator.facts_of(run_id) for run_id in orchestrator.runs())
            placements = {
                facts.run_id: stages.placement_of(context, facts)
                for facts in work
                if facts.state is RunState.TRAINING
            }
        finally:
            transaction.rollback()
        return work, placements

    def _advance(self, connection: Connection, facts: RunFacts, *, dispatching: bool) -> Outcome:
        """Do one thing to one run, in its own transaction."""

        def act(orchestrator: Orchestrator) -> Outcome:
            return stages.advance(self._context(orchestrator, dispatching), facts)

        outcome = self._commit(connection, act)
        if outcome is None:
            return Outcome(facts.run_id, Result.DEFERRED, "another writer moved it first")
        return outcome

    def _maintain(self, connection: Connection, *, now: datetime) -> tuple[Finding, ...]:
        """Perform the periodic duties that are due, in their own transaction."""

        def act(orchestrator: Orchestrator) -> tuple[Finding, ...]:
            return maintain(orchestrator, self._maintenance(connection), self.timetable, now=now)

        return self._commit(connection, act) or ()

    def _record_transfer(self, connection: Connection, actions: Sequence[Action]) -> None:
        """Record a supply transfer, in its own transaction."""

        def act(orchestrator: Orchestrator) -> None:
            self._record_supply(orchestrator, actions)

        self._commit(connection, act)

    def _maintenance(self, connection: Connection) -> Maintenance:
        """Build what the duties are performed against, for this connection."""
        scope = SiteScope(self.settings.site_id)
        site = next(
            (item for item in SiteRepository(connection).all() if item.id == scope.site_id), None
        )
        return Maintenance(
            chain=LedgerRepository(connection, scope),
            vault=self._vault(),
            scheduler=self.scheduler,
            workdir=self.settings.scratch / "probe",
            last_anchored_at=site.last_anchored_at if site else None,
            fabric_baseline_gbps=self.settings.fabric_baseline_gbps,
        )

    def _vault(self) -> CheckedVault | None:
        """The vault the capacity duty reads, if this installation has one."""
        root = self.settings.vault_root
        if root is None:
            return None
        return CheckedVault(PosixStoreDriver(root=root, local_site=self.settings.site_id))

    def _observe_supply(
        self, now: datetime, placements: Mapping[UUID, dict[str, Any] | None]
    ) -> tuple[Action, ...]:
        """Read the supply, if one is fitted, and tell the monitor about it."""
        path = self.settings.supply_status
        if path is None or not path.is_file():
            return ()
        running = tuple(str(item["job_id"]) for item in placements.values() if item is not None)
        return self.monitor.observe(read_status_file(path, at=now), running)

    def _record_supply(self, orchestrator: Orchestrator, actions: Sequence[Action]) -> None:
        """Record a transfer. SAD 11.3 alarms on it; the chain keeps it."""
        orchestrator.record(
            subject_type=duties.SITE_SUBJECT,
            subject_id=orchestrator.site_id,
            transition=SUPPLY_TRANSFER,
            payload={
                "actions": [action.as_payload() for action in actions],
                "dispatching": self.monitor.may_dispatch(),
                "halted": self.monitor.is_halted(),
            },
        )

    def _commit(self, connection: Connection, work: Callable[[Orchestrator], _T]) -> _T | None:
        """Run one piece of work in its own transaction.

        A refusal rolls that transaction back and returns None. Refusals are
        expected here in a way they are not in an API: two workers racing for
        the same run is the normal case, and the loser finds the run already
        moved, which is a guard refusing rather than anything going wrong.
        """
        transaction = connection.begin()
        try:
            result = work(self._orchestrator(connection))
        except (
            ConcurrentTransitionError,
            GuardRefusedError,
            IllegalTransitionError,
            OrchestrationError,
        ) as refusal:
            transaction.rollback()
            logger.info("worker.refused", site=self.settings.site_id, reason=str(refusal))
            return None
        except Exception:
            transaction.rollback()
            raise
        transaction.commit()
        return result

    def _log(self, report: TickReport) -> None:
        """One line per tick that did something, and none for a quiet one."""
        for outcome in report.moved:
            logger.info("worker.moved", site=self.settings.site_id, **outcome.as_payload())
        for finding in report.findings:
            (logger.warning if finding.alarm else logger.debug)(
                "worker.duty", site=self.settings.site_id, **finding.as_payload()
            )
        for action in report.supply:
            logger.warning("worker.supply", site=self.settings.site_id, **action.as_payload())


__all__ = [
    "ORDER",
    "SUPPLY_TRANSFER",
    "CheckedVault",
    "Maintenance",
    "TickReport",
    "Worker",
    "WorkerSettings",
    "maintain",
    "ordered",
    "tick",
]
