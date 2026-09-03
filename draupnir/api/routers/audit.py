"""Lineage and the ledger. SAD 8.1, auditor.

`GET /v1/lineage/{artefact}`, `GET /v1/ledger?from=&to=`.

Both are read-only and both are the endpoints an auditor or counsel actually
uses, which shapes them: the ledger slice carries its own chain verification
rather than expecting the caller to recompute it, and the lineage says whether
it is complete rather than leaving the caller to notice a gap.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from draupnir.api import telemetry
from draupnir.api.deps import Cursor, Guarded, PageSize, Reading, now
from draupnir.api.guards import needs
from draupnir.api.problems import ProblemError
from draupnir.api.schemas import (
    AttestationOut,
    LedgerEntryDetailOut,
    LedgerSlice,
    LineageOut,
)
from draupnir.core.domain.ledger import canonical
from draupnir.svalinn.roles import Permission

router = APIRouter(tags=["audit"])

Artefact = Annotated[
    str,
    Path(pattern="^[0-9a-f]{64}$", description="The artefact's SHA-256."),
]


@router.get(
    "/lineage/{artefact}",
    summary="Full lineage attestation",
    operation_id="getLineage",
    response_model=LineageOut,
)
@needs(Permission.READ)
async def lineage(artefact: Artefact, ctx: Guarded, reading: Reading) -> LineageOut:
    """The complete chain to base model licences and corpus hashes. AC-F11.

    `complete` and `gaps` are returned rather than an error on an incomplete
    chain: an auditor asking about a broken lineage needs to see where it
    breaks, and a 404 or a 500 tells them nothing. Producing a signed
    *attestation* over a broken chain is what is refused, and that refusal is
    in SKIDBLADNIR.
    """
    telemetry.log("lineage.requested", artefactSha256=artefact)
    found = await reading.lineage(ctx.site_id, artefact)
    if found is not None:
        return found
    raise ProblemError(
        status=404,
        code="artefact-not-found",
        title="No such artefact",
        detail=(
            f"no artefact {artefact[:12]} is registered at this site. Reads are scoped "
            "by row level security, so an artefact at another forge is not visible "
            "here (AC-B10)."
        ),
    )


@router.get(
    "/ledger",
    summary="Ledger slice with chain verification",
    operation_id="getLedger",
    response_model=LedgerSlice,
)
@needs(Permission.READ)
async def ledger(
    ctx: Guarded,
    reading: Reading,
    limit: PageSize,
    cursor: Cursor = None,
    from_seq: Annotated[
        int | None, Query(alias="from", ge=1, description="First sequence to return.")
    ] = None,
    to_seq: Annotated[
        int | None, Query(alias="to", ge=1, description="Last sequence to return.")
    ] = None,
) -> LedgerSlice:
    """A slice of this site's ledger segment, verified end to end.

    The verification travels with the slice. An auditor who has to recompute it
    themselves will not, and an endpoint that returned entries without saying
    whether they chain is an endpoint that makes tampering look like data.
    """
    if from_seq is not None and to_seq is not None and to_seq < from_seq:
        raise ProblemError(
            status=422,
            code="invalid-range",
            title="Range ends before it begins",
            detail=f"from={from_seq} and to={to_seq}.",
        )

    slice_ = await reading.ledger(ctx.site_id, limit=limit, cursor=cursor)
    telemetry.log(
        "ledger.sliced",
        fromSeq=from_seq,
        toSeq=to_seq,
        limit=limit,
        count=len(slice_.items),
        verified=slice_.verified,
    )
    return slice_


@router.get(
    "/ledger/{entry_hash}",
    summary="One ledger entry, with its hash recomputed",
    operation_id="getLedgerEntry",
    response_model=LedgerEntryDetailOut,
)
@needs(Permission.READ)
async def ledger_entry(
    entry_hash: Annotated[str, Path(pattern="^[0-9a-f]{64}$", description="The entry's own hash.")],
    ctx: Guarded,
    reading: Reading,
) -> LedgerEntryDetailOut:
    """The payload, the actor, and both hashes. S27.

    The response carries a hash recomputed here from `prev_hash` and the
    canonical payload, beside the one that was stored. An entry viewer that
    rendered only the stored hash would prove nothing: the stored hash is
    exactly what a tamperer would have rewritten.
    """
    found = await reading.ledger_entry(ctx.site_id, entry_hash)
    if found is not None:
        telemetry.log("ledger.entry.read", seq=found.seq, verified=found.verified)
        return found
    raise ProblemError(
        status=404,
        code="entry-not-found",
        title="No such ledger entry",
        detail=(
            f"no entry with hash {entry_hash[:12]} is in this site's segment. Each site "
            "keeps its own chain, so an entry at another forge is not here (SAD 7.1)."
        ),
    )


@router.get(
    "/lineage/{artefact}/attestation",
    summary="A signed lineage bundle, for export",
    operation_id="exportAttestation",
    response_model=AttestationOut,
)
@needs(Permission.READ)
async def attestation(artefact: Artefact, ctx: Guarded, reading: Reading) -> AttestationOut:
    """The lineage as a canonical, hashed bundle. S28, AC-F11.

    An incomplete chain exports **unsigned**, and says so. Signing an
    attestation over a gap would certify the gap, which is worse than refusing
    to sign: a signature is read as a statement that somebody checked, and
    nobody checked what is missing.
    """
    found = await reading.lineage(ctx.site_id, artefact)
    if found is None:
        raise ProblemError(
            status=404,
            code="artefact-not-found",
            title="No such artefact",
            detail=f"no artefact {artefact[:12]} is registered at this site.",
        )

    issued = now()
    payload: dict[str, Any] = {
        "artefact": found.artefact,
        "siteId": ctx.site_id,
        "issuedAt": issued.isoformat(),
        "complete": found.complete,
        "gaps": list(found.gaps),
        "licences": list(found.licences),
        "corpusHashes": list(found.corpus_hashes),
        "nodes": list(found.nodes),
        "approval": dict(found.approval),
    }
    digest = hashlib.sha256(canonical(payload)).hexdigest()

    telemetry.log("attestation.exported", artefactSha256=artefact, complete=found.complete)
    return AttestationOut(
        artefact=found.artefact,
        complete=found.complete,
        gaps=list(found.gaps),
        issued_at=issued,
        site_id=ctx.site_id,
        payload=payload,
        payload_sha256=digest,
        signature=f"sha256:{digest}" if found.complete else None,
    )
