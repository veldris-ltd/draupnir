"""The seam between executing a gate and judging one.

SAD 5.2 splits these deliberately. GLEIPNIR owns "gate definitions"; RAUN owns
"gate suite execution, baseline management, comparison, regression detection".
Decision S4 is the reason: the module that runs the evaluation must not be the
module that decides what the numbers mean, or a disappointing result and a
revised threshold become one action.

The import contracts make that structural. GLEIPNIR and RAUN are independent
siblings and cannot import each other at all, so the seam has to be a shape
rather than a call -- exactly as HODD renders facts and GLEIPNIR consumes them.

RAUN therefore takes a `Judge`. It hands over measurements, baselines and the
gate identifiers it was asked to produce, and receives outcomes and a verdict.
It never sees a threshold, a comparison operator or a blocking flag, so there
is nowhere in RAUN for a gate to be softened.

The composition -- wiring GLEIPNIR's definitions into this shape -- lives in
`draupnir.api.assurance`, which is the layer above both.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from draupnir.interfaces.types import GateOutcome


@runtime_checkable
class Judgement(Protocol):
    """What a judge returns: per gate outcomes, and whether they pass."""

    # Read-only members, so a frozen dataclass satisfies the protocol. A judge
    # hands back a result; nothing on this side of the seam may edit one.

    @property
    def outcomes(self) -> tuple[GateOutcome, ...]:
        """One outcome per gate asked for, with baseline and margin (SAD 7.1)."""
        ...

    @property
    def passed(self) -> bool:
        """Whether the artefact may proceed. Which failures block is the judge's."""
        ...

    @property
    def suite_version(self) -> str:
        """The suite version the verdict was reached under."""
        ...


@runtime_checkable
class Judge(Protocol):
    """Something that can say whether measurements satisfy a set of gates."""

    def __call__(
        self,
        measurements: Mapping[str, float],
        baselines: Mapping[str, float],
        *,
        suite_version: str,
        gate_ids: Sequence[str],
    ) -> Judgement:
        """Return outcomes and a verdict for these measurements.

        A gate identifier with no measurement is the judge's problem, not the
        caller's: a gate nobody ran is a gate nobody passed, and answering that
        here rather than at the call site keeps one definition of it.
        """
        ...
