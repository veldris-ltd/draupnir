"""Regression detection: noticing that this release is worse than the last one.

Gates answer "is this good enough". Regression answers "is this worse than what
we already shipped", and they are not the same question. A model can clear
every gate against the base model and still be measurably worse than the
release it replaces, because the gates compare against the substrate and the
customer compares against what they had yesterday.

SAD 5.2 gives RAUN "regression detection" as a separate responsibility from
gate execution for that reason. A regression does not fail a gate here -- it is
reported, and GLEIPNIR decides. RAUN never blocks a release on its own
judgement, because RAUN is not the module that judges (Decision S4).

The comparison is against the previous *released* artefact for the same
jurisdiction, not against the previous run. A run that was quarantined is not a
thing anyone is using, and comparing against it would report a regression from
a model that never shipped.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from draupnir.core.domain.evidence import Evidence

#: How far a measurement may fall before it is called a regression. Separate
#: from any gate margin: a gate says what is acceptable, this says what is
#: worth telling somebody about, and the second is the tighter number.
DEFAULT_TOLERANCE = 0.005


class RegressionError(Exception):
    """Raised when a comparison cannot be made."""


@dataclass(frozen=True, slots=True)
class Movement:
    """One measurement, then and now."""

    gate: str
    previous: float
    current: float
    tolerance: float

    @property
    def delta(self) -> float:
        """How much it moved. Negative is worse."""
        return round(self.current - self.previous, 6)

    @property
    def regressed(self) -> bool:
        """Whether it fell further than the tolerance allows."""
        return self.delta < -self.tolerance

    @property
    def improved(self) -> bool:
        """Whether it rose further than the tolerance allows."""
        return self.delta > self.tolerance

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, for the console's trend view."""
        return {
            "gate": self.gate,
            "previous": self.previous,
            "current": self.current,
            "delta": self.delta,
            "regressed": self.regressed,
            "improved": self.improved,
        }


@dataclass(frozen=True, slots=True)
class Comparison:
    """This artefact against the one it would replace."""

    #: The artefact being considered.
    current_sha256: str
    #: The released artefact it is compared against, by hash.
    previous_sha256: str
    movements: tuple[Movement, ...]
    compared_at: datetime
    jurisdiction: str | None = None
    #: Set when there is nothing to compare against, which is not a regression.
    first_release: bool = False

    @property
    def regressions(self) -> tuple[Movement, ...]:
        """Every measurement that fell beyond tolerance."""
        return tuple(item for item in self.movements if item.regressed)

    @property
    def improvements(self) -> tuple[Movement, ...]:
        """Every measurement that rose beyond tolerance."""
        return tuple(item for item in self.movements if item.improved)

    @property
    def regressed(self) -> bool:
        """Whether anything got worse."""
        return bool(self.regressions)

    def describe(self) -> str:
        """What to put in front of an approver."""
        if self.first_release:
            return "no previous release for this jurisdiction; nothing to compare against"
        if not self.regressed:
            improved = len(self.improvements)
            return f"no regression against {self.previous_sha256[:12]}" + (
                f"; {improved} measurement(s) improved" if improved else ""
            )
        parts = [
            f"{item.gate} {item.previous:.4f} to {item.current:.4f} ({item.delta:+.4f})"
            for item in self.regressions
        ]
        return f"regression against the released {self.previous_sha256[:12]}: " + "; ".join(parts)

    def as_payload(self) -> dict[str, Any]:
        """The ledger and release-package shape."""
        return {
            "currentSha256": self.current_sha256,
            "previousSha256": self.previous_sha256 or None,
            "jurisdiction": self.jurisdiction,
            "comparedAt": self.compared_at.isoformat(),
            "firstRelease": self.first_release,
            "regressed": self.regressed,
            "movements": [item.as_payload() for item in self.movements],
            "summary": self.describe(),
        }


def compare(
    current: Evidence,
    previous: Evidence | None,
    *,
    compared_at: datetime,
    tolerance: float = DEFAULT_TOLERANCE,
    jurisdiction: str | None = None,
) -> Comparison:
    """Compare an artefact against the release it would replace.

    A gate measured now but not then is not a regression: it is a gate that did
    not exist at the previous release, and reporting it as a fall from zero
    would be a lie about a model nobody has run.
    """
    if previous is None:
        return Comparison(
            current_sha256=current.artefact_sha256,
            previous_sha256="",
            movements=(),
            compared_at=compared_at,
            jurisdiction=jurisdiction,
            first_release=True,
        )

    shared = sorted(set(current.measurements) & set(previous.measurements))
    movements = tuple(
        Movement(
            gate=gate,
            previous=previous.measurements[gate],
            current=current.measurements[gate],
            tolerance=tolerance,
        )
        for gate in shared
    )
    return Comparison(
        current_sha256=current.artefact_sha256,
        previous_sha256=previous.artefact_sha256,
        movements=movements,
        compared_at=compared_at,
        jurisdiction=jurisdiction,
    )


def latest_released(history: Sequence[Evidence]) -> Evidence | None:
    """The most recent evidence in a release history, or `None` if empty.

    The caller supplies only released artefacts. RAUN does not know which runs
    were released -- that is the ledger's -- and inferring it here would be
    RAUN deciding what counts as a release.
    """
    if not history:
        return None
    return max(history, key=lambda item: item.evaluated_at)


def trend(history: Sequence[Evidence], gate: str) -> tuple[tuple[str, float], ...]:
    """One gate's measurement across a release history, oldest first.

    SAD 8.3 asks for a "per jurisdiction trend" of gate pass rates and margins;
    this is the series behind it.
    """
    ordered = sorted(history, key=lambda item: item.evaluated_at)
    return tuple(
        (item.evaluated_at.isoformat(), item.measurements[gate])
        for item in ordered
        if gate in item.measurements
    )


def summarise(comparisons: Mapping[str, Comparison]) -> dict[str, Any]:
    """Every jurisdiction's comparison, for the fleet view."""
    return {
        "regressed": sorted(name for name, item in comparisons.items() if item.regressed),
        "comparisons": {name: item.as_payload() for name, item in sorted(comparisons.items())},
    }
