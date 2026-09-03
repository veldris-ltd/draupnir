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
from typing import Any
from uuid import UUID, uuid4

import pytest

from draupnir.core.application.orchestrator import RunFacts
from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry, append
from draupnir.core.domain.states import RunState
from draupnir.hodd.retention import RETENTION
from draupnir.hodd.stores import VaultUnavailableError
from draupnir.interfaces.types import JobHandle, JobPlan
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


def test_the_fabric_probe_runs_the_benchmark_sad_11_3_names() -> None:
    plan = duties.probe_plan(Path("."), nodes=3)
    assert plan.command[0] == duties.NCCL_TESTS
    assert plan.resources.partition == "ring"
    assert plan.resources.nodes == 3


def test_an_absent_benchmark_is_reported_rather_than_alarmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ring on this machine is not a degraded fabric on the estate.

    The scheduler is deliberately one that would raise if it were used: a
    finding that names the missing benchmark must be reached without placing
    anything.
    """
    monkeypatch.setattr(duties, "probe_installed", lambda: False)
    finding = duties.probe(_NoScheduler(), workdir=Path("."))
    assert not finding.alarm
    assert duties.NCCL_TESTS in finding.detail


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
            "DRAUPNIR_WORKER_FABRIC_BASELINE_GBPS": "235.6",
            "DRAUPNIR_WORKER_DUTIES": "0",
        }
    )
    assert settings.site_id == "eitri"
    assert settings.interval == pytest.approx(0.5)
    assert settings.fabric_baseline_gbps == pytest.approx(235.6)
    assert settings.perform_duties is False


def test_the_genesis_hash_is_what_a_fresh_chain_starts_from() -> None:
    """A guard on the fixture above rather than on the worker."""
    first = entry(
        None, subject_type="run", subject_id=str(uuid4()), transition="registered", payload={}
    )
    assert first.prev_hash == GENESIS_HASH
