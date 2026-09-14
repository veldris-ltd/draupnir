"""The worker's decisions, without a database or an estate.

Three things are worth checking here rather than in an integration test,
because they are properties of the tables rather than of a run: that every
state the worker acts on is worked in a defined order, that a duty alarms when
SAD 11.3 says it does, and that the retention sweep reads a 24 month rule out
of a chain rather than out of a clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from draupnir.core.application.orchestrator import RunFacts
from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry, append
from draupnir.core.domain.states import RunState
from draupnir.hodd.retention import RETENTION
from draupnir.hodd.stores import VaultUnavailableError
from draupnir.interfaces.types import JobHandle, JobPlan, JobState, JobStatus
from draupnir.worker import duties, stages
from draupnir.worker.duties import Duty, Timetable
from draupnir.worker.loop import ORDER, WorkerSettings, ordered

SITE = "sindri"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def facts(state: RunState, run_id: UUID | None = None) -> RunFacts:
    return RunFacts(
        run_id=run_id or uuid4(),
        name="cim-gbr-v1.0",
        state=state,
        submitter="operator@veldris.internal",
        spec_hash="a" * 64,
    )


def entry(
    previous: LedgerEntry | None,
    *,
    subject_type: str,
    subject_id: str,
    transition: str,
    payload: dict[str, Any],
    ts: datetime = NOW,
) -> LedgerEntry:
    return append(
        previous=previous,
        site_id=SITE,
        ts=ts,
        actor="operator@veldris.internal",
        subject_type=subject_type,
        subject_id=subject_id,
        transition=transition,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# The table of stages
# ---------------------------------------------------------------------------


def test_every_state_the_worker_acts_on_has_a_place_in_the_order() -> None:
    """A stage the loop never reaches is a run that silently stops moving."""
    assert set(ORDER) == stages.actionable()


def test_the_order_observes_before_it_dispatches() -> None:
    """Freed capacity is used in the tick that freed it, not the next one."""
    assert ORDER.index(RunState.TRAINING) < ORDER.index(RunState.QUEUED)
    assert ORDER[-1] is RunState.QUEUED


def test_ordered_drops_nothing_and_queues_last() -> None:
    given = [facts(RunState.QUEUED), facts(RunState.DRAFT), facts(RunState.TRAINING)]
    got = ordered(given)
    assert [item.state for item in got] == [RunState.TRAINING, RunState.QUEUED]


@pytest.mark.parametrize(
    "state",
    [RunState.DRAFT, RunState.AWAITING_APPROVAL, RunState.RELEASED, RunState.FAILED],
)
def test_the_worker_leaves_alone_what_is_not_its(state: RunState) -> None:
    """Approval is a human's (Decision S6) and the terminal states are nobody's."""
    context = stages.Context(orchestrator=None, scheduler=None, scratch=None)  # type: ignore[arg-type]
    outcome = stages.advance(context, facts(state))
    assert outcome.result is stages.Result.IDLE


def test_a_queued_run_is_not_dispatched_on_battery() -> None:
    """SAD 11.2, last row: the queue drains rather than starting new work."""
    context = stages.Context(
        orchestrator=None,  # type: ignore[arg-type]
        scheduler=None,
        scratch=None,  # type: ignore[arg-type]
        may_dispatch=False,
    )
    outcome = stages.advance(context, facts(RunState.QUEUED))
    assert outcome.result is stages.Result.DEFERRED
    assert "battery" in outcome.detail


# ---------------------------------------------------------------------------
# The timetable
# ---------------------------------------------------------------------------


def test_a_fresh_worker_owes_every_duty() -> None:
    assert Timetable().outstanding(NOW) == tuple(Duty)


def test_a_duty_done_is_not_due_again_until_its_period_has_passed() -> None:
    timetable = Timetable()
    timetable.mark(Duty.CHAIN, NOW)
    assert not timetable.due(Duty.CHAIN, NOW + timedelta(minutes=59))
    assert timetable.due(Duty.CHAIN, NOW + timedelta(hours=1))


# ---------------------------------------------------------------------------
# The duties of SAD 11.3
# ---------------------------------------------------------------------------


class _Chain:
    def head(self) -> Any:
        """The chain's last entry. Added with the anchor duty (RF-07)."""
        return None

    """A chain that answers the three questions a duty asks of one."""

    def __init__(self, entries: tuple[LedgerEntry, ...] = (), divergent: int | None = None) -> None:
        self.entries = entries
        self.divergent = divergent

    def verify_chain(self, from_seq: int = 1, to_seq: int | None = None) -> int | None:
        del from_seq, to_seq
        return self.divergent

    def length(self) -> int:
        return len(self.entries)

    def stream(self, from_seq: int = 1, to_seq: int | None = None) -> Any:
        del from_seq, to_seq
        return iter(self.entries)


class _NoScheduler:
    """A scheduler that refuses to be used. Every method is an assertion."""

    def submit(self, plan: JobPlan) -> JobHandle:
        raise AssertionError("the probe placed a job it should not have")

    def poll(self, handle: JobHandle) -> Any:
        raise AssertionError("the probe polled a job it never placed")

    def cancel(self, handle: JobHandle) -> Any:
        raise AssertionError("the probe cancelled a job it never placed")

    def logs(self, handle: JobHandle) -> str:
        raise AssertionError("the probe read logs for a job it never placed")


class _Vault:
    def __init__(self, free: int, total: int) -> None:
        self._free, self._total = free, total

    def free_bytes(self) -> int:
        return self._free

    def total_bytes(self) -> int:
        return self._total


def test_a_verifying_chain_does_not_alarm() -> None:
    assert not duties.verify(_Chain(divergent=None)).alarm


def test_a_divergent_chain_alarms_and_names_the_sequence() -> None:
    finding = duties.verify(_Chain(divergent=47))
    assert finding.alarm
    assert finding.measurements["divergentSeq"] == 47
    assert "read only" in finding.detail


def test_the_vault_alarms_at_the_ceiling_of_sad_11_3() -> None:
    ceiling = duties.VAULT_CEILING
    assert not duties.capacity(_Vault(free=200, total=1000)).alarm
    assert duties.capacity(_Vault(free=int(1000 * (1 - ceiling)) - 1, total=1000)).alarm


def test_an_unmounted_vault_alarms_rather_than_raising() -> None:
    class _Unmounted:
        def free_bytes(self) -> int:
            raise VaultUnavailableError(Path("/vault"))

        def total_bytes(self) -> int:  # pragma: no cover -- never reached
            return 0

    finding = duties.capacity(_Unmounted())
    assert finding.alarm
    assert finding.duty is Duty.VAULT


def test_anchor_freshness_alarms_when_stale_and_when_never_anchored() -> None:
    assert duties.freshness(None, now=NOW).alarm
    assert duties.freshness(NOW - timedelta(hours=2), now=NOW).alarm
    assert not duties.freshness(NOW - timedelta(minutes=1), now=NOW).alarm


SINDRI_PROBE = duties.FabricProbe(
    binary="/forge/tools/nccl-tests/build/all_reduce_perf",
    interface="enp1s0f0np0",
    hca="rocep1s0f0,rocep1s0f1,roceP2p1s0f0,roceP2p1s0f1",
    baseline_gbps=235.0,
)


def test_the_fabric_probe_runs_as_a_collective_across_the_ring() -> None:
    """RF-E13. It rendered a bare binary name with no launcher.

    That would have run one process on whichever node Slurm picked and
    reported it as the fabric -- a probe run on one node measures a machine,
    which is the thing this duty exists not to do.
    """
    plan = duties.probe_plan(Path("."), SINDRI_PROBE)

    assert plan.command[0] == "srun", "the probe has no launcher, so it is not a collective"
    assert "--nodes=3" in plan.command
    assert "--ntasks=3" in plan.command
    assert plan.resources.partition == "ring"
    assert plan.resources.nodes == 3


def test_the_probe_binary_is_an_absolute_path_from_configuration() -> None:
    """The probe binary is an absolute path from configuration.

    VLD-INF-SINDRI-001 Procedure S5 builds nccl-tests under /forge/tools on
    each appliance, and it is on no PATH.
    """
    plan = duties.probe_plan(Path("."), SINDRI_PROBE)
    binary = next(part for part in plan.command if part.endswith(duties.NCCL_TESTS))
    assert binary == SINDRI_PROBE.binary
    assert binary.startswith("/")


def test_the_probe_carries_the_nccl_variables_every_ring_job_sets() -> None:
    """The probe carries the NCCL variables every ring job sets.

    Without them NCCL falls back to sockets over Fabric 2 and measures the
    Ethernet rather than BAUGR, which is a reading, and a wrong one.

    From configuration rather than constants: section 48.2 warns that a driver
    update can rename an interface, and correcting one should be a
    configuration change rather than a release.
    """
    plan = duties.probe_plan(Path("."), SINDRI_PROBE)

    assert plan.environment["NCCL_SOCKET_IFNAME"] == "enp1s0f0np0"
    assert plan.environment["NCCL_IB_HCA"] == SINDRI_PROBE.hca
    assert plan.environment["NCCL_IB_DISABLE"] == "0"


def test_the_probe_sweeps_the_sizes_the_baseline_was_measured_over() -> None:
    """The comparison is meaningless otherwise.

    Acceptance test A3 records the baseline from `-b 512M -e 8G`, and
    `Avg bus bandwidth` is an average over whatever range was swept. This
    swept from 8 bytes, averaging in the small-message sizes where the
    collective is latency bound -- a figure several times lower than the
    baseline it is compared against, so the 80 per cent alarm would have fired
    on a healthy fabric from the first tick.
    """
    plan = duties.probe_plan(Path("."), SINDRI_PROBE)
    command = list(plan.command)

    assert command[command.index("-b") + 1] == "512M"
    assert command[command.index("-e") + 1] == "8G"


def test_an_unconfigured_forge_is_unmeasured_rather_than_alarmed() -> None:
    """No fabric here is not a degraded fabric on the estate.

    The scheduler is deliberately one that would raise if it were used: the
    finding must be reached without placing anything.
    """
    finding = duties.probe(_NoScheduler(), workdir=Path("."), settings=duties.FabricProbe())
    assert not finding.alarm
    assert "no fabric probe is configured" in finding.detail


def test_the_probe_is_not_gated_on_this_machines_filesystem() -> None:
    """The defect. It asked `which all_reduce_perf` on the control plane.

    The binary lives on the appliances, on no PATH, so the answer was always
    no -- and the fabric would have reported itself unmeasured for ever on a
    fully commissioned estate, with a reason that sounded plausible.
    """
    assert not hasattr(duties, "probe_installed"), (
        "the probe still asks the control plane whether the appliances' benchmark exists"
    )
    assert "shutil" not in vars(duties), (
        "duties still imports shutil, which it needed only for that question"
    )


def test_a_probe_that_reported_nothing_is_not_a_reading_of_zero() -> None:
    assert duties.parse_bandwidth("no such line") is None
    assert duties.parse_bandwidth("# Avg bus bandwidth    : 235.6\n") == pytest.approx(235.6)


# ---------------------------------------------------------------------------
# Retention, SAD 7.3
# ---------------------------------------------------------------------------


def _chain_with_release(released_at: datetime, *, proposed: bool = False) -> _Chain:
    """A corpus, a run that consumed it, and a release derived from it."""
    corpus = "c" * 64
    run_id = str(uuid4())
    entries: list[LedgerEntry] = []
    previous: LedgerEntry | None = None

    for subject_type, subject_id, transition, payload, ts in [
        (
            "run",
            run_id,
            f"{RunState.LICENCE_CLEARED}->{RunState.CURATED}",
            {"output_sha256": corpus},
            released_at - RETENTION,
        ),
        (
            "run",
            run_id,
            f"{RunState.CURATED}->{RunState.QUEUED}",
            {"input_artefact_sha256": corpus},
            released_at - RETENTION,
        ),
        (
            "run",
            run_id,
            f"{RunState.AWAITING_APPROVAL}->{RunState.RELEASED}",
            {"approver": "approver@veldris.internal"},
            released_at,
        ),
    ]:
        previous = entry(
            previous,
            subject_type=subject_type,
            subject_id=subject_id,
            transition=transition,
            payload=payload,
            ts=ts,
        )
        entries.append(previous)

    if proposed:
        previous = entry(
            previous,
            subject_type=duties.CORPUS_SUBJECT,
            subject_id=corpus,
            transition=duties.RETENTION_PROPOSED,
            payload={"corpusSha256": corpus},
            ts=released_at + RETENTION,
        )
        entries.append(previous)

    return _Chain(tuple(entries))


def test_a_corpus_comes_due_24_months_after_its_last_derived_release() -> None:
    released_at = NOW - RETENTION - timedelta(days=1)
    due = duties.due_corpora(_chain_with_release(released_at).stream(), now=NOW)
    assert [item.corpus_sha256 for item in due] == ["c" * 64]
    assert due[0].due_at == released_at + RETENTION


def test_a_recent_release_keeps_the_corpus() -> None:
    assert duties.due_corpora(_chain_with_release(NOW).stream(), now=NOW) == ()


def test_a_corpus_already_proposed_is_not_proposed_again() -> None:
    """Otherwise a daily sweep would append a proposal a day, for ever."""
    chain = _chain_with_release(NOW - RETENTION - timedelta(days=1), proposed=True)
    assert duties.due_corpora(chain.stream(), now=NOW) == ()


def test_a_corpus_nothing_was_released_from_has_no_retention_clock() -> None:
    curated = entry(
        None,
        subject_type="run",
        subject_id=str(uuid4()),
        transition=f"{RunState.LICENCE_CLEARED}->{RunState.CURATED}",
        payload={"output_sha256": "d" * 64},
    )
    assert duties.due_corpora(_Chain((curated,)).stream(), now=NOW) == ()


def test_the_sweep_reports_without_deleting() -> None:
    chain = _chain_with_release(NOW - RETENTION - timedelta(days=1))
    finding, due = duties.sweep(chain, now=NOW)
    assert not finding.alarm
    assert len(due) == 1
    assert "Nothing has been deleted" in finding.detail


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_the_worker_reads_its_settings_from_the_environment() -> None:
    settings = WorkerSettings.from_environment(
        {
            "DRAUPNIR_SITE_ID": "eitri",
            "DRAUPNIR_WORKER_INTERVAL": "0.5",
            "DRAUPNIR_FABRIC_BASELINE_GBPS": "235.6",
            "DRAUPNIR_WORKER_DUTIES": "0",
        }
    )
    assert settings.site_id == "eitri"
    assert settings.interval == pytest.approx(0.5)
    assert settings.fabric_baseline_gbps == pytest.approx(235.6)
    assert settings.perform_duties is False


def test_the_worker_reads_the_baseline_the_installer_writes() -> None:
    """RF-28. install.sh writes DRAUPNIR_FABRIC_BASELINE_GBPS; the worker read another name.

    Asserted against the installer's own text, so the two cannot drift apart
    again without this failing.
    """
    installer = (Path(__file__).parents[2] / "deploy" / "install.sh").read_text(encoding="utf-8")
    assert "DRAUPNIR_FABRIC_BASELINE_GBPS=${FABRIC_BASELINE_GBPS}" in installer

    installed = WorkerSettings.from_environment({"DRAUPNIR_FABRIC_BASELINE_GBPS": "235.6"})
    by_hand = WorkerSettings.from_environment({"DRAUPNIR_WORKER_FABRIC_BASELINE_GBPS": "190.0"})

    assert installed.fabric_baseline_gbps == pytest.approx(235.6)
    assert by_hand.fabric_baseline_gbps == pytest.approx(190.0)


def test_the_worker_reads_the_certificate_it_presents_to_megingjord() -> None:
    """RF-37. The federation link is authenticated with what install.sh writes."""
    installer = (Path(__file__).parents[2] / "deploy" / "install.sh").read_text(encoding="utf-8")
    names = (
        "DRAUPNIR_FEDERATION_CLIENT_CERTIFICATE",
        "DRAUPNIR_FEDERATION_CLIENT_PRIVATE_KEY",
        "DRAUPNIR_INTERNAL_CA",
    )
    for name in names:
        assert f"{name}=${{" in installer, f"install.sh does not write {name}"

    configured = WorkerSettings.from_environment(
        {
            "DRAUPNIR_FEDERATION_CLIENT_CERTIFICATE": "/etc/draupnir/tls/federation.pem",
            "DRAUPNIR_FEDERATION_CLIENT_PRIVATE_KEY": "/etc/draupnir/tls/federation.key",
            "DRAUPNIR_INTERNAL_CA": "/etc/draupnir/tls/internal-ca.pem",
        }
    )
    unconfigured = WorkerSettings.from_environment({"DRAUPNIR_INTERNAL_CA": ""})

    assert configured.federation_certificate == Path("/etc/draupnir/tls/federation.pem")
    assert configured.federation_private_key == Path("/etc/draupnir/tls/federation.key")
    assert configured.internal_ca == Path("/etc/draupnir/tls/internal-ca.pem")
    assert unconfigured.federation_certificate is None
    assert unconfigured.internal_ca is None


def test_the_genesis_hash_is_what_a_fresh_chain_starts_from() -> None:
    """A guard on the fixture above rather than on the worker."""
    first = entry(
        None, subject_type="run", subject_id=str(uuid4()), transition="registered", payload={}
    )
    assert first.prev_hash == GENESIS_HASH


# ---------------------------------------------------------------------------
# RF-E12: a job the scheduler has merely accepted is not one that is training.
# ---------------------------------------------------------------------------


class _Queueing:
    """A scheduler that queues before it runs, the way a real one does."""

    def __init__(self, states: list[JobState], known: bool = False) -> None:
        self.states = states
        self.known = known
        self.submitted: list[str] = []
        self.polls = 0

    def submit(self, plan: JobPlan, name: str = "") -> JobHandle:
        del plan
        self.submitted.append(name)
        return JobHandle(driver="stub", job_id="900", node=None)

    def poll(self, handle: JobHandle) -> JobStatus:
        del handle
        state = self.states[min(self.polls, len(self.states) - 1)]
        self.polls += 1
        return JobStatus(state=state, node="dvalin")

    def find(self, name: str) -> JobHandle | None:
        del name
        return JobHandle(driver="stub", job_id="900") if self.known else None

    def cancel(self, handle: JobHandle) -> JobStatus:
        del handle
        return JobStatus(state=JobState.CANCELLED)

    def logs(self, handle: JobHandle) -> str:
        del handle
        return ""


class _RecordingOrchestrator:
    """Records transitions so a test can assert none was made."""

    def __init__(self) -> None:
        self.transitions: list[Any] = []

    def transition(self, run_id: Any, target: Any, **kwargs: Any) -> Any:
        self.transitions.append((run_id, target, kwargs))
        return SimpleNamespace(state=target)

    def history(self, run_id: Any) -> Any:
        """An empty chain. `_corpus_of` falls back to a staged corpus file."""
        del run_id
        return iter(())


def _dispatch_context(scheduler: Any, orchestrator: Any, tmp_path: Path) -> stages.Context:
    """A context that plans with the development executor.

    These tests are about what the worker does with a *scheduler's* answers --
    queued, started, rejected before it ran -- and not about what it dispatches.
    Since RF-10 the ordinary path renders the run's specification through the
    plug-in registry, which needs a specification in the chain and a driver
    installed; neither has anything to do with the question here, and a
    `_RecordingOrchestrator` holds no chain to record a specification in.

    So the stand-in is asked for explicitly, which is the only way to get it.
    That is the property RF-10 wanted: a simulation is something a caller opts
    into by name, not the default nobody chose.
    """
    return stages.Context(
        orchestrator=orchestrator,
        scheduler=scheduler,
        scratch=tmp_path,
        may_dispatch=True,
        stand_in=True,
    )


def test_a_job_the_scheduler_has_queued_leaves_the_run_queued(tmp_path: Path) -> None:
    """The finding. A pending job is not a training run.

    On this estate it is not an edge case: the adapter array is
    `--array=0-55%3`, so fifty three of fifty six elements are pending at any
    moment by design. The board would have shown fifty six runs training
    against three appliances.
    """
    scheduler = _Queueing([JobState.PENDING])
    orchestrator = _RecordingOrchestrator()

    outcome = stages.dispatch(
        _dispatch_context(scheduler, orchestrator, tmp_path), facts(RunState.QUEUED)
    )

    assert outcome.result is stages.Result.DEFERRED
    assert "queued on the scheduler" in outcome.detail
    assert orchestrator.transitions == [], (
        "a job the scheduler had merely accepted was recorded as TRAINING"
    )


def test_the_run_transitions_when_the_scheduler_says_it_started(tmp_path: Path) -> None:
    """QUEUED already means "waiting to run", which is what pending is.

    So no new lifecycle state was needed for RF-E12 -- only the transition
    moved to where the run actually starts.
    """
    scheduler = _Queueing([JobState.RUNNING])
    orchestrator = _RecordingOrchestrator()

    outcome = stages.dispatch(
        _dispatch_context(scheduler, orchestrator, tmp_path), facts(RunState.QUEUED)
    )

    assert outcome.result is stages.Result.PLACED
    assert [target for _, target, _ in orchestrator.transitions] == [RunState.TRAINING]
    recorded = orchestrator.transitions[0][2]["payload"]
    assert recorded["scheduler_job_id"] == "900"
    assert recorded["node"] == "dvalin", "the node the scheduler reported was not recorded"


def test_a_run_already_submitted_is_not_submitted_again(tmp_path: Path) -> None:
    """What replaces recording an allocation the moment it is made.

    A worker that submitted and then died must not submit a second copy on the
    next tick. It asks the scheduler instead: the job carries a name derived
    from the run, so the question can be asked of the thing that knows.
    """
    scheduler = _Queueing([JobState.PENDING], known=True)
    orchestrator = _RecordingOrchestrator()

    outcome = stages.dispatch(
        _dispatch_context(scheduler, orchestrator, tmp_path), facts(RunState.QUEUED)
    )

    assert scheduler.submitted == [], "the run was submitted twice"
    assert outcome.result is stages.Result.DEFERRED
    assert orchestrator.transitions == []


def test_the_job_carries_a_name_derived_from_the_run(tmp_path: Path) -> None:
    """The name is the only thing tying a queued job back to its run."""
    scheduler = _Queueing([JobState.RUNNING])
    run = facts(RunState.QUEUED)

    stages.dispatch(_dispatch_context(scheduler, _RecordingOrchestrator(), tmp_path), run)

    assert scheduler.submitted == [stages.job_name_for(run.run_id)]
    assert str(run.run_id) in scheduler.submitted[0]


def test_a_job_rejected_before_it_ran_leaves_the_run_queued(tmp_path: Path) -> None:
    """An invalid partition or an unsatisfiable resource is not a run failing.

    The run has not failed at anything it did, so it stays QUEUED and the
    reason is reported every tick rather than once.
    """
    scheduler = _Queueing([JobState.FAILED])
    orchestrator = _RecordingOrchestrator()

    outcome = stages.dispatch(
        _dispatch_context(scheduler, orchestrator, tmp_path), facts(RunState.QUEUED)
    )

    assert outcome.result is stages.Result.DEFERRED
    assert "before starting" in outcome.detail
    assert orchestrator.transitions == []


def test_a_scheduler_that_cannot_be_asked_still_dispatches(tmp_path: Path) -> None:
    """A local runner has no queue to search and starts immediately.

    Requiring `find` of every driver would break the development path to fix
    a problem it does not have.
    """

    class _Immediate(_Queueing):
        find = None  # type: ignore[assignment]

    scheduler = _Immediate([JobState.RUNNING])
    orchestrator = _RecordingOrchestrator()

    outcome = stages.dispatch(
        _dispatch_context(scheduler, orchestrator, tmp_path), facts(RunState.QUEUED)
    )

    assert outcome.result is stages.Result.PLACED
    assert scheduler.submitted, "nothing was submitted"


# ---------------------------------------------------------------------------
# Anchoring. RF-07.
# ---------------------------------------------------------------------------


class StubRegistry:
    """MEGINGJORD's anchoring half, without a network."""

    def __init__(self, outcome: str = "countersigned", reason: str = "") -> None:
        self.outcome = outcome
        self.reason = reason
        self.submitted: list[int] = []

    def countersign(self, head: Any, *, at: Any, countersignature: str) -> Any:
        from draupnir.core.domain.federation import Anchor, AnchorOutcome, Receipt

        del countersignature
        self.submitted.append(head.head.seq)
        outcome = AnchorOutcome(self.outcome)
        if outcome not in {AnchorOutcome.COUNTERSIGNED, AnchorOutcome.DUPLICATE}:
            return Receipt(outcome, None, self.reason or "refused")
        return Receipt(
            outcome,
            Anchor(head=head, countersigned_at=at, countersignature="c" * 64, outcome=outcome),
            "",
        )


def a_head(seq: int = 7) -> Any:
    """One chain head, ready to submit."""
    from datetime import UTC, datetime

    from draupnir.core.domain.federation import AnchorSubmission
    from draupnir.core.domain.ledger import ChainHead

    return AnchorSubmission(
        head=ChainHead(site_id="sindri", seq=seq, entry_hash="a" * 64),
        previous_hash="b" * 64,
        submitted_at=datetime.now(UTC),
        signature="s" * 64,
        key_id="forge-1",
    )


def an_agent() -> Any:
    """A site agent for `sindri`."""
    from draupnir.gullinbursti.agent import Gullinbursti

    return Gullinbursti(site_id="sindri", signing_key_id="forge-1")


def test_a_successful_anchor_reports_no_alarm() -> None:
    """RF-07. `freshness` alarmed on every tick, for ever, because nothing anchored."""
    from datetime import UTC, datetime

    registry = StubRegistry()

    finding, recorded = duties.anchor(an_agent(), registry, a_head(), now=datetime.now(UTC))

    assert not finding.alarm
    assert registry.submitted == [7]
    assert recorded is not None
    assert recorded.accepted
    assert recorded.seq == 7


def test_a_forge_with_no_link_alarms_rather_than_pretending() -> None:
    """Every estate today. The alarm names what is missing."""
    from datetime import UTC, datetime

    finding, recorded = duties.anchor(None, None, None, now=datetime.now(UTC))

    assert finding.alarm
    assert "not anchored anywhere" in finding.detail
    assert "11A.3" in finding.detail
    assert recorded is None


def test_a_rejected_anchor_alarms_and_is_still_recorded() -> None:
    """Both outcomes are a matter of record, not only the successful one.

    A chain that recorded only successes could not tell "never tried" from
    "tried and was refused", which are the two states an operator most needs
    told apart during an outage.
    """
    from datetime import UTC, datetime

    registry = StubRegistry(outcome="diverged", reason="sequence 7 does not follow 5")

    finding, recorded = duties.anchor(an_agent(), registry, a_head(), now=datetime.now(UTC))

    assert finding.alarm
    assert "does not follow" in finding.detail
    assert recorded is not None
    assert not recorded.accepted
    assert recorded.as_payload()["anchored_through"] == 0


def test_a_rejection_says_training_continues() -> None:
    """Decision S8. A partitioned forge trains and does not release.

    The message matters: an operator reading "the registry did not anchor"
    without this sentence would reasonably stop submitting work.
    """
    from datetime import UTC, datetime

    finding, _ = duties.anchor(
        an_agent(), StubRegistry(outcome="rejected"), a_head(), now=datetime.now(UTC)
    )

    assert "Training and evaluation continue" in finding.detail
    assert "release is unavailable" in finding.detail


def test_a_link_that_blinked_does_not_alarm_about_a_fresh_anchor() -> None:
    """SAD 11A.3 alarms when the last successful anchor is stale.

    Not when an attempt fails. A link down between two ticks would otherwise
    raise an alarm about a chain anchored ninety seconds ago, and an alarm
    firing on a condition an operator cannot act on is one they learn to close
    without reading.
    """
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    finding, _ = duties.anchor(
        an_agent(),
        StubRegistry(outcome="rejected", reason="the registry is unreachable"),
        a_head(),
        now=now,
        last_anchored_at=now - timedelta(minutes=2),
    )

    assert not finding.alarm, "an alarm about a chain anchored two minutes ago"
    # The reason is still reported. Silence is not the same as no alarm: the
    # operator reads what happened, and the duty simply does not escalate it.
    assert "did not anchor" in finding.detail
    assert "anchored 2 minutes ago" in finding.detail


def test_a_link_down_past_the_interval_does_alarm() -> None:
    """The other half. An hour without an anchor is the condition SAD 11A.3 names."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    finding, _ = duties.anchor(
        an_agent(),
        StubRegistry(outcome="rejected", reason="the registry is unreachable"),
        a_head(),
        now=now,
        last_anchored_at=now - timedelta(hours=4),
    )

    assert finding.alarm
    assert "release is unavailable" in finding.detail, (
        "the alarm does not say that training continues, so an operator reading it "
        "would reasonably stop submitting work (Decision S8)"
    )


def test_a_rejection_does_not_refresh_the_clock() -> None:
    """A rejection is recorded, but it is not an anchor.

    Letting one count would silence the very alarm that says the chain's end is
    unprotected -- which is the state a rejection puts the forge in.
    """
    from datetime import UTC, datetime

    _finding, recorded = duties.anchor(
        an_agent(), StubRegistry(outcome="rejected"), a_head(), now=datetime.now(UTC)
    )

    assert recorded is not None
    assert recorded.as_payload()["anchored_through"] == 0
    assert recorded.as_payload()["anchored_at"] == "", (
        "a rejection carries an anchoring instant, so the freshness clock would be "
        "refreshed by a failure"
    )


def test_the_anchor_payload_is_sealed() -> None:
    """AC-S13's boundary: hashes, names, timestamps and numbers, and nothing else."""
    from datetime import UTC, datetime

    from draupnir.core.domain.federation import sealed

    _finding, recorded = duties.anchor(an_agent(), StubRegistry(), a_head(), now=datetime.now(UTC))

    assert recorded is not None
    assert sealed(recorded.as_payload(), name="anchor record") == recorded.as_payload()


def test_a_payload_carrying_a_weight_is_refused() -> None:
    """The check is the constructor, so a payload that skipped it never existed."""
    import pytest as _pytest

    from draupnir.core.domain.federation import ContentLeakError, sealed

    # A slice of an adapter, which is what a leak looks like on the wire: too
    # long to be a name and not a hash. The check is on shape rather than on a
    # field name, which is the point -- a field called `notes` carrying a
    # tensor is the case a name-based rule would miss.
    with _pytest.raises(ContentLeakError):
        sealed({"seq": 7, "notes": "z" * 400}, name="anchor record")


def test_the_remote_registry_refuses_a_countersignature_that_is_not_there() -> None:
    """Countersigned and unsigned is not countersigned.

    Accepting it would record an anchor nobody can verify, which is worse than
    no anchor: the freshness duty goes quiet and the truncation SAD 11A.3
    exists to detect goes unnoticed.
    """
    from datetime import UTC, datetime

    from draupnir.gullinbursti import federation

    class Answer:
        status_code = 200

        def json(self) -> Any:
            return {"outcome": "countersigned", "anchoredAt": "2026-09-09T00:00:00+00:00"}

    class Client:
        def post(self, url: str, *, json: Any = None, headers: Any = None) -> Any:
            del url, json, headers
            return Answer()

    receipt = federation.RemoteRegistry(client=Client()).countersign(
        a_head(), at=datetime.now(UTC), countersignature=""
    )

    assert not receipt.accepted
    assert "no countersignature" in receipt.reason


def test_an_unreachable_registry_is_a_receipt_not_an_exception() -> None:
    """A partition is a degraded mode, not a fault that reaches a run."""
    from datetime import UTC, datetime

    from draupnir.gullinbursti import federation

    class Client:
        def post(self, url: str, *, json: Any = None, headers: Any = None) -> Any:
            del url, json, headers
            raise OSError("connection refused")

    receipt = federation.RemoteRegistry(client=Client()).countersign(
        a_head(), at=datetime.now(UTC), countersignature=""
    )

    assert not receipt.accepted
    assert "could not be reached" in receipt.reason


# ---------------------------------------------------------------------------
# Retention carried out. RF-27, AC-F19, AC-F20.
#
# The duty proposed and nothing ever acted on a proposal: S06's approval closed
# a dialog, and `hodd.retention.execute` was called by unit tests alone. These
# drive the path an approved deletion now takes through the worker.
# ---------------------------------------------------------------------------

from draupnir.hodd import retention as retention_record  # noqa: E402
from draupnir.worker.loop import Maintenance  # noqa: E402

_CORPUS = "c" * 64
_RAW = f"hodd://{SITE}/corpora/GBR/raw"
_CURATED = f"hodd://{SITE}/corpora/GBR/curated"


class _HeldStore:
    """A store holding named artefacts, recording what it deleted."""

    def __init__(self, *held: str) -> None:
        self.held = set(held)
        self.deleted: list[str] = []

    def stat(self, uri: str) -> Any:
        return SimpleNamespace(uri=uri, exists=uri in self.held)

    def delete(self, uri: str) -> int:
        self.deleted.append(uri)
        self.held.discard(uri)
        return 1024


def _retention_chain(*, approved: bool) -> _Chain:
    """A proposal for GBR's raw corpus, approved or not."""
    proposal = entry(
        None,
        subject_type=duties.CORPUS_SUBJECT,
        subject_id=_CORPUS,
        transition=retention_record.PROPOSED,
        payload={
            "corpusSha256": _CORPUS,
            "dueAt": (NOW - timedelta(days=2)).isoformat(),
            "releases": ["run-1"],
            "jurisdiction": "GBR",
            "artefact": _RAW,
        },
    )
    entries = [proposal]
    if approved:
        entries.append(
            entry(
                proposal,
                subject_type=duties.CORPUS_SUBJECT,
                subject_id=_CORPUS,
                transition=retention_record.APPROVED,
                payload={retention_record.ANSWERS: proposal.seq},
            )
        )
    return _Chain(tuple(entries))


def test_a_proposal_names_the_jurisdiction_and_the_raw_corpus_it_would_delete() -> None:
    """So an approver approves the deletion of something identified."""
    released_at = NOW - RETENTION - timedelta(days=1)
    run_id = str(uuid4())
    registered = entry(
        None,
        subject_type="run",
        subject_id=run_id,
        transition="->DRAFT",
        payload={"name": "cim-gbr-v0.1"},
    )
    curated = entry(
        registered,
        subject_type="run",
        subject_id=run_id,
        transition=f"{RunState.LICENCE_CLEARED}->{RunState.CURATED}",
        payload={"output_sha256": _CORPUS},
        ts=released_at - RETENTION,
    )
    queued = entry(
        curated,
        subject_type="run",
        subject_id=run_id,
        transition=f"{RunState.CURATED}->{RunState.QUEUED}",
        payload={"input_artefact_sha256": _CORPUS},
        ts=released_at - RETENTION,
    )
    released = entry(
        queued,
        subject_type="run",
        subject_id=run_id,
        transition=f"{RunState.AWAITING_APPROVAL}->{RunState.RELEASED}",
        payload={},
        ts=released_at,
    )

    (due,) = duties.due_corpora((registered, curated, queued, released), now=NOW)

    assert due.jurisdiction == "GBR"
    assert due.artefact_uri == _RAW
    assert due.as_payload()["artefact"] == _RAW


def test_an_approved_deletion_is_carried_out_and_the_manifests_kept() -> None:
    """AC-F19, through the duty."""
    vault = _HeldStore(_RAW, _CURATED)

    (done,) = duties.execute_approved(_retention_chain(approved=True), vault, now=NOW)

    assert done.transition == retention_record.EXECUTED
    assert vault.deleted == [_RAW]
    assert _CURATED in vault.held, "the curated manifests went with the raw corpus"
    assert done.payload["manifestsRetained"] is True


def test_a_deletion_that_would_leave_no_manifest_is_refused_naming_the_release() -> None:
    """AC-F20, through the duty. The curated corpus is the manifest a deletion leaves."""
    vault = _HeldStore(_RAW)

    (done,) = duties.execute_approved(_retention_chain(approved=True), vault, now=NOW)

    assert done.transition == retention_record.REFUSED
    assert "run-1" in str(done.payload["reason"])
    assert vault.deleted == []


def test_an_unapproved_proposal_is_never_carried_out() -> None:
    """However far past due. The unattended job SAD 7.3 rules out."""
    vault = _HeldStore(_RAW, _CURATED)

    assert duties.execute_approved(_retention_chain(approved=False), vault, now=NOW) == ()
    assert vault.deleted == []


def test_a_worker_with_no_vault_refuses_on_the_record() -> None:
    (done,) = duties.execute_approved(_retention_chain(approved=True), None, now=NOW)

    assert done.transition == retention_record.REFUSED
    assert "DRAUPNIR_VAULT_ROOT" in str(done.payload["reason"])


def test_the_retention_duty_hands_its_outcomes_to_the_loop() -> None:
    """Recorded by the transaction that owns the chain, like every other duty's."""
    vault = _HeldStore(_RAW, _CURATED)
    maintenance = Maintenance(
        chain=_retention_chain(approved=True),
        workspace=SimpleNamespace(store=vault),
    )

    maintenance.perform(Duty.RETENTION, now=NOW)

    assert [item.transition for item in maintenance.retention_outcomes] == [
        retention_record.EXECUTED
    ]
