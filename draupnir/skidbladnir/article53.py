"""Article 53 artefacts, generated from the record rather than authored.

SAD 9A.2 assigns SKIDBLADNIR three of the five Article 53 obligations:

* technical documentation of the model -- the model card and the lineage
  attestation, which are elsewhere in this package;
* information and documentation for downstream providers -- the annex below;
* a sufficiently detailed public summary of training content, on the AI Office
  template, rendered from the HODD licence register.

The fourth, the copyright policy, is GLEIPNIR's and is referenced by version
rather than restated. The fifth, cooperation with the AI Office, is the core's.

Decision S11 is the whole design: "Article 53 artefacts are generated from the
pipeline record, never authored separately ... A compliance document written by
hand after the fact describes what the author remembers, and drifts from the
system on the first revision that nobody propagates." So there is no template
with blanks. Every value is read from the licence register, and a register that
does not hold a value produces a stated absence rather than a gap for somebody
to fill in later.

**Article 50 is not here and must not be added here.** SAD 9A.1: "Article 50
attaches to systems that generate synthetic content and to their providers and
deployers. That is the Midgard Suite, not the forge." Watermarking and
synthetic-content marking belong to the Suite. A forge that marked its outputs
would be discharging somebody else's duty and would obscure whose it was.

**The template version needs checking at implementation and at each release.**
The SAD says so in terms, and it is true: the AI Office template and the text
of the Regulation are both being revised. `TEMPLATE_VERSION` below is what this
build renders; `for_release` records it on the release, and SAD 10.2 requires
existing releases to keep the version in force at their release date.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SCHEMA = "draupnir/article53-summary/v1"

#: The AI Office template this build renders against. Recorded on every
#: release, because SAD 10.2 keeps a published release on the version in force
#: at its release date rather than migrating it.
TEMPLATE_VERSION = "ai-office/training-content-summary/2025-07"

#: The three sections of the AI Office template. Named here so a section the
#: register cannot fill is rendered as an absence rather than dropped.
SECTIONS: tuple[str, ...] = ("general_information", "data_sources", "data_processing")


class Article53Error(Exception):
    """Raised when a compliance artefact cannot be generated from the record."""


@dataclass(frozen=True, slots=True)
class SourceSummary:
    """One licence-register entry as the template describes it.

    Note what is absent: no URL, and no per-source text. The summary is
    "sufficiently detailed" about categories and provenance, and publishing a
    retrieval list would republish the corpus's structure without adding
    anything the Article asks for.
    """

    licence_spdx: str
    jurisdiction: str
    count: int
    attribution_required: bool
    personal_data: bool

    def as_payload(self) -> dict[str, Any]:
        """The wire shape."""
        return {
            "licence": self.licence_spdx,
            "jurisdiction": self.jurisdiction,
            "sources": self.count,
            "attributionRequired": self.attribution_required,
            "containsPersonalData": self.personal_data,
        }


@dataclass(frozen=True, slots=True)
class TrainingContentSummary:
    """The public summary of training content. Generated, never authored."""

    model: str
    template_version: str
    generated_at: datetime
    sources: tuple[SourceSummary, ...]
    #: The copyright policy this release is governed by, by version and digest.
    copyright_policy: Mapping[str, Any] = field(default_factory=dict)
    #: Facts the register did not hold, stated rather than omitted.
    absences: tuple[str, ...] = ()

    @property
    def total_sources(self) -> int:
        """How many register entries the summary covers."""
        return sum(item.count for item in self.sources)

    @property
    def licences(self) -> tuple[str, ...]:
        """Every distinct SPDX identifier in the corpus."""
        return tuple(sorted({item.licence_spdx for item in self.sources}))

    @property
    def jurisdictions(self) -> tuple[str, ...]:
        """Every jurisdiction the corpus draws on."""
        return tuple(sorted({item.jurisdiction for item in self.sources}))

    @property
    def attribution_required(self) -> tuple[str, ...]:
        """Licences carrying an attribution obligation, for the model card."""
        return tuple(
            sorted({item.licence_spdx for item in self.sources if item.attribution_required})
        )

    def as_payload(self) -> dict[str, Any]:
        """The published shape, on the template's three sections."""
        return {
            "schema": SCHEMA,
            "templateVersion": self.template_version,
            "generatedAt": self.generated_at.isoformat(),
            "general_information": {
                "model": self.model,
                "provider": "Veldris",
                "copyrightPolicy": dict(sorted(self.copyright_policy.items())),
            },
            "data_sources": {
                "totalSources": self.total_sources,
                "licences": list(self.licences),
                "jurisdictions": list(self.jurisdictions),
                "attributionRequired": list(self.attribution_required),
                "breakdown": [item.as_payload() for item in self.sources],
            },
            "data_processing": {
                "personalDataPresent": any(item.personal_data for item in self.sources),
                "textAndDataMiningReservation": (
                    "Sources reserving the Article 4(3) exception are not ingested; "
                    "the reservation is established at registration. See the "
                    "copyright policy referenced above."
                ),
            },
            "notRecorded": list(self.absences),
        }

    def canonical(self) -> bytes:
        """Deterministic bytes, for the release manifest's hash."""
        return json.dumps(
            self.as_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def digest(self) -> str:
        """SHA-256 of the summary."""
        return hashlib.sha256(self.canonical()).hexdigest()

    def to_json(self) -> str:
        """The summary as a release artefact."""
        return json.dumps(self.as_payload(), indent=2, sort_keys=True, ensure_ascii=False)


def summarise(
    *,
    model: str,
    licence_facts: Iterable[Mapping[str, Any]],
    generated_at: datetime,
    copyright_policy: Mapping[str, Any],
    template_version: str = TEMPLATE_VERSION,
) -> TrainingContentSummary:
    """Render the training content summary from the licence register. AC-F17.

    `licence_facts` is what `LicenceRegister.facts_for_policy()` produces. It
    arrives as mappings rather than as records because SKIDBLADNIR cannot
    import HODD -- the modules are independent siblings -- and because that is
    the same seam a policy driver consumes. The consequence worth stating: the
    summary is generated from exactly the facts a licence policy was evaluated
    against, so the document and the decision cannot disagree.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    absences: list[str] = []

    for index, fact in enumerate(licence_facts):
        licence = fact.get("licenceSpdx")
        jurisdiction = fact.get("jurisdiction")
        if not licence:
            absences.append(f"source[{index}].licenceSpdx")
            licence = "unrecorded"
        if not jurisdiction:
            absences.append(f"source[{index}].jurisdiction")
            jurisdiction = "unrecorded"

        key = (str(licence), str(jurisdiction))
        entry = grouped.setdefault(
            key,
            {"count": 0, "attribution": False, "personal": False},
        )
        entry["count"] += 1
        entry["attribution"] = entry["attribution"] or bool(fact.get("attributionRequired"))
        entry["personal"] = entry["personal"] or bool(fact.get("personalData"))

    if not grouped:
        msg = (
            "the licence register holds no sources, so there is nothing to summarise. "
            "A training content summary over an empty register would assert that a "
            "model was trained on nothing (AC-F17, Decision S11)."
        )
        raise Article53Error(msg)

    return TrainingContentSummary(
        model=model,
        template_version=template_version,
        generated_at=generated_at,
        sources=tuple(
            SourceSummary(
                licence_spdx=licence,
                jurisdiction=jurisdiction,
                count=int(entry["count"]),
                attribution_required=bool(entry["attribution"]),
                personal_data=bool(entry["personal"]),
            )
            for (licence, jurisdiction), entry in sorted(grouped.items())
        ),
        copyright_policy=dict(copyright_policy),
        absences=tuple(sorted(set(absences))),
    )


# ---------------------------------------------------------------------------
# The downstream provider annex
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DownstreamAnnex:
    """Information and documentation for downstream providers. SAD 9A.2.

    Written for somebody integrating a CIM into their own system, who then
    becomes a provider under the Act themselves and needs to know what they
    have taken on. So it states the boundary explicitly -- including that
    Article 50 duties attach to them and not to us.
    """

    model: str
    generated_at: datetime
    #: What the model may be used for, from the release record.
    intended_use: tuple[str, ...] = ()
    #: Known limitations, from the gate results and the evaluation report.
    limitations: tuple[str, ...] = ()
    #: The licence the release is distributed under.
    distribution_licence: str | None = None
    #: Attribution the corpus licences oblige a downstream provider to carry.
    attribution: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        """The published shape."""
        return {
            "schema": "draupnir/downstream-annex/v1",
            "model": self.model,
            "generatedAt": self.generated_at.isoformat(),
            "intendedUse": list(self.intended_use),
            "limitations": list(self.limitations),
            "distributionLicence": self.distribution_licence,
            "attributionObligations": list(self.attribution),
            "regulatoryBoundary": {
                "providerObligations": (
                    "Veldris is the provider of this general purpose AI model and "
                    "discharges Article 53. This annex is the information Article 53 "
                    "requires be made available to downstream providers."
                ),
                "article50": (
                    "Article 50 transparency duties -- marking synthetic content, "
                    "disclosing AI interaction -- attach to the system that generates "
                    "content and to its provider and deployer. They are not discharged "
                    "by this model and are not discharged here. A downstream provider "
                    "integrating this model into a generative system takes them on."
                ),
                "systemicRisk": (
                    "Training compute for this model is below the Article 51 systemic "
                    "risk threshold. Article 55 obligations do not attach."
                ),
            },
        }

    def to_json(self) -> str:
        """The annex as a release artefact."""
        return json.dumps(self.as_payload(), indent=2, sort_keys=True, ensure_ascii=False)


def annex(
    *,
    model: str,
    generated_at: datetime,
    summary: TrainingContentSummary,
    intended_use: Sequence[str] = (),
    limitations: Sequence[str] = (),
    distribution_licence: str | None = None,
) -> DownstreamAnnex:
    """Render the downstream annex, with attribution taken from the summary.

    Attribution is derived rather than passed in, because it is an obligation
    the corpus licences impose and not a choice the release makes.
    """
    return DownstreamAnnex(
        model=model,
        generated_at=generated_at,
        intended_use=tuple(intended_use),
        limitations=tuple(limitations),
        distribution_licence=distribution_licence,
        attribution=summary.attribution_required,
    )
