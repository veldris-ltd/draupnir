"""The weight sweep as an object, not as a pile of runs that happen to be related.

AC-F8: "A merge executes with a weight sweep of at least five points, and each
point's gate results are comparable side by side in the console."

The requirement says "comparable side by side", and the way that goes wrong is
not the comparison, it is the collection. If a sweep is five independent runs
sharing a naming convention, then comparing them means somebody reconstructing
which five, from names, at the point of asking -- and a sixth run with a typo
in its name is either silently missing or silently included. Either way the
selected point is chosen from a set nobody can reproduce.

So a `Sweep` is one object holding its points, and the comparison is a method
on it. The selected point is recorded on the sweep, so what the model card
publishes and what the console displayed are the same fact rather than two
readings of the same data.

BRISINGAMEN runs the sweep and never decides which point is acceptable. SAD 5.2
is explicit: BRISINGAMEN "must not decide whether a merge is acceptable. RAUN
decides". So `select` refuses a point whose gates did not pass, and ranking is
by a stated criterion rather than by a judgement made here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from draupnir.core.domain.evidence import Evidence

#: AC-F8's floor. A sweep with fewer points has not demonstrated that the
#: weight was chosen rather than assumed.
MINIMUM_POINTS = 5


class SweepError(Exception):
    """Raised when a sweep cannot be built, compared or selected from."""


@dataclass(frozen=True, slots=True)
class MergePoint:
    """One point in a sweep: a merge configuration and what it scored."""

    #: What distinguishes this point. For a linear sweep, `{"weight": 0.4}`.
    parameters: Mapping[str, float]
    #: The merged artefact this configuration produced, once it has been built.
    artefact_sha256: str | None = None
    #: The gate results for that artefact. `None` until it has been evaluated.
    evidence: Evidence | None = None

    @property
    def label(self) -> str:
        """A stable name for the point, from its parameters."""
        return ", ".join(f"{key}={value:g}" for key, value in sorted(self.parameters.items()))

    @property
    def evaluated(self) -> bool:
        """Whether this point has gate results."""
        return self.evidence is not None

    @property
    def passed(self) -> bool:
        """Whether this point cleared every blocking gate."""
        return self.evidence is not None and self.evidence.passed

    def score(self, gate: str) -> float | None:
        """This point's measurement for one gate, if it has one."""
        if self.evidence is None:
            return None
        return self.evidence.measurements.get(gate)

    def config_hash(self) -> str:
        """The merge configuration hash SAD 6.1 records on MERGED to QUANTISED."""
        canonical = json.dumps(
            dict(sorted(self.parameters.items())), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, one row of the comparison matrix."""
        return {
            "label": self.label,
            "parameters": dict(sorted(self.parameters.items())),
            "configHash": self.config_hash(),
            "artefactSha256": self.artefact_sha256,
            "evaluated": self.evaluated,
            "passed": self.passed,
            "measurements": dict(sorted(self.evidence.measurements.items()))
            if self.evidence
            else {},
            "gates": self.evidence.as_payload()["gates"] if self.evidence else {},
        }


@dataclass(frozen=True, slots=True)
class Sweep:
    """N merge points over one base and one adapter, with their results."""

    method: str
    base_sha256: str
    adapter_sha256: str
    points: tuple[MergePoint, ...]
    #: The parameters of the point that was chosen, once one has been.
    selected: Mapping[str, float] | None = None
    #: Which gate the selection ranked on, recorded so the choice is explicable.
    selection_criterion: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Refuse a sweep that could not answer the question it exists to ask."""
        if len(self.points) < MINIMUM_POINTS:
            msg = (
                f"a sweep has at least {MINIMUM_POINTS} points and this one has "
                f"{len(self.points)} (AC-F8). Fewer points does not demonstrate that "
                "the merge weight was chosen rather than assumed."
            )
            raise SweepError(msg)
        labels = [point.label for point in self.points]
        duplicates = sorted({label for label in labels if labels.count(label) > 1})
        if duplicates:
            msg = (
                f"the sweep repeats the point(s) {', '.join(duplicates)}. A repeated "
                "point is a wasted merge and makes the comparison matrix ambiguous."
            )
            raise SweepError(msg)

    @property
    def size(self) -> int:
        """How many points the sweep holds."""
        return len(self.points)

    @property
    def evaluated(self) -> tuple[MergePoint, ...]:
        """Points that have gate results."""
        return tuple(point for point in self.points if point.evaluated)

    @property
    def passing(self) -> tuple[MergePoint, ...]:
        """Points whose gates passed. The only points that may be selected."""
        return tuple(point for point in self.points if point.passed)

    @property
    def complete(self) -> bool:
        """Whether every point has been built and evaluated."""
        return len(self.evaluated) == self.size

    def point_for(self, parameters: Mapping[str, float]) -> MergePoint:
        """One point by its parameters."""
        wanted = dict(sorted(parameters.items()))
        for point in self.points:
            if dict(sorted(point.parameters.items())) == wanted:
                return point
        msg = f"no point {wanted} is in this sweep; it holds {[p.label for p in self.points]}"
        raise SweepError(msg)

    def with_result(
        self, parameters: Mapping[str, float], *, artefact_sha256: str, evidence: Evidence
    ) -> Sweep:
        """Return the sweep with one point's artefact and results recorded."""
        if not evidence.binds(artefact_sha256):
            msg = (
                f"the evidence offered for point {parameters} was measured on "
                f"{evidence.artefact_sha256[:12]} and the artefact is "
                f"{artefact_sha256[:12]}. Gate results bind to the bytes (AC-S8)."
            )
            raise SweepError(msg)
        target = self.point_for(parameters)
        updated = replace(target, artefact_sha256=artefact_sha256, evidence=evidence)
        return replace(
            self,
            points=tuple(
                updated if point.label == target.label else point for point in self.points
            ),
        )

    # -- the comparison AC-F8 asks for --------------------------------------

    def gates(self) -> tuple[str, ...]:
        """Every gate any point was measured on, in order."""
        seen: list[str] = []
        for point in self.points:
            if point.evidence is None:
                continue
            for gate in point.evidence.measurements:
                if gate not in seen:
                    seen.append(gate)
        return tuple(sorted(seen))

    def matrix(self) -> dict[str, Any]:
        """Every point against every gate, in one structure. AC-F8.

        One object rather than a query, so that what the console renders and
        what the model card records cannot differ. An unevaluated point appears
        with a null rather than being dropped: a comparison that silently omits
        the points that failed to build is a comparison of the survivors.
        """
        gates = self.gates()
        return {
            "method": self.method,
            "baseSha256": self.base_sha256,
            "adapterSha256": self.adapter_sha256,
            "size": self.size,
            "complete": self.complete,
            "gates": list(gates),
            "points": [
                {
                    **point.as_payload(),
                    "row": {gate: point.score(gate) for gate in gates},
                }
                for point in self.points
            ],
            "selected": dict(self.selected) if self.selected else None,
            "selectionCriterion": self.selection_criterion,
            "notes": list(self.notes),
        }

    def ranked(self, criterion: str) -> tuple[MergePoint, ...]:
        """Passing points, best first on one gate.

        Only passing points, because ranking includes points that failed their
        gates would put a number next to a model that may not be released and
        invite somebody to read the ordering as a recommendation.
        """
        candidates = [point for point in self.passing if point.score(criterion) is not None]
        return tuple(
            sorted(candidates, key=lambda point: (-(point.score(criterion) or 0.0), point.label))
        )

    def select(self, parameters: Mapping[str, float], *, criterion: str = "") -> Sweep:
        """Record the chosen point. Refuses a point whose gates did not pass.

        BRISINGAMEN does not decide whether a merge is acceptable -- RAUN does
        (SAD 5.2) -- so this reads the evidence rather than forming a view.
        """
        point = self.point_for(parameters)
        if point.evidence is None:
            msg = (
                f"point {point.label} has not been evaluated, so there is nothing "
                "to choose it on. A point is chosen from its gate results, never "
                "from its parameters."
            )
            raise SweepError(msg)
        if not point.passed:
            failing = ", ".join(point.evidence.failing)
            msg = (
                f"point {point.label} failed gate(s) {failing} and cannot be selected. "
                "BRISINGAMEN runs the sweep; RAUN decides whether a merge is "
                "acceptable (SAD 5.2)."
            )
            raise SweepError(msg)
        return replace(self, selected=dict(point.parameters), selection_criterion=criterion)

    @property
    def selected_point(self) -> MergePoint | None:
        """The chosen point, or `None` if none has been chosen."""
        if self.selected is None:
            return None
        return self.point_for(self.selected)

    def for_model_card(self) -> dict[str, Any]:
        """What the model card records about the sweep.

        The whole matrix, not only the winner. A card that reports the selected
        weight alone says a number was chosen and not that four others were
        rejected, which is the part that makes the choice evidence.
        """
        chosen = self.selected_point
        return {
            "method": self.method,
            "points": self.size,
            "criterion": self.selection_criterion or None,
            "selected": {
                "parameters": dict(chosen.parameters),
                "label": chosen.label,
                "configHash": chosen.config_hash(),
                "artefactSha256": chosen.artefact_sha256,
            }
            if chosen
            else None,
            "comparison": self.matrix()["points"],
        }


def build(
    *,
    method: str,
    base_sha256: str,
    adapter_sha256: str,
    parameters: Sequence[Mapping[str, float]],
    notes: Iterable[str] = (),
) -> Sweep:
    """Build a sweep over the given merge configurations."""
    return Sweep(
        method=method,
        base_sha256=base_sha256,
        adapter_sha256=adapter_sha256,
        points=tuple(MergePoint(parameters=dict(item)) for item in parameters),
        notes=tuple(notes),
    )


def linear(
    *,
    method: str,
    base_sha256: str,
    adapter_sha256: str,
    parameter: str = "weight",
    start: float = 0.2,
    stop: float = 1.0,
    points: int = MINIMUM_POINTS,
) -> Sweep:
    """A sweep of evenly spaced values for one parameter.

    The common case, and the one AC-F8 describes. Endpoints included, so the
    sweep actually reaches the values a reader would expect it to.
    """
    if points < MINIMUM_POINTS:
        msg = f"a sweep has at least {MINIMUM_POINTS} points (AC-F8); {points} was asked for"
        raise SweepError(msg)
    step = (stop - start) / (points - 1)
    return build(
        method=method,
        base_sha256=base_sha256,
        adapter_sha256=adapter_sha256,
        parameters=[{parameter: round(start + step * index, 6)} for index in range(points)],
    )
