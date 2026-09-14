"""Runs, and the event stream. SAD 8.1, operator.

`POST /v1/runs`, `GET /v1/runs`, `GET /v1/runs/{id}`, `POST /v1/runs/{id}/cancel`,
`POST /v1/runs/{id}/retry`, and `GET /v1/runs/{id}/events`.

Cancel and retry are conditional writes: they take `If-Match` and return 412 on
a stale tag (AC-B4). That is not ceremony. Two operators looking at the same run
board, one cancelling and one retrying, is the ordinary case, and without the
precondition the retry silently undoes the cancellation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path as Path_
from tempfile import TemporaryDirectory
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from draupnir.api import events as event_stream
from draupnir.api import telemetry, writing
from draupnir.api.concurrency import ConcurrencyError, require, run_version
from draupnir.api.context import RequestContext
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
from draupnir.core.application.orchestrator import (
    DuplicateRunError,
    OrchestrationError,
    RunFacts,
    UnknownRunError,
)
from draupnir.core.domain.identifiers import new_id
from draupnir.core.domain.identity import InputHashError, RunIdentity, run_identity
from draupnir.core.domain.states import (
    GuardRefusedError,
    IllegalTransitionError,
    RunState,
)
from draupnir.core.infrastructure.config import get_settings
from draupnir.core.plugins import PluginError
from draupnir.hamarr import checkpoints, tiers
from draupnir.hamarr import config as hamarr_config
from draupnir.interfaces.types import RunSpec
from draupnir.motsognir.placement import estate_for
from draupnir.svalinn.roles import Permission

router = APIRouter(tags=["runs"])

#: What a cancelled scheduler job exits with. 143 is SIGTERM, which is what
#: `scancel` sends and what the executor sees. Naming it means the ledger entry
#: for a cancellation is indistinguishable from the truth rather than from a
#: placeholder.
CANCELLED_EXIT_CODE = 143

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

    Validated through `admit`, which is the same door `dryRunSpecification`
    uses (RF-11). This said "validate" and checked only that the specification
    was not empty: one the dry run refused with 422 was accepted here with 202
    and failed later, having consumed a run identifier, a ledger entry and a
    place in the queue.
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

    try:
        admitted = admit(body.specification, site_id=ctx.site_id)
    except ProblemError:
        # The reservation is released before the refusal propagates, so a
        # client that corrects its specification and retries with the same
        # idempotency key is not told it has already submitted something.
        release(key, ctx)
        raise

    # The *settled* specification, not what was posted. `prepare` fills in the
    # checkpoint interval HAMARR derives from the run's time budget, and the
    # identity is computed over the form that will actually be rendered -- so
    # the chain's `spec_hash` is the hash of the specification the chain holds,
    # and the worker renders exactly what was hashed (RF-10).
    specification = admitted.spec.as_mapping()
    identity = _identity_for(admitted.spec, on_error=lambda: release(key, ctx))

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
                payload={
                    "input_artefact_sha256": list(identity.input_artefact_sha256),
                    # Recorded at registration, so a later requeue reads the
                    # budget the specification allowed rather than one supplied
                    # by whoever is asking for the requeue.
                    "retry_budget": _retry_budget(body),
                    # Why the checkpoint interval is what it is, so that a
                    # reader of the chain can tell an authored interval from a
                    # derived one without re-deriving it (RF-11).
                    "checkpoint_policy": admitted.policy.as_payload,
                    # The specification itself. RF-10.
                    #
                    # SAD 6.2 calls the specification the unit of reproduction
                    # and the chain recorded only its *hash* -- so a run could
                    # be checked for having been tampered with and could not be
                    # reproduced, and the worker had nothing to render a job
                    # from. It dispatched a stand-in for every run instead,
                    # while `POST /v1/runs/dry-run` showed the operator the real
                    # driver's command.
                    #
                    # The normalised form rather than what was posted: it is the
                    # canonical form `spec_hash` is computed over, so the chain
                    # is self-verifying -- read it back, hash it, and it equals
                    # the `spec_hash` recorded beside it.
                    "specification": specification,
                },
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
    # The board learns about this run from the stream, and the stream learns
    # about it from the chain (RF-15). This used to publish here, which was the
    # only publisher in the system: one event kind, from one handler, in one
    # process, with a per-process sequence number. Every other change -- every
    # transition the worker performs -- reached nobody, and a console attached
    # to a second API process saw nothing at all.
    #
    # The registration above appends to the ledger, the append notifies, and
    # every process's listener turns that into a delta carrying the ledger's
    # own sequence. Publishing here as well would put a second event under a
    # different number for one change.
    complete(
        key, ctx, status=status.HTTP_202_ACCEPTED, body=body_out, location=f"/v1/runs/{run_id}"
    )
    return Accepted.model_validate(body_out)


def _retry_budget(body: RunSubmission) -> int:
    """The specification's `placement.retryBudget`, or none."""
    spec = body.specification.get("spec")
    placement = spec.get("placement") if isinstance(spec, dict) else None
    if not isinstance(placement, dict):
        return 0
    try:
        return max(0, int(placement.get("retryBudget", 0)))
    except (TypeError, ValueError):
        return 0


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


@dataclass(frozen=True, slots=True)
class Admitted:
    """A specification the estate is willing to act on, and what will act on it."""

    #: The specification as settled: the tier checked, the base checked against
    #: it, and the checkpoint interval derived. This is what is rendered and
    #: what is recorded, so that what the operator was shown, what the chain
    #: holds and what the worker runs are one thing.
    spec: RunSpec
    #: The driver that will render it, resolved through the production registry.
    plugin: Any
    #: The checkpoint policy, and why it says what it does. `as_payload` on it
    #: is a property rather than a method.
    policy: checkpoints.Policy


def admit(specification: Mapping[str, Any], *, site_id: str, group: str = "") -> Admitted:
    """Settle and validate a specification, or refuse it. RF-11.

    One helper, called by `dryRunSpecification` and by `submitRun`, because a
    specification the dry run accepts and the submission refuses is as bad as
    the reverse -- and two implementations of "is this runnable" agree only on
    the day they are written. `submitRun` said "Validate, hash into a run
    identity, and queue" and its only check was that the specification was not
    empty: one the dry run refused with 422 was accepted with 202, and failed
    later having consumed a run identifier, a ledger entry and a place in the
    queue.

    Four refusals, in the order that makes each of them legible:

    1. **The specification cannot be read.** A structural problem, reported as
       one before anything tries to interpret it.
    2. **The tier table has drifted.** AC-F16's enumeration, checked here
       rather than at import: a module that refuses to import is a control
       plane that will not start, and the right response to a jurisdiction list
       that no longer enumerates CIM-56 is to refuse the run that depends on
       it, loudly, with the problem named. A table that had drifted would
       assign the wrong base, and the failure would be silent.
    3. **The jurisdiction is not in the programme.** `tiers.tier_of` never
       guesses: a jurisdiction nobody assigned is not a Tier B jurisdiction by
       default, and treating it as one would silently train a fifty-seventh
       model.
    4. **The specification contradicts the tier** -- a declared tier that is
       not the assignment, or a base that is not the tier's. Separate from the
       above because the operator's next action differs: one is a jurisdiction
       that does not belong here at all, and the other is two fields in a
       specification that disagree.
    5. **No installed driver can render it.**
    6. **The driver refuses it**, in the driver's own words and all of them at
       once: an operator fixing a specification should need one round trip and
       not five.
    """
    try:
        spec = RunSpec.from_mapping(specification)
    except (ValueError, KeyError, TypeError) as error:
        raise ProblemError(
            status=422,
            code="specification-invalid",
            title="The run specification could not be read",
            detail=str(error),
        ) from error

    # AC-F16, at submission. `prepare` checks this too and would raise the same
    # way; it is called here first so that a drifted table is reported as a
    # deployment problem rather than as something wrong with the specification
    # in front of the operator.
    try:
        tiers.validate()
    except tiers.TierError as drifted:
        raise ProblemError(
            status=503,
            code="tier-table-invalid",
            title="The jurisdiction table does not enumerate the programme",
            detail=(
                f"{drifted} Nothing was validated and nothing was recorded; this is a "
                "deployment fault rather than a refusal of the specification."
            ),
        ) from drifted

    try:
        tiers.tier_of(spec.metadata.jurisdiction)
    except tiers.TierError as unassigned:
        raise ProblemError(
            status=422,
            code="jurisdiction-unassigned",
            title="This jurisdiction is not in the programme",
            detail=str(unassigned),
        ) from unassigned

    # Settled before validated. `save_steps` is derived from the run's time
    # budget by HAMARR and a driver refuses to invent one -- correctly, since a
    # wrong checkpoint interval costs a run its resumability -- so validating
    # first would refuse every specification that had not been prepared, which
    # is every specification an operator writes.
    try:
        prepared, policy = hamarr_config.prepare(spec, site=site_id)
    except (tiers.TierError, hamarr_config.ConfigurationError) as refusal:
        raise ProblemError(
            status=422,
            code="specification-rejected",
            title="This specification contradicts the tier its jurisdiction is in",
            detail=str(refusal),
        ) from refusal

    merging = prepared.kind.lower() == "mergerun"
    chosen = group or ("draupnir.merge" if merging else "draupnir.train")

    # Discovery and resolution are separate failures with separate answers.
    # A trust store that is not a directory, or a signature manifest that
    # cannot be read, is a deployment fault: nothing is wrong with the
    # specification, retrying will work once it is fixed, and 503 is what says
    # so. Reporting it as 422 would send an operator to edit a specification
    # that is fine (RF-11, and RF-02's refusal is the one being surfaced).
    try:
        registry = plugins.registry()
    except PluginError:
        raise
    except Exception as unavailable:
        raise ProblemError(
            status=503,
            code="plugins-unavailable",
            title="The driver registry could not be built",
            detail=(
                f"{unavailable} Nothing was validated and nothing was recorded; this is "
                "a deployment fault rather than a refusal of the specification."
            ),
        ) from unavailable

    try:
        plugin = registry.for_spec(prepared, chosen)
    except PluginError as error:
        raise ProblemError(
            status=422,
            code="driver-unavailable",
            title="No installed driver can render this specification",
            detail=str(error),
        ) from error

    problems = list(plugin.driver.validate(prepared))
    if problems:
        raise ProblemError(
            status=422,
            code="specification-rejected",
            title="The driver refused this specification",
            detail=" ".join(f"{problem.field}: {problem.message}" for problem in problems),
        )

    return Admitted(spec=prepared, plugin=plugin, policy=policy)


def _identity_of(body: RunSubmission, *, on_error: Callable[[], None]) -> RunIdentity:
    """Compute the run identity of a submission, or refuse it.

    Kept for callers that hold a body rather than a settled specification. The
    submission path goes through `admit` first and calls `_identity_for` with
    what came back, because the identity must be over the form that is recorded
    and rendered.
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
    return _identity_for(spec, on_error=on_error)


def _identity_for(spec: RunSpec, *, on_error: Callable[[], None]) -> RunIdentity:
    """Compute the run identity of a settled specification, or refuse it.

    AC-F1 requires the CLI and the console to arrive at the same identity for
    the same specification, and they do so by construction: both post here, and
    the identity is computed here rather than by either client. A client that
    computed it would be a second implementation of the rule, and two
    implementations of a hash is one too many.
    """
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
        response.headers["ETag"] = found.etag
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

    recorder = writing.writer()
    facts = await _facts(recorder, ctx, run_id, on_error=lambda: release(key, ctx))
    _require_current(run_id, facts, if_match, on_error=lambda: release(key, ctx))

    with telemetry.span("runs.cancel", telemetry.EDGE, runId=str(run_id)):
        try:
            applied = await recorder.transition_run(
                site_id=ctx.site_id,
                actor=ctx.actor,
                run_id=run_id,
                target=RunState.FAILED,
                # A cancelled scheduler job exits non-zero, which is exactly the
                # guard SAD 6.1 puts on TRAINING -> FAILED. Nothing here invents
                # a state: the run reaches the one the table already has for an
                # executor that stopped without finishing (AC-F13).
                facts={"exit_code": CANCELLED_EXIT_CODE, "watchdog_fired": False},
                payload={
                    "exit_code": CANCELLED_EXIT_CODE,
                    "last_log_lines": [f"cancelled by {ctx.actor}: {body.reason}"],
                    "resource_state": {"allocation": "released"},
                    "cancelled_by": ctx.actor,
                    "reason": body.reason,
                },
            )
        except (IllegalTransitionError, GuardRefusedError) as refusal:
            release(key, ctx)
            raise _not_cancellable(run_id, facts, refusal) from refusal
        except UnknownRunError as unknown:
            release(key, ctx)
            raise _no_such_run(run_id, ctx.site_id, unknown) from unknown

        telemetry.log(
            "run.cancel.accepted",
            runId=str(run_id),
            reason=body.reason,
            recorded=applied is not None,
        )

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

    recorder = writing.writer()
    facts = await _facts(recorder, ctx, run_id, on_error=lambda: release(key, ctx))
    _require_current(run_id, facts, if_match, on_error=lambda: release(key, ctx))

    with telemetry.span("runs.retry", telemetry.EDGE, runId=str(run_id)):
        failing = facts.failing_gates if facts else ()
        remaining = facts.budget_remaining if facts else 0
        if recorder.records and not failing:
            release(key, ctx)
            raise ProblemError(
                status=409,
                code="nothing-to-retry",
                title="This run has no recorded gate failure",
                detail=(
                    f"run {run_id} has no failing gate in its chain. The requeue of "
                    "SAD 6.1 is for a run that failed one within its retry budget "
                    "(AC-F7); a run that failed none has nothing to try again, and "
                    "resubmitting an unchanged specification is a duplicate (AC-F2)."
                ),
            )

        try:
            applied = await recorder.transition_run(
                site_id=ctx.site_id,
                actor=ctx.actor,
                run_id=run_id,
                target=RunState.QUEUED,
                # The budget is read from the registration entry, not from the
                # request: a budget the caller supplied is a budget the caller
                # could raise, one requeue at a time.
                facts={
                    "failing_gates": list(failing),
                    "retry_budget_remaining": remaining,
                },
                payload={
                    "failing_gate": failing[0] if failing else "",
                    "requeue_reason": (
                        f"requeued by {ctx.actor}; {remaining} of "
                        f"{facts.retry_budget if facts else 0} retries remaining"
                    ),
                },
            )
        except (IllegalTransitionError, GuardRefusedError) as refusal:
            release(key, ctx)
            raise _not_retryable(run_id, facts, refusal) from refusal
        except UnknownRunError as unknown:
            release(key, ctx)
            raise _no_such_run(run_id, ctx.site_id, unknown) from unknown

        telemetry.log("run.retry.accepted", runId=str(run_id), recorded=applied is not None)

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
    """Server-sent events for one run, carrying state deltas. RF-15.

    Two things this did not do. It took `run_id` and used it only in a log
    line, so a run's stream carried every other run's events; and it yielded
    the buffered frames plus one keep-alive comment and closed, so it was a
    page of history rather than a stream. A console watching one run saw
    fifty-five other runs' changes and then had its connection closed.

    Now it filters on the run and stays open, the way the site stream does. A
    reconnecting client sends `Last-Event-ID` and receives what it missed; a
    client asking for a point the buffer has dropped is told to resynchronise
    rather than served from the oldest event it happens to still hold, because
    a silent gap leaves the client's state wrong with nothing to detect it.
    """
    stream = stream_for(ctx.site_id)

    try:
        since = event_stream.parse_last_event_id(last_event_id)
        # Read here rather than inside the generator: a `Last-Event-ID` the
        # buffer has moved past has to become a 409 before the response
        # starts, and a stream that has already sent its first byte cannot.
        stream.since(since)
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

    return StreamingResponse(
        event_stream.live_frames(
            stream,
            since=since,
            only_run=run_id,
            disconnected=request.is_disconnected,
        ),
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
    ctx_site = ctx.site_id

    # The same door the submission goes through (RF-11). Parsing, the tier
    # check, the checkpoint interval, the driver and its `validate` are settled
    # in one place, so the two handlers cannot come to different answers about
    # whether a specification is runnable.
    admitted = admit(body.specification, site_id=ctx_site)
    spec, plugin = admitted.spec, admitted.plugin
    identity = _identity_for(spec, on_error=lambda: None)

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

    # The accelerator the estate declares, added to what the driver rendered.
    # A train driver cannot know it -- a specification is portable across the
    # Forge Matrix and must not name one forge's hardware (SAD 6.2) -- so the
    # rendered plan carries a count and the estate supplies the type. Adding it
    # here is what makes AC-F14 true of the whole submission rather than of the
    # driver's half: an operator reading this sees the `--gres` string they
    # will later see in `scontrol show job`.
    #
    # Asked of the estate rather than of `place()`, which can refuse: a dry run
    # exists to cost nothing and to report a specification problem, and a
    # refusal here because an appliance is down would be a different answer to
    # a different question.
    resources = dict(plan.as_mapping()["resources"])
    if not resources.get("gres"):
        estate = estate_for(ctx_site, get_settings().accelerator)
        offered = estate.gres(int(resources.get("gpus_per_node") or 0))
        if offered:
            resources["gres"] = offered

    telemetry.log("run.dry-run", driver=plugin.name, runIdentity=identity.digest)

    return DryRunOut(
        run_identity=identity.digest,
        spec_hash=identity.spec_hash,
        input_artefact_sha256=list(identity.input_artefact_sha256),
        driver=str(plugin.name),
        command=list(plan.command),
        environment=dict(plan.environment),
        resources=resources,
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


async def _facts(
    recorder: writing.Writer,
    ctx: RequestContext,
    run_id: UUID,
    *,
    on_error: Callable[[], None],
) -> RunFacts | None:
    """What the chain knows about a run, or None when nothing is recorded.

    None has two causes and they are different: a writer that records nothing,
    which is the contract-test configuration, and a run this site has never
    heard of. The first is answered by carrying on; the second is a 404, and
    the caller distinguishes them by asking the writer whether it records.
    """
    try:
        found: RunFacts | None = await recorder.read(
            site_id=ctx.site_id, actor=ctx.actor, question=writing.facts_of(run_id)
        )
    except UnknownRunError as unknown:
        on_error()
        raise _no_such_run(run_id, ctx.site_id, unknown) from unknown
    return found


def _require_current(
    run_id: UUID,
    facts: RunFacts | None,
    if_match: str | None,
    *,
    on_error: Callable[[], None],
) -> None:
    """Refuse a write conditional on a run state that has since moved. RF-32.

    After the facts are read, because the tag is computed from them: the
    state and retry count the write would change, which is also what `getRun`
    and the board return. It used to be checked first, over the identifier
    alone, so the tag `getRun` gave a client was refused with 412 and a tag
    nobody read could never be stale.

    A writer that records nothing has no state to be stale against, and the
    tag is the run's with none.
    """
    version = run_version(
        run_id,
        facts.state if facts is not None else None,
        facts.retry_count if facts is not None else 0,
    )
    try:
        require(f"run {run_id}", version, if_match)
    except ConcurrencyError as error:
        on_error()
        raise as_problem(error) from error


def _no_such_run(run_id: UUID, site_id: str, cause: Exception) -> ProblemError:
    """404 rather than 500. A run at another site is not visible here."""
    return ProblemError(
        status=404,
        code="run-not-found",
        title="No such run at this site",
        detail=f"{cause} (site {site_id}, run {run_id})",
    )


def _not_cancellable(run_id: UUID, facts: RunFacts | None, refusal: Exception) -> ProblemError:
    """409, naming the state and the gap it falls into.

    Cancelling stops a scheduler job. A run that is not TRAINING holds no
    allocation, so there is nothing to stop -- and SAD 6.1 has no row for
    withdrawing a queued run, which is a gap in the table rather than in this
    handler. It is recorded in `docs/acceptance/imhotep-reconciliation.md`; the
    refusal names it so an operator is not left guessing.
    """
    where = f" It is in {facts.state}." if facts else ""
    return ProblemError(
        status=409,
        code="run-not-cancellable",
        title="This run has no scheduler job to stop",
        detail=(
            f"run {run_id} cannot be cancelled.{where} Cancelling stops a scheduler "
            f"job, so it applies to a run in {RunState.TRAINING}; SAD 6.1's table has "
            f"no transition out of any other state that a cancellation fits. {refusal}"
        ),
    )


def _not_retryable(run_id: UUID, facts: RunFacts | None, refusal: Exception) -> ProblemError:
    """409, naming the state and whether the budget is what refused it."""
    where = f" It is in {facts.state}." if facts else ""
    budget = f" {facts.budget_remaining} of {facts.retry_budget} retries remain." if facts else ""
    return ProblemError(
        status=409,
        code="run-not-retryable",
        title="This run cannot be requeued",
        detail=(
            f"run {run_id} cannot be requeued.{where}{budget} The requeue of SAD 6.1 "
            f"moves a run from {RunState.EVALUATING} back to {RunState.QUEUED} when a "
            f"gate failed and the budget is not exhausted. {refusal}"
        ),
    )
