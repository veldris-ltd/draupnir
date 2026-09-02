"""The model card, rendered from recorded facts and from nothing else.

The requirement: "The model card is rendered from recorded facts only. If a
fact is absent, the card says so; it never omits the field silently."

Every value on this card is a `Fact` (see `facts`), which is either recorded
with a source or absent with a reason. There is no path by which a missing
value becomes a missing row: the sections are built from a fixed list of field
names, and a name the record cannot fill renders as "not recorded" with an
explanation.

Two things the card must carry that are easy to leave out.

The sole approver exception of constraint C-11. SAD 9.4 and AC-S15 both require
it to be visible in the model card, not only in the lineage. Where the approver
also submitted the run, the card says so in the provenance section.

The whole sweep, not only the selected point. A card reporting the chosen merge
weight says a number was picked; a card reporting all five says four others
were rejected on gate results, which is the part that makes the choice
evidence rather than assertion.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from draupnir.skidbladnir.facts import Fact, FactSet, absences

SCHEMA = "draupnir/model-card/v1"

#: What the identity section must state. A fixed list, because building the
#: section from whatever keys happened to arrive is how a field goes missing
#: without anybody noticing.
IDENTITY_FIELDS: tuple[str, ...] = (
    "model",
    "jurisdiction",
    "tier",
    "version",
    "artefactSha256",
    "route",
    "formats",
)

PROVENANCE_FIELDS: tuple[str, ...] = (
    "baseModel",
    "baseModelLicence",
    "runId",
    "specificationHash",
    "trainedAt",
    "approver",
    "approvedAt",
    "soleApproverException",
)

EVALUATION_FIELDS: tuple[str, ...] = (
    "suite",
    "suiteVersion",
    "baselineSha256",
    "gatesPassed",
    "regression",
)

COMPLIANCE_FIELDS: tuple[str, ...] = (
    "trainingContentSummary",
    "copyrightPolicy",
    "copyrightPolicyVersion",
    "downstreamAnnex",
    "personalDataPresent",
    "attributionObligations",
)


class ModelCardError(Exception):
    """Raised when a card cannot be rendered."""


@dataclass(frozen=True, slots=True)
class ModelCard:
    """One released model, described entirely from the record."""

    model: str
    generated_at: datetime
    identity: FactSet
    provenance: FactSet
    evaluation: FactSet
    compliance: FactSet
    #: The whole sweep comparison, from `Sweep.for_model_card()`.
    sweep: Mapping[str, Any] | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def sections(self) -> tuple[FactSet, ...]:
        """Every section, in render order."""
        return (self.identity, self.provenance, self.evaluation, self.compliance)

    @property
    def unrecorded(self) -> tuple[str, ...]:
        """Every fact the record could not supply, as `section.fact`."""
        return absences(self.sections)

    @property
    def complete(self) -> bool:
        """Whether every expected fact was recorded.

        An incomplete card is publishable. It says what it does not know, which
        is the requirement; refusing to publish would push somebody towards
        writing the missing value in by hand, which is what Decision S11 exists
        to prevent.
        """
        return not self.unrecorded

    def as_payload(self) -> dict[str, Any]:
        """The machine-readable card."""
        return {
            "schema": SCHEMA,
            "model": self.model,
            "generatedAt": self.generated_at.isoformat(),
            "complete": self.complete,
            "notRecorded": list(self.unrecorded),
            "sections": {item.section: item.as_payload() for item in self.sections},
            "sweep": dict(self.sweep) if self.sweep else None,
            "notes": list(self.notes),
        }

    def canonical(self) -> bytes:
        """Deterministic bytes, for the release manifest's hash."""
        return json.dumps(
            self.as_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def digest(self) -> str:
        """SHA-256 of the card."""
        return hashlib.sha256(self.canonical()).hexdigest()

    def to_json(self) -> str:
        """The card as a release artefact."""
        return json.dumps(self.as_payload(), indent=2, sort_keys=True, ensure_ascii=False)

    def to_markdown(self) -> str:
        """The card as a document, with absences visible in the table.

        A reader scanning this should be able to see what is missing without
        comparing it against another card, which is why an absent fact keeps
        its row.
        """
        lines = [f"# {self.model}", ""]
        if not self.complete:
            lines += [
                f"> **{len(self.unrecorded)} fact(s) not recorded.** {', '.join(self.unrecorded)}",
                "",
            ]
        for section in self.sections:
            lines += [f"## {section.section.title()}", "", "| Field | Value |", "|---|---|"]
            lines += [f"| {name} | {value} |" for name, value in section.as_rows()]
            lines.append("")
        if self.sweep:
            lines += [
                "## Merge sweep",
                "",
                f"Method: {self.sweep.get('method')}. "
                f"Points: {self.sweep.get('points')}. "
                f"Selected: {(self.sweep.get('selected') or {}).get('label', 'none')}.",
                "",
            ]
        if self.notes:
            lines += ["## Notes", "", *[f"- {note}" for note in self.notes], ""]
        return "\n".join(lines)


def render(
    *,
    model: str,
    generated_at: datetime,
    identity: Mapping[str, Any],
    provenance: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    compliance: Mapping[str, Any],
    sweep: Mapping[str, Any] | None = None,
    notes: Sequence[str] = (),
) -> ModelCard:
    """Render a card from recorded facts, stating every field the record lacks."""
    return ModelCard(
        model=model,
        generated_at=generated_at,
        identity=_section("identity", identity, IDENTITY_FIELDS, "run record"),
        provenance=_section("provenance", provenance, PROVENANCE_FIELDS, "ledger"),
        evaluation=_section("evaluation", evaluation, EVALUATION_FIELDS, "gate results"),
        compliance=_section("compliance", compliance, COMPLIANCE_FIELDS, "licence register"),
        sweep=dict(sweep) if sweep else None,
        notes=tuple(notes),
    )


def _section(name: str, values: Mapping[str, Any], expected: Sequence[str], source: str) -> FactSet:
    """Build one section, stating every expected field the record did not hold.

    Booleans are handled explicitly. `Fact.optional` treats an empty value as
    an absence, and `False` is a recorded answer -- a release where the
    approver did not also submit the run has `soleApproverException: false`,
    which is a fact and not a gap.
    """
    facts: list[Fact] = []
    for key in expected:
        if key in values and isinstance(values[key], bool):
            facts.append(Fact.recorded(key, values[key], source))
            continue
        facts.append(
            Fact.optional(
                key,
                values.get(key),
                source,
                f"the {source} holds no {key!r} for this release",
            )
        )
    return FactSet(section=name, facts=tuple(facts))
