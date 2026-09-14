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
from functools import cached_property
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
from draupnir.core.domain.federation import ANCHOR_SUBMITTED as _ANCHOR_SUBMITTED
from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import (
    GuardRefusedError,
    IllegalTransitionError,
    RunState,
)
from draupnir.core.infrastructure.config import get_settings
from draupnir.core.infrastructure.orchestration import for_connection
from draupnir.core.infrastructure.repositories import LedgerRepository
from draupnir.hodd.reconcile import require_vault
from draupnir.hodd.stores import PosixStoreDriver
from draupnir.motsognir import arrays, execution
from draupnir.motsognir.placement import Estate, estate_for
from draupnir.motsognir.supply import (
    Action,
    SupplyError,
    SupplyMonitor,
    read_status_file,
    signal_lost,
)
from draupnir.worker import array_queue, corpora, duties, stages
from draupnir.worker.duties import Duty, Finding, Timetable
from draupnir.worker.measurements import MeasurementStore
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

#: The transition a declared ring is recorded under.
#:
#: In the chain rather than only in a log, because the ring's size is a
#: property of every substrate run placed while it holds. A forge that quietly
#: became a two-node ring is a forge whose later runs are not comparable to its
#: earlier ones -- three ranks and two ranks are different collectives, with
#: different step times and different numerics -- and "was this a two-node
#: ring" is a question asked months later about a specific release.
#:
#: Recorded once per worker, when it first observes the configuration it was
#: started with. The declaration comes from `draupnir.env`, so it changes when
#: an operator changes it and the units are restarted, which is exactly the
#: event worth a row.
RING_DECLARED = "site.ring.declared"

#: The transition an anchoring attempt is recorded under. RF-07.
#:
#: Both outcomes, not only the successful one. A rejection is what a
#: partitioned forge produces, and a chain that recorded only successes could
#: not distinguish "never tried" from "tried and was refused" -- which are the
#: two states an operator most needs told apart during an outage.
#: Re-exported from the domain, which owns the name. The orchestrator reads
#: these entries back to decide whether a release may publish, and the core may
#: not import the worker -- so the constant lives there and this is the worker's
#: view of it rather than a second spelling.
ANCHOR_SUBMITTED = _ANCHOR_SUBMITTED

#: How long an anchor round trip is given. AC-N11 budgets one second over
#: WireGuard; this is the transport's patience, not the budget, and it is short
#: because a worker tick must not block on a link that is down.
REGISTRY_TIMEOUT_SECONDS = 10.0


def _corpus_queue(ledger: LedgerRepository) -> tuple[Any, ...]:
    """Every accepted corpus request no outcome has closed, oldest first.

    One read of the corpus entries per tick rather than a query per request.
    The chain is the queue (RF-12): the entries are the record of what was
    asked for and what was done about it, so a restarted worker finds the same
    queue and two workers reach the same answer.

    Ingests before curations, and each in acceptance order: a curation asked
    for before its ingest completed depends on it, and doing the two in the
    order the chain records them is what makes that work rather than fail.
    """
    entries = ledger.entries_of_type(corpora.CORPUS_SUBJECT)
    return (
        *corpora.outstanding(entries, accepted=corpora.INGEST_ACCEPTED),
        *corpora.outstanding(entries, accepted=corpora.CURATE_ACCEPTED),
    )


def _array_queue(ledger: LedgerRepository) -> tuple[Any, ...]:
    """Every accepted array request no outcome has closed, oldest first.

    Submissions before requeues, and each in acceptance order: a requeue of an
    element of an array that has not been submitted yet depends on it, and
    doing the two in the order the chain records them is what makes that work
    rather than fail.
    """
    entries = ledger.entries_of_type(arrays.ARRAY_SUBJECT)
    return (
        *array_queue.outstanding(entries, accepted_transition=arrays.ARRAY_ACCEPTED),
        *array_queue.outstanding(entries, accepted_transition=arrays.ELEMENT_REQUEUE_ACCEPTED),
    )


def _baselines_from(orchestrator: Orchestrator) -> Any:
    """The baselines this site has captured, rebuilt from the chain. RF-10.

    Per tick rather than once, unlike the registry and the store: a baseline
    can be captured while a worker is running, and a worker holding a stale
    registry would judge against the number it started with. It is one indexed
    read of a handful of rows.

    A payload that will not reconstruct is skipped and logged rather than
    raised on: one malformed baseline must not stop every run on the estate,
    and a gate with no baseline is already refused in its own words by
    `Gate.holds`.
    """
    from draupnir.raun.baselines import BaselineError, BaselineRegistry, from_payload

    registry = BaselineRegistry()
    for payload in orchestrator.baseline_payloads():
        try:
            registry.capture(from_payload(payload), replace_existing=True)
        except BaselineError:
            logger.warning("baseline.unreadable", site=orchestrator.site_id)
    return registry


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
    #: The generic resource type Slurm knows this estate's accelerator by, from
    #: `gres.conf` on REGIN. Empty where the scheduler declares no type, and a
    #: job then asks for an untyped count.
    accelerator: str = ""
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
    #: Absolute path to `all_reduce_perf` on the appliances. Empty at a forge
    #: with no fabric, and the probe then reports it unmeasured rather than
    #: alarming about a cable that does not exist.
    fabric_probe_binary: str = ""
    #: The NCCL bootstrap interface and the RoCE devices, from the estate. SAD
    #: 48.2 warns that a driver update can rename these, so they are settings
    #: rather than constants: correcting one is a configuration change.
    fabric_interface: str = ""
    fabric_hca: str = ""
    #: The appliances cabled into the ring, when the forge declares them.
    #: Empty means all of them, which is the ordinary case.
    #:
    #: Named rather than counted: ring membership is which machines have a DAC
    #: cable between them, and a count cannot say which two. See
    #: `placement.Estate.ring_members`.
    ring_members: tuple[str, ...] = ()
    #: Where the supply daemon writes its status block, if one is fitted.
    supply_status: Path | None = None
    #: Where the HODD vault is mounted. None means this installation has none,
    #: and the capacity duty is skipped rather than alarming every quarter hour
    #: about an NFS export that was never there.
    vault_root: Path | None = None
    #: Set false to run the runs and skip the periodic duties, which is what a
    #: second worker on the same site should do: one of them verifies.
    perform_duties: bool = True
    #: Where MEGINGJORD answers. Empty means this forge has no federation link,
    #: and the anchor duty says so rather than pretending -- which is the
    #: honest state for a development machine and for a forge whose WireGuard
    #: tunnel is not built yet (RF-E21).
    registry_url: str = ""
    #: Where a curator drops a jurisdiction's retrieved sources, one directory
    #: per ISO 3166-1 alpha-3 code. `None` means this worker performs no
    #: ingest, and the duty says so against the request rather than leaving it
    #: silently outstanding (RF-12).
    #:
    #: A directory rather than a fetch: retrieving a corpus is outbound traffic
    #: to a host no allow-list entry covers, and threat T11 makes that the
    #: broker's decision rather than a duty's. On an air-gapped forge the
    #: curator copies the files in.
    incoming_root: Path | None = None
    #: Where the evaluation sets are, for decontamination. `None` means
    #: curation refuses: a corpus curated without that check is one whose
    #: evaluation scores measure the overlap rather than the model, and SAD 6.1
    #: makes `decontamination_confirmed` a guard rather than a note.
    evaluation_sets: Path | None = None
    #: Run the development executor rather than the driver each specification
    #: names. RF-10.
    #:
    #: For `make procedure` on a machine with no GPU and no training framework,
    #: and for nothing else. Every use logs `executor.stand-in` at warning
    #: level and the chain records `development-stand-in` as the executor, so a
    #: run that was simulated says so for as long as the chain does. A
    #: simulation nobody is told about is the problem; one that announces
    #: itself is a development tool.
    stand_in: bool = False
    #: Where the secrets this estate brokers are held: a JSON object of name
    #: to value, readable only by the worker's user. `None` at Sindri, which
    #: brokers none to a training job today.
    #:
    #: A file rather than the process environment, because the environment of a
    #: long-lived process is readable from `/proc` by anything running as the
    #: same user and is inherited by every child it spawns -- which is the
    #: opposite of what a lease is for. In deployment this is a hardware-backed
    #: key store reached over mTLS (SAD 9.5); what the broker hands out does not
    #: change with it.
    secret_store: Path | None = None
    #: The site's Ed25519 signing key, in PKCS#8 PEM. An unsigned head is
    #: refused by MEGINGJORD in as many words -- "not a claim about a chain, it
    #: is a packet" -- so a forge with no key has no federation link rather
    #: than an anonymous one. The key names itself: `signing.key_id` derives
    #: the identifier from the public half, so there is no second setting to
    #: keep in step with it.
    signing_key: Path | None = None

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
        signing_key = source.get("DRAUPNIR_SITE_SIGNING_KEY", "").strip()
        secret_store = source.get("DRAUPNIR_SECRET_STORE", "").strip()
        incoming = source.get("DRAUPNIR_INCOMING_ROOT", "").strip()
        evaluation_sets = source.get("DRAUPNIR_EVALUATION_SETS", "").strip()
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
            fabric_probe_binary=source.get("DRAUPNIR_FABRIC_PROBE_BINARY", "").strip(),
            fabric_interface=source.get("DRAUPNIR_FABRIC_INTERFACE", "").strip(),
            fabric_hca=source.get("DRAUPNIR_FABRIC_HCA", "").strip(),
            # DRAUPNIR_FABRIC_BASELINE_GBPS, like the other probe settings, is
            # what install.sh writes. The worker read only the WORKER_ spelling,
            # so an installed site's baseline was always zero (RF-28); that
            # spelling is still honoured for a site configured by hand.
            fabric_baseline_gbps=float(
                source.get(
                    "DRAUPNIR_FABRIC_BASELINE_GBPS",
                    source.get(
                        "DRAUPNIR_WORKER_FABRIC_BASELINE_GBPS", str(DEFAULT_FABRIC_BASELINE_GBPS)
                    ),
                )
            ),
            accelerator=source.get("DRAUPNIR_ACCELERATOR", shared.accelerator).strip(),
            ring_members=tuple(
                part.strip()
                for part in source.get("DRAUPNIR_RING_MEMBERS", shared.ring_members).split(",")
                if part.strip()
            ),
            supply_status=Path(supply) if supply else None,
            vault_root=Path(vault) if vault else None,
            perform_duties=source.get("DRAUPNIR_WORKER_DUTIES", "1").strip()
            not in {"0", "false", "FALSE", "no"},
            registry_url=source.get("DRAUPNIR_REGISTRY_URL", "").strip(),
            signing_key=Path(signing_key) if signing_key else None,
            secret_store=Path(secret_store) if secret_store else None,
            incoming_root=Path(incoming) if incoming else None,
            evaluation_sets=Path(evaluation_sets) if evaluation_sets else None,
            stand_in=source.get("DRAUPNIR_WORKER_STAND_IN", "").strip()
            in {"1", "true", "TRUE", "yes"},
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
    fabric: duties.FabricProbe = field(default_factory=duties.FabricProbe)
    #: Which site this maintenance is for. The key sweep is site scoped like
    #: every other write, so it needs to know whose keys it is dropping.
    site_id: str = "sindri"
    #: The federation agent and the registry it reaches. `None` at a forge with
    #: no link, where the anchor duty alarms rather than pretending.
    agent: Any = None
    registry: Any = None
    #: Where corpus work reads from and writes to. `None` on a worker with no
    #: vault, where the duty reports the accepted work it cannot perform rather
    #: than leaving it silently outstanding (RF-12).
    workspace: Any = None
    #: What the corpus duty did this tick, for the loop to record. One entry
    #: per request, both outcomes.
    corpus_outcomes: tuple[Any, ...] = ()
    #: What the array duty did this tick. Same shape, same reason.
    array_outcomes: tuple[Any, ...] = ()
    #: The site's private key, used to sign the head before it is submitted.
    #: MEGINGJORD refuses an unsigned head, so a submission built without this
    #: would be rejected on arrival every time -- and the rejection would read
    #: like a federation problem rather than a missing setting.
    signing_key: Any = None
    #: Reads the outstanding corpus requests out of the chain. A callable
    #: rather than the chain itself, because what is outstanding is a question
    #: about entries the `Chain` protocol deliberately does not expose.
    corpora: Any = None
    #: The same for arrays, and what performs them.
    array_requests: Any = None
    submitter: Any = None
    #: The idempotency store whose expired records this sweeps. `None` on a
    #: worker that shares no database with an API, where there are no keys.
    keys: Any = None
    #: Where the periodic readings go, so `/metrics` can report them (RF-18).
    #: Two of SAD 11.3's signals cannot be read at scrape time -- verifying the
    #: chain is this duty's whole cost, and asking an NFS mount how full it is
    #: can block uninterruptibly -- so the worker measures and the API reads
    #: what it wrote. `None` in a test that is not exercising that path.
    measurements: Any = None
    #: What the retention duty carried out or refused this tick (RF-27), for
    #: the loop to record. Same shape, and same reason, as the corpus outcomes.
    retention_outcomes: tuple[Any, ...] = ()

    #: What the last anchoring attempt produced, for the loop to record.
    anchored: duties.Anchored | None = None

    def _head(self) -> Any:
        """The chain head to anchor, or `None` where there is no chain."""
        if self.chain is None or self.agent is None:
            return None

        from datetime import UTC
        from datetime import datetime as _datetime

        from draupnir.core.domain.federation import AnchorSubmission
        from draupnir.core.domain.ledger import ChainHead

        found = self.chain.head()
        if found is None:
            return None

        head = ChainHead(site_id=self.agent.site_id, seq=found.seq, entry_hash=found.entry_hash)
        if self.signing_key is None:
            return None

        from draupnir.svalinn.signing import sign_chain_head

        signed = sign_chain_head(head, self.signing_key)
        return AnchorSubmission(
            head=head,
            previous_hash=found.prev_hash,
            submitted_at=_datetime.now(UTC),
            signature=signed.signature,
            key_id=signed.key_id,
        )

    def _drain_corpus_queue(self, *, now: datetime) -> tuple[Finding | None, tuple[Any, ...]]:
        """Do the corpus work the chain says was accepted. RF-12.

        The chain is the queue: an accepted entry is outstanding until an entry
        naming its sequence says otherwise. Nothing is held between ticks, so a
        restarted worker reads the same queue.

        No finding when the queue is empty, which is the ordinary case: a duty
        that logged "nothing to do" every minute would be the noise SAD 11.3's
        recording rule exists to avoid. A failure *is* a finding, and it alarms:
        a curator waiting on an ingest that will never complete is exactly who
        an alarm is for.
        """
        del now
        if self.corpora is None or self.workspace is None:
            return None, ()

        requests = self.corpora()
        if not requests:
            return None, ()

        outcomes = corpora.perform(requests, self.workspace)
        failed = [item for item in outcomes if not item.succeeded]
        detail = f"{len(outcomes) - len(failed)} of {len(outcomes)} corpus request(s) performed"
        if failed:
            named = ", ".join(
                f"{item.request.jurisdiction} ({item.request.transition})" for item in failed
            )
            detail = f"{detail}; {named} failed"
        return (
            Finding(
                Duty.CORPORA,
                bool(failed),
                detail,
                {"performed": len(outcomes), "failed": len(failed)},
            ),
            outcomes,
        )

    def _sweep_keys(self, *, now: datetime) -> Finding | None:
        """Drop idempotency records past their twenty-four hours. RF-14.

        No finding when nothing went, which is the ordinary case: a duty that
        recorded "swept nothing" hourly would be the noise SAD 11.3's recording
        rule exists to avoid. A sweep that *did* remove records is a reading
        rather than an alarm -- keys expiring is the system working, and the
        number is worth a log line so that a table growing without bound has
        somewhere to be noticed.

        A store that cannot be swept is not an alarm either. The keys expire by
        age whether or not anything deletes them: `reserve` takes over an
        expired row, so a failed sweep costs disk and never correctness.
        """
        try:
            removed = self.keys.purge(now, site_id=self.site_id)
        except Exception as unavailable:
            logger.warning("idempotency.sweep.failed", reason=str(unavailable))
            return None

        if not removed:
            return None
        return Finding(
            Duty.KEYS,
            False,
            f"{removed} expired idempotency record(s) removed",
            {"removed": removed},
        )

    def _drain_array_queue(self) -> tuple[Finding | None, tuple[Any, ...]]:
        """Submit accepted arrays and requeue accepted elements. RF-13.

        Nothing when the queue is empty. A refusal alarms: an operator who
        asked for an array or a requeue and got a 202 is watching the board,
        and on this estate the requeue refusal is the *expected* one --
        slurmrestd exposes no requeue, and what the entry carries is the
        instruction to run `scontrol requeue <job>_<index>` on REGIN (AC-F6).
        """
        if self.array_requests is None or self.submitter is None:
            return None, ()

        requests = self.array_requests()
        if not requests:
            return None, ()

        outcomes = array_queue.perform(requests, self.submitter)
        failed = [item for item in outcomes if not item.succeeded]
        detail = f"{len(outcomes) - len(failed)} of {len(outcomes)} array request(s) performed"
        if failed:
            detail = f"{detail}; {', '.join(item.subject for item in failed)} refused"
        return (
            Finding(
                Duty.ARRAYS,
                bool(failed),
                detail,
                {"performed": len(outcomes), "refused": len(failed)},
            ),
            outcomes,
        )

    def perform(
        self, duty: Duty, *, now: datetime
    ) -> tuple[Finding | None, tuple[duties.Due, ...]]:
        """Do one duty, or return None where its subject is not present."""
        if duty is Duty.CHAIN and self.chain is not None:
            return duties.verify(self.chain), ()
        if duty is Duty.VAULT and self.vault is not None:
            return duties.capacity(self.vault), ()
        if duty is Duty.ANCHOR:
            # Anchor first, then report freshness against what just happened.
            # This used to be freshness alone, gated on `last_anchored_at`
            # being set -- and nothing ever set it, so the duty was a no-op
            # that alarmed for ever (RF-07).
            finding, recorded = duties.anchor(
                self.agent,
                self.registry,
                self._head(),
                now=now,
                last_anchored_at=self.last_anchored_at,
            )
            self.anchored = recorded
            return finding, ()
        if duty is Duty.CORPORA:
            drained, outcomes = self._drain_corpus_queue(now=now)
            self.corpus_outcomes = outcomes
            return drained, ()
        if duty is Duty.ARRAYS:
            drained, placed = self._drain_array_queue()
            self.array_outcomes = placed
            return drained, ()
        if duty is Duty.KEYS and self.keys is not None:
            return self._sweep_keys(now=now), ()
        if duty is Duty.FABRIC and self.scheduler is not None:
            self.workdir.mkdir(parents=True, exist_ok=True)
            return (
                duties.probe(
                    self.scheduler,
                    workdir=self.workdir,
                    settings=self.fabric,
                ),
                (),
            )
        if duty is Duty.RETENTION and self.chain is not None:
            # Approved deletions first, then new proposals. The store is the
            # vault corpus work uses, because that is where the raw corpus is.
            store = self.workspace.store if self.workspace is not None else None
            self.retention_outcomes = duties.execute_approved(self.chain, store, now=now)
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

        # The reading, whether or not it alarms (RF-18). This is the half the
        # chain deliberately does not carry: a vault at forty per cent is not a
        # state transition, and a duty appending "nothing wrong" every fifteen
        # minutes is the noise SAD 11.3's recording rule exists to avoid. It is
        # a current measurement, overwritten in place, and the alarm below is
        # what becomes a matter of record.
        if maintenance.measurements is not None:
            try:
                maintenance.measurements.record(
                    site_id=orchestrator.site_id,
                    duty=str(finding.duty),
                    measured_at=now,
                    alarm=finding.alarm,
                    measurements=dict(finding.measurements),
                )
            except Exception as unwritable:
                # Never fatal. A duty that found something has already found
                # it, and losing the scrape's copy must not lose the alarm the
                # ledger entry below carries.
                logger.warning(
                    "duty.measurement.unwritten", duty=str(finding.duty), reason=str(unwritable)
                )

        if finding.alarm:
            orchestrator.record(
                subject_type=duties.SITE_SUBJECT,
                subject_id=orchestrator.site_id,
                transition=duties.ALARM_RAISED,
                payload=finding.as_payload(),
            )

        # The anchoring outcome, whichever way it went. RF-07: anchor state is
        # derived from the chain rather than from a side table, so a rejection
        # is as much a matter of record as a countersignature -- and
        # `Orchestrator.publication_facts` reads `anchored_through` back out of
        # exactly these entries when it decides whether a release may publish
        # (AC-S13, RF-05).
        if duty is Duty.ANCHOR and maintenance.anchored is not None:
            orchestrator.record(
                subject_type=duties.SITE_SUBJECT,
                subject_id=orchestrator.site_id,
                transition=ANCHOR_SUBMITTED,
                payload=maintenance.anchored.as_payload(),
            )
            maintenance.anchored = None
        # Every corpus request that was performed, and every one that was not.
        # Recorded here rather than inside the duty because a duty writes
        # nothing: the chain is written by the transaction that owns it, and a
        # duty that appended would be a duty that had to know about rollback.
        if duty is Duty.CORPORA and maintenance.corpus_outcomes:
            for outcome in maintenance.corpus_outcomes:
                orchestrator.record(
                    subject_type=corpora.CORPUS_SUBJECT,
                    subject_id=outcome.request.jurisdiction,
                    transition=outcome.transition,
                    payload=dict(outcome.payload),
                )
            maintenance.corpus_outcomes = ()

        # Every array request, performed or refused. A refused requeue is the
        # ordinary outcome at Sindri and its entry carries the driver's own
        # message, which names what to run on REGIN instead -- so it is a
        # matter of record rather than a log line somebody has to find.
        if duty is Duty.ARRAYS and maintenance.array_outcomes:
            for placed in maintenance.array_outcomes:
                orchestrator.record(
                    subject_type=arrays.ARRAY_SUBJECT,
                    subject_id=placed.subject,
                    transition=placed.transition,
                    payload=dict(placed.payload),
                )
            maintenance.array_outcomes = ()

        # Every approved deletion carried out, and every one refused, with the
        # reason where it was (RF-27). The approver reads the outcome on S06.
        if duty is Duty.RETENTION and maintenance.retention_outcomes:
            for carried in maintenance.retention_outcomes:
                orchestrator.record(
                    subject_type=duties.CORPUS_SUBJECT,
                    subject_id=carried.corpus_sha256,
                    transition=carried.transition,
                    payload=dict(carried.payload),
                )
            maintenance.retention_outcomes = ()

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
        registry: Any = None,
    ) -> None:
        """Build a worker. Nothing is connected and nothing is dispatched yet.

        `registry` is injected the way `scheduler` is, and for the same reason:
        `Gullinbursti.drain` cannot tell an in-process `AnchorStore` from
        MEGINGJORD over HTTP, so a test drives the real anchor path without a
        network and without the reconnect case being the only one exercised
        during an outage (RF-07, AC-S13).
        """
        self.settings = settings
        self.timetable = Timetable()
        self.monitor = SupplyMonitor()
        self._scheduler = scheduler
        self._injected_registry = registry
        self._engine = engine
        self._owns_engine = engine is None
        self._estate = estate or estate_for(
            settings.site_id, settings.accelerator, settings.ring_members
        )
        #: What this worker has already written a ring row for. `None` until it
        #: has written one, which is what makes the record once-per-declaration
        #: rather than once-per-tick.
        self._ring_recorded: tuple[str, ...] | None = None
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
            self._observe_estate()
            self._record_ring(connection)
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
            baselines=_baselines_from(orchestrator),
            orchestrator=orchestrator,
            scheduler=self.scheduler,
            scratch=self.settings.scratch,
            estate=self._estate,
            may_dispatch=may_dispatch,
            timeout=self.settings.timeout,
            site_id=self.settings.site_id,
            store=self._store,
            secrets=self._secrets,
            registry=self._registry_of_plugins,
            stand_in=self.settings.stand_in,
        )

    @cached_property
    def _registry_of_plugins(self) -> Any:
        """The plug-in registry a specification's driver is resolved through.

        `svalinn.pki.registry` and not a second discovery: it is the one place
        a production registry is built, and it is what `dryRunSpecification`
        calls. Two registries would be two answers to "which driver renders
        this specification", and the whole point of RF-10 is that the plan an
        operator was shown is the plan that is submitted.

        Discovered once. Entry-point discovery reads distribution metadata and
        verifies signatures; doing that four times a minute would turn a
        signing problem into a log flood without making it more visible.

        `None` where discovery fails, which defers every run with the reason
        rather than falling back to the stand-in -- a stand-in substituted
        silently is the finding.
        """
        if self.settings.stand_in:
            return None

        from draupnir.svalinn.pki import registry

        try:
            return registry()
        except Exception:
            logger.exception("worker.plugins.unavailable", site=self.settings.site_id)
            return None

    @cached_property
    def _keys(self) -> Any:
        """The idempotency store this worker sweeps. RF-14.

        The same table the API reserves into, on the worker's own engine. The
        two share a database by construction: a worker pointed at a different
        one would be acting on another forge's chain, which is why
        `WorkerSettings.from_environment` takes the database from the
        process-wide configuration rather than from a setting of its own.
        """
        from draupnir.api.idempotency_store import DatabaseIdempotencyStore

        return DatabaseIdempotencyStore(engine=self.engine)

    @cached_property
    def _workspace(self) -> Any:
        """Where corpus work reads from and writes to. RF-12.

        Built once, because each path is a mount an operator made and checking
        one four times a minute turns a configuration error into a log flood.
        `None` where there is no vault: an ingest needs somewhere to publish
        to, and a workspace that invented a directory would ingest a corpus
        onto the control plane's local disk and report it as vaulted.
        """
        if self._store is None:
            return None

        return corpora.Workspace(
            site_id=self.settings.site_id,
            incoming=self.settings.incoming_root,
            store=self._store,
            evaluation_sets=self.settings.evaluation_sets,
            scratch=self.settings.scratch / "curation",
        )

    @cached_property
    def _secrets(self) -> Any:
        """The secrets broker every plan is checked against. RF-09.

        Always built, even where the store is empty. An empty broker's leak
        check passes vacuously, and that is the point: the check is on the path
        from the day it is written rather than from the day somebody remembers
        to add it, so the first secret this estate brokers is covered by it
        rather than covered later.

        Built once. The broker holds the leases it has issued, and one rebuilt
        per tick would lose the record of what is outstanding -- which is what
        `active` is for and what revoking at the end of a run needs.
        """
        import json

        from draupnir.svalinn.secrets import SecretsBroker

        if self.settings.secret_store is None:
            return SecretsBroker()

        held = json.loads(self.settings.secret_store.read_text(encoding="utf-8"))
        return SecretsBroker(store={str(k): str(v) for k, v in held.items()})

    @cached_property
    def _store(self) -> Any:
        """The vault this worker stages artefacts into. RF-08.

        `None` where none is configured, which is a development machine: the
        stages still run and still record digests, and the outcome says the
        artefacts are not staged. That is a truthful degradation rather than a
        silent one -- a run whose artefacts have no address cannot be released,
        and the publication refusal already says why.

        Built once and not per tick, because a driver's construction is where
        the vault's mount and the bucket's object lock are checked, and doing
        that four times a minute would turn a configuration error into a log
        flood without making it any more visible.

        A vault that is configured and unavailable is *not* softened to `None`.
        `PosixStoreDriver` raises `VaultUnavailableError` on the first stage,
        the run defers, and the next tick asks again -- which is what an NFS
        export coming back should look like.
        """
        if self.settings.vault_root is None:
            return None
        return PosixStoreDriver(root=self.settings.vault_root, local_site=self.settings.site_id)

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
            return maintain(
                orchestrator,
                self._maintenance(connection, orchestrator),
                self.timetable,
                now=now,
            )

        return self._commit(connection, act) or ()

    def _record_transfer(self, connection: Connection, actions: Sequence[Action]) -> None:
        """Record a supply transfer, in its own transaction."""

        def act(orchestrator: Orchestrator) -> None:
            self._record_supply(orchestrator, actions)

        self._commit(connection, act)

    def _maintenance(self, connection: Connection, orchestrator: Orchestrator) -> Maintenance:
        """Build what the duties are performed against, for this connection.

        `last_anchored_at` comes out of the chain (RF-07). It used to come off
        `site.last_anchored_at`, a column nothing ever wrote, so the freshness
        duty read `None` on every tick of every worker that ever ran and
        alarmed for ever about a chain that may well have been anchored twenty
        minutes ago.
        """
        scope = SiteScope(self.settings.site_id)
        return Maintenance(
            chain=LedgerRepository(connection, scope),
            vault=self._vault(),
            scheduler=self.scheduler,
            workdir=self.settings.scratch / "probe",
            last_anchored_at=orchestrator.last_anchored_at(),
            agent=self._agent,
            registry=self._registry,
            signing_key=self._signing_key,
            workspace=self._workspace,
            site_id=self.settings.site_id,
            keys=self._keys,
            # On this connection, so a reading and the alarm entry that may
            # accompany it commit or roll back together (RF-18).
            measurements=MeasurementStore(connection),
            corpora=lambda: _corpus_queue(LedgerRepository(connection, scope)),
            array_requests=lambda: _array_queue(LedgerRepository(connection, scope)),
            submitter=array_queue.Submitter(
                scheduler=self.scheduler,
                estate=self._estate,
                entries=LedgerRepository(connection, scope).entries_of_type(arrays.ARRAY_SUBJECT),
            ),
            fabric=duties.FabricProbe(
                binary=self.settings.fabric_probe_binary,
                interface=self.settings.fabric_interface,
                hca=self.settings.fabric_hca,
                baseline_gbps=self.settings.fabric_baseline_gbps,
            ),
        )

    @cached_property
    def _agent(self) -> Any:
        """The site agent, held for the life of the process. RF-07.

        Held rather than rebuilt per tick because the agent *is* the queue: a
        head submitted during a partition waits in it, and an agent rebuilt
        every tick would drop the queue and re-submit only the current head.
        The reconnect path of AC-S13 drains what accumulated, and there would
        be nothing to drain.

        There is an agent exactly when there is a registry to reach. An agent
        with nowhere to submit queues heads nothing will ever drain, which
        looks like a working federation link right up to the moment somebody
        asks what it anchored.
        """
        if self._registry is None:
            return None

        from draupnir.gullinbursti.agent import Gullinbursti

        return Gullinbursti(
            site_id=self.settings.site_id,
            signing_key_id=self._key_id,
        )

    @cached_property
    def _signing_key(self) -> Any:
        """The site's private key, loaded once. `None` where none is configured.

        A key that will not load is not softened to `None`: a forge configured
        to anchor and unable to read its key is misconfigured, and reporting it
        as "no federation link" would send an operator looking for a tunnel
        that is fine.
        """
        if self.settings.signing_key is None:
            return None

        from draupnir.svalinn.signing import load_private_key

        return load_private_key(self.settings.signing_key.read_bytes())

    @cached_property
    def _key_id(self) -> str:
        """What MEGINGJORD knows this site's key by. Derived, never configured."""
        if self._signing_key is None:
            return ""

        from draupnir.svalinn.signing import key_id

        return key_id(self._signing_key.public_key())

    @cached_property
    def _registry(self) -> Any:
        """MEGINGJORD, reached through the egress broker.

        Through the broker and not around it: an anchor submission is outbound
        traffic to another site, which is threat T11's whole subject. The
        composition root is the only place allowed to put the declaration
        (GULLINBURSTI's purpose) and the decision (SVALINN's allow list)
        together, which is why the client is built here and injected rather
        than constructed inside the driver.
        """
        if self._injected_registry is not None:
            return self._injected_registry
        if not self.settings.registry_url or self.settings.signing_key is None:
            # Both or neither. A registry URL with no key would submit unsigned
            # heads, which MEGINGJORD refuses in as many words -- "not a claim
            # about a chain, it is a packet" -- so the duty's "no federation
            # link configured" alarm is a truer description of that deployment
            # than a stream of rejections would be.
            return None

        import httpx

        from draupnir.gullinbursti.federation import RemoteRegistry
        from draupnir.svalinn.egress import (
            FEDERATION_POLICY,
            FEDERATION_PURPOSE,
            BrokeredClient,
        )

        return RemoteRegistry(
            base_url=self.settings.registry_url,
            client=BrokeredClient(
                inner=httpx.Client(timeout=REGISTRY_TIMEOUT_SECONDS),
                purpose=FEDERATION_PURPOSE,
                approving_policy=FEDERATION_POLICY,
            ),
        )

    def _vault(self) -> CheckedVault | None:
        """The vault the capacity duty reads, if this installation has one."""
        root = self.settings.vault_root
        if root is None:
            return None
        return CheckedVault(PosixStoreDriver(root=root, local_site=self.settings.site_id))

    def _observe_estate(self) -> None:
        """Ask the scheduler which appliances can take work, and believe it.

        RF-E11. `Estate` was a constant with every appliance marked available,
        so the behaviour SAD 11.2 row 3 describes -- concurrency reduced, ring
        runs refused -- could not happen: nothing ever told MOTSOGNIR an
        appliance was down. The runbook's section 3 documented a response to a
        state the code could not reach.

        Two failures that must not be confused, and this is where they part.
        A scheduler that cannot be reached says nothing about the appliances,
        so the estate is left as it was: dispatch suspends on its own (SAD 11.2
        row 2), and reporting every appliance as down would refuse every ring
        run for the duration of a controller restart. A scheduler that answers
        and reports a node drained is evidence, and it is taken.
        """
        reader = getattr(self.scheduler, "nodes", None)
        if reader is None:
            return
        try:
            reported = reader()
        except Exception:
            logger.debug("estate.unreadable", scheduler=type(self.scheduler).__name__)
            return
        if not reported:
            return

        down = tuple(item.name for item in reported if not item.available)
        updated = self._estate.without(*down) if down else self._estate.with_all_available()
        if updated.down != self._estate.down:
            logger.info(
                "estate.changed",
                down=list(updated.down),
                available=[item.name for item in updated.available],
            )
        self._estate = updated

    def _observe_supply(
        self, now: datetime, placements: Mapping[UUID, dict[str, Any] | None]
    ) -> tuple[Action, ...]:
        """Read the supply, if one is fitted, and tell the monitor about it.

        A supply that cannot be read does not stop the tick. SAD 11.2 requires
        degraded modes to be visible rather than fatal, and a worker that died
        on an unreadable status file would take run dispatch, the duties and
        the alarms down with it -- over a signal for hardware that is not
        fitted yet.
        """
        path = self.settings.supply_status
        if path is None or not path.is_file():
            return ()
        running = tuple(str(item["job_id"]) for item in placements.values() if item is not None)
        try:
            reading = read_status_file(path, at=now)
        except SupplyError as error:
            logger.warning(
                "worker.supply.unreadable",
                site=self.settings.site_id,
                path=str(path),
                reason=str(error),
            )
            return (signal_lost(error),)
        return self.monitor.observe(reading, running)

    def _record_ring(self, connection: Connection) -> None:
        """Record the declared ring, once, when this worker first sees it.

        Only when the forge declares one. An estate whose ring is simply all of
        its appliances has nothing to say that the appliance list does not
        already say, and a row per worker start on every ordinary forge would
        be noise in the one place noise is expensive.
        """
        if not self._estate.ring_is_declared:
            return
        declared = tuple(item.name for item in self._estate.ring)
        if declared == self._ring_recorded:
            return

        def record(orchestrator: Orchestrator) -> None:
            orchestrator.record(
                subject_type=duties.SITE_SUBJECT,
                subject_id=orchestrator.site_id,
                transition=RING_DECLARED,
                payload={
                    "members": list(declared),
                    "ringSize": len(declared),
                    "estateSize": self._estate.size,
                    "reason": (
                        "the forge declares a ring smaller than its estate. "
                        "VLD-WIR-SINDRI-001 section 7.4 recabling, or a site with "
                        "an appliance out of the ring."
                    ),
                },
            )

        self._commit(connection, record)
        # Set after the commit, so a refused write is retried on the next tick
        # rather than being remembered as done.
        self._ring_recorded = declared
        logger.info("estate.ring.declared", members=list(declared), site=self.settings.site_id)

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
