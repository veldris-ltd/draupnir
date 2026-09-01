"""The transition table of SAD 6.1 is data, so it can be tested as data."""

from __future__ import annotations

import pytest

from draupnir.core.domain.states import (
    ALLOWED_TRANSITIONS,
    RUN_PHASE_STATES,
    TERMINAL_STATES,
    IllegalTransitionError,
    RunState,
    assert_allowed,
    is_allowed,
)


def test_every_state_appears_in_the_table() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(RunState)


def test_every_target_is_a_known_state() -> None:
    for targets in ALLOWED_TRANSITIONS.values():
        assert targets <= set(RunState)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (RunState.DRAFT, RunState.CORPUS_REGISTERED),
        (RunState.CORPUS_REGISTERED, RunState.QUARANTINED),
        (RunState.TRAINING, RunState.FAILED),
        (RunState.EVALUATING, RunState.QUEUED),
        (RunState.AWAITING_APPROVAL, RunState.RELEASED),
    ],
)
def test_tabulated_transitions_are_permitted(source: RunState, target: RunState) -> None:
    assert is_allowed(source, target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (RunState.DRAFT, RunState.RELEASED),
        (RunState.QUEUED, RunState.TRAINED),
        (RunState.RELEASED, RunState.QUARANTINED),
        (RunState.FAILED, RunState.TRAINING),
    ],
)
def test_untabulated_transitions_are_refused(source: RunState, target: RunState) -> None:
    assert not is_allowed(source, target)
    with pytest.raises(IllegalTransitionError):
        assert_allowed(source, target)


def test_terminal_states_are_the_three_that_end_a_lifecycle() -> None:
    assert {RunState.FAILED, RunState.RELEASED, RunState.QUARANTINED} == TERMINAL_STATES


def test_release_is_reachable_only_through_approval() -> None:
    sources = [
        state for state, targets in ALLOWED_TRANSITIONS.items() if RunState.RELEASED in targets
    ]
    assert sources == [RunState.AWAITING_APPROVAL]


def test_run_phase_states_are_the_twelve_the_seed_covers() -> None:
    # SAD 6.1 tabulates fourteen states; CORPUS_REGISTERED and LICENCE_CLEARED
    # describe a corpus before a run specification exists and are carried by
    # `source`. See scripts/seed.py.
    assert len(RUN_PHASE_STATES) == 12
    assert set(RUN_PHASE_STATES) | {RunState.CORPUS_REGISTERED, RunState.LICENCE_CLEARED} == set(
        RunState
    )
