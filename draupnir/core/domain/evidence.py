"""Gate results bound to the bytes they were measured on, never to a path.

Threat T8 of SAD 12.1: "Artefact tampered between passing a gate and being
released". The mitigation is one sentence and this module is that sentence made
structural: "Gate results bind to the artefact hash, not its path. Publish
re-verifies the hash and refuses on mismatch" (AC-S8).

A path is a name for wherever the bytes happen to be now. Binding evidence to
`s3://andvari/cim-gbr/merged/` records that *something at that address* passed,
which stays true after the bytes are replaced. Binding to the SHA-256 records
that *those bytes* passed, which cannot become true of any other bytes.

`Evidence` carries no path, no URI and no location of any kind, and
`test_evidence_carries_no_location` walks its fields and fails if one is added.
That is deliberate rather than fastidious: the way this control decays is
somebody adding `artefact_uri` for a console's convenience, and the next person
resolving the URI instead of the hash.

It lives in the core rather than in RAUN because every module downstream of
evaluation depends on it -- BRISINGAMEN's sweep, SKIDBLADNIR's publish -- and
the modules are independent siblings that cannot import each other. Evidence
defined in one of them would mean evidence redefined in the others, and a
second definition of "which bytes passed" is the whole of threat T8.

It records a verdict rather than reaching one. Whether a suite result passes is
GLEIPNIR's judgement (Decision S4); what this holds is that judgement, attached
to the artefact it was made about.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from draupnir.interfaces.types import GateOutcome

#: A lowercase hex SHA-256. Checked on construction, because evidence bound to
#: a malformed hash binds to nothing and would fail open at publish.
SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: The artefact kinds of SAD 7.1 that carry gate evidence.
EVALUABLE_KINDS: frozenset[str] = frozenset({"substrate", "adapter", "merged", "quantised"})


class EvidenceError(Exception):
    """Raised when evidence cannot be recorded or trusted."""


class ArtefactMismatchError(EvidenceError):
    """Raised when the artefact being published is not the one that was gated.

    The exception AC-S8 asks for. It names both hashes, because an operator
    seeing this needs to know whether they are looking at a tampered artefact
    or at a rebuild that legitimately produced different bytes, and those have
    very different responses.
    """

    def __init__(self, expected: str, observed: str, artefact_kind: str = "artefact") -> None:
        """Name what was gated and what is in front of us."""
        self.expected = expected
        self.observed = observed
        self.artefact_kind = artefact_kind
        super().__init__(
            f"the {artefact_kind} being published is not the one the gates passed. "
            f"Gated: {expected}. Present: {observed}. Publication is refused. Gate "
            "results bind to the bytes, not to where they are kept (AC-S8), so this "
            "artefact has either been modified since evaluation or is a different "
            "build that has never been evaluated. Re-gate it."
        )


class UngatedArtefactError(EvidenceError):
    """Raised when an artefact reaches publication with no evidence at all.

    Distinct from a mismatch on purpose. A mismatch means something changed; an
    absence means a stage was skipped, and "there is no path from quantisation
    to approval that skips evaluation" is a different failure to investigate.
    """

    def __init__(self, artefact_kind: str, sha256: str) -> None:
        """Name what arrived unevaluated."""
        self.artefact_kind = artefact_kind
        self.sha256 = sha256
        super().__init__(
            f"no gate evidence exists for the {artefact_kind} {sha256[:12]}. Every "
            "artefact is evaluated before it can be approved, so an artefact with no "
            "evidence has not been evaluated -- it has bypassed evaluation."
        )


@dataclass(frozen=True, slots=True)
class Evidence:
    """One suite execution, bound to the artefact it measured.

    Carries no path, URI, bucket or filename. See the module docstring.
    """

    #: The bytes this evidence is about. The binding, and the only one.
    artefact_sha256: str
    #: `substrate`, `adapter`, `merged` or `quantised`, per SAD 7.1.
    artefact_kind: str
    #: Per gate result and margin, as SAD 6.1 requires the ledger to record.
    outcomes: tuple[GateOutcome, ...]
    #: The verdict reached on those outcomes. Recorded, not computed: whether a
    #: failing gate blocks is GLEIPNIR's to say (Decision S4).
    passed: bool
    suite: str
    suite_version: str
    evaluated_at: datetime
    #: Which baseline the relative gates were measured against, by its hash.
    baseline_sha256: str | None = None
    #: For a quantised artefact, the format it was built in.
    format: str | None = None
    #: Raw measurements, kept so a later suite version can re-judge the same
    #: numbers without re-running an evaluation that costs an allocation.
    measurements: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse evidence that binds to nothing or describes nothing."""
        if not SHA256.match(self.artefact_sha256):
            msg = (
                f"{self.artefact_sha256!r} is not a SHA-256. Evidence bound to a "
                "malformed hash binds to nothing, and would let any artefact through "
                "at publish."
            )
            raise EvidenceError(msg)
        if self.baseline_sha256 is not None and not SHA256.match(self.baseline_sha256):
            msg = f"{self.baseline_sha256!r} is not a SHA-256"
            raise EvidenceError(msg)
        if self.artefact_kind not in EVALUABLE_KINDS:
            msg = (
                f"{self.artefact_kind!r} is not an evaluable artefact kind; expected "
                f"one of {', '.join(sorted(EVALUABLE_KINDS))} (SAD 7.1)"
            )
            raise EvidenceError(msg)
        if self.evaluated_at.tzinfo is None:
            msg = "evidence timestamps carry an explicit offset (SAD 11E.2)"
            raise EvidenceError(msg)

    @property
    def failing(self) -> tuple[str, ...]:
        """Gates that did not pass."""
        return tuple(outcome.gate for outcome in self.outcomes if not outcome.passed)

    def binds(self, sha256: str) -> bool:
        """Whether this evidence is about exactly these bytes."""
        return self.artefact_sha256 == sha256

    def verify(self, observed_sha256: str) -> None:
        """Raise unless `observed_sha256` is what was gated. AC-S8."""
        if not self.binds(observed_sha256):
            raise ArtefactMismatchError(self.artefact_sha256, observed_sha256, self.artefact_kind)

    def score(self, gate: str) -> float | None:
        """The measurement for one gate, if it was measured."""
        return self.measurements.get(gate)

    def as_payload(self) -> dict[str, Any]:
        """The ledger and release-package shape."""
        return {
            "artefactSha256": self.artefact_sha256,
            "artefactKind": self.artefact_kind,
            "format": self.format,
            "suite": self.suite,
            "suiteVersion": self.suite_version,
            "baselineSha256": self.baseline_sha256,
            "evaluatedAt": self.evaluated_at.isoformat(),
            "passed": self.passed,
            "failing": list(self.failing),
            "gates": {
                outcome.gate: {
                    "value": outcome.value,
                    "baseline": outcome.baseline_value,
                    "margin": outcome.margin,
                    "passed": outcome.passed,
                }
                for outcome in self.outcomes
            },
        }


@dataclass(frozen=True, slots=True)
class EvidenceLog:
    """Everything known about the artefacts of one release, indexed by hash.

    Indexed by hash rather than by kind, because the question publish asks is
    "what do we know about these bytes". A lookup by kind answers a different
    question convincingly.
    """

    entries: tuple[Evidence, ...] = ()

    def with_evidence(self, evidence: Evidence) -> EvidenceLog:
        """Return the log with one more result, replacing any for the same bytes.

        Re-evaluating the same artefact under a newer suite replaces the older
        result rather than accumulating, so "what do we know about these bytes"
        keeps having one answer.
        """
        others = tuple(
            item for item in self.entries if item.artefact_sha256 != evidence.artefact_sha256
        )
        return EvidenceLog(entries=(*others, evidence))

    def for_artefact(self, sha256: str) -> Evidence | None:
        """The evidence about these bytes, if any exists."""
        for item in self.entries:
            if item.binds(sha256):
                return item
        return None

    def require(self, sha256: str, kind: str = "artefact") -> Evidence:
        """The evidence about these bytes, or raise. Never returns a near miss."""
        found = self.for_artefact(sha256)
        if found is None:
            raise UngatedArtefactError(kind, sha256)
        return found

    def of_kind(self, kind: str) -> tuple[Evidence, ...]:
        """Every result for one artefact kind."""
        return tuple(item for item in self.entries if item.artefact_kind == kind)

    @property
    def failing(self) -> tuple[Evidence, ...]:
        """Every artefact that did not clear its gates."""
        return tuple(item for item in self.entries if not item.passed)

    def as_payload(self) -> dict[str, Any]:
        """The release-package shape, ordered by hash so it is diffable."""
        return {
            "evidence": [
                item.as_payload()
                for item in sorted(self.entries, key=lambda entry: entry.artefact_sha256)
            ]
        }
