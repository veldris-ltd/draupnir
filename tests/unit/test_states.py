"""The state machine of SAD 6.1.

AC-N8 requires every transition in section 6.1 to be exercised. That is not a
promise this module makes in prose: `test_every_transition_has_a_case` fails if
a row is added to the table without a case here, so the coverage claim is
maintained by the build rather than by memory.

Each transition is exercised four ways: it is permitted, its guard admits it on
satisfying facts, its guard refuses it on failing facts, and `apply` refuses a
ledger payload that omits a field SAD 6.1 says the entry must record.
"""

from __future__ import annotations

from typing import Any

import pytest

from draupnir.core.domain.states import (
    ALLOWED_TRANSITIONS,
    GUARDS,
    RUN_PHASE_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    GuardRefusedError,
    IllegalTransitionError,
    MissingFactError,
    RunState,
    Transition,
    TransitionContext,
    apply,
    assert_allowed,
    evaluate,
    find,
    is_allowed,
    missing_records,
    transitions_from,
)

#: Facts that admit each transition, and facts that refuse it. Keyed by the
#: transition as it appears in a ledger entry.
CASES: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "DRAFT->CORPUS_REGISTERED": (
        {"sources_without_declaration": []},
        {"sources_without_declaration": ["hodd://sindri/corpora/GBR/raw"]},
    ),
    "CORPUS_REGISTERED->LICENCE_CLEARED": (
        {"sources_failing_policy": [], "base_model_cleared": True},
        {"sources_failing_policy": [], "base_model_cleared": False},
    ),
    "CORPUS_REGISTERED->QUARANTINED": (
        {"sources_failing_policy": ["LicenseRef-Proprietary-Unclear"]},
        {"sources_failing_policy": []},
    ),
    "LICENCE_CLEARED->CURATED": (
        {"curation_complete": True, "decontamination_confirmed": True},
        {"curation_complete": True, "decontamination_confirmed": False},
    ),
    "CURATED->QUEUED": (
        {"specification_hash": "a" * 64, "specification_valid": True},
        {"specification_hash": "a" * 64, "specification_valid": False},
    ),
    "QUEUED->TRAINING": (
        {"scheduler_job_id": "421337"},
        {"scheduler_job_id": None},
    ),
    "TRAINING->TRAINED": (
        {"exit_code": 0, "checkpoint_sha256": "b" * 64},
        {"exit_code": 1, "checkpoint_sha256": "b" * 64},
    ),
    "TRAINING->FAILED": (
        {"exit_code": 137, "watchdog_fired": False},
        {"exit_code": 0, "watchdog_fired": False},
    ),
    "TRAINED->EVALUATING": (
        {"suite_version": "raun-suite/2026.02"},
        {"suite_version": ""},
    ),
    "EVALUATING->MERGED": (
        {"failing_gates": []},
        {"failing_gates": ["E3"]},
    ),
    "EVALUATING->QUEUED": (
        {"failing_gates": ["E3"], "retry_budget_remaining": 2},
        {"failing_gates": ["E3"], "retry_budget_remaining": 0},
    ),
    "MERGED->QUANTISED": (
        {"failing_gates": []},
        {"failing_gates": ["E1"]},
    ),
    "QUANTISED->AWAITING_APPROVAL": (
        {"formats_regated": ["nvfp4", "gguf-q4km"], "formats_failing": []},
        {"formats_regated": ["nvfp4", "gguf-q4km"], "formats_failing": ["gguf-q4km"]},
    ),
    "AWAITING_APPROVAL->RELEASED": (
        {"approver_has_role": True, "decision": "APPROVED", "signature": "sig"},
        {"approver_has_role": False, "decision": "APPROVED", "signature": "sig"},
    ),
    "AWAITING_APPROVAL->QUARANTINED": (
        {"approver_has_role": True, "decision": "REJECTED"},
        {"approver_has_role": True, "decision": "APPROVED"},
    ),
}


def payload_for(transition: Transition) -> dict[str, Any]:
    """A payload carrying every field SAD 6.1 requires of this transition."""
    return {field: f"<{field}>" for field in transition.records}


def identify(transition: Transition) -> str:
    """Name a parametrised case after the transition it covers."""
    return transition.name


# ---------------------------------------------------------------------------
# The table itself
# ---------------------------------------------------------------------------


def test_the_table_has_one_row_per_transition_in_sad_61() -> None:
    expected = {
        "DRAFT->CORPUS_REGISTERED",
        "CORPUS_REGISTERED->LICENCE_CLEARED",
        "CORPUS_REGISTERED->QUARANTINED",
        "LICENCE_CLEARED->CURATED",
        "CURATED->QUEUED",
        "QUEUED->TRAINING",
        "TRAINING->TRAINED",
        "TRAINING->FAILED",
        "TRAINED->EVALUATING",
        "EVALUATING->MERGED",
        "EVALUATING->QUEUED",
        "MERGED->QUANTISED",
        "QUANTISED->AWAITING_APPROVAL",
        "AWAITING_APPROVAL->RELEASED",
        "AWAITING_APPROVAL->QUARANTINED",
    }
    assert {transition.name for transition in TRANSITIONS} == expected
    assert len(TRANSITIONS) == len(expected), "the table contains a duplicate row"


def test_every_transition_has_a_case() -> None:
    """AC-N8. Adding a row to the table without a case here fails the build."""
    assert {transition.name for transition in TRANSITIONS} == set(CASES)


def test_every_transition_names_a_registered_guard() -> None:
    for transition in TRANSITIONS:
        assert transition.guard in GUARDS, transition.name
        assert transition.description, f"{transition.guard} has no statement from SAD 6.1"


def test_every_transition_records_something() -> None:
    # A transition that records nothing would leave a state change with no
    # audit trail, which is the one thing the ledger exists to prevent.
    for transition in TRANSITIONS:
        assert transition.records, transition.name


def test_the_allowed_map_is_derived_from_the_table() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(RunState)
    for transition in TRANSITIONS:
        assert transition.target in ALLOWED_TRANSITIONS[transition.source]
    assert sum(len(targets) for targets in ALLOWED_TRANSITIONS.values()) == len(TRANSITIONS)


def test_terminal_states_are_the_three_that_end_a_lifecycle() -> None:
    assert {RunState.FAILED, RunState.RELEASED, RunState.QUARANTINED} == TERMINAL_STATES


def test_release_is_reachable_only_through_approval() -> None:
    sources = [t.source for t in TRANSITIONS if t.target is RunState.RELEASED]
    assert sources == [RunState.AWAITING_APPROVAL]


def test_run_phase_states_are_the_twelve_a_run_rests_in() -> None:
    # The two states of SAD 6.1 absent here are traversed by every run but
    # rested in by none: they describe a corpus awaiting a judgement.
    assert len(RUN_PHASE_STATES) == 12
    assert set(RUN_PHASE_STATES) | {
        RunState.CORPUS_REGISTERED,
        RunState.LICENCE_CLEARED,
    } == set(RunState)


def test_transitions_from_a_terminal_state_is_empty() -> None:
    for state in TERMINAL_STATES:
        assert transitions_from(state) == ()


# ---------------------------------------------------------------------------
# Every transition, four ways
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("transition", TRANSITIONS, ids=identify)
def test_the_transition_is_permitted(transition: Transition) -> None:
    assert is_allowed(transition.source, transition.target)
    assert assert_allowed(transition.source, transition.target) is transition
    assert find(transition.source, transition.target) is transition


@pytest.mark.parametrize("transition", TRANSITIONS, ids=identify)
def test_the_guard_admits_it_on_satisfying_facts(transition: Transition) -> None:
    passing, _ = CASES[transition.name]
    outcome = evaluate(transition.source, transition.target, TransitionContext(passing))
    assert outcome.passed, f"{transition.name}: {outcome.reason}"
    assert outcome.guard == transition.guard
    assert outcome.reason == ""


@pytest.mark.parametrize("transition", TRANSITIONS, ids=identify)
def test_the_guard_refuses_it_on_failing_facts(transition: Transition) -> None:
    _, failing = CASES[transition.name]
    outcome = evaluate(transition.source, transition.target, TransitionContext(failing))
    assert not outcome.passed, f"{transition.name} was admitted on facts that should refuse it"
    assert outcome.reason, "a refusal must say why"

    with pytest.raises(GuardRefusedError) as raised:
        apply(
            transition.source,
            transition.target,
            TransitionContext(failing),
            payload_for(transition),
        )
    assert raised.value.transition is transition


@pytest.mark.parametrize("transition", TRANSITIONS, ids=identify)
def test_apply_admits_a_complete_payload(transition: Transition) -> None:
    passing, _ = CASES[transition.name]
    applied = apply(
        transition.source, transition.target, TransitionContext(passing), payload_for(transition)
    )
    assert applied is transition


@pytest.mark.parametrize("transition", TRANSITIONS, ids=identify)
def test_apply_refuses_a_payload_missing_a_required_field(transition: Transition) -> None:
    passing, _ = CASES[transition.name]
    payload = payload_for(transition)
    dropped = transition.records[0]
    del payload[dropped]

    assert missing_records(transition, payload) == (dropped,)
    with pytest.raises(ValueError, match=dropped):
        apply(transition.source, transition.target, TransitionContext(passing), payload)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (RunState.DRAFT, RunState.RELEASED),
        (RunState.QUEUED, RunState.TRAINED),
        (RunState.RELEASED, RunState.QUARANTINED),
        (RunState.FAILED, RunState.TRAINING),
        (RunState.DRAFT, RunState.CURATED),
    ],
)
def test_untabulated_transitions_are_refused(source: RunState, target: RunState) -> None:
    assert not is_allowed(source, target)
    assert find(source, target) is None
    with pytest.raises(IllegalTransitionError):
        assert_allowed(source, target)


def test_a_guard_without_its_facts_raises_rather_than_defaulting() -> None:
    # A guard that treated an absent fact as False would refuse a transition
    # for a reason nobody evaluated; as True, it would wave one through.
    with pytest.raises(MissingFactError) as raised:
        evaluate(RunState.EVALUATING, RunState.MERGED, TransitionContext({}))
    assert raised.value.fact == "failing_gates"
    assert raised.value.guard == "gates-pass"


def test_a_guard_name_cannot_be_registered_twice() -> None:
    from draupnir.core.domain.states import guard

    with pytest.raises(ValueError, match="already registered"):
        guard("gates-pass", "a second definition")(lambda context: None)  # type: ignore[arg-type,return-value]


def test_an_outcome_is_truthy_only_when_it_passed() -> None:
    passing, failing = CASES["EVALUATING->MERGED"]
    assert evaluate(RunState.EVALUATING, RunState.MERGED, TransitionContext(passing))
    assert not evaluate(RunState.EVALUATING, RunState.MERGED, TransitionContext(failing))


def test_the_requeue_transition_is_the_only_route_back_to_queued() -> None:
    # SAD 6.1: EVALUATING -> QUEUED. The projector counts retries off this
    # transition alone, so a second route into QUEUED would silently stop
    # retry budgets being enforced.
    sources = [t.source for t in TRANSITIONS if t.target is RunState.QUEUED]
    assert sorted(sources) == sorted([RunState.CURATED, RunState.EVALUATING])


# ---------------------------------------------------------------------------
# The other ways each guard can refuse
# ---------------------------------------------------------------------------

#: A guard usually has more than one reason to refuse. Each is a distinct
#: sentence an operator will read on the run board, and an untested one is
#: where an inverted condition hides.
ALTERNATIVE_REFUSALS: list[tuple[str, dict[str, Any], str]] = [
    (
        "CORPUS_REGISTERED->LICENCE_CLEARED",
        {"sources_failing_policy": ["LicenseRef-Proprietary"], "base_model_cleared": True},
        "licence policy refuses",
    ),
    (
        "LICENCE_CLEARED->CURATED",
        {"curation_complete": False, "decontamination_confirmed": True},
        "curation pipeline has not completed",
    ),
    (
        "CURATED->QUEUED",
        {"specification_hash": "", "specification_valid": True},
        "no run specification",
    ),
    (
        "TRAINING->TRAINED",
        {"exit_code": 0, "checkpoint_sha256": ""},
        "did not hash",
    ),
    (
        "TRAINING->FAILED",
        {"exit_code": 0, "watchdog_fired": False},
        "did not fire",
    ),
    (
        "EVALUATING->QUEUED",
        {"failing_gates": [], "retry_budget_remaining": 3},
        "no gate failed",
    ),
    (
        "QUANTISED->AWAITING_APPROVAL",
        {"formats_regated": [], "formats_failing": []},
        "no quantised build was re-gated",
    ),
    (
        "AWAITING_APPROVAL->RELEASED",
        {"approver_has_role": True, "decision": "REJECTED", "signature": "sig"},
        "did not approve",
    ),
    (
        "AWAITING_APPROVAL->RELEASED",
        {"approver_has_role": True, "decision": "APPROVED", "signature": ""},
        "not signed",
    ),
    (
        "AWAITING_APPROVAL->QUARANTINED",
        {"approver_has_role": False, "decision": "REJECTED"},
        "does not hold the approver role",
    ),
]


@pytest.mark.parametrize(
    ("name", "facts", "expected"),
    ALTERNATIVE_REFUSALS,
    ids=[f"{name}:{expected}" for name, _, expected in ALTERNATIVE_REFUSALS],
)
def test_a_guard_says_which_of_its_conditions_failed(
    name: str, facts: dict[str, Any], expected: str
) -> None:
    source, target = (RunState(part) for part in name.split("->"))
    outcome = evaluate(source, target, TransitionContext(facts))
    assert not outcome.passed
    assert expected in outcome.reason


def test_a_refusal_carries_no_reason_when_it_passed() -> None:
    passing, _ = CASES["QUEUED->TRAINING"]
    assert evaluate(RunState.QUEUED, RunState.TRAINING, TransitionContext(passing)).reason == ""
