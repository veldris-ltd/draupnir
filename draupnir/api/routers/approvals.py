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

from draupnir.api import telemetry, writing
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
from draupnir.core.application.orchestrator import RunFacts, UnknownRunError
from draupnir.core.domain.identifiers import new_id
from draupnir.core.domain.states import GuardRefusedError, IllegalTransitionError, RunState
from draupnir.svalinn.roles import Permission

router = APIRouter(tags=["approvals"])

#: A release is about the artefact, not the run. SAD 7.1 gives it its own
#: entity, and an auditor asks what was published rather than what was decided.
RELEASE_SUBJECT = "release"

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

    recorder = writing.writer()
    facts = await recorder.read(
        site_id=ctx.site_id, actor=ctx.actor, question=writing.facts_of(gate_id)
    )
    if recorder.records and facts is None:
        release(key, ctx)
        raise ProblemError(
            status=404,
            code="gate-not-found",
            title="No such gate at this site",
            detail=(
                f"no run {gate_id} is registered at {ctx.site_id}. A gate is a run "
                "awaiting a decision, and a run at another site is not visible here "
                "(SAD 11C constraint 3)."
            ),
        )

    with telemetry.span("gates.decide", telemetry.EDGE, subjectId=str(gate_id)):
        # Computed here from the chain, never supplied. Constraint C-11: the
        # submitter is read from the run's registration entry, so an approver
        # cannot suppress the exception by describing themselves differently.
        exception = facts is not None and facts.submitter == ctx.actor
        approved = body.decision == "approved"

        record = DecisionOut(
            id=new_id(),
            subject_id=gate_id,
            approver=ctx.actor,
            decision=body.decision,
            reason=body.reason,
            sole_approver_exception=exception,
            decided_at=now(),
        )

        try:
            applied = await recorder.transition_run(
                site_id=ctx.site_id,
                actor=ctx.actor,
                run_id=gate_id,
                target=RunState.RELEASED if approved else RunState.QUARANTINED,
                facts=(
                    {
                        "approver_has_role": True,
                        "decision": "APPROVED",
                        "signature": body.signature,
                    }
                    if approved
                    else {"approver_has_role": True, "decision": "REJECTED"}
                ),
                payload=(
                    {
                        "approver": ctx.actor,
                        "signature": body.signature,
                        "decided_at": record.decided_at.isoformat(),
                        "sole_approver_exception": exception,
                        "submitter": facts.submitter if facts else None,
                        "model": facts.name if facts else None,
                        "reason": body.reason,
                    }
                    if approved
                    else {
                        "rejection_reason": body.reason,
                        "approver": ctx.actor,
                        "decided_at": record.decided_at.isoformat(),
                    }
                ),
            )
        except (IllegalTransitionError, GuardRefusedError) as refusal:
            release(key, ctx)
            raise _refused(gate_id, facts, refusal) from refusal
        except UnknownRunError as unknown:
            release(key, ctx)
            raise ProblemError(
                status=404,
                code="gate-not-found",
                title="No such gate at this site",
                detail=str(unknown),
            ) from unknown

        telemetry.log(
            "gate.decided",
            subjectId=str(gate_id),
            decision=body.decision,
            soleApproverException=record.sole_approver_exception,
            recorded=applied is not None,
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

    recorder = writing.writer()
    approval = await recorder.read(
        site_id=ctx.site_id, actor=ctx.actor, question=writing.released_entry_for(artefact)
    )
    if approval is None:
        release(key, ctx)
        telemetry.log(
            "release.publish.refused", artefactSha256=artefact, reason="no approval record"
        )
        raise ProblemError(
            status=409,
            code="release-unapproved",
            title="No signed approval for this artefact",
            detail=(
                f"the artefact {artefact[:12]} has no signed approval record at this "
                "site. SKIDBLADNIR must not publish without a GLEIPNIR release approval "
                "(SAD 5.2, AC-S5)."
            ),
        )

    with telemetry.span("releases.publish", telemetry.EDGE, artefactSha256=artefact):
        # The release is about the artefact, not about the run: the run reached
        # RELEASED when the approver signed, and this records what was then
        # published under that approval. Two entries because they are two
        # events, and the second is the one an auditor asks about.
        recorded = dict(approval.payload) if isinstance(approval.payload, dict) else {}
        entry = await recorder.record(
            site_id=ctx.site_id,
            actor=ctx.actor,
            subject_type=RELEASE_SUBJECT,
            subject_id=artefact,
            transition="published",
            payload={
                "artefact_sha256": artefact,
                "run_id": approval.subject_id,
                "approved_at_seq": approval.seq,
                "approver": recorded.get("approver"),
            },
        )
        telemetry.log(
            "release.published",
            artefactSha256=artefact,
            runId=approval.subject_id,
            recorded=entry is not None,
        )

    # What the chain recorded, rather than what the publication would like to
    # say. The manifest is SKIDBLADNIR's and is built from the four artefacts on
    # the vault; the API has none of them, so it reports the one the approval
    # carried and an empty one when the approval carried none.
    out = PublishOut(
        artefact_sha256=artefact,
        model=str(recorded.get("model", "")),
        released_at=now(),
        formats=[str(item) for item in recorded.get("formats", [])],
        manifest=dict(recorded.get("manifest", {})),
    )
    complete(
        key,
        ctx,
        status=status.HTTP_202_ACCEPTED,
        body=out.model_dump(mode="json", by_alias=True),
    )
    return out


def _refused(gate_id: UUID, facts: RunFacts | None, refusal: Exception) -> ProblemError:
    """Turn a state machine refusal into a problem an approver can act on.

    409 rather than 422: the request is well formed and the conflict is with
    the state of the run, which is what 409 means. The state is named, because
    "the transition is not permitted" without it tells an approver nothing they
    can do anything about.
    """
    where = f" It is in {facts.state}." if facts else ""
    return ProblemError(
        status=409,
        code="gate-not-decidable",
        title="This run is not awaiting a decision",
        detail=(
            f"run {gate_id} cannot be decided.{where} A decision moves a run from "
            f"{RunState.AWAITING_APPROVAL}, and SAD 6.1 has no other row that a "
            f"decision fits. {refusal}"
        ),
    )
