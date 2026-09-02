"""The checkpoint budget, and the retry policy that sits behind AC-F6.

The checkpoint requirement is a number with a reason: no more than thirty
minutes of work is ever unwritten. An interval fixed in a specification cannot
deliver that, because it is a guess about hardware. So the interval is derived
from observed step time, and recomputed once fifty steps have actually run.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from draupnir.hamarr import checkpoints
from draupnir.hamarr.checkpoints import (
    MAX_UNWRITTEN,
    RECOMPUTE_AFTER,
    CheckpointError,
    Observation,
)
from draupnir.motsognir import retry

# -- the thirty minute budget ----------------------------------------------


@pytest.mark.parametrize(
    "seconds",
    [1.0, 2.5, 4.0, 7.5, 12.0, 30.0, 60.0, 119.0],
)
def test_no_interval_ever_leaves_more_than_thirty_minutes_unwritten(
    seconds: float,
) -> None:
    """The requirement, across every plausible step time."""
    policy = checkpoints.derive(timedelta(seconds=seconds))

    assert policy.exposure <= MAX_UNWRITTEN
    assert policy.within_budget


def test_the_interval_is_the_largest_that_fits_the_budget() -> None:
    """Not merely safe: as large as it can be, so the estate trains."""
    policy = checkpoints.derive(timedelta(seconds=6))

    assert policy.save_steps == 300
    assert policy.exposure == timedelta(minutes=30)

    # One more step would exceed it.
    assert checkpoints.exposure_of(301, timedelta(seconds=6)) > MAX_UNWRITTEN


def test_a_very_slow_step_is_held_at_the_floor() -> None:
    """A 27B checkpoint costs more than the work a shorter interval protects."""
    policy = checkpoints.derive(timedelta(seconds=600))

    assert policy.save_steps == checkpoints.MIN_SAVE_STEPS
    assert not policy.within_budget
    assert "floor" in policy.reason


def test_a_very_fast_step_is_held_at_the_ceiling() -> None:
    policy = checkpoints.derive(timedelta(seconds=0.1))

    assert policy.save_steps == checkpoints.MAX_SAVE_STEPS
    assert policy.within_budget


def test_a_nonsensical_step_time_is_refused() -> None:
    with pytest.raises(CheckpointError):
        checkpoints.derive(timedelta(0))


# -- provisional, then measured --------------------------------------------


def test_the_first_interval_is_provisional_and_says_so() -> None:
    """A job needs a save_steps at submission, before anything has run."""
    policy = checkpoints.initial()

    assert policy.provisional is True
    assert policy.within_budget
    assert "Recomputed after 50 steps" in policy.reason


def test_the_interval_is_not_recomputed_before_fifty_steps() -> None:
    """Early steps carry compilation and allocator warm-up, and are not typical."""
    initial = checkpoints.initial()
    too_early = Observation(steps=RECOMPUTE_AFTER - 1, elapsed=timedelta(seconds=49))

    assert checkpoints.recompute(too_early, initial) is None


def test_the_interval_is_recomputed_after_fifty_steps() -> None:
    """The measurement replaces the guess, and the guess was wrong."""
    initial = checkpoints.initial()
    observed = Observation(steps=50, elapsed=timedelta(seconds=150))  # 3s per step

    revised = checkpoints.recompute(observed, initial)

    assert revised is not None
    assert revised.provisional is False
    assert revised.step_time == timedelta(seconds=3)
    assert revised.save_steps == 600
    assert revised.exposure == timedelta(minutes=30)
    assert revised.save_steps != initial.save_steps


def test_a_measurement_that_changes_nothing_produces_no_revision() -> None:
    """A driver rewriting its configuration on every poll is a ledger of noise."""
    settled = checkpoints.derive(timedelta(seconds=3))
    observed = Observation(steps=400, elapsed=timedelta(seconds=1200))

    assert checkpoints.recompute(observed, settled) is None


def test_an_authored_interval_is_checked_against_the_budget() -> None:
    """A specification may name save_steps; it is still checked at submission."""
    checkpoints.check(100, timedelta(seconds=10))

    with pytest.raises(CheckpointError, match="unwritten"):
        checkpoints.check(1000, timedelta(seconds=10))


def test_the_policy_is_recorded_as_numbers_and_a_reason() -> None:
    payload = checkpoints.derive(timedelta(seconds=6)).as_payload

    assert payload["saveSteps"] == 300
    assert payload["exposureSeconds"] == 1800.0
    assert payload["provisional"] is False


# -- retry and backoff ------------------------------------------------------


def test_backoff_is_exponential_and_bounded() -> None:
    # Seeded, not cryptographic: a backoff test needs a repeatable draw.
    fixed = random.Random(0)  # noqa: S311
    delays = [retry.backoff(attempt, jitter=0, rng=fixed) for attempt in range(1, 9)]

    assert delays[0] == timedelta(seconds=30)
    assert delays[1] == timedelta(seconds=60)
    assert delays[2] == timedelta(seconds=120)
    assert all(delay <= retry.MAX_DELAY for delay in delays)
    assert delays[-1] == retry.MAX_DELAY


def test_jitter_spreads_a_thundering_herd() -> None:
    """Fifty six elements that fail together must not retry together."""
    source = random.Random(1)  # noqa: S311
    delays = {retry.backoff(3, rng=source) for _ in range(56)}

    assert len(delays) > 50
    assert all(delay <= retry.backoff(3, jitter=0) for delay in delays)


def test_backoff_counts_attempts_from_one() -> None:
    with pytest.raises(retry.RetryError):
        retry.backoff(0)


def test_a_retry_within_budget_is_permitted() -> None:
    decision = retry.decide(attempts=1, budget=2)

    assert decision
    assert decision.attempt == 2
    assert decision.delay > timedelta(0)


def test_an_exhausted_budget_stops_the_element() -> None:
    decision = retry.decide(attempts=3, budget=2)

    assert not decision
    assert "exhausted" in decision.reason


def test_an_out_of_memory_kill_is_not_retried() -> None:
    """The same job exhausts the same memory; retrying spends an allocation."""
    decision = retry.decide(attempts=1, budget=3, exit_code=137)

    assert not decision
    assert "out of memory" in decision.reason


def test_a_scheduled_retry_carries_an_offset_aware_due_time() -> None:
    now = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

    decision, due = retry.schedule(1, 2, now)

    assert decision
    assert due is not None
    assert due > now
    assert due.tzinfo is not None


def test_a_naive_timestamp_is_refused() -> None:
    """SAD 11E.2: timestamps carry an explicit offset."""
    decision = retry.decide(attempts=1, budget=2)

    with pytest.raises(retry.RetryError):
        decision.due_at(datetime(2026, 3, 2, 9, 0))  # noqa: DTZ001


def test_step_time_cannot_be_derived_from_no_steps() -> None:
    """Dividing by zero steps would report an interval with nothing behind it."""
    with pytest.raises(CheckpointError):
        _ = Observation(steps=0, elapsed=timedelta(seconds=10)).step_time
