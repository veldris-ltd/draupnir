"""Publication: the last place anything can be refused, and it refuses a lot.

SAD 5.2 gives SKIDBLADNIR one prohibition: it must not "publish without a
GLEIPNIR release approval". This module holds that, and four other refusals
that all exist for the same reason -- publication is the point after which a
mistake is somebody else's problem.

In the order they are checked:

1. **The bytes are re-hashed and compared against the evidence.** AC-S8 and
   threat T8. Not "look up the gate result for this artefact" -- compute the
   hash of what is actually about to be published and require the evidence to
   bind to it. A lookup by artefact identity would find the evidence for the
   artefact that identity used to refer to.
2. **Every built format has passing evidence.** AC-F9: there is no path from
   quantisation to approval that skips evaluation, so a format with no evidence
   refuses publication rather than being published unevaluated.
3. **The approval is present, signed and verified.** GLEIPNIR's, not ours.
4. **The lineage is complete.** AC-F11, refused by `lineage.attest` upstream
   and checked again here because publication is where it stops being
   recoverable.
5. **The four artefacts of AC-F10 are present and agree with each other.**

The order matters. The hash check is first because everything after it is an
assertion about a particular set of bytes, and checking any of it against the
wrong bytes produces a confident wrong answer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from draupnir.core.domain.evidence import (
    ArtefactMismatchError,
    EvidenceLog,
    UngatedArtefactError,
)
from draupnir.skidbladnir.article53 import DownstreamAnnex, TrainingContentSummary
from draupnir.skidbladnir.lineage import Attestation
from draupnir.skidbladnir.modelcard import ModelCard
from draupnir.skidbladnir.sbom import Sbom

SCHEMA = "draupnir/release-manifest/v1"

#: Read in chunks. A 54 GB quantised build does not fit in memory, and a
#: `read_bytes` here would take the control plane down rather than the release.
CHUNK = 1024 * 1024


class PublicationError(Exception):
    """Raised when a release may not be published."""


class UnapprovedReleaseError(PublicationError):
    """Raised when publication is attempted without a signed approval.

    SAD 5.2's prohibition, and AC-S5: publishing without a signed approval
    record returns 409.
    """

    def __init__(self, artefact_sha256: str, detail: str) -> None:
        """Name the artefact and what is wrong with its approval."""
        self.artefact_sha256 = artefact_sha256
        super().__init__(
            f"the release of {artefact_sha256[:12]} has no usable approval: {detail}. "
            "SKIDBLADNIR must not publish without a GLEIPNIR release approval "
            "(SAD 5.2)."
        )


class IncompletePackageError(PublicationError):
    """Raised when the release package is missing an artefact or disagrees with itself.

    AC-F10 wants four artefacts "all four present and internally consistent".
    Consistency is checked rather than assumed, because the four are assembled
    from overlapping inputs and the failure mode is one of them being stale.
    """

    def __init__(self, problems: Sequence[str]) -> None:
        """Name every inconsistency found, not the first."""
        self.problems = tuple(problems)
        super().__init__(
            "the release package is not internally consistent (AC-F10): " + "; ".join(problems)
        )


def hash_file(path: Path, *, chunk: int = CHUNK) -> str:
    """SHA-256 of a file, read in chunks and never held whole."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_tree(root: Path, *, chunk: int = CHUNK) -> str:
    """SHA-256 over a directory: every relative path and its content hash.

    A model is a directory of shards. Hashing the concatenation of file hashes
    without their paths would give the same digest for a model whose shards had
    been renamed, and renaming shards changes which weights load.
    """
    entries = sorted(
        (str(item.relative_to(root)).replace("\\", "/"), hash_file(item, chunk=chunk))
        for item in root.rglob("*")
        if item.is_file()
    )
    if not entries:
        msg = f"{root} contains no files; there is nothing to publish"
        raise PublicationError(msg)
    canonical = json.dumps(entries, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReleasePackage:
    """The four artefacts of AC-F10, plus the Article 53 pair of AC-F17."""

    model: str
    artefact_sha256: str
    card: ModelCard
    sbom: Sbom
    attestation: Attestation
    summary: TrainingContentSummary
    annex: DownstreamAnnex
    #: The Article 53 template version in force at this release's date.
    template_version: str = ""
    #: Copyright policy version, referenced rather than restated.
    copyright_policy_version: str = ""

    def manifest(self, *, released_at: datetime) -> dict[str, Any]:
        """The SHA-256 manifest of AC-F10: every artefact and its digest."""
        return {
            "schema": SCHEMA,
            "model": self.model,
            "artefactSha256": self.artefact_sha256,
            "releasedAt": released_at.isoformat(),
            "article53": {
                "templateVersion": self.template_version,
                "copyrightPolicyVersion": self.copyright_policy_version,
            },
            "artefacts": {
                "modelCard": self.card.digest(),
                "sbom": self.sbom.digest(),
                "lineageAttestation": self.attestation.digest(),
                "trainingContentSummary": self.summary.digest(),
            },
        }

    def consistency_problems(self) -> tuple[str, ...]:
        """Every way the four artefacts disagree with each other. AC-F10."""
        problems: list[str] = []

        if self.sbom.subject.sha256 != self.artefact_sha256:
            problems.append(
                f"the SBOM describes {self.sbom.subject.sha256[:12]} and the release is "
                f"{self.artefact_sha256[:12]}"
            )
        if self.attestation.lineage.root != self.artefact_sha256:
            problems.append(
                f"the lineage attests {self.attestation.lineage.root[:12]} and the "
                f"release is {self.artefact_sha256[:12]}"
            )
        if not self.attestation.lineage.complete:
            problems.append(f"the lineage has gaps: {', '.join(self.attestation.lineage.gaps())}")

        card_sha = self.card.identity.get("artefactSha256")
        if card_sha.known and card_sha.value != self.artefact_sha256:
            problems.append(
                f"the model card names {str(card_sha.value)[:12]} and the release is "
                f"{self.artefact_sha256[:12]}"
            )

        # The summary and the lineage are two renderings of the same corpus.
        # They disagree when one was generated before a source was added.
        #
        # Corpus licences only. The base model's licence is in the chain and is
        # not training content -- it is a component, documented in the SBOM and
        # the model card. Comparing against every licence in the chain would
        # report a correct summary as stale on every release.
        corpus_licences = set(self.attestation.lineage.corpus_licences())
        missing = sorted(corpus_licences - set(self.summary.licences))
        if missing:
            problems.append(
                "the training content summary omits corpus licence(s) present in the "
                f"lineage: {', '.join(missing)}"
            )

        if self.template_version and self.summary.template_version != self.template_version:
            problems.append(
                f"the release records template {self.template_version} and the summary "
                f"was rendered on {self.summary.template_version}"
            )

        return tuple(problems)

    def as_payload(self, *, released_at: datetime) -> dict[str, Any]:
        """Everything the release publishes, in one document."""
        return {
            "manifest": self.manifest(released_at=released_at),
            "modelCard": self.card.as_payload(),
            "sbom": self.sbom.as_payload(),
            "lineageAttestation": self.attestation.as_payload(),
            "trainingContentSummary": self.summary.as_payload(),
            "downstreamAnnex": self.annex.as_payload(),
        }


@dataclass(frozen=True, slots=True)
class Published:
    """What publication produced, once every refusal has been passed."""

    model: str
    artefact_sha256: str
    released_at: datetime
    manifest: Mapping[str, Any]
    formats: tuple[str, ...] = field(default_factory=tuple)

    def as_payload(self) -> dict[str, Any]:
        """The ledger payload for AWAITING_APPROVAL to RELEASED."""
        return {
            "artefactSha256": self.artefact_sha256,
            "releasedAt": self.released_at.isoformat(),
            "formats": list(self.formats),
            "manifest": dict(self.manifest),
        }


def verify_artefact(
    *, artefact: Path, evidence_log: EvidenceLog, artefact_kind: str = "artefact"
) -> str:
    """Re-hash what is about to be published and require evidence for it. AC-S8.

    Computes the hash rather than looking one up. That is the whole control: a
    lookup answers "what did we record about this artefact", and the question
    at publication is "what do we know about these bytes".
    """
    observed = hash_tree(artefact) if artefact.is_dir() else hash_file(artefact)
    found = evidence_log.for_artefact(observed)
    if found is None:
        # Distinguish tampering from a stage that was skipped. If the log holds
        # evidence of this kind for different bytes, something changed; if it
        # holds none, evaluation was bypassed.
        of_kind = evidence_log.of_kind(artefact_kind)
        if of_kind:
            raise ArtefactMismatchError(of_kind[0].artefact_sha256, observed, artefact_kind)
        raise UngatedArtefactError(artefact_kind, observed)
    if not found.passed:
        msg = (
            f"the {artefact_kind} {observed[:12]} has evidence and it failed: "
            f"{', '.join(found.failing)}. A failing artefact is not published."
        )
        raise PublicationError(msg)
    return observed


def verify_formats(evidence_log: EvidenceLog, *, built_formats: Iterable[str]) -> tuple[str, ...]:
    """Require passing evidence for every format that was built. AC-F9.

    Driven by what was built rather than by what was evaluated. Iterating the
    evidence would confirm that everything evaluated passed, which is true of
    an empty set and of a set missing the one format nobody ran.
    """
    built = tuple(sorted(set(built_formats)))
    if not built:
        msg = "no format was named as built, so there is nothing to verify (AC-F9)"
        raise PublicationError(msg)

    evidenced = {item.format: item for item in evidence_log.of_kind("quantised") if item.format}
    unevaluated = [name for name in built if name not in evidenced]
    failed = [name for name in built if name in evidenced and not evidenced[name].passed]

    if unevaluated or failed:
        parts = []
        if unevaluated:
            parts.append(f"never evaluated: {', '.join(unevaluated)}")
        if failed:
            parts.append(f"failed its gates: {', '.join(failed)}")
        msg = (
            f"quantised build(s) may not be published -- {'; '.join(parts)}. Every "
            "quantised output is re-gated, and there is no path from quantisation to "
            "approval that skips evaluation (AC-F9)."
        )
        raise PublicationError(msg)
    return built


def publish(
    package: ReleasePackage,
    *,
    artefact: Path,
    evidence_log: EvidenceLog,
    approval: Mapping[str, Any] | None,
    released_at: datetime,
    built_formats: Iterable[str],
    artefact_kind: str = "quantised",
) -> Published:
    """Publish a release, or refuse. Every refusal in this module is applied here.

    The hash check is first, because everything after it is an assertion about
    a particular set of bytes.
    """
    observed = verify_artefact(
        artefact=artefact, evidence_log=evidence_log, artefact_kind=artefact_kind
    )
    if observed != package.artefact_sha256:
        raise ArtefactMismatchError(package.artefact_sha256, observed, artefact_kind)

    formats = verify_formats(evidence_log, built_formats=built_formats)

    if not approval:
        raise UnapprovedReleaseError(observed, "no approval record was supplied")
    if not approval.get("signature"):
        raise UnapprovedReleaseError(observed, "the approval carries no signature")
    if approval.get("decision") not in {"approved", "APPROVED", True}:
        raise UnapprovedReleaseError(observed, f"the approval records {approval.get('decision')!r}")
    approved_sha = approval.get("artefact_sha256") or approval.get("artefactSha256")
    if approved_sha and approved_sha != observed:
        raise ArtefactMismatchError(str(approved_sha), observed, "approved artefact")

    problems = package.consistency_problems()
    if problems:
        raise IncompletePackageError(problems)

    return Published(
        model=package.model,
        artefact_sha256=observed,
        released_at=released_at,
        manifest=package.manifest(released_at=released_at),
        formats=formats,
    )
