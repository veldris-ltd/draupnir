"""The release package, as documents a person can take away. S17, RF-27.

S17's primary action is "Download the package", and there was nothing to
download. `getRelease` returned five URIs, the console printed them as code,
and nothing in the application stored or served the documents they named.

So each document is generated here, from the record, when it is asked for --
which is what Decision S11 requires of them in any case: "Article 53 artefacts
are generated from the pipeline record, never authored separately". The
generators are SKIDBLADNIR's and GLEIPNIR's, the ones a publication uses.
Nothing here restates what a model card or a CycloneDX document contains.

Two properties are the point of it.

**The same request produces the same bytes.** Every document is dated by the
release, never by the request, so a download today and one next month have the
same digest -- and a digest is what somebody checks a downloaded document
against. A card dated by its download would change every time it was fetched,
and a document that changes when nothing changed is one nobody can verify.

**An absence is stated, not filled.** A fact the record does not hold renders
as not recorded, with its reason. The card already does that; the lineage
nodes now carry the register's own attribution and personal data answers, so
the training content summary neither invents them nor reports them missing.

One limit, stated rather than hidden: a release does not record which licence
policy version it was judged under, so the copyright policy is rendered under
the version in force now, and says which version that is. SAD 10.2 asks for
the version in force at the release date, and that needs the release to record
it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from draupnir import __version__
from draupnir.api.schemas import AttestationOut, LineageOut, ModelDetailOut, ReleasePackageOut
from draupnir.core.domain.ledger import canonical
from draupnir.gleipnir import copyright as copyright_policy
from draupnir.gleipnir.licence import CURRENT
from draupnir.hamarr import tiers
from draupnir.skidbladnir import article53, modelcard, sbom

#: The documents of a package, by the name a download addresses them with.
DocumentName = Literal["model-card", "sbom", "lineage", "training-summary", "copyright-policy"]


@dataclass(frozen=True, slots=True)
class Document:
    """One document of a release package, as it is served."""

    title: str
    filename: str
    media_type: str


#: Every document S17 lists, in the order the screen lists them.
DOCUMENTS: Final[dict[str, Document]] = {
    "model-card": Document("Model card", "model-card.md", "text/markdown; charset=utf-8"),
    "sbom": Document("SBOM", "sbom.cdx.json", "application/vnd.cyclonedx+json"),
    "lineage": Document("Lineage attestation", "lineage-attestation.json", "application/json"),
    "training-summary": Document(
        "Training data summary", "article-53-training-summary.json", "application/json"
    ),
    "copyright-policy": Document(
        "Copyright policy", "article-53-copyright-policy.json", "application/json"
    ),
}


class UnissuedReleaseError(Exception):
    """Raised for a release with no publication date to date its documents by.

    Dating them by the request instead would make every download a different
    document, which is the property this module exists to prevent.
    """

    def __init__(self, artefact: str) -> None:
        """Name the release."""
        self.artefact = artefact
        super().__init__(
            f"the release of {artefact[:12]} records no publication date, so its documents "
            "have no date to be generated at. A document dated by its download would have a "
            "different digest every time it was fetched."
        )


def attestation(found: LineageOut, *, site_id: str, issued_at: datetime) -> AttestationOut:
    """The lineage attestation. Shared with `exportAttestation`, S28.

    One builder for both, because the downloaded attestation and the exported
    one are the same document and two builders would be two documents the
    first time either changed. An incomplete chain is **unsigned**: a signature
    is read as a statement that somebody checked, and nobody checked a gap.
    """
    payload: dict[str, Any] = {
        "artefact": found.artefact,
        "siteId": site_id,
        "issuedAt": issued_at.isoformat(),
        "complete": found.complete,
        "gaps": list(found.gaps),
        "licences": list(found.licences),
        "corpusHashes": list(found.corpus_hashes),
        "nodes": list(found.nodes),
        "approval": dict(found.approval),
    }
    digest = hashlib.sha256(canonical(payload)).hexdigest()
    return AttestationOut(
        artefact=found.artefact,
        complete=found.complete,
        gaps=list(found.gaps),
        issued_at=issued_at,
        site_id=site_id,
        payload=payload,
        payload_sha256=digest,
        signature=f"sha256:{digest}" if found.complete else None,
    )


def issued_at(release: ReleasePackageOut) -> datetime:
    """The instant every document of this release is dated at."""
    if release.published_at is None:
        raise UnissuedReleaseError(release.artefact)
    return release.published_at


def _sources(lineage: LineageOut) -> tuple[Mapping[str, Any], ...]:
    return tuple(node for node in lineage.nodes if node.get("kind") == "source")


def policy(at: datetime) -> copyright_policy.CopyrightPolicy:
    """The copyright policy, under the licence policy in force. See the module note."""
    return copyright_policy.for_release(CURRENT.version, at)


def summary(release: ReleasePackageOut, lineage: LineageOut) -> article53.TrainingContentSummary:
    """The Article 53 training content summary, from the register's answers.

    Raises `Article53Error` where the lineage holds no source: a summary over
    nothing would assert the model was trained on nothing.
    """
    at = issued_at(release)
    governing = policy(at)
    return article53.summarise(
        model=release.model,
        licence_facts=[
            {
                "licenceSpdx": node.get("licence"),
                "jurisdiction": node.get("jurisdiction"),
                "attributionRequired": node.get("attributionRequired"),
                "personalData": node.get("personalData"),
            }
            for node in _sources(lineage)
        ],
        generated_at=at,
        copyright_policy={
            "uri": release.copyright_policy_uri,
            "version": governing.version,
            "sha256": governing.digest(),
        },
    )


def card(
    release: ReleasePackageOut, lineage: LineageOut, model: ModelDetailOut | None
) -> modelcard.ModelCard:
    """The model card, from recorded facts.

    Anything unrecorded is left out of the mappings rather than filled, and the
    card renders it as not recorded, with its reason.
    """
    at = issued_at(release)
    identity: dict[str, Any] = {"model": release.model, "artefactSha256": release.artefact}
    provenance: dict[str, Any] = {
        "approver": release.approver,
        "soleApproverException": release.sole_approver_exception,
    }
    evaluation: dict[str, Any] = {}

    if model is not None:
        if model.jurisdiction:
            identity["jurisdiction"] = model.jurisdiction
            # A jurisdiction outside the programme has no tier, and the card
            # says "not recorded" rather than guessing one (`tier_of` never does).
            with contextlib.suppress(tiers.TierError):
                identity["tier"] = str(tiers.tier_of(model.jurisdiction))
        formats = sorted(
            {item.uri.rsplit("/", 1)[-1] for item in model.artefacts if item.kind == "quantised"}
        )
        if formats:
            identity["formats"] = formats
        if model.run_id is not None:
            provenance["runId"] = str(model.run_id)
        if model.spec_hash:
            provenance["specificationHash"] = model.spec_hash
        if model.gates:
            evaluation["suiteVersion"] = ", ".join(
                sorted({gate.suite_version for gate in model.gates})
            )
            evaluation["gatesPassed"] = all(gate.passed for gate in model.gates)

    decided = lineage.approval.get("decided_at")
    if decided:
        provenance["approvedAt"] = decided

    compliance: dict[str, Any] = {
        "trainingContentSummary": release.training_summary_uri,
        "copyrightPolicy": release.copyright_policy_uri,
        "copyrightPolicyVersion": policy(at).version,
    }
    sources = _sources(lineage)
    if sources:
        compliance["personalDataPresent"] = any(bool(node.get("personalData")) for node in sources)
        obligations = sorted(
            {str(node.get("licence")) for node in sources if node.get("attributionRequired")}
        )
        # "None" is an answer the register gave, not a gap in it.
        compliance["attributionObligations"] = ", ".join(obligations) or "none"

    return modelcard.render(
        model=release.model,
        generated_at=at,
        identity=identity,
        provenance=provenance,
        evaluation=evaluation,
        compliance=compliance,
    )


def bill_of_materials(release: ReleasePackageOut, lineage: LineageOut) -> sbom.Sbom:
    """The CycloneDX SBOM, from the same lineage the attestation is built from.

    The run node is not a component: its digest is a specification hash, and
    a specification is how the model was made rather than something in it.
    """
    nodes: Sequence[Mapping[str, Any]] = [
        {
            "sha256": node["digest"],
            "kind": node.get("kind"),
            "label": node.get("label"),
            "licence": node.get("licence"),
        }
        for node in lineage.nodes
        if node.get("digest") and node.get("kind") not in {"release", "run"}
    ]
    return sbom.from_lineage(
        model=release.model,
        version="",
        artefact_sha256=release.artefact,
        lineage_nodes=nodes,
        generated_at=issued_at(release),
        tools=[{"name": "draupnir", "version": __version__}],
    )


def render(
    name: str,
    *,
    release: ReleasePackageOut,
    lineage: LineageOut,
    model: ModelDetailOut | None,
    site_id: str,
) -> bytes:
    """One document's bytes. The same inputs give the same bytes, every time."""
    if name == "model-card":
        text = card(release, lineage, model).to_markdown()
    elif name == "sbom":
        text = bill_of_materials(release, lineage).to_json()
    elif name == "lineage":
        issued = attestation(lineage, site_id=site_id, issued_at=issued_at(release))
        text = json.dumps(
            issued.model_dump(mode="json", by_alias=True),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
    elif name == "training-summary":
        text = summary(release, lineage).to_json()
    elif name == "copyright-policy":
        text = policy(issued_at(release)).to_json()
    else:
        msg = f"{name!r} is not a document of a release package; they are {', '.join(DOCUMENTS)}"
        raise KeyError(msg)
    return (text if text.endswith("\n") else f"{text}\n").encode("utf-8")


__all__ = [
    "DOCUMENTS",
    "Document",
    "DocumentName",
    "UnissuedReleaseError",
    "attestation",
    "bill_of_materials",
    "card",
    "issued_at",
    "policy",
    "render",
    "summary",
]
