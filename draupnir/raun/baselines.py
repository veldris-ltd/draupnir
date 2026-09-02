"""Baseline management: what a relative gate is measured against.

Four of the six gates are relative -- E1, E2, E3 and E5 compare against a
baseline -- so a baseline is not reference data, it is half of every gate
result. SAD 5.2 gives RAUN "baseline management" and forbids it from changing
any artefact, which is why a baseline here is a recorded measurement rather
than something re-derived on demand.

Two properties do the work.

A baseline is identified by the hash of the artefact it was measured on, for
the same reason gate evidence is (AC-S8). "The base model's score" is not a
fact until you say which bytes of base model, and a substrate rebuilt with a
different seed is a different baseline wearing the same name.

A baseline is captured once and never recomputed. `GateOutcome` records the
baseline value alongside the measurement (SAD 7.1), so a result stays
explicable after the baseline moves on; re-deriving would quietly rewrite the
margin of every historical result the next time the numbers shifted.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from draupnir.core.domain.evidence import SHA256


class BaselineError(Exception):
    """Raised when a baseline cannot be captured or resolved."""


class NoBaselineError(BaselineError):
    """Raised when a relative gate has nothing to compare against.

    Not a pass. `Gate.holds` already refuses a relative comparison with no
    baseline, and this is the same refusal earlier, where the message can name
    what was looked for.
    """

    def __init__(self, suite: str, artefact_kind: str, jurisdiction: str | None) -> None:
        """Name the baseline that was wanted."""
        self.suite = suite
        self.artefact_kind = artefact_kind
        self.jurisdiction = jurisdiction
        where = f" for {jurisdiction}" if jurisdiction else ""
        super().__init__(
            f"no baseline is registered for suite {suite!r} against a "
            f"{artefact_kind}{where}. Four of the six gates are relative, and a "
            "relative gate with no baseline is not a pass, it is an unknown. "
            "Capture the base model's measurements before evaluating against them."
        )


@dataclass(frozen=True, slots=True)
class Baseline:
    """Measurements taken on one artefact, kept for others to be judged against."""

    #: The bytes these numbers describe.
    artefact_sha256: str
    #: What the artefact is, in the vocabulary of SAD 7.1.
    artefact_kind: str
    suite: str
    suite_version: str
    measurements: Mapping[str, float]
    captured_at: datetime
    #: Set where the suite is jurisdiction specific. `None` is the general suite.
    jurisdiction: str | None = None
    #: A human-readable name for the console. Never used to resolve a baseline;
    #: two substrates can share a label and cannot share a hash.
    label: str = ""

    def __post_init__(self) -> None:
        """Refuse a baseline that cannot be trusted or attributed."""
        if not SHA256.match(self.artefact_sha256):
            msg = f"{self.artefact_sha256!r} is not a SHA-256; a baseline names its bytes"
            raise BaselineError(msg)
        if self.captured_at.tzinfo is None:
            msg = "baseline timestamps carry an explicit offset (SAD 11E.2)"
            raise BaselineError(msg)
        if not self.measurements:
            msg = (
                "a baseline with no measurements is not a baseline. Every relative "
                "gate compared against it would fail for want of a value, which "
                "looks like a bad model rather than a missing baseline."
            )
            raise BaselineError(msg)

    @property
    def key(self) -> tuple[str, str, str | None]:
        """How a baseline is looked up: suite, artefact kind, jurisdiction."""
        return (self.suite, self.artefact_kind, self.jurisdiction)

    def value_for(self, gate: str) -> float | None:
        """The baseline value for one gate, or `None` if it was not measured."""
        return self.measurements.get(gate)

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, for the release package and the console."""
        return {
            "artefactSha256": self.artefact_sha256,
            "artefactKind": self.artefact_kind,
            "suite": self.suite,
            "suiteVersion": self.suite_version,
            "jurisdiction": self.jurisdiction,
            "label": self.label,
            "capturedAt": self.captured_at.isoformat(),
            "measurements": dict(sorted(self.measurements.items())),
        }


@dataclass
class BaselineRegistry:
    """Every captured baseline, resolvable by what a run is being judged as."""

    _baselines: dict[tuple[str, str, str | None], Baseline] = field(default_factory=dict)

    def __len__(self) -> int:
        """How many baselines are held."""
        return len(self._baselines)

    def __iter__(self) -> Iterator[Baseline]:
        """Every baseline, in capture order."""
        return iter(self._baselines.values())

    def capture(self, baseline: Baseline, *, replace_existing: bool = False) -> Baseline:
        """Record a baseline. Refuses to overwrite silently.

        Overwriting is how a regression disappears: re-capture the baseline
        from the model that regressed and every subsequent comparison agrees
        with it. So replacing one is explicit, and `moved_from` records what it
        displaced.
        """
        existing = self._baselines.get(baseline.key)
        if existing is not None and not replace_existing:
            if existing.artefact_sha256 == baseline.artefact_sha256:
                return existing
            msg = (
                f"a baseline for {baseline.key} already exists, measured on "
                f"{existing.artefact_sha256[:12]}. Replacing it silently is how a "
                "regression vanishes: re-baseline against the model that regressed "
                "and every later comparison agrees with it. Pass replace_existing "
                "if that is genuinely what is intended."
            )
            raise BaselineError(msg)
        self._baselines[baseline.key] = baseline
        return baseline

    def resolve(self, suite: str, artefact_kind: str, jurisdiction: str | None = None) -> Baseline:
        """Return the baseline for this suite and artefact, or raise.

        Falls back from the jurisdiction-specific baseline to the general one,
        because the general suite is measured once per substrate and shared.
        """
        for key in ((suite, artefact_kind, jurisdiction), (suite, artefact_kind, None)):
            found = self._baselines.get(key)
            if found is not None:
                return found
        raise NoBaselineError(suite, artefact_kind, jurisdiction)

    def find(
        self, suite: str, artefact_kind: str, jurisdiction: str | None = None
    ) -> Baseline | None:
        """The baseline if one exists, without raising."""
        try:
            return self.resolve(suite, artefact_kind, jurisdiction)
        except NoBaselineError:
            return None

    def values_for(
        self, suite: str, artefact_kind: str, jurisdiction: str | None = None
    ) -> dict[str, float]:
        """The per-gate baseline values a suite evaluation needs."""
        return dict(self.resolve(suite, artefact_kind, jurisdiction).measurements)

    def as_payload(self) -> dict[str, Any]:
        """Every baseline, for the release package."""
        return {
            "baselines": [
                item.as_payload() for item in sorted(self._baselines.values(), key=lambda b: b.key)
            ]
        }


def capture(
    *,
    artefact_sha256: str,
    artefact_kind: str,
    suite: str,
    suite_version: str,
    measurements: Mapping[str, float],
    captured_at: datetime,
    jurisdiction: str | None = None,
    label: str = "",
) -> Baseline:
    """Build a baseline from a measured artefact."""
    return Baseline(
        artefact_sha256=artefact_sha256,
        artefact_kind=artefact_kind,
        suite=suite,
        suite_version=suite_version,
        measurements=dict(measurements),
        captured_at=captured_at,
        jurisdiction=jurisdiction,
        label=label,
    )


def registry_of(baselines: Iterable[Baseline]) -> BaselineRegistry:
    """A registry holding these baselines."""
    registry = BaselineRegistry()
    for item in baselines:
        registry.capture(item)
    return registry
