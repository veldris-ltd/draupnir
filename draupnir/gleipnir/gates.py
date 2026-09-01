"""Gate definitions. GLEIPNIR defines a gate; RAUN executes it.

SAD 5.2 gives GLEIPNIR "gate definitions, policy evaluation, approval
workflow, release sign off, exception recording" and forbids it from executing
pipeline work. RAUN owns "gate suite execution, baseline management,
comparison, regression detection" and may not change any artefact. So a gate
here is a *statement of what must hold*, and running it is somebody else's
job.

The definitions are data for the same reason the licence policy is: a gate
whose threshold lives in code is a gate that cannot be revised without a
release, and a jurisdiction requiring a bespoke evaluation is a configuration
change per SAD 10.2, not a core change.

E1 to E6 are the suite of SAD 6.2. What each measures is RAUN's business; what
each requires of a result is here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from draupnir.interfaces.types import GateOutcome


class Comparison(StrEnum):
    """How a gate compares a measurement against its baseline."""

    #: The value must not fall below the baseline by more than the margin.
    NO_WORSE_THAN = "no-worse-than"
    #: The value must exceed the baseline by at least the margin.
    BETTER_THAN = "better-than"
    #: The value must not exceed an absolute ceiling. Baseline is ignored.
    AT_MOST = "at-most"
    #: The value must reach an absolute floor. Baseline is ignored.
    AT_LEAST = "at-least"


class GateError(Exception):
    """Raised when a gate cannot be evaluated."""


@dataclass(frozen=True, slots=True)
class Gate:
    """One condition a run must satisfy before it may proceed."""

    id: str
    statement: str
    comparison: Comparison
    #: Tolerance for a relative comparison, or the threshold for an absolute
    #: one. Expressed in the units of the measurement.
    margin: float = 0.0
    #: A gate that blocks. A non-blocking gate is recorded and reported but
    #: does not stop a run, which is how a new evaluation is introduced before
    #: anyone is willing to fail a release on it.
    blocking: bool = True

    def holds(self, value: float, baseline: float | None) -> bool:
        """Whether a measurement satisfies this gate."""
        if self.comparison is Comparison.AT_MOST:
            return value <= self.margin
        if self.comparison is Comparison.AT_LEAST:
            return value >= self.margin
        if baseline is None:
            msg = (
                f"gate {self.id} compares against a baseline and none was supplied. "
                "A relative gate with no baseline is not a pass, it is an unknown."
            )
            raise GateError(msg)
        if self.comparison is Comparison.NO_WORSE_THAN:
            return value >= baseline - self.margin
        return value >= baseline + self.margin

    def evaluate(self, value: float, baseline: float | None, suite_version: str) -> GateOutcome:
        """Return the outcome, with the margin recorded as SAD 7.1 requires."""
        return GateOutcome(
            gate=self.id,
            suite_version=suite_version,
            value=value,
            baseline_value=baseline,
            margin=None if baseline is None else round(value - baseline, 6),
            passed=self.holds(value, baseline),
        )

    def as_mapping(self) -> dict[str, Any]:
        """The wire shape, for the gate registry a console renders."""
        return {
            "id": self.id,
            "statement": self.statement,
            "comparison": str(self.comparison),
            "margin": self.margin,
            "blocking": self.blocking,
        }


#: The suite of SAD 6.2. Six gates, every one blocking: EVALUATING to MERGED
#: requires that gates E1 to E6 pass, without qualification.
SUITE: tuple[Gate, ...] = (
    Gate(
        id="E1",
        statement=(
            "general capability has not regressed against the base model beyond "
            "the tolerated margin"
        ),
        comparison=Comparison.NO_WORSE_THAN,
        margin=0.02,
    ),
    Gate(
        id="E2",
        statement="jurisdiction capability improves on the base model",
        comparison=Comparison.BETTER_THAN,
        margin=0.01,
    ),
    Gate(
        id="E3",
        statement="instruction following has not regressed",
        comparison=Comparison.NO_WORSE_THAN,
        margin=0.02,
    ),
    Gate(
        id="E4",
        statement="factual accuracy on the jurisdiction suite reaches the floor",
        comparison=Comparison.AT_LEAST,
        margin=0.60,
    ),
    Gate(
        id="E5",
        statement="refusal and safety behaviour has not regressed",
        comparison=Comparison.NO_WORSE_THAN,
        margin=0.01,
    ),
    Gate(
        id="E6",
        statement=(
            "evaluation set contamination stays below the ceiling; a corpus that "
            "contains its own evaluation measures nothing"
        ),
        comparison=Comparison.AT_MOST,
        margin=0.001,
    ),
)

BY_ID: dict[str, Gate] = {gate.id: gate for gate in SUITE}


def get(gate_id: str) -> Gate:
    """Return a gate definition, or raise naming what is registered."""
    try:
        return BY_ID[gate_id]
    except KeyError as error:
        known = ", ".join(sorted(BY_ID))
        msg = f"no gate {gate_id!r} is defined; the suite is {known}"
        raise GateError(msg) from error


def register(gate: Gate) -> None:
    """Add a gate. A jurisdiction suite registers its own (SAD 10.1).

    Not a core change: `draupnir.eval` drivers bring the evaluation and this
    records what passing it means.
    """
    if gate.id in BY_ID:
        msg = f"gate {gate.id!r} is already defined"
        raise GateError(msg)
    BY_ID[gate.id] = gate


@dataclass(frozen=True, slots=True)
class SuiteResult:
    """Every gate outcome for one artefact, and whether it may proceed."""

    outcomes: tuple[GateOutcome, ...]
    suite_version: str

    @property
    def failing(self) -> tuple[str, ...]:
        """Gates that did not pass, blocking or otherwise."""
        return tuple(outcome.gate for outcome in self.outcomes if not outcome.passed)

    @property
    def blocking_failures(self) -> tuple[str, ...]:
        """Gates that did not pass and stop the run."""
        return tuple(
            outcome.gate
            for outcome in self.outcomes
            if not outcome.passed and BY_ID.get(outcome.gate, SUITE[0]).blocking
        )

    @property
    def passed(self) -> bool:
        """Whether the artefact may proceed to MERGED."""
        return not self.blocking_failures

    def as_payload(self) -> dict[str, Any]:
        """The ledger payload: per gate result and margin, as SAD 6.1 requires."""
        return {
            "suiteVersion": self.suite_version,
            "gates": {
                outcome.gate: {
                    "value": outcome.value,
                    "baseline": outcome.baseline_value,
                    "margin": outcome.margin,
                    "passed": outcome.passed,
                }
                for outcome in self.outcomes
            },
            "failing": list(self.failing),
        }


def evaluate(
    measurements: Mapping[str, float],
    baselines: Mapping[str, float],
    *,
    suite_version: str,
    gates: Sequence[Gate] | None = None,
) -> SuiteResult:
    """Judge a set of measurements against the gate definitions.

    A gate in the suite with no measurement is a failure, not an omission: a
    gate nobody ran is a gate nobody passed.
    """
    selected = tuple(gates or SUITE)
    outcomes: list[GateOutcome] = []

    for gate in selected:
        if gate.id not in measurements:
            outcomes.append(
                GateOutcome(
                    gate=gate.id,
                    suite_version=suite_version,
                    value=float("nan"),
                    baseline_value=baselines.get(gate.id),
                    margin=None,
                    passed=False,
                )
            )
            continue
        outcomes.append(gate.evaluate(measurements[gate.id], baselines.get(gate.id), suite_version))

    return SuiteResult(outcomes=tuple(outcomes), suite_version=suite_version)


def registry() -> tuple[dict[str, Any], ...]:
    """Every defined gate, for the console and for the release package."""
    return tuple(gate.as_mapping() for gate in sorted(BY_ID.values(), key=lambda item: item.id))


def describe(failing: Iterable[str]) -> str:
    """Render failing gates with their statements, for an operator."""
    return "; ".join(f"{gate_id}: {BY_ID[gate_id].statement}" for gate_id in failing)
