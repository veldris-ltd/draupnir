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
from draupnir.hodd.stores import StoreError
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

#: The message sizes the probe sweeps, and they are the ones VLD-INF-SINDRI-001
#: acceptance test A3 sweeps: `all_reduce_perf -b 512M -e 8G`.
#:
#: This matters more than it looks. A3 is where the commissioned baseline comes
#: from, and `Avg bus bandwidth` is an average over whatever range was swept.
#: Sweeping from 8 bytes -- which this did -- averages in the small-message
#: sizes where the collective is latency bound, producing a figure several
#: times lower than the baseline it is compared against. The alarm would then
#: fire on a perfectly healthy fabric, every hour, from the first tick.
PROBE_BEGIN = "512M"
PROBE_END = "8G"

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
    #: The corpus work the API accepts. RF-12: a curator pressed Ingest, got a
    #: 202, and nothing ever happened -- the accepted entry was consumed by
    #: nothing.
    CORPORA = "corpus-queue"
    #: The array work the API accepts. RF-13: `--array=0-55%3` and the
    #: single-element retry were described at length and submitted by nothing.
    ARRAYS = "array-queue"
    #: Expired idempotency keys. RF-14: on the timetable rather than on a
    #: request path, because a sweep on the request path makes one unlucky
    #: caller pay for everybody else's expired keys and does nothing at all on
    #: a quiet estate -- which is exactly when the table grows unwatched.
    KEYS = "idempotency-sweep"


#: How often each duty is due. The first four are SAD 11.3's; the retention
#: sweep is daily, because its subject matures over 24 months and an hourly
#: scan of the whole chain would cost more than it could ever find.
PERIODS: Mapping[Duty, timedelta] = {
    Duty.CHAIN: timedelta(hours=1),
    Duty.FABRIC: timedelta(hours=1),
    Duty.VAULT: timedelta(minutes=15),
    Duty.ANCHOR: ANCHOR_INTERVAL,
    Duty.RETENTION: timedelta(days=1),
    # A minute, because this one has somebody waiting on it. The others are
    # checks nobody asked for at a particular moment; this is work a curator
    # requested and is watching the board for. Cheap to run: one indexed read
    # of the corpus entries, and nothing to do when the queue is empty.
    Duty.CORPORA: timedelta(minutes=1),
    # As often as the corpus queue, and for the same reason: somebody pressed a
    # button and is watching the board. Cheap when the queue is empty, which is
    # nearly always -- an estate submits one fifty-six element array and then
    # waits most of a week for it.
    Duty.ARRAYS: timedelta(minutes=1),
    # Hourly. A key is honoured for twenty-four hours, so an hour of slack
    # either way changes nothing an operator can observe, and a sweep that ran
    # every minute would be a delete statement a minute that almost always
    # deletes nothing.
    Duty.KEYS: timedelta(hours=1),
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

    def head(self) -> Any:
        """The chain's last entry, or `None` for an empty chain.

        Added for the anchor duty (RF-07): anchoring submits the head, and a
        duty that could not ask for one would have to be given it by a caller
        that read the chain a second time.
        """
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


@dataclass(frozen=True, slots=True)
class FabricProbe:
    """How this forge runs the probe of SAD 11.3.

    Every value is the estate's, so every value is configuration. The
    interface and HCA names in particular: VLD-INF-SINDRI-001 section 48.2
    warns that "a kernel or driver update that renames or reorders network
    interfaces breaks the ring configuration silently", and a name compiled
    into this file would be a name nobody could correct without a release.

    An empty `binary` means this forge has no probe configured, which is what a
    development machine is. The duty then reports the fabric as unmeasured
    rather than alarming about a cable that does not exist.
    """

    #: Absolute path to `all_reduce_perf`. VLD-INF-SINDRI-001 Procedure S5
    #: builds nccl-tests under /forge/tools on each appliance, and it is not on
    #: any PATH.
    binary: str = ""
    #: The interface NCCL uses for its bootstrap, e.g. `enp1s0f0np0`.
    interface: str = ""
    #: The RoCE devices, comma separated, as `NCCL_IB_HCA` takes them.
    hca: str = ""
    #: The commissioned figure from acceptance test A3, in GB/s. Zero means
    #: none has been recorded, and the alarm cannot be raised without one.
    baseline_gbps: float = 0.0
    nodes: int = 3
    begin: str = PROBE_BEGIN
    end: str = PROBE_END

    @property
    def configured(self) -> bool:
        """Whether this forge has a fabric to probe."""
        return bool(self.binary)


def probe_plan(workdir: Path, probe: FabricProbe) -> JobPlan:
    """The `nccl-tests` job of SAD 11.3, as MOTSOGNIR would dispatch it.

    On the ring partition and across every appliance, because the number that
    matters is the one a ring training job would get. A probe run on one node
    measures a machine rather than a fabric -- and that is what this rendered
    before: a bare binary name with no launcher, which would have run one
    process on whichever node Slurm picked and reported it as the fabric.

    `srun` across the allocation is what makes it a collective. The NCCL
    variables are the ones every ring job in VLD-INF-SINDRI-001 Part 5 sets;
    without them NCCL falls back to sockets over Fabric 2 and measures the
    Ethernet rather than BAUGR, which is a reading, and a wrong one.
    """
    environment = {"NCCL_DEBUG": "WARN", "NCCL_IB_DISABLE": "0"}
    if probe.interface:
        environment["NCCL_SOCKET_IFNAME"] = probe.interface
    if probe.hca:
        environment["NCCL_IB_HCA"] = probe.hca

    return JobPlan(
        command=(
            "srun",
            f"--nodes={probe.nodes}",
            f"--ntasks={probe.nodes}",
            probe.binary or NCCL_TESTS,
            "-b",
            probe.begin,
            "-e",
            probe.end,
            "-f",
            "2",
            "-g",
            "1",
        ),
        environment=environment,
        workdir=str(workdir),
        resources=ResourceRequest(partition="ring", nodes=probe.nodes),
        expected_artefacts=(),
    )


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
    settings: FabricProbe | None = None,
    timeout: float = 300.0,
) -> Finding:
    """Dispatch the fabric probe and compare it to the commissioned baseline.

    The baseline is configuration rather than a constant: it is measured at
    commissioning, and a figure written into this file would be a claim about
    somebody else's cable. With none configured the reading is still taken;
    what cannot be done is raise the alarm SAD 11.3 asks for, and the finding
    says so rather than passing quietly.

    **The probe is not gated on this machine's filesystem.** It used to ask
    `shutil.which("all_reduce_perf")`, which is a question about the control
    plane -- and the binary lives on the appliances, built under /forge/tools
    by Procedure S5, on no PATH. So the answer was always no, and the fabric
    would have reported itself unmeasured for ever, on a fully commissioned
    estate, with the reason sounding plausible. What gates it now is whether
    the forge has a probe configured at all; where it does, the probe is
    dispatched and the failure is read, which is a better signal than a
    `which` on the wrong host.
    """
    probe_settings = settings or FabricProbe()
    baseline_gbps = probe_settings.baseline_gbps

    if not probe_settings.configured:
        return Finding(
            Duty.FABRIC,
            False,
            (
                "no fabric probe is configured for this forge, so the fabric is "
                "unmeasured. Set the probe binary and the NCCL interface names "
                "(VLD-INF-SINDRI-001 Procedure S5)."
            ),
        )

    try:
        completed = execution.dispatch(
            scheduler, probe_plan(workdir, probe_settings), timeout=timeout
        )
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
    """Read the vault's fill, and alarm at the ceiling. SAD 11.3.

    Any `StoreError` is an alarm, not an exception: a vault that has dropped
    and a directory somebody created where the vault should be are both states
    an operator has to know about, and both are refusals rather than numbers.
    """
    try:
        free = vault.free_bytes()
        total = vault.total_bytes()
    except StoreError as unavailable:
        return Finding(Duty.VAULT, True, f"the vault cannot be read: {unavailable}")

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


@dataclass(frozen=True, slots=True)
class Anchored:
    """What one anchoring attempt produced, for the chain to record.

    Returned rather than written here, because a duty that wrote to the ledger
    would be a duty that could not be run twice in a test without a database.
    The loop records it, in its own transaction, which is where every other
    write in this system happens.
    """

    seq: int
    entry_hash: str
    outcome: str
    countersignature: str
    reason: str
    anchored_at: datetime | None = None

    @property
    def accepted(self) -> bool:
        """Whether the registry countersigned or already held this head."""
        return self.outcome in {"countersigned", "duplicate"}

    def as_payload(self) -> dict[str, Any]:
        """The ledger payload. Hashes, names, times and numbers only."""
        return {
            "seq": self.seq,
            "entry_hash": self.entry_hash,
            "outcome": self.outcome,
            "countersignature": self.countersignature,
            "reason": self.reason,
            "anchored_through": self.seq if self.accepted else 0,
            "anchored_at": self.anchored_at.isoformat() if self.anchored_at else "",
        }


def anchor(
    agent: Any,
    registry: Any,
    head: Any,
    *,
    now: datetime,
    last_anchored_at: datetime | None = None,
    interval: timedelta = ANCHOR_INTERVAL,
) -> tuple[Finding, Anchored | None]:
    """Submit the chain head to MEGINGJORD, and say what came back. RF-07.

    Nothing anchored, so `last_anchored_at` was always `None` and `freshness`
    returned its "this site has never anchored its chain" alarm on every tick,
    forever -- while its own message said publication is refused while the
    anchor is stale, and nothing refused anything. SAD 11A.3 makes the anchor
    what detects truncation; nothing detected truncation.

    **A failed anchor is a finding, never a failed run.** Decision S8: a
    partitioned forge trains and evaluates and does not release. So a rejection
    alarms and leaves the head queued, and the queue is drained on reconnect by
    the same code the ordinary path uses -- a path only exercised during an
    outage is a path that does not work.

    **A failure alarms only once the last good anchor is stale.** SAD 11A.3
    asks for an alarm when the last successful anchor exceeds the interval, not
    when an attempt fails: a link that blinks between two ticks would otherwise
    raise an alarm about a chain anchored ninety seconds ago, and an alarm that
    fires on a condition an operator cannot act on is one they learn to close.
    The judgement is `freshness`, so there is one place that decides how old is
    too old, and `last_anchored_at` comes out of the chain rather than off a
    side table nothing writes.
    """
    stale = freshness(last_anchored_at, now=now, interval=interval)
    # Its detail is a sentence in its own right, so it is joined rather than
    # nested: an operator reading the alarm wants what just failed and how long
    # the chain has been unprotected, in that order.
    since = stale.detail.rstrip(".")

    if agent is None or registry is None:
        return (
            Finding(
                Duty.ANCHOR,
                True,
                (
                    "this site has no federation link configured, so its chain is not "
                    "anchored anywhere. Until it is, a truncation of the chain's end "
                    "verifies as an intact chain (SAD 11A.3)."
                ),
            ),
            None,
        )

    if head is None:
        # A link, and nothing to anchor with it. Told apart from the case above
        # because the operator's next action differs entirely: one is a tunnel
        # to build and the other is a forge that has not done anything yet.
        # No alarm, because an empty chain has no end to truncate.
        return (
            Finding(
                Duty.ANCHOR,
                False,
                "this site's chain is empty, so there is no head to anchor yet.",
            ),
            None,
        )

    agent.submit(head, at=now)
    receipts = agent.drain(registry, at=now, countersignature="")
    if not receipts:
        return (
            Finding(
                Duty.ANCHOR,
                stale.alarm,
                (
                    "the federation link is down; the chain head is queued and not "
                    f"anchored. {since}."
                ),
                {"queueDepth": agent.queue_depth, **stale.measurements},
            ),
            None,
        )

    last = receipts[-1]
    recorded = Anchored(
        seq=head.head.seq,
        entry_hash=head.head.entry_hash,
        outcome=str(last.outcome),
        countersignature=last.anchor.countersignature if last.anchor else "",
        reason=last.reason,
        anchored_at=last.anchor.countersigned_at if last.anchor else None,
    )

    if not last.accepted:
        return (
            Finding(
                Duty.ANCHOR,
                stale.alarm,
                (
                    f"the registry did not anchor sequence {recorded.seq}: {last.reason}. "
                    "Training and evaluation continue; release is unavailable until the "
                    f"chain head is countersigned (Decision S8, AC-S13). {since}."
                ),
                {"seq": recorded.seq, "outcome": recorded.outcome, **stale.measurements},
            ),
            recorded,
        )

    return (
        Finding(
            Duty.ANCHOR,
            False,
            f"anchored through sequence {recorded.seq}",
            {"seq": recorded.seq, "queueDepth": agent.queue_depth},
        ),
        recorded,
    )


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
    "PROBE_BEGIN",
    "PROBE_END",
    "RETENTION_PROPOSED",
    "SITE_SUBJECT",
    "VAULT_CEILING",
    "Chain",
    "Due",
    "Duty",
    "FabricProbe",
    "Finding",
    "Timetable",
    "Vault",
    "alarms",
    "capacity",
    "due_corpora",
    "freshness",
    "parse_bandwidth",
    "probe",
    "probe_plan",
    "sweep",
    "verify",
]
