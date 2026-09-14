"""Sources and corpora. SAD 8.1, curator.

`POST /v1/sources`, `POST /v1/corpora/{iso3}/ingest`, `POST /v1/corpora/{iso3}/curate`.

Ingest and curate both return 202. Ingesting a corpus hashes gigabytes and
curation runs a pipeline; neither is something to hold an HTTP connection open
for, and AC-B9 says so in terms. The client gets a run identifier and watches
the event stream.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Response, status

from draupnir.api import telemetry, writing
from draupnir.api.concurrency import ConcurrencyError, require
from draupnir.api.deps import (
    Cursor,
    Guarded,
    IdempotencyKey,
    IfMatch,
    PageSize,
    Reading,
    accepted,
    as_problem,
    complete,
    now,
    release,
    replay_or_reserve,
    require_idempotency_key,
)
from draupnir.api.guards import needs
from draupnir.api.problems import ProblemError
from draupnir.api.reading import retention_out
from draupnir.api.schemas import (
    Accepted,
    CorpusPage,
    RetentionOut,
    RetentionPage,
    SourceIn,
    SourceOut,
    SourcePage,
)
from draupnir.core.domain.identifiers import new_id
from draupnir.core.domain.states import RunState
from draupnir.hodd import retention as retention_record
from draupnir.svalinn.roles import Permission

router = APIRouter(tags=["corpora"])

#: What these entries are about. Neither is a run: a source is a fact HODD
#: recorded and a corpus is an input to many runs, and SAD 7.1 gives each its
#: own entity. The projector folds `run` entries and passes these through.
SOURCE_SUBJECT = "source"
CORPUS_SUBJECT = "corpus"

Iso3 = Annotated[
    str,
    Path(
        min_length=3,
        max_length=3,
        pattern="^[A-Z]{3}$",
        description="ISO 3166-1 alpha-3 code of the jurisdiction.",
    ),
]


@router.post(
    "/sources",
    summary="Register a corpus source",
    operation_id="registerSource",
    status_code=status.HTTP_201_CREATED,
    response_model=SourceOut,
)
@needs(Permission.REGISTER_SOURCE)
async def register_source(
    body: SourceIn,
    ctx: Guarded,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> SourceOut:
    """Register a source with its licence and personal data determination.

    HODD records the facts and never interprets them (Decision S4). A licence
    identifier arriving here is stored as a string; whether it permits anything
    is GLEIPNIR's question, asked later against these recorded facts.
    """
    key = require_idempotency_key(idempotency_key)
    payload = body.model_dump(mode="json")

    replayed = replay_or_reserve(key, ctx, payload)
    if replayed is not None and replayed.body:
        response.status_code = replayed.status or status.HTTP_201_CREATED
        return SourceOut.model_validate(replayed.body)

    if body.personal_data and not body.dpia_ref:
        release(key, ctx)
        raise ProblemError(
            status=422,
            code="dpia-reference-required",
            title="A source holding personal data names its DPIA",
            detail=(
                "personalData is true and no dpiaRef was given. The determination and "
                "its reference are one fact, not two (SAD 7.1)."
            ),
        )

    with telemetry.span("sources.register", telemetry.EDGE, jurisdiction=body.jurisdiction):
        record = SourceOut(
            id=new_id(),
            jurisdiction=body.jurisdiction,
            url=body.url,
            licence_spdx=body.licence_spdx,
            attribution_required=body.attribution_required,
            personal_data=body.personal_data,
            dpia_ref=body.dpia_ref,
            retrieved_at=body.retrieved_at,
            sha256=body.sha256,
            state=RunState.DRAFT,
        )
        # HODD records; GLEIPNIR judges. What goes in the chain is the facts,
        # including the personal data determination and its DPIA reference,
        # because a licence decision taken later has to be explicable against
        # the facts that were recorded at the time and not against today's.
        entry = await writing.writer().record(
            site_id=ctx.site_id,
            actor=ctx.actor,
            subject_type=SOURCE_SUBJECT,
            subject_id=str(record.id),
            transition="registered",
            payload={
                "jurisdiction": record.jurisdiction,
                "url": record.url,
                "licence_spdx": record.licence_spdx,
                "attribution_required": record.attribution_required,
                "personal_data": record.personal_data,
                "dpia_ref": record.dpia_ref,
                "sha256": record.sha256,
                "retrieved_at": record.retrieved_at.isoformat(),
            },
        )
        telemetry.log(
            "source.registered",
            sourceId=str(record.id),
            sha256=record.sha256,
            recorded=entry is not None,
        )

    complete(
        key,
        ctx,
        status=status.HTTP_201_CREATED,
        body=record.model_dump(mode="json", by_alias=True),
        location=f"/v1/sources/{record.id}",
    )
    response.headers["Location"] = f"/v1/sources/{record.id}"
    return record


@router.get(
    "/sources",
    summary="List registered sources",
    operation_id="listSources",
    response_model=SourcePage,
)
@needs(Permission.READ)
async def list_sources(
    ctx: Guarded, reading: Reading, limit: PageSize, cursor: Cursor = None
) -> SourcePage:
    """List sources for the scoped site, cursor paginated.

    Scoped by the row level security variable the site resolver sets, so a
    request cannot read another forge's register even if it names one.
    """
    page = await reading.sources(ctx.site_id, limit=limit, cursor=cursor)
    telemetry.log("sources.listed", limit=limit, count=len(page.items))
    return page


@router.post(
    "/corpora/{iso3}/ingest",
    summary="Ingest and hash a jurisdiction's sources",
    operation_id="ingestCorpus",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Accepted,
)
@needs(Permission.CURATE)
async def ingest(iso3: Iso3, ctx: Guarded, idempotency_key: IdempotencyKey = None) -> Accepted:
    """Stage, hash, publish, seal and register. Returns 202.

    Hashing a corpus is minutes to hours of work; AC-B9 requires that no
    endpoint blocks an HTTP request on it.
    """
    key = require_idempotency_key(idempotency_key)
    replayed = replay_or_reserve(key, ctx, {"iso3": iso3, "action": "ingest"})
    if replayed is not None and replayed.body:
        return Accepted.model_validate(replayed.body)

    run_id = new_id()
    with telemetry.span("corpora.ingest", telemetry.EDGE, jurisdiction=iso3):
        # A corpus is not a run: it is an input to many of them, and SAD 7.1
        # gives it its own entity. So the entry is about the corpus, and the
        # projector -- which folds runs -- passes it through untouched.
        entry = await writing.writer().record(
            site_id=ctx.site_id,
            actor=ctx.actor,
            subject_type=CORPUS_SUBJECT,
            subject_id=iso3,
            transition="ingest-accepted",
            payload={"jurisdiction": iso3, "run_id": str(run_id)},
        )
        telemetry.log(
            "corpus.ingest.accepted",
            jurisdiction=iso3,
            runId=str(run_id),
            recorded=entry is not None,
        )

    body = accepted(run_id, detail=f"ingesting the {iso3} corpus")
    complete(key, ctx, status=status.HTTP_202_ACCEPTED, body=body)
    return Accepted.model_validate(body)


@router.post(
    "/corpora/{iso3}/curate",
    summary="Run the curation pipeline",
    operation_id="curateCorpus",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Accepted,
)
@needs(Permission.CURATE)
async def curate(iso3: Iso3, ctx: Guarded, idempotency_key: IdempotencyKey = None) -> Accepted:
    """Deduplicate, filter and decontaminate against the evaluation sets. 202."""
    key = require_idempotency_key(idempotency_key)
    replayed = replay_or_reserve(key, ctx, {"iso3": iso3, "action": "curate"})
    if replayed is not None and replayed.body:
        return Accepted.model_validate(replayed.body)

    run_id = new_id()
    with telemetry.span("corpora.curate", telemetry.EDGE, jurisdiction=iso3):
        entry = await writing.writer().record(
            site_id=ctx.site_id,
            actor=ctx.actor,
            subject_type=CORPUS_SUBJECT,
            subject_id=iso3,
            transition="curate-accepted",
            payload={"jurisdiction": iso3, "run_id": str(run_id)},
        )
        telemetry.log(
            "corpus.curate.accepted",
            jurisdiction=iso3,
            runId=str(run_id),
            recorded=entry is not None,
        )

    body = accepted(run_id, detail=f"curating the {iso3} corpus")
    complete(key, ctx, status=status.HTTP_202_ACCEPTED, body=body)
    return Accepted.model_validate(body)


@router.get(
    "/corpora",
    summary="Corpora by jurisdiction, with curation progress",
    operation_id="listCorpora",
    response_model=CorpusPage,
)
@needs(Permission.READ)
async def list_corpora(ctx: Guarded, reading: Reading) -> CorpusPage:
    """The corpus of each jurisdiction, and how far curation has reached. S05.

    The counts are what a curator acts on: how many sources are registered,
    how many cleared the licence gate, and how many were quarantined by it. A
    quarantined source is not a failure of the pipeline -- it is the licence
    gate doing its job -- so it is counted beside the others rather than
    hidden in an error state.
    """
    page = await reading.corpora(ctx.site_id)
    telemetry.log("corpora.listed", jurisdictions=len(page.items))
    return page


@router.get(
    "/retention",
    summary="Retention actions, due and executed",
    operation_id="listRetention",
    response_model=RetentionPage,
)
@needs(Permission.READ)
async def list_retention(ctx: Guarded, reading: Reading) -> RetentionPage:
    """Corpora approaching their deletion point. S06, SAD 7.3.

    Deletion here is an approved, ledgered action rather than a timer firing,
    so an unapproved action that is past its due date is a thing somebody has
    to decide about rather than a thing that has silently happened. The overdue
    count is on the response for exactly that reason.
    """
    page = await reading.retention(ctx.site_id)
    telemetry.log("retention.listed", actions=len(page.items), overdue=page.overdue)
    return page


@router.post(
    "/retention/{action_id}/approve",
    summary="Approve the deletion a retention action proposes",
    operation_id="approveRetention",
    response_model=RetentionOut,
)
@needs(Permission.APPROVE_RETENTION)
async def approve_retention(
    action_id: Annotated[
        UUID, Path(description="The retention action, as `listRetention` returns it.")
    ],
    ctx: Guarded,
    response: Response,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> RetentionOut:
    """Approve one deletion. S06's primary action, RF-27; SAD 7.3.

    The approval is recorded and nothing is deleted here. The worker's
    retention duty carries it out, through `hodd.retention.execute`, and
    records the outcome -- including a refusal that names the releases where
    the curated manifests would not survive (AC-F20). Deletion cannot be
    undone, so this takes a hardware authenticator (SVALINN), an
    `Idempotency-Key`, and an `If-Match` over the action's state: an approver
    who read the action before somebody else approved or refused it is refused
    with 412 rather than approving what they did not see.
    """
    key = require_idempotency_key(idempotency_key)
    replayed = replay_or_reserve(key, ctx, {"actionId": str(action_id), "action": "approve"})
    if replayed is not None and replayed.body:
        return RetentionOut.model_validate(replayed.body)

    recorder = writing.writer()
    entries = await recorder.read(
        site_id=ctx.site_id,
        actor=ctx.actor,
        question=writing.entries_of(retention_record.CORPUS_SUBJECT),
    )
    proposal = next(
        (item for item in retention_record.fold(entries or ()) if item.id == action_id), None
    )
    if proposal is None:
        release(key, ctx)
        raise ProblemError(
            status=404,
            code="retention-action-not-found",
            title="No such retention action",
            detail=(
                f"no retention action {action_id} is recorded at this site. Actions are "
                "proposed by the daily retention duty; read them at /v1/retention."
            ),
        )

    try:
        require(f"retention action {action_id}", proposal.version(), if_match)
    except ConcurrencyError as error:
        release(key, ctx)
        raise as_problem(error) from error

    if not proposal.approvable:
        release(key, ctx)
        raise ProblemError(
            status=409,
            code="retention-not-approvable",
            title="This retention action is not awaiting approval",
            detail=(
                f"retention action {action_id} is {proposal.state}. An action is approved "
                "once; a refused one may be approved again, and an executed one is done."
            ),
        )

    moment = now()
    if moment < proposal.due_at:
        release(key, ctx)
        raise ProblemError(
            status=409,
            code="retention-not-due",
            title="This corpus is not yet due for deletion",
            detail=str(
                retention_record.NotDueError(
                    proposal.artefact_uri or proposal.corpus_sha256, proposal.due_at, moment
                )
            ),
        )

    with telemetry.span("retention.approve", telemetry.EDGE, actionId=str(action_id)):
        entry = await recorder.record(
            site_id=ctx.site_id,
            actor=ctx.actor,
            subject_type=retention_record.CORPUS_SUBJECT,
            subject_id=proposal.corpus_sha256,
            transition=retention_record.APPROVED,
            payload={
                retention_record.ANSWERS: proposal.seq,
                "corpusSha256": proposal.corpus_sha256,
                "artefact": proposal.artefact_uri,
                "releases": list(proposal.releases),
                "approvedAt": moment.isoformat(),
            },
        )
        telemetry.log(
            "retention.approved",
            actionId=str(action_id),
            corpusSha256=proposal.corpus_sha256,
            recorded=entry is not None,
        )

    approved = replace(
        proposal,
        state=retention_record.ProposalState.APPROVED,
        approved_by=ctx.actor,
        approved_seq=entry.seq if entry is not None else None,
        reason=None,
    )
    out = retention_out(approved, now=moment)
    response.headers["ETag"] = out.etag
    complete(
        key,
        ctx,
        status=status.HTTP_200_OK,
        body=out.model_dump(mode="json", by_alias=True),
    )
    return out
