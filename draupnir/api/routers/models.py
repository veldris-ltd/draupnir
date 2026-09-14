"""The model registry and the command palette's search. SAD 8.1, viewer.

`GET /v1/models` is where the audit journey starts: an auditor selects a
release before walking its lineage, and AC-U1 measures that walk at three
interactions or fewer, so the registry has to carry enough to choose from
without a second round trip. It therefore returns the artefact digest -- which
is the lineage key -- alongside the release state, the anchor state and the
sole approver exception.

That last field is on the list rather than buried in a detail view on purpose.
SAD 9.4 makes a sole approver a disclosed fact rather than a blocked action,
and a disclosure that requires two clicks to find is a disclosure in name only.

`GET /v1/search` backs the command palette. It is scoped to the current site
like every other read: a palette that searched across sites would be the
unscoped aggregate view AC-U11 forbids, arriving through the one control that
is on every screen.
"""

from __future__ import annotations

import hashlib
import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Response, status

from draupnir.api import release_documents, telemetry, writing
from draupnir.api.concurrency import ConcurrencyError, etag, require
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
from draupnir.api.schemas import (
    Accepted,
    ArrayOut,
    ArraySubmission,
    ModelDetailOut,
    ModelPage,
    ReleasePackageOut,
    SearchPage,
    SweepOut,
    SweepPointOut,
    SweepSelectionIn,
)
from draupnir.brisingamen import sweep as sweeps
from draupnir.core.application.orchestrator import UnknownRunError
from draupnir.core.domain.identifiers import new_id
from draupnir.core.domain.states import RunState
from draupnir.hamarr import tiers
from draupnir.motsognir import arrays as array_domain
from draupnir.skidbladnir.article53 import Article53Error
from draupnir.svalinn.roles import Permission

router = APIRouter(tags=["models"])


@router.get(
    "/models",
    summary="The model registry",
    operation_id="listModels",
    response_model=ModelPage,
)
@needs(Permission.READ)
async def list_models(
    ctx: Guarded, reading: Reading, limit: PageSize, cursor: Cursor = None
) -> ModelPage:
    """Models at the scoped site, released and unreleased."""
    page = await reading.models(ctx.site_id, limit=limit, cursor=cursor)
    telemetry.log("models.listed", count=len(page.items))
    return page


@router.get(
    "/search",
    summary="Search runs, sources and ledger entries",
    operation_id="search",
    response_model=SearchPage,
)
@needs(Permission.READ)
async def search(
    ctx: Guarded,
    reading: Reading,
    limit: PageSize,
    q: Annotated[str, Query(min_length=1, max_length=200, description="What to search for.")],
) -> SearchPage:
    """What the command palette queries. Scoped to this site (AC-U11)."""
    page = await reading.search(ctx.site_id, q, limit=limit)
    telemetry.log("search.performed", hits=len(page.items))
    return page


Artefact = Annotated[
    str,
    Path(pattern="^[0-9a-f]{64}$", description="The artefact's SHA-256."),
]


@router.get(
    "/models/{artefact}",
    summary="One model, its artefacts and its gate results",
    operation_id="getModel",
    response_model=ModelDetailOut,
)
@needs(Permission.READ)
async def get_model(artefact: Artefact, ctx: Guarded, reading: Reading) -> ModelDetailOut:
    """A model's artefacts and evidence. S14.

    Every artefact the producing run made, not only the one asked for: a
    quantised build and the adapter it came from are the same model in two
    forms, and an operator comparing them should not have to find the second
    one by guessing its digest.
    """
    found = await reading.model(ctx.site_id, artefact)
    if found is not None:
        return found
    raise ProblemError(
        status=404,
        code="artefact-not-found",
        title="No such model",
        detail=(
            f"no artefact {artefact[:12]} is registered at this site. Reads are scoped by "
            "row level security, so a model at another forge is not visible here (AC-B10)."
        ),
    )


@router.get(
    "/releases/{artefact}",
    summary="The release package",
    operation_id="getRelease",
    response_model=ReleasePackageOut,
)
@needs(Permission.READ)
async def get_release(artefact: Artefact, ctx: Guarded, reading: Reading) -> ReleasePackageOut:
    """Card, SBOM, manifest and the two Article 53 artefacts. S17, SAD 9A.

    The sole approver exception travels with the package. SAD 9.4 makes it a
    disclosed fact about the release, and a package that carried the signature
    without the exception would be a package that concealed how it was signed.
    """
    found = await reading.release(ctx.site_id, artefact)
    if found is not None:
        return found
    raise ProblemError(
        status=404,
        code="release-not-found",
        title="No such release",
        detail=(
            f"artefact {artefact[:12]} has no release record at this site. An artefact that "
            "exists and is unreleased is not an error: read it at /v1/models/{artefact}."
        ),
    )


#: What may appear in a download's file name. A model name is ours, but a
#: `Content-Disposition` header is parsed by every browser differently, and the
#: safe set is the one no browser disagrees about.
_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


@router.get(
    "/releases/{artefact}/documents/{document}",
    summary="Download one document of the release package",
    operation_id="downloadReleaseDocument",
    response_class=Response,
    responses={
        200: {
            "description": "The document, generated from the release record.",
            "content": {item.media_type: {} for item in release_documents.DOCUMENTS.values()},
        }
    },
)
@needs(Permission.READ)
async def download_release_document(
    artefact: Artefact,
    document: Annotated[
        release_documents.DocumentName, Path(description="Which document of the package.")
    ],
    ctx: Guarded,
    reading: Reading,
) -> Response:
    """One document of the package, generated from the record. S17, RF-27.

    S17's primary action, which had no operation: the release record held five
    addresses and nothing served what was at them. Each document is generated
    by the module that owns it and dated by the release, so the same download
    produces the same bytes -- and the `ETag` is their SHA-256, which is what a
    downloaded document is checked against.
    """
    found = await reading.release(ctx.site_id, artefact)
    if found is None:
        raise ProblemError(
            status=404,
            code="release-not-found",
            title="No such release",
            detail=(
                f"artefact {artefact[:12]} has no release record at this site, so it has no "
                "package to download."
            ),
        )
    chain = await reading.lineage(ctx.site_id, artefact)
    if chain is None:
        raise ProblemError(
            status=404,
            code="artefact-not-found",
            title="No such artefact",
            detail=f"no artefact {artefact[:12]} is registered at this site.",
        )
    model = await reading.model(ctx.site_id, artefact)

    try:
        content = release_documents.render(
            document, release=found, lineage=chain, model=model, site_id=ctx.site_id
        )
    except release_documents.UnissuedReleaseError as unissued:
        raise ProblemError(
            status=409,
            code="release-unpublished",
            title="This release has not been published",
            detail=str(unissued),
        ) from unissued
    except Article53Error as empty:
        raise ProblemError(
            status=409,
            code="training-content-unrecorded",
            title="The licence register holds no source for this release",
            detail=str(empty),
        ) from empty

    served = release_documents.DOCUMENTS[document]
    digest = hashlib.sha256(content).hexdigest()
    filename = _UNSAFE_IN_FILENAME.sub("-", f"{found.model}-{served.filename}")
    telemetry.log("release.document.downloaded", artefactSha256=artefact, document=document)
    return Response(
        content=content,
        media_type=served.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "ETag": f'"{digest}"',
        },
    )


@router.get(
    "/arrays",
    summary="The adapter array and its element states",
    operation_id="getArray",
    response_model=ArrayOut,
)
@needs(Permission.READ)
async def get_array(ctx: Guarded, reading: Reading, limit: PageSize) -> ArrayOut:
    """The fifty-six element adapter array. S12, SAD 5.2 MOTSOGNIR.

    Read from the array the chain records (RF-13). This was built from the runs
    at the site: they were listed, sorted and numbered `0..n`, so `size` was the
    number of runs rather than fifty-six, `attempts` was `max(1, 4 -
    retry_budget)` -- a formula rather than a count -- and a site with sixty
    runs from other work reported a sixty-element array. An array that had been
    submitted and had not started reported size zero.

    A site that has submitted no array gets an empty one that says so, rather
    than one derived from whatever runs happen to exist. That is the answer the
    old handler could not give, because it always had a number.
    """
    del limit
    found = await reading.array(ctx.site_id)
    if found is None:
        telemetry.log("array.read", size=0, submitted=False)
        return ArrayOut(
            name="no array submitted",
            size=0,
            elements=[],
            summary={},
        )

    telemetry.log("array.read", size=found.size, submitted=True, slurmArray=found.slurm_array)
    return found


@router.post(
    "/arrays",
    summary="Submit an array over many subjects as one scheduler array",
    operation_id="submitArray",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Accepted,
)
@needs(Permission.SUBMIT_RUN)
async def submit_array(
    body: ArraySubmission, ctx: Guarded, idempotency_key: IdempotencyKey = None
) -> Accepted:
    """Accept an array submission. Returns 202. RF-13.

    Recorded here and submitted by the worker, like every other piece of work
    the API accepts: SAD 5.1 puts the scheduler behind the worker, and a
    handler that submitted would be an HTTP request blocking on the scheduler
    (AC-B9).

    The subjects default to the whole programme, because that is what CIM-56
    is: fifty-six jurisdictions, one element each. They are validated against
    the tier table rather than taken as given -- an array over a jurisdiction
    outside the programme would train a fifty-seventh model, and `tiers.tier_of`
    never guesses (RF-11).
    """
    key = require_idempotency_key(idempotency_key)
    replayed = replay_or_reserve(key, ctx, body.model_dump(mode="json"))
    if replayed is not None and replayed.body:
        return Accepted.model_validate(replayed.body)

    subjects = list(body.subjects) if body.subjects else list(tiers.ALL)
    unknown = [item for item in subjects if item not in set(tiers.ALL)]
    if unknown:
        release(key, ctx)
        raise ProblemError(
            status=422,
            code="jurisdiction-unassigned",
            title="An array element names a jurisdiction outside the programme",
            detail=(
                f"{', '.join(sorted(unknown))} is not a CIM-56 jurisdiction. The "
                f"programme covers {len(tiers.ALL)} Commonwealth member states; there "
                "is no default tier, and an element for a jurisdiction nobody assigned "
                "would train a model nobody asked for."
            ),
        )

    run_id = new_id()
    with telemetry.span("arrays.submit", telemetry.EDGE, size=len(subjects)):
        await writing.writer().record(
            site_id=ctx.site_id,
            actor=ctx.actor,
            subject_type=array_domain.ARRAY_SUBJECT,
            subject_id=body.name,
            transition=array_domain.ARRAY_ACCEPTED,
            payload={
                "name": body.name,
                "subjects": subjects,
                "retryBudget": body.retry_budget,
                "run_id": str(run_id),
            },
        )
        telemetry.log("array.accepted", name=body.name, size=len(subjects))

    result = accepted(run_id, detail=f"submitting {len(subjects)} elements as one array")
    complete(key, ctx, status=status.HTTP_202_ACCEPTED, body=result)
    return Accepted.model_validate(result)


@router.post(
    "/arrays/{name}/elements/{index}/requeue",
    summary="Resubmit one element of an array, leaving the others untouched",
    operation_id="requeueArrayElement",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Accepted,
)
@needs(Permission.SUBMIT_RUN)
async def requeue_element(
    name: Annotated[str, Path(min_length=1, max_length=64)],
    index: Annotated[int, Path(ge=0)],
    ctx: Guarded,
    idempotency_key: IdempotencyKey = None,
) -> Accepted:
    """Requeue one element. S12's primary action, and AC-F6.

    One element, never the array. Slurm restarts every element of a resubmitted
    array, discarding the compute of the ones that succeeded -- for fifty-six
    elements against three appliances, most of a week. So this records a
    request for `--array=<index>` and the worker submits exactly that.

    S12 named this as the screen's primary action and there was no operation
    behind it (RF-13).
    """
    key = require_idempotency_key(idempotency_key)
    replayed = replay_or_reserve(key, ctx, {"name": name, "index": index, "action": "requeue"})
    if replayed is not None and replayed.body:
        return Accepted.model_validate(replayed.body)

    run_id = new_id()
    with telemetry.span("arrays.requeue", telemetry.EDGE, index=index):
        await writing.writer().record(
            site_id=ctx.site_id,
            actor=ctx.actor,
            subject_type=array_domain.ARRAY_SUBJECT,
            subject_id=name,
            transition=array_domain.ELEMENT_REQUEUE_ACCEPTED,
            payload={"name": name, "index": index, "run_id": str(run_id)},
        )
        telemetry.log("array.requeue.accepted", name=name, index=index)

    result = accepted(run_id, detail=f"resubmitting element {index} of {name}")
    complete(key, ctx, status=status.HTTP_202_ACCEPTED, body=result)
    return Accepted.model_validate(result)


#: How a run state reads as an array element state. Distinct vocabularies on
#: purpose (SAD 5.2): an element that failed within its retry budget is neither
#: running nor finished, and collapsing the two loses the budget.
_ELEMENT_STATE = {
    "DRAFT": "PENDING",
    "CORPUS_REGISTERED": "PENDING",
    "LICENCE_CLEARED": "PENDING",
    "CURATED": "PENDING",
    "QUEUED": "PENDING",
    "TRAINING": "RUNNING",
    "EVALUATING": "RUNNING",
    "TRAINED": "COMPLETED",
    "MERGED": "COMPLETED",
    "EVALUATED": "COMPLETED",
    "QUANTISED": "COMPLETED",
    "AWAITING_APPROVAL": "COMPLETED",
    "RELEASED": "COMPLETED",
    "FAILED": "AWAITING_RETRY",
    "QUARANTINED": "EXHAUSTED",
    "CANCELLED": "CANCELLED",
}


def _element_state(run_state: str) -> str:
    return _ELEMENT_STATE.get(run_state, "PENDING")


@router.get(
    "/sweeps/{run_id}",
    summary="A reweighting sweep, as merge points against gates",
    operation_id="getSweep",
    response_model=SweepOut,
)
@needs(Permission.READ)
async def get_sweep(
    run_id: Annotated[UUID, Path(description="The merge run.")],
    ctx: Guarded,
    reading: Reading,
    response: Response,
) -> SweepOut:
    """Merge points against gate results, with the trade stated in words. S15.

    "The reweighting decision is a trade, and the screen presents it as one."
    The sentence is generated from the data rather than written, because a
    hard-coded sentence stops being true the first time the numbers move --
    and the whole point of it is that a matrix of twenty numbers does not by
    itself tell an operator that the higher scoring points fail a different
    gate.

    The numbers are the sweep's own (RF-27). This used to invent five points by
    scaling one run's gate values and call the first that passed "selected", so
    the screen compared numbers nobody measured and reported a choice nobody
    made. A run whose sweep has not been evaluated has no points, and says so.
    """
    run = await reading.run(ctx.site_id, run_id)
    if run is None:
        raise ProblemError(
            status=404,
            code="run-not-found",
            title="No such run",
            detail=f"no run {run_id} exists at this site.",
        )

    out = _sweep_out(run_id, run.name, await reading.sweep(ctx.site_id, run_id))
    response.headers["ETag"] = out.etag
    telemetry.log("sweep.read", runId=str(run_id), points=len(out.points))
    return out


def _sweep_out(run_id: UUID, model: str, recorded: sweeps.Sweep | None) -> SweepOut:
    """A folded sweep, as S15 shows it. Shared by the read and by the selection."""
    tag = etag(sweeps.version(recorded))
    if recorded is None:
        return SweepOut(
            run_id=run_id,
            model=model,
            gates=[],
            floors={},
            points=[],
            evaluated=False,
            etag=tag,
            trade=(
                "No sweep has been evaluated for this run. The worker merges and re-gates "
                "every point once the run reaches MERGED; until then there are no results to "
                "compare, and none are estimated."
            ),
        )

    gates = list(recorded.gates())
    floors: dict[str, float] = {}
    for point in recorded.points:
        for outcome in point.evidence.outcomes if point.evidence else ():
            if outcome.baseline_value is not None:
                floors.setdefault(outcome.gate, outcome.baseline_value)
    points = [
        SweepPointOut(
            label=point.label,
            parameters=dict(point.parameters),
            artefact_sha256=point.artefact_sha256,
            evaluated=point.evaluated,
            passed=point.passed,
            scores={gate: score for gate in gates if (score := point.score(gate)) is not None},
        )
        for point in recorded.points
    ]
    chosen = recorded.selected_point
    return SweepOut(
        run_id=run_id,
        model=model,
        gates=gates,
        floors=floors,
        points=points,
        selected=chosen.label if chosen else None,
        selected_parameters=dict(chosen.parameters) if chosen else None,
        evaluated=True,
        etag=tag,
        trade=_trade(points, floors),
    )


@router.post(
    "/sweeps/{run_id}/select",
    summary="Choose the merge point a run is quantised from",
    operation_id="selectMergePoint",
    response_model=SweepOut,
)
@needs(Permission.SELECT_MERGE_POINT)
async def select_merge_point(
    run_id: Annotated[UUID, Path(description="The merge run.")],
    body: SweepSelectionIn,
    ctx: Guarded,
    response: Response,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> SweepOut:
    """Choose one evaluated point. S15's primary action, RF-27; AC-F8.

    BRISINGAMEN ran the sweep and RAUN decided which points are acceptable, so
    a point that failed a blocking gate, or was never evaluated, is refused
    here by `Sweep.select` rather than by a rule written again. The choice is
    recorded against the run and the worker quantises that point's bytes; the
    whole comparison it was chosen from goes on the model card.

    Conditional on the sweep's version, so an operator who chose from the
    matrix before somebody else chose, or before a re-evaluation, is refused
    with 412 rather than choosing from numbers that are no longer the record.
    """
    key = require_idempotency_key(idempotency_key)
    replayed = replay_or_reserve(
        key, ctx, {"runId": str(run_id), "action": "select", **body.model_dump(mode="json")}
    )
    if replayed is not None and replayed.body:
        return SweepOut.model_validate(replayed.body)

    recorder = writing.writer()
    try:
        facts = await recorder.read(
            site_id=ctx.site_id, actor=ctx.actor, question=writing.facts_of(run_id)
        )
    except UnknownRunError as unknown:
        release(key, ctx)
        raise ProblemError(
            status=404,
            code="run-not-found",
            title="No such run",
            detail=f"no run {run_id} exists at this site.",
        ) from unknown
    history = await recorder.read(
        site_id=ctx.site_id, actor=ctx.actor, question=writing.history_of(run_id)
    )
    recorded = sweeps.fold(history or ())

    if recorded is None:
        release(key, ctx)
        raise ProblemError(
            status=409,
            code="sweep-not-evaluated",
            title="This run's sweep has not been evaluated",
            detail=(
                f"run {run_id} has no evaluated merge sweep, so there is no point to choose. "
                "The worker merges and re-gates every point once the run reaches MERGED."
            ),
        )

    try:
        require(f"the sweep of run {run_id}", sweeps.version(recorded), if_match)
    except ConcurrencyError as error:
        release(key, ctx)
        raise as_problem(error) from error

    if facts is not None and facts.state is not RunState.MERGED:
        release(key, ctx)
        raise ProblemError(
            status=409,
            code="run-not-awaiting-selection",
            title="This run is not waiting for a merge point",
            detail=(
                f"run {run_id} is {facts.state}. A merge point is chosen while the run is "
                f"{RunState.MERGED}, before it is quantised from that point."
            ),
        )
    if recorded.selected is not None:
        release(key, ctx)
        raise ProblemError(
            status=409,
            code="merge-point-already-selected",
            title="A merge point has already been chosen",
            detail=(
                f"run {run_id} already has a chosen merge point. "
                "The choice is recorded once, because what was quantised has to be one point."
            ),
        )

    try:
        chosen_sweep = recorded.select(body.parameters, criterion=body.criterion or "")
    except sweeps.SweepError as refusal:
        release(key, ctx)
        raise ProblemError(
            status=409,
            code="merge-point-not-selectable",
            title="This merge point cannot be chosen",
            detail=str(refusal),
        ) from refusal

    # The point `select` just validated, by the parameters it was chosen with.
    chosen = chosen_sweep.point_for(body.parameters)
    with telemetry.span("sweeps.select", telemetry.EDGE, runId=str(run_id)):
        entry = await recorder.record(
            site_id=ctx.site_id,
            actor=ctx.actor,
            subject_type=sweeps.SWEEP_SUBJECT,
            subject_id=str(run_id),
            transition=sweeps.SELECTED,
            payload={
                "parameters": dict(chosen.parameters),
                "label": chosen.label,
                "configHash": chosen.config_hash(),
                "artefactSha256": chosen.artefact_sha256,
                "criterion": body.criterion,
            },
        )
        telemetry.log(
            "sweep.selected", runId=str(run_id), point=chosen.label, recorded=entry is not None
        )

    out = _sweep_out(run_id, facts.name if facts is not None else str(run_id), chosen_sweep)
    response.headers["ETag"] = out.etag
    complete(key, ctx, status=status.HTTP_200_OK, body=out.model_dump(mode="json", by_alias=True))
    return out


def _trade(points: list[SweepPointOut], floors: dict[str, float]) -> str:
    """State the trade in words, from the data."""
    if not points or not floors:
        return (
            "This run has no gate results yet, so there is no trade to describe. A sweep "
            "without evidence is a set of configurations, not a decision."
        )
    passing = [point for point in points if point.passed]
    if not passing:
        return (
            "No merge point clears every floor. The reweighting cannot be resolved by "
            "choosing between these points; the floors or the corpus have to change."
        )
    best = max(points, key=lambda point: sum(point.scores.values()))
    if best.passed:
        return (
            f"{best.label} scores highest overall and clears every floor. "
            f"{len(passing)} of {len(points)} points do."
        )
    return (
        f"{best.label} scores highest overall but fails at least one floor. The highest "
        f"scoring point that clears every floor is {passing[0].label}, which is the trade: "
        "aggregate score against the gate that would otherwise block release."
    )
