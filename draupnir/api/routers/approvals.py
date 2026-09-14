"""Gates, decisions and publication. SAD 8.1, approver.

`GET /v1/gates?state=pending`, `POST /v1/gates/{id}/decide`,
`POST /v1/releases/{artefact}/publish`.

All three sit behind `approver`, and deciding and publishing additionally
require hardware-backed multi-factor authentication (AC-S15). That check is in
SVALINN and reaches here through the guard, so it applies to any route
requiring those permissions rather than to the two somebody remembered.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path as FsPath
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from draupnir.api import telemetry, writing
from draupnir.api.concurrency import ConcurrencyError, release_version, require, run_version
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
from draupnir.core.domain.evidence import (
    ArtefactMismatchError,
    EvidenceError,
    UngatedArtefactError,
)
from draupnir.core.domain.identifiers import new_id
from draupnir.core.domain.states import GuardRefusedError, IllegalTransitionError, RunState
from draupnir.skidbladnir import publish as publication
from draupnir.svalinn.roles import Permission, Role

router = APIRouter(tags=["approvals"])

#: The approval policy these decisions are signed under.
POLICY_VERSION = "gleipnir/2026.01"

#: A release is about the artefact, not the run. SAD 7.1 gives it its own
#: entity, and an auditor asks what was published rather than what was decided.
#: Defined beside the question that reads publications back (RF-32).
RELEASE_SUBJECT = writing.RELEASE_SUBJECT

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

    # RF-32. Over the run's state and retry count, from the facts just read, so
    # the tag the approval queue gave the approver is the tag checked here. It
    # was over the identifier alone, which never changes: an approver who read
    # the queue before somebody else decided could not be told so.
    version = run_version(
        gate_id,
        facts.state if facts is not None else None,
        facts.retry_count if facts is not None else 0,
    )
    try:
        require(f"gate {gate_id}", version, if_match)
    except ConcurrencyError as error:
        release(key, ctx)
        raise as_problem(error) from error

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
            # The approver's instant, not the server's. It is inside the signed
            # payload, so a server-generated one could not be signed by anybody
            # -- which is a thing worth stating because the first version of
            # this used `now()` and no client could have produced a valid
            # signature for it.
            #
            # A rejection needs no signature and so needs no instant from the
            # caller; the server's is right there.
            decided_at=_decided_at(body, approved),
        )

        # Derived from the verified claims, never asserted. RF-06: this was the
        # literal `True`, one field away from the sole-approver exception that
        # the reconciliation had already fixed for exactly this reason --
        # "computed, never supplied".
        #
        # The route guard refuses a caller without the role before reaching
        # here, so in practice this is always true. It is computed anyway,
        # because a fact recorded in the chain should be a measurement rather
        # than a restatement of an assumption made elsewhere: an auditor
        # reading the entry is entitled to a fact, and a second layer that
        # agrees by construction is not a second layer.
        has_role = Role.APPROVER in (ctx.principal.roles if ctx.principal else frozenset())

        verified = False
        if approved:
            verified = _verify_signature(record, body.signature)

        try:
            applied = await recorder.transition_run(
                site_id=ctx.site_id,
                actor=ctx.actor,
                run_id=gate_id,
                target=RunState.RELEASED if approved else RunState.QUARANTINED,
                facts=(
                    {
                        "approver_has_role": has_role,
                        "decision": "APPROVED",
                        "signature": body.signature,
                        "signature_verified": verified,
                    }
                    if approved
                    else {"approver_has_role": has_role, "decision": "REJECTED"}
                ),
                payload=(
                    {
                        "approver": ctx.actor,
                        "signature": body.signature,
                        "decided_at": record.decided_at.isoformat(),
                        "signature_verified": verified,
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

    recorder = writing.writer()
    # RF-32. Over the approval and any publication already recorded, asked the
    # same way `getLineage` asks it, so the tag the publish panel was given is
    # the tag checked here. It was over the artefact digest alone, which never
    # changes, so a second publication from a stale screen could not be refused.
    version = await recorder.read(
        site_id=ctx.site_id, actor=ctx.actor, question=writing.publication_version_of(artefact)
    )
    try:
        require(
            f"release {artefact[:12]}",
            version if version is not None else release_version(artefact, None, None),
            if_match,
        )
    except ConcurrencyError as error:
        release(key, ctx)
        raise as_problem(error) from error

    facts = await recorder.read(
        site_id=ctx.site_id, actor=ctx.actor, question=writing.publication_facts_for(artefact)
    )
    approval = facts.approval if facts is not None else None
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

    # Every control the docstring above names, applied by the module that owns
    # them. RF-05: this handler used to read one entry and, if it existed,
    # record a `published` entry -- no re-hash, no per-format evidence, no
    # signature check, no anchor. `skidbladnir.publish` enforced AC-S8, AC-F9
    # and AC-S13 and the only publication path never called it.
    #
    # Before anything is recorded. A refusal must leave the chain exactly as it
    # was, or the refusal is itself an event somebody has to explain.
    try:
        _resolve_and_check(artefact, facts)
    except StoreUnreachableError as outage:
        release(key, ctx)
        telemetry.log("release.publish.deferred", artefactSha256=artefact, reason=str(outage))
        raise ProblemError(
            status=503,
            code="store-unreachable",
            title="The artefact store could not be reached",
            detail=(
                f"the bytes of {artefact[:12]} could not be resolved, so they could not "
                "be re-hashed against the gate evidence (AC-S8). This is an outage and "
                "not a refusal: nothing was published and nothing was recorded. Retry "
                "when the store is back."
            ),
        ) from outage
    # `EvidenceError` as well as `PublicationError`, because AC-S8's two
    # refusals -- the artefact is not the one the gates passed, and the artefact
    # has no evidence at all -- are raised by `core.domain.evidence` and derive
    # from neither `PublicationError` nor each other. Catching only the latter
    # turned the two most important refusals in this handler into 500s, while
    # `_REFUSAL_CODES` named both and made the gap invisible to a reader.
    except (publication.PublicationError, EvidenceError) as refusal:
        release(key, ctx)
        code = _REFUSAL_CODES.get(type(refusal), "release-inadmissible")
        telemetry.log(
            "release.publish.refused",
            artefactSha256=artefact,
            reason=type(refusal).__name__,
        )
        raise ProblemError(
            status=409,
            code=code,
            title="This release may not be published",
            detail=str(refusal),
        ) from refusal

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
            transition=writing.PUBLISHED,
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


class StoreUnreachableError(Exception):
    """The artefact's bytes could not be fetched.

    Distinct from every publication refusal, because the answers differ: a
    refusal is a 409 and is final until something changes, and this is a 503
    and is a retry. Conflating them would tell an operator that a correct
    release was rejected when the store was merely down.
    """


#: Which problem code each refusal becomes. A mapping rather than a chain of
#: `isinstance`, so that a caller reading the code learns which control
#: refused -- AC-S8, AC-F9, the approval, or AC-S13 -- rather than only that
#: something did.
_REFUSAL_CODES: dict[type[Exception], str] = {
    ArtefactMismatchError: "artefact-mismatch",
    UngatedArtefactError: "artefact-ungated",
    publication.UnapprovedReleaseError: "release-unapproved",
    publication.StaleAnchorError: "anchor-behind",
    publication.IncompletePackageError: "package-incomplete",
}


def _resolve_and_check(artefact: str, facts: Any) -> None:
    """Fetch the bytes and apply every admissibility rule. RF-05.

    The artefact is fetched and hashed rather than trusted: AC-S8 is "re-hash
    what is about to be published", and a digest supplied by a caller is that
    caller's claim about the bytes rather than a measurement of them.
    """
    import tempfile

    if not facts.artefact_uri:
        # A refusal, not an outage. The chain never recorded where these bytes
        # are, so AC-S8's re-hash has nothing to hash -- and building a path
        # from a naming convention would hash whatever happened to be there,
        # which is the opposite of the control.
        msg = (
            f"the chain records no location for {artefact[:12]}, so its bytes cannot be "
            "re-hashed against the gate evidence (AC-S8). A publication is admitted "
            "against what the bytes are, not against what was recorded about them."
        )
        raise publication.PublicationError(msg)

    with tempfile.TemporaryDirectory(prefix="draupnir-publish-") as scratch:
        # Fetched rather than trusted. The artefact store is on ANDVARI and the
        # API has credentials for it; the vault stays mounted into the worker
        # only, which is RF-E04's decision and is untouched here.
        local = FsPath(scratch) / "artefact"
        try:
            _fetch(facts.artefact_uri, local)
        except Exception as error:
            raise StoreUnreachableError(f"{facts.artefact_uri}: {error}") from error

        publication.admissible(
            artefact=local,
            evidence_log=facts.evidence,
            approval=(facts.approval.payload if isinstance(facts.approval.payload, dict) else None),
            built_formats=facts.built_formats,
            release_seq=facts.release_seq,
            anchored_through=facts.anchored_through,
        )


def _fetch(uri: str, destination: FsPath) -> None:
    """Bring the artefact's bytes here, through the configured store driver.

    Through `store_for` rather than building a MinIO client inline (RF-08).
    This constructed an `ObjectStoreDriver` unconditionally, so a forge whose
    artefacts are on the NFS vault -- which is how Sindri is configured -- had
    its publication path reach for an object store that may not be there at
    all. One factory, one answer to "which driver is this deployment's", and
    the refusals it raises at construction are the same ones the worker sees.
    """
    from draupnir.core.infrastructure.config import get_settings
    from draupnir.hodd.stores import store_for

    store_for(get_settings()).get(uri, destination)


def _verify_signature(record: DecisionOut, signature: str) -> bool:
    """Verify an approval signature, or refuse. RF-06.

    Two refusals with different statuses, because they are different problems.
    An approver with **no registered key** is a 409: the estate is not set up
    to accept their decision, and no signature they could produce would change
    that. A signature that **does not verify** is a 422: the request is wrong.

    The bytes are `Approval.signing_payload()`, which already includes the
    sole-approver exception -- so suppressing the exception invalidates the
    signature. That is the property the release path relies on, and the reason
    the payload is built from the record this handler computed rather than from
    anything the request supplied.
    """
    from draupnir.core.infrastructure.config import get_settings
    from draupnir.gleipnir.approvals import Approval, Decision
    from draupnir.svalinn import signing

    settings = get_settings()
    try:
        keys = signing.approver_keys(settings.approver_key_store)
    except signing.ApproverKeyError as unreadable:
        raise ProblemError(
            status=503,
            code="approver-keys-unavailable",
            title="Approver keys could not be read",
            detail=(
                f"{unreadable} Nothing was decided and nothing was recorded; this is a "
                "deployment fault rather than a refusal of the decision."
            ),
        ) from unreadable

    key = keys.get(record.approver)
    if key is None:
        raise ProblemError(
            status=409,
            code="approver-unregistered",
            title="This approver has no registered key",
            detail=(
                f"{record.approver} holds the approver role and no public key is "
                "registered for them, so a signature over their decision cannot be "
                "checked (SAD 9.4). Register the key before they decide: an approval "
                "nobody can verify is not an approval."
            ),
        )

    payload = Approval(
        id=record.id,
        subject_id=record.subject_id,
        approver=record.approver,
        submitter="",
        decision=Decision.APPROVED,
        policy_version=POLICY_VERSION,
        decided_at=record.decided_at,
        signature=signature,
        sole_approver_exception=record.sole_approver_exception,
    ).signing_payload()

    if not signing.verify_approval(payload, signature, key):
        raise ProblemError(
            status=422,
            code="approval-signature-invalid",
            title="The approval's signature did not verify",
            detail=(
                "the signature does not verify against the key registered for "
                f"{record.approver}, over the canonical bytes of {{subject, approver, "
                "decision, policyVersion, decidedAt, soleApproverException}}. The "
                "exception flag is inside those bytes deliberately: suppressing it "
                "invalidates the signature, which is what stops an approver "
                "describing themselves differently to escape the second pair of eyes "
                "(constraint C-11)."
            ),
        )
    return True


#: How far an approver's own timestamp may sit from ours. Five minutes is
#: generous for a person and a clock, and short enough that a signature cannot
#: be prepared far in advance or replayed long afterwards.
DECISION_SKEW = timedelta(minutes=5)


def _decided_at(body: DecisionIn, approved: bool) -> datetime:
    """The instant the approval was signed over, checked for freshness."""
    if not approved or body.decided_at is None:
        if approved:
            raise ProblemError(
                status=422,
                code="decision-undated",
                title="An approval must carry the instant it was signed over",
                detail=(
                    "`decidedAt` is part of the signed payload, so the approver has to "
                    "say which instant they signed. Without it there is nothing to "
                    "verify the signature against."
                ),
            )
        return now()

    if body.decided_at.tzinfo is None:
        raise ProblemError(
            status=422,
            code="decision-naive-timestamp",
            title="`decidedAt` carries no offset",
            detail="Timestamps carry an explicit offset (SAD 11E.2).",
        )

    drift = abs(now() - body.decided_at)
    if drift > DECISION_SKEW:
        raise ProblemError(
            status=422,
            code="decision-stale",
            title="`decidedAt` is too far from now",
            detail=(
                f"the decision is dated {body.decided_at.isoformat()}, which is "
                f"{drift.total_seconds():.0f}s from now. A signature prepared far in "
                "advance, or replayed long afterwards, is refused: the instant is "
                "inside the signed payload precisely so that it can be bounded."
            ),
        )
    return body.decided_at
