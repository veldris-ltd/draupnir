"""Registering a release with MEGINGJORD at publish. G8.

VLD-SAD-MEGINGJORD-001 AC-X5: "A DRAUPNIR release cannot publish without a
valid receipt, demonstrated end to end across both systems." Publication here
was refused without a countersigned anchor (AC-S13), and that anchor was this
forge's own record of what the federation said. Now the release is also
registered with MEGINGJORD, which checks for itself that the anchor it names
carries a receipt it issued, and refuses a site it has quarantined (AC-X6).
Nothing is published that MEGINGJORD would not register.

The answer becomes part of the release: the registration, the signed receipt
the release was registered against, and the estate posture MEGINGJORD stamped
onto it (AC-I1i), which the lineage attestation and the model card then carry.

Metadata only crosses (SAD 11A, MEGINGJORD MD-2): names, a jurisdiction, a
tier, a digest, a sequence and four addresses. The artefact stays here.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any, Final
from urllib.parse import urlsplit

from shared_protocol import ModelRelease, ProtocolError, parse_digest

from draupnir.api.problems import ProblemError
from draupnir.core.application.orchestrator import PublicationFacts
from draupnir.core.domain.releases import document_address
from draupnir.core.domain.sources import jurisdiction_of
from draupnir.core.infrastructure.config import Settings
from draupnir.gullinbursti.protocol import (
    NOT_ANCHORABLE,
    UNREACHABLE,
    Link,
    Refusal,
    Registration,
)
from draupnir.hamarr import tiers

#: `cim-gbr-v1.0` is model `cim-gbr` at version `1.0`.
_VERSIONED: Final = re.compile(r"^(?P<name>[a-z0-9][a-z0-9.-]*?)-v(?P<version>\d[0-9A-Za-z.+-]*)$")


def origin(settings: Settings) -> str:
    """This console's https origin, or "" where nothing says what it is."""
    if settings.public_origin:
        return settings.public_origin.rstrip("/")
    if settings.oidc_redirect_uri:
        parts = urlsplit(settings.oidc_redirect_uri)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    return ""


def name_and_version(model: str, artefact: str) -> tuple[str, str]:
    """MEGINGJORD's name and version for a run's model.

    A run named for its version (`cim-gbr-v1.0`) is split at the `-v`; any
    other is the whole name, at a version naming the bytes released, so two
    releases of one unversioned model are never the same record.
    """
    lowered = re.sub(r"[^a-z0-9.-]", "-", model.lower()).strip("-.") or "model"
    found = _VERSIONED.match(lowered)
    if found:
        return found["name"], found["version"]
    return lowered[:128], artefact[:12]


def release_record(
    *,
    link: Link,
    artefact: str,
    facts: PublicationFacts,
    model: str,
    console: str,
    released_at: datetime,
) -> ModelRelease:
    """The release record MEGINGJORD is asked to register, or a refusal.

    Raises `ProblemError` naming the missing fact, before anything is sent:
    a record MEGINGJORD would refuse for want of a field is a refusal this
    forge can state better.
    """
    covering = facts.covering_anchor
    if covering is None:
        raise _refusal(
            "release-not-registered",
            "This release cannot be registered",
            f"no countersigned receipt covers sequence {facts.release_seq}, so there is "
            "nothing to register it against (VLD-SAD-MEGINGJORD-001 AC-X5). It is "
            "published once MEGINGJORD countersigns this site's chain that far.",
        )
    jurisdiction = jurisdiction_of(model)
    if jurisdiction is None:
        raise _refusal(
            "release-not-registered",
            "This release cannot be registered",
            f"the run {model!r} names no jurisdiction, and MEGINGJORD registers a "
            "release under the jurisdiction it was trained for.",
        )
    try:
        tier = f"tier-{str(tiers.tier_of(jurisdiction)).lower()}"
    except tiers.TierError as error:
        raise _refusal(
            "release-not-registered", "This release cannot be registered", str(error)
        ) from error
    if not console.startswith("https://"):
        raise _refusal(
            "release-not-registered",
            "This release cannot be registered",
            "MEGINGJORD's record names where the release's documents are, by https "
            "address, and this console's is not configured. Set DRAUPNIR_PUBLIC_ORIGIN "
            "(or DRAUPNIR_OIDC_REDIRECT_URI) to the console's https address.",
        )

    name, version = name_and_version(model, artefact)

    def address(document: str) -> str:
        return f"{console}{document_address(artefact, document)}"

    try:
        return ModelRelease(
            site_id=link.site_id,
            name=name,
            version=version,
            jurisdiction=jurisdiction,
            tier=tier,
            manifest_sha256=parse_digest(f"sha256:{artefact}"),
            anchor_seq=int(covering["anchored_through"]),
            card_uri=address("model-card"),
            sbom_uri=address("sbom"),
            lineage_uri=address("lineage"),
            training_summary_uri=address("training-summary"),
            released_at=released_at.replace(microsecond=0),
        )
    except ProtocolError as error:
        raise _refusal(
            "release-not-registered",
            "This release cannot be registered",
            f"the release record does not satisfy the shared protocol: {error}",
        ) from error


async def register_release(
    link: Link | None,
    *,
    artefact: str,
    facts: PublicationFacts,
    model: str,
    console: str,
    released_at: datetime,
) -> Registration:
    """Register the release with MEGINGJORD, or raise the problem that says why not."""
    if link is None:
        raise _refusal(
            "federation-unconfigured",
            "This forge has no link to MEGINGJORD",
            "a release is registered with MEGINGJORD before it is published, and this "
            "forge has no MEGINGJORD bundle configured (DRAUPNIR_MEGINGJORD_BUNDLE). "
            "Nothing was published.",
        )
    record = release_record(
        link=link,
        artefact=artefact,
        facts=facts,
        model=model,
        console=console,
        released_at=released_at,
    )
    answer = await asyncio.to_thread(link.register, record)
    if isinstance(answer, Registration):
        return answer
    raise refused(answer)


def refused(answer: Refusal) -> ProblemError:
    """MEGINGJORD's refusal as this API's problem, its reason kept whole."""
    if answer.problem == UNREACHABLE:
        return ProblemError(
            status=503,
            code="federation-unreachable",
            title="MEGINGJORD could not be reached",
            detail=(
                f"{answer.detail} A release is registered with MEGINGJORD before it is "
                "published, so nothing was published; training and evaluation continue "
                "(Decision S8). Retry when the link is back."
            ),
        )
    return _refusal(
        "release-not-registered",
        "MEGINGJORD refused to register this release",
        f"{answer.reason} Nothing was published.",
    )


def registered(registration: Registration, facts: PublicationFacts) -> dict[str, Any]:
    """What the `published` entry records of the registration."""
    covering = facts.covering_anchor or {}
    return {
        "model_id": registration.model_id,
        "receipt_id": registration.receipt_id,
        "replay": registration.replay,
        "anchor_seq": int(covering.get("anchored_through", 0)),
        "receipt": str(covering.get("countersignature", "")),
        "estate_posture": dict(registration.estate_posture),
    }


def last_answer(facts: PublicationFacts) -> str:
    """What MEGINGJORD last said about this site's anchoring, as a sentence.

    Added to an anchor-behind refusal, so a release blocked because MEGINGJORD
    quarantined the site says that, in MEGINGJORD's words, rather than only
    how far behind the chain is (AC-X6).
    """
    attempt = facts.last_anchoring or {}
    reason = str(attempt.get("reason") or "")
    if not reason or attempt.get("outcome") in {"countersigned", "duplicate"}:
        return ""
    if attempt.get("problem") == NOT_ANCHORABLE:
        return (
            " MEGINGJORD will not anchor this site's chain, so no release from it can "
            f"be registered until it does: {reason}"
        )
    return f" MEGINGJORD's last answer to this site: {reason}"


def _refusal(code: str, title: str, detail: str) -> ProblemError:
    return ProblemError(status=409, code=code, title=title, detail=detail)


__all__ = [
    "last_answer",
    "name_and_version",
    "origin",
    "refused",
    "register_release",
    "registered",
    "release_record",
]
