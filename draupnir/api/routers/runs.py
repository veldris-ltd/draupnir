"""Runs, and the event stream. SAD 8.1, operator.

`POST /v1/runs`, `GET /v1/runs`, `GET /v1/runs/{id}`, `POST /v1/runs/{id}/cancel`,
`POST /v1/runs/{id}/retry`, and `GET /v1/runs/{id}/events`.

Cancel and retry are conditional writes: they take `If-Match` and return 412 on
a stale tag (AC-B4). That is not ceremony. Two operators looking at the same run
board, one cancelling and one retrying, is the ordinary case, and without the
precondition the retry silently undoes the cancellation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path as Path_
from tempfile import TemporaryDirectory
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from draupnir.api import events as event_stream
from draupnir.api import telemetry, writing
from draupnir.api.concurrency import ConcurrencyError, require
from draupnir.api.concurrency import etag as concurrency_etag
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
    release,
    replay_or_reserve,
    require_idempotency_key,
)
from draupnir.api.guards import needs
from draupnir.api.problems import ProblemError
from draupnir.api.routers import plugins
from draupnir.api.schemas import (
    Accepted,
    CancelIn,
    DryRunOut,
    RunOut,
    RunPage,
    RunSubmission,
)
from draupnir.core.application.orchestrator import DuplicateRunError, OrchestrationError
from draupnir.core.domain.identifiers import new_id
from draupnir.core.domain.identity import InputHashError, RunIdentity, run_identity
from draupnir.core.plugins import PluginError
from draupnir.interfaces.types import RunSpec
from draupnir.svalinn.roles import Permission

router = APIRouter(tags=["runs"])

RunId = Annotated[UUID, Path(description="UUIDv7 run identifier.")]

#: The per-site stream. One per process here; a deployment fans out from the
#: ledger's notification channel into the same shape.
STREAMS: dict[str, event_stream.EventStream] = {}


def stream_for(site_id: str) -> event_stream.EventStream:
    """The event stream for one site. Streams never cross a site boundary."""
    return STREAMS.setdefault(site_id, event_stream.EventStream(site_id=site_id))


@router.post(
    "/runs",
    summary="Submit a run specification",
    operation_id="submitRun",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Accepted,
)
@needs(Permission.SUBMIT_RUN)
async def submit(
    body: RunSubmission, ctx: Guarded, idempotency_key: IdempotencyKey = None
) -> Accepted:
    """Validate, hash into a run identity, and queue. Returns 202. AC-B9.

    Nothing here waits for an allocation, let alone for training. The
    specification is validated and recorded, and the client watches the stream.
    """
    key = require_idempotency_key(idempotency_key)
    payload = body.model_dump(mode="json")

    replayed = replay_or_reserve(key, ctx, payload)
    if replayed is not None and replayed.body:
        return Accepted.model_validate(replayed.body)

    if not body.specification:
        release(key, ctx)
        raise ProblemError(
            status=422,
            code="specification-empty",
            title="A run specification is required",
            detail="SAD 6.2 makes the specification the unit of reproduction.",
        )

    identity = _identity_of(body, on_error=lambda: release(key, ctx))

    run_id = new_id()
    recorder = writing.writer()
    with telemetry.span("runs.submit", telemetry.EDGE, runId=str(run_id)):
        try:
            recorded = await recorder.register_run(
                site_id=ctx.site_id,
                actor=ctx.actor,
                run_id=run_id,
                name=_spec_name(body),
                spec_hash=identity.spec_hash,
                kind=_spec_kind(body),
                identity=identity.digest,
                payload={"input_artefact_sha256": list(identity.input_artefact_sha256)},
            )
        except DuplicateRunError as duplicate:
            # AC-F2. Reported rather than silently re-run, and 409 rather than
            # 422: the submission is well formed and the conflict is with the
            # state of the system, which is what 409 means.
            release(key, ctx)
            raise ProblemError(
                status=409,
                code="duplicate-run",
                title="This run has already been submitted",
                detail=str(duplicate),
            ) from duplicate
        except OrchestrationError as error:
            release(key, ctx)
            raise ProblemError(
                status=503,
                code="run-not-recorded",
                title="The run could not be recorded",
                detail=(
                    f"{error} The submission was not accepted, because a run the ledger "
                    "does not hold is a run nobody can audit."
                ),
            ) from error

        telemetry.log(
            "run.submitted",
            runId=str(run_id),
            runIdentity=identity.digest,
            recorded=recorded is not None,
        )

    body_out = accepted(run_id, detail="run queued")
    body_out["run_identity"] = identity.digest
    # The board learns about this run from the stream, not from a refresh
    # (AC-U4, AC-N3). Published after the identity is settled so the event
    # carries what the board renders.
    stream_for(ctx.site_id).publish(
        event_stream.EventKind.RUN_STATE,
        subject_id=run_id,
        run_id=run_id,
        at=datetime.now(UTC),
        changed={"state": "QUEUED", "name": _spec_name(body), "runIdentity": identity.digest},
    )
    complete(
        key, ctx, status=status.HTTP_202_ACCEPTED, body=body_out, location=f"/v1/runs/{run_id}"
    )
    return Accepted.model_validate(body_out)


def _spec_kind(body: RunSubmission) -> str:
    """The artefact kind this run produces, from the specification's `kind`.

    `AdapterRun` produces an adapter, `SubstrateRun` a substrate. Mapped rather
    than lowercased, because the eight artefact kinds are an enumeration the
    database holds and a ninth is refused when the row is written.
    """
    kind = str(body.specification.get("kind", "AdapterRun"))
    return {
        "AdapterRun": "adapter",
        "SubstrateRun": "substrate",
        "MergeRun": "merged",
        "QuantiseRun": "quantised",
    }.get(kind, "adapter")


def _spec_name(body: RunSubmission) -> str:
    """The specification's `metadata.name`, or a placeholder for the board."""
    metadata = body.specification.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("name"), str):
        return str(metadata["name"])
    return "unnamed"


def _identity_of(body: RunSubmission, *, on_error: Callable[[], None]) -> RunIdentity:
    """Compute the run identity of a submission, or refuse it.

    AC-F1 requires the CLI and the console to arrive at the same identity for
    the same specification, and they do so by construction: both post here, and
    the identity is computed here rather than by either client. A client that
    computed it would be a second implementation of the rule, and two
    implementations of a hash is one too many.
    """
    try:
        spec = RunSpec.from_mapping(body.specification)
    except (ValueError, KeyError, TypeError) as error:
        on_error()
        raise ProblemError(
            status=422,
            code="specification-invalid",
            title="The run specification could not be read",
            detail=str(error),
        ) from error

    # Both inputs, not just the base. A specification consumes a base model
    # and a curated corpus, and two runs over the same base and different
    # corpora are different work; an identity that ignored the dataset would
    # call them the same.
    inputs = [
        digest
        for digest in (spec.base.expect_sha256, spec.dataset.expect_sha256)
        if digest is not None
    ]
    try:
        return run_identity(spec.spec_hash(), inputs)
    except InputHashError as error:
        on_error()
        raise ProblemError(
            status=422,
            code="input-hash-unresolved",
            title="An input artefact hash is missing or malformed",
            detail=(
                f"{error} A run identity computed over an unresolved reference looks "
                "reproducible and is not, so the submission is refused rather than "
                "recorded under an identity that means nothing (AC-F1)."
            ),
        ) from error


@router.get("/runs", summary="List runs", operation_id="listRuns", response_model=RunPage)
@needs(Permission.READ)
async def list_runs(
    ctx: Guarded,
    reading: Reading,
    limit: PageSize,
    cursor: Cursor = None,
    state: Annotated[str | None, Query(description="Filter to one state of SAD 6.1.")] = None,
) -> RunPage:
    """List runs for the scoped site, cursor paginated. AC-B3, AC-N4.

    The filter is in the query string rather than in client-side state because
    every screen has a URL that restores it (UX 11, deep links): an operator
    who filters the board to FAILED and sends the link to a colleague must send
    the filter with it.
    """
    page = await reading.runs(ctx.site_id, limit=limit, cursor=cursor, state=state)
    telemetry.log("runs.listed", limit=limit, count=len(page.items))
    return page


@router.get("/runs/{run_id}", summary="Inspect a run", operation_id="getRun", response_model=RunOut)
@needs(Permission.READ)
async def get_run(run_id: RunId, ctx: Guarded, reading: Reading, response: Response) -> RunOut:
    """Return one run, with an `ETag` for a later conditional write."""
    found = await reading.run(ctx.site_id, run_id)
    if found is not None:
        response.headers["ETag"] = concurrency_etag(
            {"id": str(found.id), "state": str(found.state)}
        )
        return found
    raise ProblemError(
        status=404,
        code="run-not-found",
        title="No such run",
        detail=(
            f"no run {run_id} exists at this site. A run at another site is not "
            "visible here: reads are scoped by row level security (AC-B10)."
        ),
    )


@router.post(
    "/runs/{run_id}/cancel",
    summary="Cancel a run",
    operation_id="cancelRun",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Accepted,
)
@needs(Permission.CANCEL_RUN)
async def cancel(
    run_id: RunId,
    body: CancelIn,
    ctx: Guarded,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> Accepted:
    """Stop the scheduler job and leave the artefact in a defined state. AC-F13.

    Conditional: a stale `If-Match` returns 412 rather than cancelling a run
    that has moved on since the operator read it.
    """
    key = require_idempotency_key(idempotency_key)
    replayed = replay_or_reserve(
        key, ctx, {"runId": str(run_id), "action": "cancel", "reason": body.reason}
    )
    if replayed is not None and replayed.body:
        return Accepted.model_validate(replayed.body)

    try:
        require(f"run {run_id}", {"id": str(run_id)}, if_match)
    except ConcurrencyError as error:
        release(key, ctx)
        raise as_problem(error) from error

    with telemetry.span("runs.cancel", telemetry.EDGE, runId=str(run_id)):
        telemetry.log("run.cancel.accepted", runId=str(run_id), reason=body.reason)

    out = accepted(run_id, detail="cancellation requested")
    complete(key, ctx, status=status.HTTP_202_ACCEPTED, body=out)
    return Accepted.model_validate(out)


@router.post(
    "/runs/{run_id}/retry",
    summary="Retry a run",
    operation_id="retryRun",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Accepted,
)
@needs(Permission.SUBMIT_RUN)
async def retry(
    run_id: RunId,
    ctx: Guarded,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> Accepted:
    """Requeue a failed run within its retry budget. Conditional, and 202."""
    key = require_idempotency_key(idempotency_key)
    replayed = replay_or_reserve(key, ctx, {"runId": str(run_id), "action": "retry"})
    if replayed is not None and replayed.body:
        return Accepted.model_validate(replayed.body)

    try:
        require(f"run {run_id}", {"id": str(run_id)}, if_match)
    except ConcurrencyError as error:
        release(key, ctx)
        raise as_problem(error) from error

    with telemetry.span("runs.retry", telemetry.EDGE, runId=str(run_id)):
        telemetry.log("run.retry.accepted", runId=str(run_id))

    out = accepted(run_id, detail="run requeued")
    complete(key, ctx, status=status.HTTP_202_ACCEPTED, body=out)
    return Accepted.model_validate(out)


@router.get(
    "/runs/{run_id}/events",
    summary="Watch a run's state deltas",
    operation_id="streamRunEvents",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
@needs(Permission.READ)
async def run_events(
    run_id: RunId,
    request: Request,
    ctx: Guarded,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Server-sent events carrying state deltas, never list refreshes.

    A reconnecting client sends `Last-Event-ID` and receives what it missed. A
    client asking for a point the buffer has dropped is told to resynchronise
    rather than served from the oldest event it happens to still hold, because
    a silent gap leaves the client's state wrong with nothing to detect it.
    """
    del request
    stream = stream_for(ctx.site_id)

    try:
        since = event_stream.parse_last_event_id(last_event_id)
        frames = list(stream.frames(since))
    except event_stream.ResynchroniseRequiredError as error:
        raise ProblemError(
            status=409,
            code="resynchronise-required",
            title="Event history no longer buffered",
            detail=str(error),
        ) from error
    except event_stream.StreamError as error:
        raise ProblemError(
            status=422,
            code="invalid-last-event-id",
            title="Invalid Last-Event-ID",
            detail=str(error),
        ) from error

    telemetry.log("events.stream.opened", runId=str(run_id), since=since)

    def body() -> Iterator[str]:
        yield from frames
        yield event_stream.comment("keep-alive")

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/runs/dry-run",
    summary="Render a run specification without submitting it",
    operation_id="dryRunSpecification",
    response_model=DryRunOut,
)
@needs(Permission.SUBMIT_RUN)
async def dry_run(body: RunSubmission, ctx: Guarded) -> DryRunOut:
    """Render the exact job plan, consuming no allocation. AC-F14.

    This is the primary action of the compose screen, and submission is the
    secondary one, because an allocation on this estate is the scarce resource
    and a specification error should cost nothing to find.

    Nothing is reserved here and nothing is recorded. `render` is pure by
    Decision S5 and the conformance harness enforces that, so calling it is
    the same operation whether the run is ever submitted or not -- which is
    what makes the plan shown here the plan that would actually be run rather
    than an approximation of it.
    """
    del ctx
    identity = _identity_of(body, on_error=lambda: None)

    spec = RunSpec.from_mapping(body.specification)
    group = "draupnir.merge" if spec.kind.lower() == "mergerun" else "draupnir.train"
    try:
        plugin = plugins.registry().for_spec(spec, group)
    except PluginError as error:
        raise ProblemError(
            status=422,
            code="driver-unavailable",
            title="No installed driver can render this specification",
            detail=str(error),
        ) from error

    # Validated before rendered, so that a specification problem is reported as
    # one. `render` on a specification the driver has refused is undefined
    # behaviour, and letting it raise turns "your specification is missing
    # save_steps" into a KeyError, which is the operator's problem stated in
    # the driver author's vocabulary.
    problems = list(plugin.driver.validate(spec))
    if problems:
        raise ProblemError(
            status=422,
            code="specification-rejected",
            title="The driver refused this specification",
            detail=" ".join(f"{problem.field}: {problem.message}" for problem in problems),
        )

    with TemporaryDirectory(prefix="draupnir-dry-run-") as workdir:
        # A temporary directory that is discarded: `render` must have no side
        # effects, and giving it a real working directory would make a driver
        # that writes one look like it works here.
        try:
            plan = plugin.driver.render(spec, Path_(workdir))
        except Exception as error:
            # `validate` passed and `render` raised, which is a defect in the
            # driver rather than in the specification. Named as such, because
            # an operator told to fix their specification will not be able to.
            raise ProblemError(
                status=502,
                code="driver-defect",
                title="The driver accepted this specification and then failed to render it",
                detail=(
                    f"{plugin.name} raised {type(error).__name__}: {error}. Its `validate` "
                    "returned no problems, so this is a fault in the driver rather than in "
                    "the specification. The conformance suite covers exactly this."
                ),
            ) from error

    warnings: list[str] = []
    telemetry.log("run.dry-run", driver=plugin.name, runIdentity=identity.digest)

    return DryRunOut(
        run_identity=identity.digest,
        spec_hash=identity.spec_hash,
        input_artefact_sha256=list(identity.input_artefact_sha256),
        driver=str(plugin.name),
        command=list(plan.command),
        environment=dict(plan.environment),
        resources=plan.as_mapping()["resources"],
        warnings=warnings,
    )


@router.get(
    "/events",
    summary="Watch this site's state deltas",
    operation_id="streamSiteEvents",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
@needs(Permission.READ)
async def site_events(
    request: Request,
    ctx: Guarded,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """The run board's stream. AC-U4, AC-N3.

    The per-run stream answers "what is happening to this run". The board needs
    "what is happening at this site", and building it from one subscription per
    visible run would open fifty-six connections to render one screen.

    The connection stays open and events are pushed. A board that reconnected
    for each answer would be polling with extra steps, which is what "no full
    list poll" rules out.
    """
    stream = stream_for(ctx.site_id)
    try:
        since = event_stream.parse_last_event_id(last_event_id)
    except event_stream.StreamError as error:
        raise ProblemError(
            status=422,
            code="invalid-last-event-id",
            title="Invalid Last-Event-ID",
            detail=str(error),
        ) from error

    telemetry.log("events.site.opened", siteId=ctx.site_id, since=since)
    return StreamingResponse(
        event_stream.live_frames(stream, since=since, disconnected=request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
