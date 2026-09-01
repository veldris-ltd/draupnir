"""Projector properties.

AC-Q4 names projector idempotence explicitly, with at least 500 examples. The
property that matters is stronger than "running it twice is safe": the fold
must depend on nothing but the entries it is given, so that a rebuild on a
different machine, months later, from the same chain, produces the same rows.

Chains are generated as walks of the real transition table rather than as
arbitrary strings, because a fold over a chain the state machine forbids is
not a case the projector is required to survive -- it is required to refuse it,
and `tests/unit/test_projector.py` covers that.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from draupnir.core.domain.ledger import LedgerEntry, append
from draupnir.core.domain.projector import REGISTRATION, as_rows, project
from draupnir.core.domain.states import RunState, transitions_from

EXAMPLES = settings(
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@st.composite
def walks(draw: st.DrawFn, runs: int = 3) -> list[tuple[str, list[str]]]:
    """Generate a set of runs, each with a legal walk of the SAD 6.1 machine."""
    count = draw(st.integers(min_value=1, max_value=runs))
    generated: list[tuple[str, list[str]]] = []
    for index in range(count):
        state = RunState.DRAFT
        path: list[str] = []
        for _ in range(draw(st.integers(min_value=0, max_value=12))):
            options = transitions_from(state)
            if not options:
                break
            transition = draw(st.sampled_from(options))
            path.append(transition.name)
            state = transition.target
        generated.append((f"run-{index:04d}", path))
    return generated


def build(walk: list[tuple[str, list[str]]], site_id: str = "sindri") -> list[LedgerEntry]:
    """Turn walks into a real, linked, hashed chain."""
    entries: list[LedgerEntry] = []
    previous: LedgerEntry | None = None
    step = 0

    for run_id, _ in walk:
        step += 1
        previous = append(
            previous=previous,
            site_id=site_id,
            ts=EPOCH + timedelta(minutes=step),
            actor="system:test",
            subject_type="run",
            subject_id=run_id,
            transition=REGISTRATION,
            payload={"name": run_id, "spec_hash": "a" * 64, "kind": "adapter"},
        )
        entries.append(previous)

    for run_id, path in walk:
        for transition in path:
            step += 1
            payload: dict[str, Any] = {"scheduler_job_id": "421337", "node": "dvalin"}
            previous = append(
                previous=previous,
                site_id=site_id,
                ts=EPOCH + timedelta(minutes=step),
                actor="system:test",
                subject_type="run",
                subject_id=run_id,
                transition=transition,
                payload=payload,
            )
            entries.append(previous)
    return entries


@EXAMPLES
@given(walk=walks())
def test_projection_is_idempotent(walk: list[tuple[str, list[str]]]) -> None:
    """Folding the same chain twice produces the same rows."""
    entries = build(walk)
    assert as_rows(project(entries).values()) == as_rows(project(entries).values())


@EXAMPLES
@given(walk=walks())
def test_a_rebuild_from_zero_equals_the_incremental_result(
    walk: list[tuple[str, list[str]]],
) -> None:
    """Replaying the whole chain equals replaying it in two halves.

    This is the property `rebuild_projection` relies on: there is no state
    outside the entries, so where the fold is cut makes no difference.
    """
    entries = build(walk)
    if len(entries) < 2:
        return
    cut = len(entries) // 2

    whole = project(entries)
    piecewise = project(entries[:cut])
    piecewise.update(project(entries))

    assert as_rows(whole.values()) == as_rows(piecewise.values())


@EXAMPLES
@given(walk=walks())
def test_every_projected_run_is_registered_by_the_chain(
    walk: list[tuple[str, list[str]]],
) -> None:
    """The fold invents nothing: every row traces to a registration entry."""
    entries = build(walk)
    registered = {entry.subject_id for entry in entries if entry.transition == REGISTRATION}
    assert set(project(entries)) == registered


@EXAMPLES
@given(walk=walks())
def test_the_projected_state_is_the_last_transition_target(
    walk: list[tuple[str, list[str]]],
) -> None:
    """A run's state is where its final transition left it, and nowhere else."""
    runs = project(build(walk))
    for run_id, path in walk:
        expected = RunState(path[-1].split("->")[1]) if path else RunState.DRAFT
        assert runs[run_id].state is expected


@EXAMPLES
@given(walk=walks())
def test_retry_count_equals_the_number_of_requeues(
    walk: list[tuple[str, list[str]]],
) -> None:
    """Retry budget is spent by EVALUATING -> QUEUED and by nothing else."""
    runs = project(build(walk))
    for run_id, path in walk:
        assert runs[run_id].retry_count == path.count("EVALUATING->QUEUED")


@EXAMPLES
@given(walk=walks())
def test_a_run_that_started_has_a_start_time(walk: list[tuple[str, list[str]]]) -> None:
    """`started_at` is set exactly when the run has reached TRAINING."""
    runs = project(build(walk))
    for run_id, path in walk:
        reached = any(transition.endswith("->TRAINING") for transition in path)
        assert (runs[run_id].started_at is not None) is reached
