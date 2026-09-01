"""The model lifecycle state machine of SAD section 6.

The transition table is data, not control flow, so that the orchestrator can
guard a transition and the projector can replay one from the same source.
"""

from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    """States of the lifecycle in SAD 6.1."""

    DRAFT = "DRAFT"
    CORPUS_REGISTERED = "CORPUS_REGISTERED"
    LICENCE_CLEARED = "LICENCE_CLEARED"
    CURATED = "CURATED"
    QUEUED = "QUEUED"
    TRAINING = "TRAINING"
    TRAINED = "TRAINED"
    FAILED = "FAILED"
    EVALUATING = "EVALUATING"
    MERGED = "MERGED"
    QUANTISED = "QUANTISED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    RELEASED = "RELEASED"
    QUARANTINED = "QUARANTINED"


#: Permitted transitions, exactly as tabulated in SAD 6.1.
ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.DRAFT: frozenset({RunState.CORPUS_REGISTERED}),
    RunState.CORPUS_REGISTERED: frozenset({RunState.LICENCE_CLEARED, RunState.QUARANTINED}),
    RunState.LICENCE_CLEARED: frozenset({RunState.CURATED}),
    RunState.CURATED: frozenset({RunState.QUEUED}),
    RunState.QUEUED: frozenset({RunState.TRAINING}),
    RunState.TRAINING: frozenset({RunState.TRAINED, RunState.FAILED}),
    RunState.TRAINED: frozenset({RunState.EVALUATING}),
    RunState.FAILED: frozenset(),
    RunState.EVALUATING: frozenset({RunState.MERGED, RunState.QUEUED}),
    RunState.MERGED: frozenset({RunState.QUANTISED}),
    RunState.QUANTISED: frozenset({RunState.AWAITING_APPROVAL}),
    RunState.AWAITING_APPROVAL: frozenset({RunState.RELEASED, RunState.QUARANTINED}),
    RunState.RELEASED: frozenset(),
    RunState.QUARANTINED: frozenset(),
}

#: States a run occupies once a specification exists. The states before QUEUED
#: belong to the corpus, and are carried by `source` rather than by `run`.
RUN_PHASE_STATES: tuple[RunState, ...] = (
    RunState.DRAFT,
    RunState.CURATED,
    RunState.QUEUED,
    RunState.TRAINING,
    RunState.TRAINED,
    RunState.FAILED,
    RunState.EVALUATING,
    RunState.MERGED,
    RunState.QUANTISED,
    RunState.AWAITING_APPROVAL,
    RunState.RELEASED,
    RunState.QUARANTINED,
)


class IllegalTransitionError(Exception):
    """Raised when a transition is not in the table of SAD 6.1."""

    def __init__(self, source: RunState, target: RunState) -> None:
        """Record the refused transition."""
        self.source = source
        self.target = target
        super().__init__(f"{source} -> {target} is not a permitted transition")


def is_allowed(source: RunState, target: RunState) -> bool:
    """Return whether `source -> target` appears in the transition table."""
    return target in ALLOWED_TRANSITIONS[source]


def assert_allowed(source: RunState, target: RunState) -> None:
    """Raise `IllegalTransitionError` unless the transition is permitted."""
    if not is_allowed(source, target):
        raise IllegalTransitionError(source, target)


TERMINAL_STATES: frozenset[RunState] = frozenset(
    state for state, targets in ALLOWED_TRANSITIONS.items() if not targets
)
