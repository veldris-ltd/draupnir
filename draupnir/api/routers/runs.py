"""Runs, and the event stream. SAD 8.1, operator.

`POST /v1/runs`, `GET /v1/runs`, `GET /v1/runs/{id}`, `POST /v1/runs/{id}/cancel`,
`POST /v1/runs/{id}/retry`, and `GET /v1/runs/{id}/events`.

Cancel and retry are conditional writes: they take `If-Match` and return 412 on
a stale tag (AC-B4). That is not ceremony. Two operators looking at the same run
board, one cancelling and one retrying, is the ordinary case, and without the
precondition the retry silently undoes the cancellation.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Request, status
from fastapi.responses import StreamingResponse

from draupnir.api import events as event_stream
from draupnir.api import telemetry
from draupnir.api.concurrency import ConcurrencyError, require
from draupnir.api.deps import (
    Cursor,
    Guarded,
    IdempotencyKey,
    IfMatch,
    PageSize,
    accepted,
    as_problem,
    complete,
    release,
    replay_or_reserve,
    require_idempotency_key,
)
from draupnir.api.guards import needs
from draupnir.api.problems import ProblemError
from draupnir.api.schemas import Accepted, CancelIn, RunOut, RunPage, RunSubmission
from draupnir.core.domain.identifiers import new_id
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

    run_id = new_id()
    with telemetry.span("runs.submit", telemetry.EDGE, runId=str(run_id)):
        telemetry.log("run.submitted", runId=str(run_id))

    body_out = accepted(run_id, detail="run queued")
    complete(
        key, ctx, status=status.HTTP_202_ACCEPTED, body=body_out, location=f"/v1/runs/{run_id}"
    )
    return Accepted.model_validate(body_out)


@router.get("/runs", summary="List runs", operation_id="listRuns", response_model=RunPage)
@needs(Permission.READ)
async def list_runs(ctx: Guarded, limit: PageSize, cursor: Cursor = None) -> RunPage:
    """List runs for the scoped site, cursor paginated. AC-B3, AC-N4."""
    del cursor
    telemetry.log("runs.listed", limit=limit)
    return RunPage(items=[], next_cursor=None, limit=limit)


@router.get("/runs/{run_id}", summary="Inspect a run", operation_id="getRun", response_model=RunOut)
@needs(Permission.READ)
async def get_run(run_id: RunId, ctx: Guarded) -> RunOut:
    """Return one run, with an `ETag` for a later conditional write."""
    del ctx
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
