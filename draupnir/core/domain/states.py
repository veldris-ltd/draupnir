"""The model lifecycle state machine of SAD section 6.

The machine is data, not control flow. `TRANSITIONS` is one entry per row of
the table in SAD 6.1, each naming the guard that admits it and the fields its
ledger entry must record. Adding a state means adding rows; it never means
editing a branch, and there is no `if` anywhere below that enumerates states.

Guards are pure predicates over facts the caller supplies. The domain does not
fetch anything: "GLEIPNIR licence policy passes" is a fact presented to the
guard, not a call the guard makes. That is what keeps this module free of the
framework imports `.importlinter` forbids, and what lets every guard be tested
without a database.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


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


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


class MissingFactError(KeyError):
    """A guard was evaluated without a fact it needs.

    Raised rather than defaulting. A guard that silently treats an absent fact
    as False would let a transition be refused for the wrong reason, and one
    that treats it as True would let a run through a gate nobody evaluated.
    """

    def __init__(self, guard: str, fact: str) -> None:
        """Record which guard wanted which fact."""
        self.guard = guard
        self.fact = fact
        super().__init__(f"guard {guard!r} needs the fact {fact!r}")


@dataclass(frozen=True, slots=True)
class TransitionContext:
    """The facts available to a guard.

    A plain mapping rather than a typed record: the fact set grows with each
    module that contributes one, and a fixed shape here would make the domain
    depend on what GLEIPNIR or MOTSOGNIR happens to know this month.
    """

    facts: Mapping[str, Any]

    def require(self, guard: str, name: str) -> Any:
        """Return a fact, or raise naming the guard that wanted it."""
        try:
            return self.facts[name]
        except KeyError as error:
            raise MissingFactError(guard, name) from error

    def flag(self, guard: str, name: str) -> bool:
        """Return a fact coerced to bool."""
        return bool(self.require(guard, name))


@dataclass(frozen=True, slots=True)
class GuardOutcome:
    """Whether a guard admitted the transition, and why not if it did not."""

    passed: bool
    guard: str
    reason: str = ""

    def __bool__(self) -> bool:
        """Allow `if evaluate(...)`."""
        return self.passed


Guard = Callable[[TransitionContext], GuardOutcome]

#: Every guard, by the name the transition table refers to it by.
GUARDS: dict[str, Guard] = {}
#: The human readable statement of each guard, taken from SAD 6.1.
GUARD_DESCRIPTIONS: dict[str, str] = {}


def guard(name: str, description: str) -> Callable[[Guard], Guard]:
    """Register a guard under `name`."""

    def register(function: Guard) -> Guard:
        if name in GUARDS:
            msg = f"guard {name!r} is already registered"
            raise ValueError(msg)
        GUARDS[name] = function
        GUARD_DESCRIPTIONS[name] = description
        return function

    return register


def _outcome(name: str, passed: bool, reason: str) -> GuardOutcome:
    return GuardOutcome(passed=passed, guard=name, reason="" if passed else reason)


@guard(
    "every-source-declared",
    "Every source has a licence declaration and a personal data determination",
)
def _every_source_declared(context: TransitionContext) -> GuardOutcome:
    name = "every-source-declared"
    undeclared = context.require(name, "sources_without_declaration")
    return _outcome(
        name,
        not undeclared,
        f"{len(undeclared)} source(s) lack a licence or personal data determination: "
        f"{', '.join(map(str, undeclared))}",
    )


@guard(
    "licence-policy-passes",
    "GLEIPNIR licence policy passes for every source and for the base model",
)
def _licence_policy_passes(context: TransitionContext) -> GuardOutcome:
    name = "licence-policy-passes"
    failing = context.require(name, "sources_failing_policy")
    base_model_cleared = context.flag(name, "base_model_cleared")
    if failing:
        return _outcome(name, False, f"licence policy refuses: {', '.join(map(str, failing))}")
    return _outcome(name, base_model_cleared, "the base model is not licence cleared")


@guard("licence-policy-fails", "Any source fails licence policy")
def _licence_policy_fails(context: TransitionContext) -> GuardOutcome:
    name = "licence-policy-fails"
    failing = context.require(name, "sources_failing_policy")
    return _outcome(name, bool(failing), "no source fails licence policy")


@guard(
    "curation-complete",
    "Curation pipeline completes; decontamination confirmed against the evaluation sets",
)
def _curation_complete(context: TransitionContext) -> GuardOutcome:
    name = "curation-complete"
    if not context.flag(name, "curation_complete"):
        return _outcome(name, False, "the curation pipeline has not completed")
    return _outcome(
        name,
        context.flag(name, "decontamination_confirmed"),
        "decontamination against the evaluation sets is not confirmed",
    )


@guard("specification-validates", "A run specification exists and validates")
def _specification_validates(context: TransitionContext) -> GuardOutcome:
    name = "specification-validates"
    if not context.require(name, "specification_hash"):
        return _outcome(name, False, "no run specification")
    return _outcome(
        name,
        context.flag(name, "specification_valid"),
        "the run specification does not validate against its schema",
    )


@guard("allocation-obtained", "MOTSOGNIR obtains an allocation")
def _allocation_obtained(context: TransitionContext) -> GuardOutcome:
    name = "allocation-obtained"
    return _outcome(
        name,
        bool(context.require(name, "scheduler_job_id")),
        "the scheduler has not granted an allocation",
    )


@guard("executor-succeeded", "Executor exits zero and the final checkpoint hashes")
def _executor_succeeded(context: TransitionContext) -> GuardOutcome:
    name = "executor-succeeded"
    if context.require(name, "exit_code") != 0:
        return _outcome(name, False, f"executor exited {context.require(name, 'exit_code')}")
    return _outcome(
        name,
        bool(context.require(name, "checkpoint_sha256")),
        "the final checkpoint did not hash",
    )


@guard("executor-failed", "Executor exits non zero, or the watchdog fires")
def _executor_failed(context: TransitionContext) -> GuardOutcome:
    name = "executor-failed"
    failed = context.require(name, "exit_code") != 0 or context.flag(name, "watchdog_fired")
    return _outcome(name, failed, "the executor exited zero and the watchdog did not fire")


@guard("suite-resolves", "RAUN suite resolves for the artefact type")
def _suite_resolves(context: TransitionContext) -> GuardOutcome:
    name = "suite-resolves"
    return _outcome(
        name,
        bool(context.require(name, "suite_version")),
        "no RAUN suite resolves for this artefact type",
    )


@guard("gates-pass", "Gates E1 to E6 pass")
def _gates_pass(context: TransitionContext) -> GuardOutcome:
    name = "gates-pass"
    failing = context.require(name, "failing_gates")
    return _outcome(name, not failing, f"gate(s) failed: {', '.join(map(str, failing))}")


@guard("gate-failed-within-budget", "Any gate fails and the retry budget is not exhausted")
def _gate_failed_within_budget(context: TransitionContext) -> GuardOutcome:
    name = "gate-failed-within-budget"
    failing = context.require(name, "failing_gates")
    if not failing:
        return _outcome(name, False, "no gate failed")
    remaining = int(context.require(name, "retry_budget_remaining"))
    return _outcome(name, remaining > 0, "the retry budget is exhausted")


@guard("merge-regate-passes", "Re-gate of the merged artefact passes")
def _merge_regate_passes(context: TransitionContext) -> GuardOutcome:
    name = "merge-regate-passes"
    failing = context.require(name, "failing_gates")
    return _outcome(
        name, not failing, f"the merged artefact failed: {', '.join(map(str, failing))}"
    )


@guard("quantised-regate-passes", "Re-gate of every quantised build passes")
def _quantised_regate_passes(context: TransitionContext) -> GuardOutcome:
    name = "quantised-regate-passes"
    formats = context.require(name, "formats_regated")
    failing = context.require(name, "formats_failing")
    if not formats:
        return _outcome(name, False, "no quantised build was re-gated")
    return _outcome(name, not failing, f"format(s) failed: {', '.join(map(str, failing))}")


@guard("approver-signed", "A human with the approver role signs off")
def _approver_signed(context: TransitionContext) -> GuardOutcome:
    name = "approver-signed"
    if not context.flag(name, "approver_has_role"):
        return _outcome(name, False, "the actor does not hold the approver role")
    if context.require(name, "decision") != "APPROVED":
        return _outcome(name, False, "the approver did not approve")
    return _outcome(name, bool(context.require(name, "signature")), "the approval is not signed")


@guard("approver-rejected", "Approver rejects")
def _approver_rejected(context: TransitionContext) -> GuardOutcome:
    name = "approver-rejected"
    if not context.flag(name, "approver_has_role"):
        return _outcome(name, False, "the actor does not hold the approver role")
    return _outcome(
        name, context.require(name, "decision") == "REJECTED", "the approver did not reject"
    )


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Transition:
    """One row of the transition table in SAD 6.1."""

    source: RunState
    target: RunState
    guard: str
    #: The fields the ledger entry for this transition must carry, from the
    #: "Ledger entry" column of SAD 6.1. The orchestrator refuses a transition
    #: whose payload omits one, so the table is the schema for its own audit
    #: record rather than a description of it.
    records: tuple[str, ...]

    @property
    def name(self) -> str:
        """The transition as it appears in a ledger entry, `SOURCE->TARGET`."""
        return f"{self.source}->{self.target}"

    @property
    def description(self) -> str:
        """The guard's statement from SAD 6.1."""
        return GUARD_DESCRIPTIONS[self.guard]


#: The table of SAD 6.1, in order. One entry per row; nothing else defines
#: which transitions exist.
TRANSITIONS: tuple[Transition, ...] = (
    Transition(
        RunState.DRAFT,
        RunState.CORPUS_REGISTERED,
        "every-source-declared",
        ("sources", "source_sha256", "curator"),
    ),
    Transition(
        RunState.CORPUS_REGISTERED,
        RunState.LICENCE_CLEARED,
        "licence-policy-passes",
        ("policy_version", "evaluation_result"),
    ),
    Transition(
        RunState.CORPUS_REGISTERED,
        RunState.QUARANTINED,
        "licence-policy-fails",
        ("failing_source", "rule", "actor"),
    ),
    Transition(
        RunState.LICENCE_CLEARED,
        RunState.CURATED,
        "curation-complete",
        ("stage_retention", "output_sha256", "token_count"),
    ),
    Transition(
        RunState.CURATED,
        RunState.QUEUED,
        "specification-validates",
        ("spec_hash", "input_artefact_sha256"),
    ),
    Transition(
        RunState.QUEUED,
        RunState.TRAINING,
        "allocation-obtained",
        ("scheduler_job_id", "node", "placement"),
    ),
    Transition(
        RunState.TRAINING,
        RunState.TRAINED,
        "executor-succeeded",
        ("checkpoint_sha256", "steps", "final_loss"),
    ),
    Transition(
        RunState.TRAINING,
        RunState.FAILED,
        "executor-failed",
        ("exit_code", "last_log_lines", "resource_state"),
    ),
    Transition(
        RunState.TRAINED,
        RunState.EVALUATING,
        "suite-resolves",
        ("suite_version", "baseline"),
    ),
    Transition(
        RunState.EVALUATING,
        RunState.MERGED,
        "gates-pass",
        ("gate_results",),
    ),
    Transition(
        RunState.EVALUATING,
        RunState.QUEUED,
        "gate-failed-within-budget",
        ("failing_gate", "requeue_reason"),
    ),
    Transition(
        RunState.MERGED,
        RunState.QUANTISED,
        "merge-regate-passes",
        ("merge_config_hash", "sweep_result"),
    ),
    Transition(
        RunState.QUANTISED,
        RunState.AWAITING_APPROVAL,
        "quantised-regate-passes",
        ("format_gate_results",),
    ),
    Transition(
        RunState.AWAITING_APPROVAL,
        RunState.RELEASED,
        "approver-signed",
        ("approver", "signature", "decided_at"),
    ),
    Transition(
        RunState.AWAITING_APPROVAL,
        RunState.QUARANTINED,
        "approver-rejected",
        ("rejection_reason",),
    ),
)

_BY_PAIR: dict[tuple[RunState, RunState], Transition] = {
    (transition.source, transition.target): transition for transition in TRANSITIONS
}

#: Derived from `TRANSITIONS`, so the two can never disagree.
ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    state: frozenset(transition.target for transition in TRANSITIONS if transition.source is state)
    for state in RunState
}

TERMINAL_STATES: frozenset[RunState] = frozenset(
    state for state, targets in ALLOWED_TRANSITIONS.items() if not targets
)

#: States a `run` row holds. The two states of SAD 6.1 that are missing here --
#: CORPUS_REGISTERED and LICENCE_CLEARED -- describe a corpus before a run
#: specification exists, and are carried by `source`. See scripts/seed.py.
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


class GuardRefusedError(Exception):
    """Raised when a transition exists but its guard refuses it."""

    def __init__(self, transition: Transition, outcome: GuardOutcome) -> None:
        """Record the refusal and its stated reason."""
        self.transition = transition
        self.outcome = outcome
        super().__init__(f"{transition.name} refused by {outcome.guard}: {outcome.reason}")


def find(source: RunState, target: RunState) -> Transition | None:
    """Return the transition for this pair, or None if there is not one."""
    return _BY_PAIR.get((source, target))


def transitions_from(source: RunState) -> tuple[Transition, ...]:
    """Return every transition leaving `source`."""
    return tuple(transition for transition in TRANSITIONS if transition.source is source)


def is_allowed(source: RunState, target: RunState) -> bool:
    """Return whether `source -> target` appears in the transition table."""
    return (source, target) in _BY_PAIR


def assert_allowed(source: RunState, target: RunState) -> Transition:
    """Return the transition, or raise `IllegalTransitionError`."""
    transition = find(source, target)
    if transition is None:
        raise IllegalTransitionError(source, target)
    return transition


def evaluate(source: RunState, target: RunState, context: TransitionContext) -> GuardOutcome:
    """Evaluate the guard admitting `source -> target` against `context`."""
    transition = assert_allowed(source, target)
    return GUARDS[transition.guard](context)


def missing_records(transition: Transition, payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the fields SAD 6.1 requires of this transition that `payload` omits."""
    return tuple(field for field in transition.records if field not in payload)


def apply(
    source: RunState,
    target: RunState,
    context: TransitionContext,
    payload: Mapping[str, Any],
) -> Transition:
    """Guard, then check the audit record, then return the transition.

    This is the whole of the domain's contribution to a state change. Writing
    the ledger entry and projecting it belong to the orchestrator, in one
    transaction, per SAD 11B.
    """
    transition = assert_allowed(source, target)
    outcome = GUARDS[transition.guard](context)
    if not outcome.passed:
        raise GuardRefusedError(transition, outcome)

    absent = missing_records(transition, payload)
    if absent:
        msg = (
            f"the ledger entry for {transition.name} must record "
            f"{', '.join(transition.records)}; missing {', '.join(absent)} (SAD 6.1)"
        )
        raise ValueError(msg)
    return transition
