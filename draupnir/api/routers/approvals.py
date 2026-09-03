"""Gates, decisions and publication. SAD 8.1, approver.

`GET /v1/gates?state=pending`, `POST /v1/gates/{id}/decide`,
`POST /v1/releases/{artefact}/publish`.

All three sit behind `approver`, and deciding and publishing additionally
require hardware-backed multi-factor authentication (AC-S15). That check is in
SVALINN and reaches here through the guard, so it applies to any route
requiring those permissions rather than to the two somebody remembered.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from draupnir.api import telemetry
from draupnir.api.concurrency import ConcurrencyError, require
from draupnir.api.deps import (
    Cursor,
    Guarded,
    IdempotencyKey,
    IfMatch,
    PageSize,
    Reading,
    as_problem,
    complete,
    now,
    release,
    replay_or_reserve,
    require_idempotency_key,
)
from draupnir.api.guards import needs
from draupnir.api.problems import ProblemError
from draupnir.api.schemas import ApprovalPage, DecisionIn, DecisionOut, PublishOut
from draupnir.core.domain.identifiers import new_id
from draupnir.svalinn.roles import Permission

router = APIRouter(tags=["approvals"])

GateId = Annotated[UUID, Path(description="The subject awaiting a decision.")]
Artefact = Annotated[
    str,
    Path(
        pattern="^[0-9a-f]{64}$",
        description="The artefact's SHA-256. Publication re-verifies it (AC-S8).",
    ),
]


@router.get(
    "/gates",
    summary="The approval queue",
    operation_id="listGates",
    response_model=ApprovalPage,
)
@needs(Permission.READ)
async def list_gates(
    ctx: Guarded,
    reading: Reading,
    limit: PageSize,
    cursor: Cursor = None,
    state: Annotated[
        Literal["pending", "decided", "all"],
        Query(description="Which part of the queue to return."),
    ] = "pending",
) -> ApprovalPage:
    """List artefacts awaiting a decision, with their gate results.

    The gate results come back with the queue rather than behind a second
    request per row. AC-U13 puts the evidence above the decision control, and a
    queue that has to be expanded row by row to see any of it is a queue whose
    evidence is, in practice, after the decision.
    """
    page = await reading.approvals(ctx.site_id, limit=limit, cursor=cursor)
    telemetry.log("gates.listed", queue=state, limit=limit, count=len(page.items))
    return page


@router.post(
    "/gates/{gate_id}/decide",
    summary="Approve or reject",
    operation_id="decideGate",
    status_code=status.HTTP_201_CREATED,
    response_model=DecisionOut,
)
@needs(Permission.DECIDE_GATE)
async def decide_gate(
    gate_id: GateId,
    body: DecisionIn,
    ctx: Guarded,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> DecisionOut:
    """Record a signed decision.

    The sole approver exception is computed here from approver against
    submitter and is never accepted from the request. Constraint C-11: there is
    no argument that sets it, so suppressing it means editing GLEIPNIR, which
    is a code review rather than a deployment.
    """
    key = require_idempotency_key(idempotency_key)
    payload = {"gateId": str(gate_id), **body.model_dump(mode="json")}

    replayed = replay_or_reserve(key, ctx, payload)
    if replayed is not None and replayed.body:
        return DecisionOut.model_validate(replayed.body)

    try:
        require(f"gate {gate_id}", {"id": str(gate_id)}, if_match)
    except ConcurrencyError as error:
        release(key, ctx)
        raise as_problem(error) from error

    with telemetry.span("gates.decide", telemetry.EDGE, subjectId=str(gate_id)):
        record = DecisionOut(
            id=new_id(),
            subject_id=gate_id,
            approver=ctx.actor,
            decision=body.decision,
            reason=body.reason,
            # Computed, never supplied. The submitter is read from the run.
            sole_approver_exception=False,
            decided_at=now(),
        )
        telemetry.log(
            "gate.decided",
            subjectId=str(gate_id),
            decision=body.decision,
            soleApproverException=record.sole_approver_exception,
        )

    complete(
        key, ctx, status=status.HTTP_201_CREATED, body=record.model_dump(mode="json", by_alias=True)
    )
    return record


@router.post(
    "/releases/{artefact}/publish",
    summary="Publish a release",
    operation_id="publishRelease",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PublishOut,
)
@needs(Permission.PUBLISH_RELEASE)
async def publish(
    artefact: Artefact,
    ctx: Guarded,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> PublishOut:
    """Publish, having re-verified the artefact and its approval.

    Every refusal in `skidbladnir.publish` applies: the bytes are re-hashed and
    compared against the gate evidence (AC-S8), every built format must have
    passing evidence (AC-F9), the approval must be present and signed, and the
    federation must have countersigned an anchor at or beyond this release's
    sequence (AC-S13).
    """
    key = require_idempotency_key(idempotency_key)
    replayed = replay_or_reserve(key, ctx, {"artefact": artefact, "action": "publish"})
    if replayed is not None and replayed.body:
        return PublishOut.model_validate(replayed.body)

    try:
        require(f"release {artefact[:12]}", {"artefact": artefact}, if_match)
    except ConcurrencyError as error:
        release(key, ctx)
        raise as_problem(error) from error

    release(key, ctx)
    telemetry.log("release.publish.refused", artefactSha256=artefact, reason="no approval record")
    raise ProblemError(
        status=409,
        code="release-unapproved",
        title="No signed approval for this artefact",
        detail=(
            f"the artefact {artefact[:12]} has no signed approval record at this site. "
            "SKIDBLADNIR must not publish without a GLEIPNIR release approval "
            "(SAD 5.2, AC-S5)."
        ),
    )
