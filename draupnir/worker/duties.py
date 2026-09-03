"""The periodic duties of SAD 11.3, and the retention sweep of SAD 7.3.

Six signals in SAD 11.3's table have a source that is not a run: the chain is
"verified hourly", the fabric is probed by an "hourly `nccl-tests` job
dispatched by MOTSOGNIR", vault capacity alarms "at 85 per cent", and anchor
freshness alarms "when the last successful anchor exceeds the configured
interval". Each needs something to run it on a clock, and the worker is the
only deployable unit that has one.

**What is recorded and what is not.** A duty that finds nothing wrong writes a
log line and no ledger entry. The chain is the audit record of what happened to
the estate's subjects, and an hourly "the chain still verifies" entry would add
tens of thousands of entries a year saying nothing, to a chain AC-N5 sizes at a
hundred thousand. Alarms are recorded, because an alarm is a fact somebody will
later need to establish; readings go to the log and to `/metrics`, which is
where SAD 11.3 puts them.

The cost of that choice is that a restarted worker cannot tell when a duty was
last done and runs each of them early. That is the right way round: the duties
are idempotent and running one twice costs a chain scan.

**On deletion.** The retention sweep proposes and never deletes. SAD 7.3 gives
raw corpus deletion an approver, and `hodd.retention` refuses an unapproved
action in as many words -- deletion is "an approved, ledgered action, never a
cron job". A worker that deleted on a timer would be exactly the cron job that
sentence forbids, so this finds what has come due, records that it has, and
leaves the decision where it belongs.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from draupnir.core.domain.ledger import LedgerEntry
from draupnir.core.domain.states import RunState
from draupnir.gullinbursti.agent import ANCHOR_INTERVAL
from draupnir.hodd.retention import RETENTION, due_at
from draupnir.hodd.stores import VaultUnavailableError
from draupnir.interfaces.types import JobPlan, ResourceRequest
from draupnir.motsognir import execution

#: The fraction of the vault at which SAD 11.3 alarms.
VAULT_CEILING = 0.85

#: The fraction of the commissioned baseline below which SAD 11.3 alarms on the
#: fabric probe.
FABRIC_FLOOR = 0.80

#: The benchmark SAD 11.3 names. The all-reduce collective is the one a ring
#: training job actually performs, which is why the table names this suite
#: rather than a point-to-point bandwidth test.
NCCL_TESTS = "all_reduce_perf"

#: How large a message the probe reduces. Large enough to reach the asymptotic
#: bus bandwidth, which is the figure a commissioning report records; a small
#: message measures latency and would alarm on a healthy fabric.
PROBE_SIZE = "8G"

#: `nccl-tests` ends with a line reading `# Avg bus bandwidth : 235.6`. That
#: average is the number a commissioned baseline is expressed in.
BUS_BANDWIDTH = re.compile(r"Avg bus bandwidth\s*:\s*([0-9]+(?:\.[0-9]+)?)")

#: The subject an alarm is recorded against: the forge itself.
SITE_SUBJECT = "site"
#: The subject a retention proposal is recorded against.
CORPUS_SUBJECT = "corpus"
#: The transition string an alarm carries.
ALARM_RAISED = "alarm.raised"
#: The transition string a retention proposal carries. Read back on the next
#: sweep, so a corpus is proposed once rather than once a day.
RETENTION_PROPOSED = "retention.due"


class Duty(StrEnum):
    """The periodic duties, named as SAD 11.3's signal column names them."""

    CHAIN = "ledger-chain-integrity"
    FABRIC = "fabric-bandwidth-probe"
    VAULT = "vault-capacity"
    ANCHOR = "anchor-freshness"
    RETENTION = "retention-sweep"


#: How often each duty is due. The first four are SAD 11.3's; the retention
#: sweep is daily, because its subject matures over 24 months and an hourly
#: scan of the whole chain would cost more than it could ever find.
PERIODS: Mapping[Duty, timedelta] = {
    Duty.CHAIN: timedelta(hours=1),
    Duty.FABRIC: timedelta(hours=1),
    Duty.VAULT: timedelta(minutes=15),
    Duty.ANCHOR: ANCHOR_INTERVAL,
    Duty.RETENTION: timedelta(days=1),
}


@dataclass(frozen=True, slots=True)
class Finding:
    """What one duty found."""

    duty: Duty
    alarm: bool
    detail: str
    measurements: Mapping[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        """The ledger payload for an alarm, and the log line for anything else."""
        return {
            "duty": str(self.duty),
            "alarm": self.alarm,
            "detail": self.detail,
            "measurements": dict(self.measurements),
        }


class Chain(Protocol):
    """What a duty needs from the ledger. Narrower than a repository."""

    def verify_chain(self, from_seq: int = 1, to_seq: int | None = None) -> int | None:
        """Return the first divergent sequence number, or None."""
        ...

    def length(self) -> int:
        """How many entries the chain holds."""
        ...

    def stream(self, from_seq: int = 1, to_seq: int | None = None) -> Iterator[LedgerEntry]:
        """Every entry in the window, oldest first."""
        ...


class Vault(Protocol):
    """What the capacity duty needs from a store driver."""

    def free_bytes(self) -> int:
        """Bytes available."""
        ...

    def total_bytes(self) -> int:
        """Bytes the vault holds when full."""
        ...


class Timetable:
    """When each duty was last done.

    In memory, and deliberately: see the module docstring. A worker that has
    just started has done nothing, so everything is due, so everything runs on
    the first tick.
    """

    def __init__(self, last: Mapping[Duty, datetime] | None = None) -> None:
        """Take what is already known, which for a fresh worker is nothing."""
        self._last: dict[Duty, datetime] = dict(last or {})

    def due(self, duty: Duty, now: datetime) -> bool:
        """Whether this duty is due. A duty never done is always due."""
        previous = self._last.get(duty)
        return previous is None or now - previous >= PERIODS[duty]

    def mark(self, duty: Duty, now: datetime) -> None:
        """Record that it has been done, whatever it found."""
        self._last[duty] = now

    def outstanding(self, now: datetime) -> tuple[Duty, ...]:
        """Every duty due at this moment, in the order they are declared."""
        return tuple(duty for duty in Duty if self.due(duty, now))

    def as_payload(self) -> dict[str, str]:
        """When each was last done, for an operator asking a running worker."""
        return {str(duty): moment.isoformat() for duty, moment in sorted(self._last.items())}


# ---------------------------------------------------------------------------
# Ledger chain integrity
# ---------------------------------------------------------------------------


def verify(chain: Chain) -> Finding:
    """Verify the whole chain. SAD 11.3: alarm on any divergence.

    The whole of it rather than the tail, because the failure this catches is
    an entry rewritten in the middle, and a check that only looked at what was
    written since the last check would never look there again.
    """
    length = chain.length()
    divergent = chain.verify_chain()
    if divergent is None:
        return Finding(Duty.CHAIN, False, f"{length} entries verify", {"entries": length})
    return Finding(
        Duty.CHAIN,
        True,
        (
            f"the chain diverges at seq {divergent}: the entry does not hash to what "
            "its successor records. The forge is read only until an operator has "
            "established which entry is authentic (SAD 11A.4)."
        ),
        {"entries": length, "divergentSeq": divergent},
    )


# ---------------------------------------------------------------------------
# Fabric bandwidth
# ---------------------------------------------------------------------------


def probe_plan(workdir: Path, *, size: str = PROBE_SIZE, nodes: int = 3) -> JobPlan:
    """The `nccl-tests` job of SAD 11.3, as MOTSOGNIR would dispatch it.

    On the ring partition and across every appliance, because the number that
    matters is the one a ring training job would get. A probe run on one node
    measures a machine rather than a fabric.
    """
    return JobPlan(
        command=(NCCL_TESTS, "-b", "8", "-e", size, "-f", "2", "-g", "1"),
        environment={"NCCL_DEBUG": "WARN"},
        workdir=str(workdir),
        resources=ResourceRequest(partition="ring", nodes=nodes),
        expected_artefacts=(),
    )


def probe_installed() -> bool:
    """Whether the benchmark SAD 11.3 names is on this machine at all."""
    return shutil.which(NCCL_TESTS) is not None


def parse_bandwidth(output: str) -> float | None:
    """The average bus bandwidth in GB/s from `nccl-tests` output, if it is there.

    None rather than zero when the line is absent. Zero is a reading, and a
    reading of zero would raise an alarm about the fabric when what actually
    happened is that the probe did not run.
    """
    found = BUS_BANDWIDTH.search(output)
    return float(found.group(1)) if found else None


def probe(
    scheduler: execution.Scheduler,
    *,
    workdir: Path,
    baseline_gbps: float = 0.0,
    nodes: int = 3,
    timeout: float = 300.0,
) -> Finding:
    """Dispatch the fabric probe and compare it to the commissioned baseline.

    The baseline is configuration rather than a constant here: it is measured
    at commissioning, and a figure written into this file would be a claim
    about somebody else's cable. With none configured the reading is still
    taken; what cannot be done is raise the alarm SAD 11.3 asks for, and the
    finding says so rather than passing quietly.

    Where the benchmark is not installed the duty reports that and does not
    alarm. A control plane on a machine with no ring has no fabric to be
    degraded, and an hourly alarm about a cable that does not exist is how an
    operator learns to stop reading alarms.
    """
    if not probe_installed():
        return Finding(
            Duty.FABRIC,
            False,
            (
                f"{NCCL_TESTS} is not installed on this machine, so the fabric is "
                "unmeasured. On an appliance it is, and the probe runs there."
            ),
        )

    try:
        completed = execution.dispatch(scheduler, probe_plan(workdir, nodes=nodes), timeout=timeout)
    except execution.DispatchError as refusal:
        return Finding(Duty.FABRIC, True, f"the fabric probe could not be placed: {refusal}")

    reading = parse_bandwidth("\n".join(completed.tail))
    if not completed.succeeded or reading is None:
        return Finding(
            Duty.FABRIC,
            True,
            (
                f"the fabric probe exited {completed.exit_code} and reported no bus "
                "bandwidth. Nothing is known about the fabric until it runs; this is "
                "not a reading of zero."
            ),
            {"exitCode": completed.exit_code, "tail": list(completed.tail)},
        )

    measurements: dict[str, Any] = {"busBandwidthGbps": reading, "baselineGbps": baseline_gbps}
    if baseline_gbps <= 0:
        return Finding(
            Duty.FABRIC,
            False,
            (
                f"{reading:.1f} GB/s. No commissioned baseline is configured, so the "
                f"{FABRIC_FLOOR:.0%} alarm of SAD 11.3 cannot be raised against it."
            ),
            measurements,
        )

    fraction = reading / baseline_gbps
    measurements["fractionOfBaseline"] = round(fraction, 4)
    if fraction < FABRIC_FLOOR:
        return Finding(
            Duty.FABRIC,
            True,
            (
                f"{reading:.1f} GB/s is {fraction:.0%} of the commissioned "
                f"{baseline_gbps:.1f} GB/s, below the {FABRIC_FLOOR:.0%} floor. A ring "
                "run placed now would train slower than its specification assumes."
            ),
            measurements,
        )
    return Finding(
        Duty.FABRIC, False, f"{reading:.1f} GB/s, {fraction:.0%} of baseline", measurements
    )


# ---------------------------------------------------------------------------
# Vault capacity
# ---------------------------------------------------------------------------


def capacity(vault: Vault, *, ceiling: float = VAULT_CEILING) -> Finding:
    """Read the vault's fill, and alarm at the ceiling. SAD 11.3."""
    try:
        free = vault.free_bytes()
        total = vault.total_bytes()
    except VaultUnavailableError as unavailable:
        return Finding(Duty.VAULT, True, f"the vault is not mounted: {unavailable}")

    if total <= 0:
        return Finding(Duty.VAULT, False, "the store reports no capacity to fill")

    used = max(total - free, 0) / total
    measurements = {"usedFraction": round(used, 4), "freeBytes": free, "totalBytes": total}
    if used >= ceiling:
        return Finding(
            Duty.VAULT,
            True,
            (
                f"the vault is {used:.0%} full, at or above the {ceiling:.0%} alarm. "
                "A checkpoint that cannot be written loses the run that produced it."
            ),
            measurements,
        )
    return Finding(Duty.VAULT, False, f"the vault is {used:.0%} full", measurements)


# ---------------------------------------------------------------------------
# Anchor freshness
# ---------------------------------------------------------------------------


def freshness(
    last_anchored_at: datetime | None,
    *,
    now: datetime,
    interval: timedelta = ANCHOR_INTERVAL,
) -> Finding:
    """Alarm when the last successful anchor is older than the interval.

    A site that has never anchored alarms too. SAD 11A.3 makes the anchor what
    detects truncation, and a chain nobody has ever anchored is a chain nothing
    would notice being shortened.
    """
    if last_anchored_at is None:
        return Finding(
            Duty.ANCHOR,
            True,
            (
                "this site has never anchored its chain. Until it does, a truncation "
                "of the chain's end verifies as an intact chain (SAD 11A.3)."
            ),
        )

    age = now - last_anchored_at
    measurements = {
        "ageSeconds": round(age.total_seconds()),
        "intervalSeconds": int(interval.total_seconds()),
    }
    if age > interval:
        return Finding(
            Duty.ANCHOR,
            True,
            (
                f"the last anchor is {age.total_seconds() / 60:.0f} minutes old and the "
                f"interval is {interval.total_seconds() / 60:.0f}. Publication is "
                "refused while the anchor is stale."
            ),
            measurements,
        )
    return Finding(
        Duty.ANCHOR, False, f"anchored {age.total_seconds() / 60:.0f} minutes ago", measurements
    )


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Due:
    """A raw corpus whose 24 months have elapsed."""

    corpus_sha256: str
    curated_by: str
    last_release_at: datetime
    due_at: datetime
    releases: tuple[str, ...]

    def as_payload(self) -> dict[str, Any]:
        """The ledger payload of the proposal."""
        return {
            "corpusSha256": self.corpus_sha256,
            "curatedBy": self.curated_by,
            "lastReleaseAt": self.last_release_at.isoformat(),
            "dueAt": self.due_at.isoformat(),
            "releases": list(self.releases),
            "policy": "raw-corpus",
            "retentionMonths": round(RETENTION.days / 30),
        }


_CURATED = f"{RunState.LICENCE_CLEARED}->{RunState.CURATED}"
_QUEUED = f"{RunState.CURATED}->{RunState.QUEUED}"
_RELEASED = f"{RunState.AWAITING_APPROVAL}->{RunState.RELEASED}"


def due_corpora(entries: Iterable[LedgerEntry], *, now: datetime) -> tuple[Due, ...]:
    """Which raw corpora are past retention, read from the chain alone.

    Three joins, all of them already in the chain: a curation entry names the
    corpus it produced, a run's move to QUEUED names the corpus it consumed,
    and a release entry dates the release. A corpus is due 24 months after the
    *last* release derived from it, so one recent release on an old corpus
    keeps the whole corpus (SAD 7.3).

    A corpus nothing was ever released from is not returned. Its retention
    clock has not started, and deleting a corpus whose models never shipped is
    a decision about storage rather than about retention.
    """
    curated: dict[str, str] = {}
    consumed: dict[str, str] = {}
    released: dict[str, datetime] = {}
    proposed: set[str] = set()

    for entry in entries:
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        if entry.transition == RETENTION_PROPOSED:
            proposed.add(entry.subject_id)
        elif entry.transition == _CURATED and payload.get("output_sha256"):
            curated[str(payload["output_sha256"])] = entry.subject_id
        elif entry.transition == _QUEUED and payload.get("input_artefact_sha256"):
            consumed[entry.subject_id] = str(payload["input_artefact_sha256"])
        elif entry.transition == _RELEASED:
            released[entry.subject_id] = entry.ts

    latest: dict[str, datetime] = {}
    lineage: dict[str, list[str]] = {}
    for run_id, released_at in sorted(released.items()):
        corpus = consumed.get(run_id)
        if corpus is None:
            continue
        lineage.setdefault(corpus, []).append(run_id)
        if corpus not in latest or released_at > latest[corpus]:
            latest[corpus] = released_at

    return tuple(
        Due(
            corpus_sha256=corpus,
            curated_by=curated.get(corpus, ""),
            last_release_at=released_at,
            due_at=due_at(released_at),
            releases=tuple(sorted(lineage[corpus])),
        )
        for corpus, released_at in sorted(latest.items())
        if corpus not in proposed and due_at(released_at) <= now
    )


def sweep(chain: Chain, *, now: datetime) -> tuple[Finding, tuple[Due, ...]]:
    """Find what has come due, and say so. Deletes nothing.

    The proposals come back beside the finding rather than being recorded here:
    appending is the orchestrator's, and a duty that wrote to the chain itself
    would be a second write path past the guards.
    """
    due = due_corpora(chain.stream(), now=now)
    if not due:
        return Finding(Duty.RETENTION, False, "no raw corpus is past retention"), ()
    return (
        Finding(
            Duty.RETENTION,
            False,
            (
                f"{len(due)} raw corpus/corpora are more than {round(RETENTION.days / 30)} "
                "months past their last derived release and await an approver. Nothing "
                "has been deleted (SAD 7.3)."
            ),
            {"corpora": [item.corpus_sha256 for item in due]},
        ),
        due,
    )


def alarms(findings: Sequence[Finding]) -> tuple[Finding, ...]:
    """Only the findings that alarm. What gets recorded and paged."""
    return tuple(finding for finding in findings if finding.alarm)


__all__ = [
    "ALARM_RAISED",
    "CORPUS_SUBJECT",
    "FABRIC_FLOOR",
    "NCCL_TESTS",
    "PERIODS",
    "RETENTION_PROPOSED",
    "SITE_SUBJECT",
    "VAULT_CEILING",
    "Chain",
    "Due",
    "Duty",
    "Finding",
    "Timetable",
    "Vault",
    "alarms",
    "capacity",
    "due_corpora",
    "freshness",
    "parse_bandwidth",
    "probe",
    "probe_installed",
    "probe_plan",
    "sweep",
    "verify",
]
