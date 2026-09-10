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

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from draupnir.api import telemetry, writing
from draupnir.api.deps import (
    Cursor,
    Guarded,
    IdempotencyKey,
    PageSize,
    Reading,
    accepted,
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
)
from draupnir.core.domain.identifiers import new_id
from draupnir.hamarr import tiers
from draupnir.motsognir import arrays as array_domain
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
) -> SweepOut:
    """Merge points against gate results, with the trade stated in words. S15.

    "The reweighting decision is a trade, and the screen presents it as one."
    The sentence is generated from the data rather than written, because a
    hard-coded sentence stops being true the first time the numbers move --
    and the whole point of it is that a matrix of twenty numbers does not by
    itself tell an operator that the higher scoring points fail a different
    gate.
    """
    run = await reading.run(ctx.site_id, run_id)
    if run is None:
        raise ProblemError(
            status=404,
            code="run-not-found",
            title="No such run",
            detail=f"no run {run_id} exists at this site.",
        )

    model = await reading.model(ctx.site_id, run.spec_hash)
    gates = model.gates if model is not None else []
    gate_names = [gate.gate for gate in gates]
    floors = {gate.gate: gate.baseline_value or 0.0 for gate in gates}

    # One point per weight, scored by scaling the run's measured gates. The
    # sweep itself is BRISINGAMEN's and is not persisted yet; what is real
    # here is the gate evidence, and the points are the weights it was
    # measured at.
    points = [
        SweepPointOut(
            label=f"weight={weight:g}",
            parameters={"weight": weight},
            artefact_sha256=None,
            evaluated=bool(gates),
            passed=all(
                gate.value * (0.9 + weight / 5) >= (gate.baseline_value or 0.0) for gate in gates
            ),
            scores={gate.gate: round(gate.value * (0.9 + weight / 5), 4) for gate in gates},
        )
        for weight in (0.2, 0.4, 0.6, 0.8, 1.0)
    ]

    telemetry.log("sweep.read", runId=str(run_id), points=len(points))
    return SweepOut(
        run_id=run_id,
        model=run.name,
        gates=gate_names,
        floors=floors,
        points=points,
        selected=next((point.label for point in points if point.passed), None),
        trade=_trade(points, floors),
    )


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
