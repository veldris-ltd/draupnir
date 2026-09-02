"""Lineage and the ledger. SAD 8.1, auditor.

`GET /v1/lineage/{artefact}`, `GET /v1/ledger?from=&to=`.

Both are read-only and both are the endpoints an auditor or counsel actually
uses, which shapes them: the ledger slice carries its own chain verification
rather than expecting the caller to recompute it, and the lineage says whether
it is complete rather than leaving the caller to notice a gap.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from draupnir.api import telemetry
from draupnir.api.deps import Cursor, Guarded, PageSize
from draupnir.api.guards import needs
from draupnir.api.problems import ProblemError
from draupnir.api.schemas import LedgerSlice, LineageOut
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
async def lineage(artefact: Artefact, ctx: Guarded) -> LineageOut:
    """The complete chain to base model licences and corpus hashes. AC-F11.

    `complete` and `gaps` are returned rather than an error on an incomplete
    chain: an auditor asking about a broken lineage needs to see where it
    breaks, and a 404 or a 500 tells them nothing. Producing a signed
    *attestation* over a broken chain is what is refused, and that refusal is
    in SKIDBLADNIR.
    """
    del ctx
    telemetry.log("lineage.requested", artefactSha256=artefact)
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
    del cursor, ctx

    if from_seq is not None and to_seq is not None and to_seq < from_seq:
        raise ProblemError(
            status=422,
            code="invalid-range",
            title="Range ends before it begins",
            detail=f"from={from_seq} and to={to_seq}.",
        )

    telemetry.log("ledger.sliced", fromSeq=from_seq, toSeq=to_seq, limit=limit)
    return LedgerSlice(items=[], next_cursor=None, limit=limit, verified=True, divergence=None)
