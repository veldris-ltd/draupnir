"""Suites: which evaluation runs against which artefact, and at what version.

The TRAINED to EVALUATING guard of SAD 6.1 is "RAUN suite resolves for the
artefact type", which is a guard that can fail: an artefact kind nobody
registered a suite for does not get evaluated by the nearest available suite,
it stops. A suite chosen by approximation produces gate results that look
exactly like gate results and mean something else.

Suites are versioned because a gate result is only explicable alongside the
suite that produced it. SAD 7.1 records `suite_version` on every `gate_result`
for that reason, and a suite is immutable once results exist against it:
changing a task set under a fixed version rewrites the meaning of every
historical result without changing a single stored number.

What a suite *measures* is the driver's business (`draupnir.eval`, an
lm-evaluation-harness implementation). What a measurement *means* is
GLEIPNIR's, and arrives through the `Judge` seam. What a suite is called, what
it applies to and which gates it feeds is here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from draupnir.core.domain.evidence import EVALUABLE_KINDS, Evidence
from draupnir.raun.baselines import BaselineRegistry
from draupnir.raun.judging import Judge


class SuiteError(Exception):
    """Raised when a suite cannot be resolved or executed."""


class NoSuiteError(SuiteError):
    """Raised when nothing is registered for an artefact kind.

    The TRAINED to EVALUATING guard failing. Deliberately not a fallback to the
    general suite: an artefact evaluated by the wrong suite produces numbers
    that pass gates designed for something else.
    """

    def __init__(self, artefact_kind: str, known: Iterable[str]) -> None:
        """Name what was wanted and what is registered."""
        self.artefact_kind = artefact_kind
        self.known = tuple(sorted(known))
        super().__init__(
            f"no evaluation suite is registered for a {artefact_kind!r}. Registered "
            f"for: {', '.join(self.known) or 'nothing'}. The run stops here rather "
            "than being evaluated by the nearest available suite, which would "
            "produce numbers that look like gate results and mean something else."
        )


@dataclass(frozen=True, slots=True)
class Suite:
    """One evaluation suite: what it applies to, and which gates it feeds."""

    name: str
    version: str
    #: Artefact kinds this suite evaluates.
    applies_to: frozenset[str]
    #: The gate identifiers this suite produces measurements for. Identifiers
    #: only: a threshold here would be a threshold in two places.
    gates: tuple[str, ...]
    #: The evaluation driver that runs it, by versioned entry point name.
    driver: str = "raun.lmeval/v1"
    #: Set where the suite is specific to one jurisdiction.
    jurisdiction: str | None = None
    #: The tasks the driver resolves. Opaque here on purpose.
    tasks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse a suite that cannot produce a judgeable result."""
        unknown = tuple(sorted(set(self.applies_to) - EVALUABLE_KINDS))
        if unknown:
            msg = (
                f"suite {self.name} claims to evaluate {', '.join(unknown)}, which is "
                f"not an evaluable artefact kind. Evaluable: "
                f"{', '.join(sorted(EVALUABLE_KINDS))}"
            )
            raise SuiteError(msg)
        if not self.gates:
            msg = f"suite {self.name} feeds no gates, so running it decides nothing"
            raise SuiteError(msg)

    @property
    def key(self) -> str:
        """`name/version`, which is what a gate result records."""
        return f"{self.name}/{self.version}"

    def covers(self, artefact_kind: str) -> bool:
        """Whether this suite evaluates that kind of artefact."""
        return artefact_kind in self.applies_to

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, for the console and the release package."""
        return {
            "name": self.name,
            "version": self.version,
            "appliesTo": sorted(self.applies_to),
            "gates": list(self.gates),
            "driver": self.driver,
            "jurisdiction": self.jurisdiction,
            "tasks": list(self.tasks),
        }


#: The general suite of SAD 6.2: gates E1 to E6 against anything releasable.
GENERAL = Suite(
    name="general-core",
    version="2026.01",
    applies_to=frozenset(EVALUABLE_KINDS),
    gates=("E1", "E2", "E3", "E4", "E5", "E6"),
    tasks=("mmlu", "arc_challenge", "hellaswag", "truthfulqa", "ifeval"),
)


@dataclass
class SuiteRegistry:
    """Every registered suite, resolvable by artefact kind and jurisdiction."""

    _suites: dict[str, Suite] = field(default_factory=dict)

    def __len__(self) -> int:
        """How many suites are registered."""
        return len(self._suites)

    def register(self, suite: Suite) -> Suite:
        """Add a suite. A version already registered is immutable.

        SAD 10.2 makes a jurisdiction suite a configuration change rather than
        a core change, so this is the entry point the `raun-suite` skill uses.
        """
        existing = self._suites.get(suite.key)
        if existing is not None and existing != suite:
            msg = (
                f"suite {suite.key} is already registered with a different definition. "
                "A suite is immutable once results exist against it: changing the task "
                "set under a fixed version rewrites the meaning of every historical "
                "result without changing a stored number. Issue a new version."
            )
            raise SuiteError(msg)
        self._suites[suite.key] = suite
        return suite

    def resolve(self, artefact_kind: str, jurisdiction: str | None = None) -> tuple[Suite, ...]:
        """Every suite that applies, general first then jurisdiction specific.

        Returns a tuple because a jurisdiction model is judged by both the
        general suite and its own: SAD 6.2's example names `general-core` and
        `cim-gbr` together.
        """
        chosen = [
            item
            for item in self._suites.values()
            if item.covers(artefact_kind)
            and (item.jurisdiction is None or item.jurisdiction == jurisdiction)
        ]
        if not chosen:
            raise NoSuiteError(
                artefact_kind,
                {kind for item in self._suites.values() for kind in item.applies_to},
            )
        chosen.sort(key=lambda item: (item.jurisdiction is not None, item.name))
        return tuple(chosen)

    def get(self, key: str) -> Suite:
        """A suite by `name/version`."""
        try:
            return self._suites[key]
        except KeyError as error:
            known = ", ".join(sorted(self._suites))
            msg = f"no suite {key!r} is registered; known: {known or 'none'}"
            raise SuiteError(msg) from error

    def as_payload(self) -> dict[str, Any]:
        """Every suite, for the release package."""
        return {
            "suites": [
                item.as_payload() for item in sorted(self._suites.values(), key=lambda s: s.key)
            ]
        }


def default_registry() -> SuiteRegistry:
    """A registry holding the general suite and nothing else."""
    registry = SuiteRegistry()
    registry.register(GENERAL)
    return registry


def evaluate(
    *,
    suite: Suite,
    judge: Judge,
    artefact_sha256: str,
    artefact_kind: str,
    measurements: Mapping[str, float],
    baselines: BaselineRegistry,
    evaluated_at: datetime,
    jurisdiction: str | None = None,
    export_format: str | None = None,
) -> Evidence:
    """Judge measurements and bind the verdict to the artefact that produced them.

    The one place a suite result becomes evidence. Everything downstream -- the
    sweep comparison, the quantisation re-gate, publication -- consumes
    `Evidence`, so nothing downstream can hold a gate result that is not
    attached to a hash.
    """
    if not suite.covers(artefact_kind):
        msg = (
            f"suite {suite.key} does not evaluate a {artefact_kind}; it applies to "
            f"{', '.join(sorted(suite.applies_to))}"
        )
        raise SuiteError(msg)

    baseline = baselines.find(suite.name, artefact_kind, jurisdiction)
    if baseline is None:
        # A substrate baseline serves a merged or quantised derivative: what E1
        # asks is whether capability regressed against the model this came from.
        baseline = baselines.find(suite.name, "substrate", jurisdiction)

    judgement = judge(
        measurements,
        baseline.measurements if baseline else {},
        suite_version=suite.version,
        gate_ids=suite.gates,
    )

    return Evidence(
        artefact_sha256=artefact_sha256,
        artefact_kind=artefact_kind,
        outcomes=tuple(judgement.outcomes),
        passed=judgement.passed,
        suite=suite.name,
        suite_version=judgement.suite_version,
        evaluated_at=evaluated_at,
        baseline_sha256=baseline.artefact_sha256 if baseline else None,
        format=export_format,
        measurements=dict(measurements),
    )
