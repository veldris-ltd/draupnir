"""What a run in each state needs next, and how to do one thing about it.

The lifecycle of SAD 6.1 is a table, and so is this: one row per state the
worker can act on, each saying what work to place and how to read the result.
Nothing here decides whether a gate passed or where a job may run -- those
belong to GLEIPNIR and MOTSOGNIR, and this calls them.

**The shape of a stage.** Each returns an `Outcome`, which is either "I moved
it", "I placed work and it is running", "there is nothing for me here", or "I
could not, and here is why". The last is deliberately not an exception: a stage
that cannot run this tick is a stage that runs next tick, and a loop that
unwound on every transient failure would stop the estate over a full disk.

**Where the artefacts live.** Each run gets a scratch directory derived from its
identifier, so a restarted worker finds what the one before it wrote. The
scratch tree is a workbench, not a home: what a stage produces is put into
HODD at its `hodd://` address and sealed there before the transition that
records it, and the address goes in the chain beside the digest.

That ordering is the point (RF-08). A transition recorded first and a put
attempted afterwards leaves a chain saying an artefact exists at an address
holding nothing, which is the one failure the whole provenance argument cannot
survive -- a publication would re-hash bytes that are not there, and the
refusal would name the wrong cause. So the vault write happens first and a
vault that will not take it defers the run.

**On the executors.** The plans are placed through the real schedule driver and
run as real processes with real exit codes. What they run is what the run's
specification renders to, through the plug-in registry and the same
`validate`-then-`render` the dry run performs -- so the command an operator was
shown before submitting is the command that is submitted (AC-F14).

That was not true (RF-10). Every dispatch, for every run, built
`execution.stand_in_plan`, which runs a Python one-liner; the specification was
not consulted and neither was the registry, while `POST /v1/runs/dry-run`
rendered the real driver's command and returned it. The console showed a
LLaMA-Factory invocation and the worker ran `sys.executable -c`.

The stand-in remains, behind `DRAUPNIR_WORKER_STAND_IN`, because `make
procedure` runs on a machine with no GPU and no training framework. It logs
`executor.stand-in` at warning level on every use, in the shape
`plugin.unverified` uses: a simulation nobody is told about is the problem, and
a simulation that announces itself is a development tool.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from draupnir.api import assurance
from draupnir.brisingamen.sweep import Sweep, linear
from draupnir.core.application.orchestrator import Orchestrator, RunFacts
from draupnir.core.domain.states import RunState
from draupnir.gleipnir import gates as gleipnir_gates
from draupnir.hodd import quota, stores
from draupnir.interfaces.types import JobHandle, JobPlan, JobState, RunSpec
from draupnir.motsognir import execution
from draupnir.motsognir.placement import Estate, Partition, Placement, PlacementError
from draupnir.motsognir.placement import plan as place
from draupnir.raun import suites as raun_suites
from draupnir.svalinn import integrity

logger = structlog.get_logger(__name__)

#: What the chain records as the executor when the development stand-in ran.
#: A name rather than a flag, so that a reader of a run's history sees it
#: without knowing to look for one -- and so that a query for runs that were
#: simulated is a query rather than an inference.
STAND_IN_EXECUTOR = "development-stand-in"

#: The release formats of SAD 6.2's worked example. What a run is quantised to
#: comes from its specification; this is the default for a run whose
#: specification the chain does not carry.
FORMATS: tuple[str, ...] = ("nvfp4", "gguf-q4km", "mlx4")

#: Where a stage's output goes, by the state it produces. Named so that a
#: restarted worker looks in the same place as the one before it.
ARTEFACTS: Mapping[str, str] = {
    "adapter": "adapter.safetensors",
    "merged": "merged.safetensors",
}


class Result(StrEnum):
    """What a stage did."""

    #: The run moved. The chain has one more entry.
    MOVED = "MOVED"
    #: Work was placed and is running. Nothing moved yet.
    PLACED = "PLACED"
    #: Still running. Asked, and it has not finished.
    WAITING = "WAITING"
    #: Nothing for the worker to do in this state.
    IDLE = "IDLE"
    #: Could not act this tick. Try again next tick.
    DEFERRED = "DEFERRED"


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one stage did to one run, and why."""

    run_id: UUID
    result: Result
    detail: str
    state: RunState | None = None

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, for a log line and for the tick's report."""
        return {
            "runId": str(self.run_id),
            "result": str(self.result),
            "detail": self.detail,
            "state": str(self.state) if self.state else None,
        }


@dataclass
class Context:
    """What a stage is given. One per tick, shared by every run in it."""

    orchestrator: Orchestrator
    scheduler: Any
    scratch: Path
    estate: Estate = field(default_factory=Estate)
    #: Which site's addresses this worker writes. An artefact URI always names
    #: its site (SAD 7.4), so a worker that guessed would write addresses no
    #: other forge could resolve.
    site_id: str = "sindri"
    #: The artefact store. `None` on an installation with no vault configured,
    #: where the stages still run and still record digests but the artefacts
    #: stay in scratch -- so nothing can be released, which is the truthful
    #: consequence and is said out loud in the outcome rather than discovered
    #: at publication.
    store: Any = None
    #: The secrets broker. Held so that every plan this worker submits is
    #: checked for a materialised secret before it reaches a scheduler, and so
    #: that a job's environment carries lease references rather than values
    #: (RF-09, threat T6).
    secrets: Any = None
    #: The leases this run's jobs are started with. Empty at Sindri, which
    #: brokers no secret to a training job today -- and the environment is
    #: built through `brokered_environment` anyway, because the point is that
    #: there is no code path by which a value could be written into one.
    leases: tuple[Any, ...] = ()
    #: The plug-in registry a specification's driver is resolved through. The
    #: same one the dry run uses, which is what makes the plan an operator was
    #: shown the plan that is submitted (RF-10, AC-F14).
    registry: Any = None
    #: Run the development executor instead of the specification's driver.
    #: False everywhere but a machine with no GPU, and loud when true.
    stand_in: bool = False
    #: Which driver rendered the plan most recently placed, for the chain to
    #: record. Set by `_plan_for` and read by the transition that follows it,
    #: rather than threaded through four signatures that would each have to
    #: remember it.
    planned_by: str = ""
    #: What relative gates are compared against, read out of the chain. `None`
    #: on a worker that has not loaded them, which defers a judgement rather
    #: than inventing one -- a run judged against a baseline derived from its
    #: own score passes by construction (RF-10).
    baselines: Any = None

    def confinement(self, workdir: Path, artefacts: Sequence[Path] = ()) -> dict[str, Any]:
        """The sandbox profile this job runs under, rendered for the plan. RF-09.

        Composed here because the composition root is the only place allowed to
        put SVALINN and MOTSOGNIR together: they are siblings in the layering
        and neither imports the other. `sandbox.py` said the plan carries lease
        references and `execution.stand_in_plan` wrote a two-entry environment;
        nothing ever built a profile, so the whole module read as coverage.

        Inputs mount read-only and the working directory is the one writable
        place. That is not a policy this chooses -- `sandbox.for_job` forces it,
        and a writable artefact mount is threat T8 reached from inside.
        """
        from draupnir.svalinn import sandbox, secrets

        return sandbox.for_job(
            workdir=str(workdir),
            artefacts=[(str(item), f"/inputs/{item.name}") for item in artefacts],
            environment=secrets.brokered_environment(self.leases),
        ).as_payload()

    def job_environment(self) -> dict[str, str]:
        """What a job is started with: lease references, never values."""
        from draupnir.svalinn import secrets

        return secrets.brokered_environment(self.leases)

    #: False while the supply is on battery. Dispatch stops; running work does
    #: not (SAD 11.2, last row).
    may_dispatch: bool = True
    #: How long a placed job is given before the worker gives up on it.
    timeout: float = execution.DEFAULT_TIMEOUT_SECONDS

    def workdir(self, run_id: UUID) -> Path:
        """The scratch directory for one run. Derived, never remembered.

        Absolute, always. A plan's output path is passed to a child process
        that runs *in* the working directory, so a relative one resolves twice
        and the job writes to a path nobody looks in -- exits zero, produces
        nothing, and the digest of a file that is not there is the failure.
        Resolving here rather than at the caller means it cannot be forgotten
        at one of them.
        """
        target = (self.scratch / str(run_id)).resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target


Stage = Callable[[Context, RunFacts], Outcome]


class NoJudgementError(Exception):
    """The gates could not be judged, and the reason is not a failing gate.

    Distinct from a gate failure on purpose. A run that failed a gate has been
    measured and found wanting; a run that could not be judged has not been
    measured, and treating the second as the first would record a failure
    against a model nobody evaluated.
    """


def _judged(
    context: Context,
    facts: RunFacts,
    *,
    artefact_kind: str,
    digest: str,
    workdir: Path,
    suite: Any,
) -> Any:
    """Measure the artefact and put the numbers to GLEIPNIR. RF-10.

    The measurements come from the `draupnir.eval` driver the specification
    names, read out of what the harness wrote; the baseline comes out of the
    chain; the verdict is GLEIPNIR's, reached through `api.assurance`, which is
    the seam that already existed for exactly this and which nothing called.

    What this replaced was worse than a stub. `_measurements` derived a score
    per gate from the artefact's SHA-256 and `_baselines` returned
    `measurement * 0.95`, so every run was judged against ninety-five per cent
    of its own score and every run passed. The docstrings were candid; the
    consequence was that six gates, four of them relative, decided nothing.

    A run with no baseline is refused rather than given one. That is the whole
    point: `Gate.holds` already refuses a relative comparison with no baseline,
    and deriving one from the run under judgement turns the refusal into a pass.
    """
    if context.stand_in:
        logger.warning(
            "gates.stand-in",
            runId=str(facts.run_id),
            artefactKind=artefact_kind,
            reason=(
                "DRAUPNIR_WORKER_STAND_IN is set, so these gate measurements are "
                "derived from the artefact's digest rather than measured, and the "
                "baseline is derived from them. Nothing here is an evaluation."
            ),
        )
        measurements = _measurements(digest, suite.gates)
        return gleipnir_gates.evaluate(
            measurements, _baselines(suite.gates, measurements), suite_version=suite.version
        )

    measurements = _measured(context, facts, workdir=workdir, suite=suite)
    baselines = _baseline_for(context, suite=suite, artefact_kind=artefact_kind, facts=facts)
    return assurance.gleipnir_judge(
        measurements,
        baselines,
        suite_version=suite.version,
        gate_ids=list(suite.gates),
    )


def _measured(context: Context, facts: RunFacts, *, workdir: Path, suite: Any) -> dict[str, float]:
    """What the evaluation harness measured, read through its driver."""
    if context.registry is None or facts.specification is None:
        msg = (
            "no evaluation driver can be resolved for this run, so its gates cannot be "
            "measured. Set DRAUPNIR_WORKER_STAND_IN to derive numbers deliberately, or "
            "install the suite the specification names."
        )
        raise NoJudgementError(msg)

    try:
        spec = RunSpec.from_mapping(facts.specification)
        plugin = context.registry.for_spec(spec, "draupnir.eval")
    except Exception as error:
        raise NoJudgementError(f"no installed evaluation driver: {error}") from error

    try:
        outcomes = plugin.driver.collect_gates(workdir, suite.version)
    except Exception as error:
        raise NoJudgementError(f"{plugin.name} could not read its results: {error}") from error

    # The value, not the verdict. A driver that filled `passed` in would be a
    # driver that could pass its own evaluation, and Decision S4 puts that with
    # GLEIPNIR; `lm-eval` leaves it unset for exactly this reason.
    measurements = {outcome.gate: float(outcome.value) for outcome in outcomes}
    missing = [gate for gate in suite.gates if gate not in measurements]
    if missing:
        raise NoJudgementError(
            f"{plugin.name} reported no measurement for {', '.join(sorted(missing))}. A gate "
            "with no measurement is not a gate that passed, and judging the rest would "
            "report a pass for a suite that did not run."
        )
    return measurements


def _baseline_for(
    context: Context, *, suite: Any, artefact_kind: str, facts: RunFacts
) -> dict[str, float]:
    """The values relative gates are compared against, out of the chain."""
    from draupnir.raun.baselines import NoBaselineError

    jurisdiction = None
    if facts.specification is not None:
        metadata = facts.specification.get("metadata")
        if isinstance(metadata, Mapping):
            found = metadata.get("jurisdiction")
            jurisdiction = str(found) if found else None

    if context.baselines is None:
        msg = (
            "this worker holds no baselines, so a relative gate has nothing to compare "
            "against. Four of the six gates are relative and a baseline derived from the "
            "run under judgement passes by construction, so the run is deferred."
        )
        raise NoJudgementError(msg)

    try:
        values: dict[str, float] = context.baselines.values_for(
            suite.key, artefact_kind, jurisdiction
        )
    except NoBaselineError as refusal:
        raise NoJudgementError(str(refusal)) from refusal
    return values


class NoPlanError(Exception):
    """The run's specification could not be turned into a job. RF-10.

    Caught by the stage that asked and turned into a `DEFERRED` outcome. Every
    reason it carries is one an operator can act on -- a missing driver, a
    specification the driver refused, a driver that accepted and then failed --
    and each is named as what it is, because "the run did not start" sends
    somebody to the wrong place.
    """


def _plan_for(
    context: Context,
    facts: RunFacts,
    *,
    group: str,
    output: Path,
    inputs: Sequence[Path],
    workdir: Path,
    partition: str,
    nodes: int = 1,
    gres: str = "",
) -> JobPlan:
    """Render the job this run's specification asks for, or say why not.

    The registry, `validate`, then `render` -- the same three steps
    `dryRunSpecification` takes, in the same order and through the same
    registry. `render` is pure by Decision S5 and the conformance harness
    enforces it, so the plan produced here and the plan the operator was shown
    are the same bytes; that is asserted rather than assumed.

    Validated before rendered, because `render` on a specification the driver
    has refused is undefined behaviour: letting it raise turns "your
    specification is missing save_steps" into a `KeyError`, which is the
    operator's problem stated in the driver author's vocabulary.

    What the estate adds afterwards is its own and is not the driver's to know:
    the accelerator type this forge offers (a specification is portable across
    the Forge Matrix and must not name one forge's hardware), the confinement
    profile, and the lease references a job redeems at start.
    """
    context.planned_by = STAND_IN_EXECUTOR if context.stand_in else ""
    if context.stand_in:
        # Loud, and every time. A tick report that said nothing would let a
        # development flag survive into an estate, and the run's own chain
        # would record a checkpoint nobody could reproduce.
        logger.warning(
            "executor.stand-in",
            runId=str(facts.run_id),
            group=group,
            reason=(
                "DRAUPNIR_WORKER_STAND_IN is set, so this job runs the development "
                "executor rather than the driver the specification names. What it "
                "produces is not a model."
            ),
        )
        return execution.stand_in_plan(
            output,
            inputs,
            workdir=workdir,
            partition=partition,
            nodes=nodes,
            gres=gres,
            environment=context.job_environment(),
            sandbox=context.confinement(workdir, inputs),
        )

    if context.registry is None:
        msg = (
            "this worker has no plug-in registry, so no driver can be resolved for "
            "the specification. Set DRAUPNIR_WORKER_STAND_IN to run the development "
            "executor deliberately, or install the drivers the specification names."
        )
        raise NoPlanError(msg)

    if facts.specification is None:
        # A run registered before the chain recorded specifications, or by
        # something that does not record one. Deferred rather than run with a
        # stand-in, because a stand-in substituted silently is the finding.
        msg = (
            "the chain records no specification for this run, only its hash, so "
            "there is nothing to render a job from (SAD 6.2 makes the specification "
            "the unit of reproduction). This run was registered before the "
            "specification was recorded."
        )
        raise NoPlanError(msg)

    try:
        spec = RunSpec.from_mapping(facts.specification)
    except (ValueError, KeyError, TypeError) as error:
        raise NoPlanError(f"the recorded specification could not be read: {error}") from error

    try:
        plugin = context.registry.for_spec(spec, group)
    except Exception as error:
        raise NoPlanError(f"no installed driver can render this specification: {error}") from error

    context.planned_by = str(plugin.name)
    problems = list(plugin.driver.validate(spec))
    if problems:
        named = " ".join(f"{problem.field}: {problem.message}" for problem in problems)
        raise NoPlanError(f"{plugin.name} refused this specification -- {named}")

    try:
        rendered: JobPlan = plugin.driver.render(spec, workdir)
    except Exception as error:
        # `validate` passed and `render` raised, which is a defect in the
        # driver rather than in the specification. Named as such, because an
        # operator told to fix their specification will not be able to.
        raise NoPlanError(
            f"{plugin.name} accepted this specification and then failed to render it: "
            f"{type(error).__name__}: {error}. Its `validate` returned no problems, so "
            "this is a fault in the driver rather than in the specification."
        ) from error

    envelope: JobPlan = replace(
        rendered,
        environment={**rendered.environment, **context.job_environment()},
        sandbox=context.confinement(workdir, inputs),
        resources=replace(
            rendered.resources,
            partition=partition or rendered.resources.partition,
            gres=gres or rendered.resources.gres,
        ),
    )
    return envelope


def _expected(plan: JobPlan, workdir: Path, fallback: str) -> Path:
    """Where the job is expected to leave its output.

    From the plan rather than from a constant. `ARTEFACTS["adapter"]` is the
    stand-in's filename; a real driver names its own -- LLaMA-Factory writes
    `adapter_model.safetensors` -- and a worker looking for the wrong name
    would report "exited zero and wrote nothing" about a job that wrote
    exactly what it said it would (RF-10).
    """
    first = plan.expected_artefacts[0] if plan.expected_artefacts else fallback
    return workdir / first


class NotStagedError(Exception):
    """The vault was asked to hold an artefact and would not.

    Caught by the stage that raised it and turned into a `DEFERRED` outcome,
    the way a dispatch refusal is. It is an exception rather than a return
    value because every staging site has the same answer to it -- try again
    next tick -- and threading a second failure channel through three call
    sites would let one of them forget.
    """


@dataclass(frozen=True, slots=True)
class Staged:
    """Where an artefact was put, and what its bytes hash to."""

    sha256: str
    #: The `hodd://` address, or empty where this installation has no vault.
    uri: str = ""
    #: Why there is no address. Empty when there is one.
    reason: str = ""

    def as_record(self) -> dict[str, Any]:
        """The `{uri, sha256}` shape `Orchestrator._uri_for` matches on."""
        return {"uri": self.uri, "sha256": self.sha256}


def _stage(context: Context, kind: str, run_id: UUID, source: Path, *, name: str = "") -> Staged:
    """Put an artefact into HODD, seal it, and return its address. RF-08.

    Nothing did this. The worker hashed what a job wrote and recorded the
    digest, and the bytes stayed in a scratch directory that SAD 11.2 calls
    disposable -- so `hodd.quota` was checked by nothing, `stores.store_for`
    was called by nothing, and a release approval resolved an artefact URI the
    chain had never recorded.

    **Sealed at the put, not at the approval.** AC-S8 re-hashes at publication
    to detect a post-gate modification (T8); a seal placed only once a release
    is approved leaves the whole window between evaluation and approval open,
    which is exactly the window an insider has time to act in.

    **Capacity is checked before the write and not after.** A put that fills
    the vault has already caused the harm the check exists to prevent, and on a
    copy-on-write filesystem a full vault cannot even be emptied. Deferring
    costs the run a tick; the alternative costs the estate its vault.

    Idempotent by digest. A stage whose transition failed re-runs next tick and
    finds its own bytes already at the address; putting them again would be
    refused as an overwrite of a sealed artefact and the run would defer for
    ever. Bytes that differ are *not* accepted -- that is a second artefact at
    one address, and the seal refusing it is the seal working.
    """
    digest = _digest(source)
    if context.store is None:
        return Staged(
            sha256=digest,
            reason=(
                "this worker has no artefact store configured (DRAUPNIR_VAULT_ROOT), so "
                f"the {kind} stays in scratch and the run cannot be released"
            ),
        )

    uri = stores.artefact_uri(context.site_id, kind, str(run_id), name)
    try:
        held = context.store.stat(uri)
    except Exception as error:
        raise NotStagedError(f"the vault did not answer about {uri}: {error}") from error

    if held.exists and held.sha256 == digest:
        # Already ours, byte for byte. Seal again in case the last tick put and
        # then died: `seal` is idempotent and an unsealed artefact is the gap.
        context.store.seal(uri)
        return Staged(sha256=digest, uri=uri)

    size = source.stat().st_size
    room = quota.room_for(size, context.store, what=f"{kind} for run {run_id}")
    if not room.fits:
        raise NotStagedError(
            f"{uri} needs {stores.readable_size(size)} and the vault is "
            f"{stores.readable_size(room.shortfall)} short of taking it. The run is "
            f"deferred rather than the vault filled (AC-S10).{chr(10)}{room.explain()}"
        )

    try:
        context.store.put(uri, source)
        context.store.seal(uri)
    except Exception as error:
        raise NotStagedError(f"{uri} was not stored: {error}") from error
    return Staged(sha256=digest, uri=uri)


# ---------------------------------------------------------------------------
# QUEUED -> TRAINING
# ---------------------------------------------------------------------------


def job_name_for(run_id: UUID) -> str:
    """The name a run's job carries on the scheduler.

    Derived from the run rather than remembered, which is what lets a worker
    ask "did I already submit this?" without having written anything down. A
    worker holds nothing between ticks by design (SAD 11.2 row 1), and this is
    how that survives contact with a scheduler that queues.
    """
    return f"draupnir-{run_id}"


def dispatch(context: Context, facts: RunFacts) -> Outcome:
    """Submit a queued run, or transition it once the scheduler starts it.

    **A queued job is a QUEUED run.** This used to submit and immediately
    transition to TRAINING, which recorded a job Slurm had merely accepted as
    one that was training. On this estate that is not an edge case: the adapter
    array is `--array=0-55%3`, so fifty three of fifty six elements are pending
    at any moment by design, and the board would have shown fifty six runs
    training against three appliances.

    QUEUED already means "waiting to run", which is exactly what a pending job
    is, so no new state was needed -- only the transition moved to where the
    run actually starts.

    That leaves one problem: something has to stop the next tick submitting a
    second copy. The answer is the scheduler itself. The job carries a name
    derived from the run, and a driver that can be asked about it is asked;
    nothing is recorded until there is something true to record.
    """
    if not context.may_dispatch:
        return Outcome(
            facts.run_id,
            Result.DEFERRED,
            "the supply is on battery; queued work will not finish before it does",
        )

    name = job_name_for(facts.run_id)
    existing = _already_submitted(context, name)
    if existing is not None:
        return _start_if_running(context, facts, existing)

    try:
        placement = place(
            partition=Partition.ADAPTERS,
            estate=context.estate,
            requested_concurrency=1,
        )
    except PlacementError as refusal:
        return Outcome(facts.run_id, Result.DEFERRED, f"no placement: {refusal}")

    workdir = context.workdir(facts.run_id)
    corpus = _corpus_of(context, facts, workdir)
    output = workdir / ARTEFACTS["adapter"]

    try:
        plan = _plan_for(
            context,
            facts,
            group="draupnir.train",
            output=output,
            inputs=[corpus],
            workdir=workdir,
            partition=str(placement.partition),
            nodes=placement.nodes_per_element,
            # What the placement decided the estate offers, carried into the
            # plan rather than re-derived by the driver: a specification is
            # portable across the Forge Matrix and must not name one forge's
            # hardware (SAD 6.2).
            gres=placement.gres,
        )
    except NoPlanError as refusal:
        # The run stays QUEUED. Nothing about the run failed -- there is no job
        # to fail -- and a specification the estate cannot render is an
        # operator's problem to fix, not a run to mark FAILED.
        return Outcome(facts.run_id, Result.DEFERRED, f"no job plan: {refusal}")

    try:
        handle = execution.submit(context.scheduler, plan, name=name, leak_check=context.secrets)
    except execution.DispatchError as refusal:
        # Dispatch suspends; the run stays QUEUED. SAD 11.2 row 2: a queued run
        # is not marked failed, because nothing about the run failed.
        return Outcome(facts.run_id, Result.DEFERRED, f"dispatch suspended: {refusal}")

    return _start_if_running(context, facts, handle, placement=placement, plan=plan)


def _already_submitted(context: Context, name: str) -> JobHandle | None:
    """Whether the scheduler is already holding this run's job.

    Asked of a driver that can answer and skipped on one that cannot. A local
    runner has no queue to search, and on it a submission starts immediately,
    so the question does not arise.
    """
    finder = getattr(context.scheduler, "find", None)
    if finder is None:
        return None
    try:
        found: JobHandle | None = finder(name)
    except Exception:
        return None
    return found


def _start_if_running(
    context: Context,
    facts: RunFacts,
    handle: JobHandle,
    *,
    placement: Placement | None = None,
    plan: JobPlan | None = None,
) -> Outcome:
    """Transition to TRAINING when the scheduler says the job is running.

    Until then the run stays QUEUED and the outcome says why, so an operator
    reading the tick report sees "queued behind the throttle" rather than
    nothing at all.
    """
    status = context.scheduler.poll(handle)

    if status.state is JobState.PENDING:
        return Outcome(
            facts.run_id,
            Result.DEFERRED,
            f"job {handle.job_id} is queued on the scheduler and has not started",
        )

    if status.state in {JobState.FAILED, JobState.CANCELLED}:
        # Rejected before it ever ran: an invalid partition, a resource the
        # cluster cannot satisfy. The run has not failed at anything it did,
        # so it stays QUEUED and the reason is reported every tick.
        return Outcome(
            facts.run_id,
            Result.DEFERRED,
            f"job {handle.job_id} {status.state} before starting: "
            f"{status.message or 'no reason given'}",
        )

    applied = context.orchestrator.transition(
        facts.run_id,
        RunState.TRAINING,
        facts={"scheduler_job_id": handle.job_id},
        payload={
            "scheduler_job_id": handle.job_id,
            "node": handle.node
            or status.node
            or (placement.appliances[0] if placement and placement.appliances else None),
            "placement": {
                "partition": str(placement.partition) if placement else str(Partition.ADAPTERS),
                "nodes": placement.nodes_per_element if placement else 1,
                "driver": handle.driver,
            },
            # What the job said it would leave behind, so the tick that
            # observes it looks for the right filename. A real driver names its
            # own -- LLaMA-Factory writes `adapter_model.safetensors` -- and a
            # worker checking for the stand-in's `adapter.safetensors` would
            # report "exited zero and wrote nothing" about a job that wrote
            # exactly what it said (RF-10).
            "expected_artefacts": list(plan.expected_artefacts) if plan else [],
            # Which driver rendered this, so a reader of the chain can tell a
            # run the estate actually trained from one the development
            # stand-in simulated. This used to be recorded at TRAINED as the
            # literal string "stand-in" whatever had run, which was true then
            # and would have quietly stopped being true (RF-10).
            "job_driver": context.planned_by,
            # And the plan itself, which is what makes a run reproducible from
            # the chain: SAD 6.2 asks a reader to reconstruct, and a chain that
            # records only that a job ran cannot answer what it ran.
            "job_plan": plan.as_mapping() if plan else None,
        },
    )
    return Outcome(facts.run_id, Result.PLACED, f"job {handle.job_id} started", applied.state)


# ---------------------------------------------------------------------------
# TRAINING -> TRAINED or FAILED
# ---------------------------------------------------------------------------


def observe(context: Context, facts: RunFacts) -> Outcome:
    """Ask the scheduler whether the job has finished, and record what it did.

    Asked rather than waited for. A tick that blocked on one job would stop
    every other run on the estate, and the loop's whole shape is that a tick is
    short and there is always another.
    """
    placed = placement_of(context, facts)
    if placed is None:
        return Outcome(
            facts.run_id,
            Result.DEFERRED,
            "the chain records no scheduler job for this run; nothing to observe",
        )

    handle = execution.handle_for(placed["driver"], placed["job_id"], placed.get("node"))
    try:
        status = context.scheduler.poll(handle)
    # A scheduler that cannot answer is not a run that failed: the job is still
    # out there, and the next tick asks again.
    except Exception as error:
        return Outcome(facts.run_id, Result.DEFERRED, f"the scheduler did not answer: {error}")

    if not execution.settled(status):
        return Outcome(facts.run_id, Result.WAITING, f"job {handle.job_id} is {status.state}")

    if status.state is JobState.FAILED and getattr(status, "exit_code", None) is None:
        # A failure with no exit code is a scheduler that has lost the job, not
        # a job that failed. Slurm reports a code for anything it watched run;
        # a driver that says FAILED with nothing attached is saying it has no
        # record. Marking the run FAILED on that would take a run that is very
        # possibly still training on an appliance and declare it dead, which is
        # the opposite of what SAD 11.2 row 1 asks for. Left where it is, with
        # the driver's own words, for an operator to settle.
        message = getattr(status, "message", "") or "no reason given"
        return Outcome(
            facts.run_id,
            Result.DEFERRED,
            (
                f"the scheduler reports job {handle.job_id} as failed with no exit code "
                f"({message}). That is a lost job, not a failed run, and it is not "
                "recorded as one."
            ),
        )

    completed = execution.observe(context.scheduler, handle, status)
    workdir = context.workdir(facts.run_id)
    produced = workdir / _recorded_artefact(context, facts, ARTEFACTS["adapter"])

    if not completed.succeeded or not produced.is_file():
        applied = context.orchestrator.transition(
            facts.run_id,
            RunState.FAILED,
            facts={"exit_code": completed.exit_code or 1, "watchdog_fired": False},
            payload={
                "exit_code": completed.exit_code,
                "last_log_lines": list(completed.tail) or ["the executor produced no output"],
                "resource_state": {"allocation": "released", "node": completed.node},
            },
        )
        return Outcome(facts.run_id, Result.MOVED, f"exit {completed.exit_code}", applied.state)

    try:
        staged = _stage(context, "adapter", facts.run_id, produced)
    except NotStagedError as refusal:
        # Before the transition, deliberately. A run recorded TRAINED whose
        # checkpoint is in a scratch directory and nowhere else is a run whose
        # weights the next disk failure takes with it.
        return Outcome(facts.run_id, Result.DEFERRED, str(refusal))

    digest = staged.sha256
    applied = context.orchestrator.transition(
        facts.run_id,
        RunState.TRAINED,
        facts={"exit_code": completed.exit_code, "checkpoint_sha256": digest},
        payload={
            "checkpoint_sha256": digest,
            "artefact_sha256": digest,
            "artefact_uri": staged.uri,
            "steps": 1,
            "final_loss": 0.0,
            "executor": _recorded(context, facts, "job_driver") or STAND_IN_EXECUTOR,
            "node": completed.node,
        },
    )
    where = staged.uri or f"not staged: {staged.reason}"
    return Outcome(
        facts.run_id, Result.MOVED, f"checkpoint {digest[:12]} at {where}", applied.state
    )


# ---------------------------------------------------------------------------
# TRAINED -> EVALUATING
# ---------------------------------------------------------------------------


def begin_evaluation(context: Context, facts: RunFacts) -> Outcome:
    """Resolve the suite for the artefact, or stop.

    SAD 6.1's guard is "RAUN suite resolves for the artefact type", and it can
    fail: an artefact kind nobody registered a suite for does not get evaluated
    by the nearest available one. A run that stops here is a run whose gate
    results would have meant something else.
    """
    try:
        resolved = raun_suites.default_registry().resolve("adapter")
    except raun_suites.SuiteError as refusal:
        return Outcome(facts.run_id, Result.DEFERRED, f"no suite resolves: {refusal}")

    suite = resolved[0]
    applied = context.orchestrator.transition(
        facts.run_id,
        RunState.EVALUATING,
        facts={"suite_version": suite.version},
        payload={
            "suite_version": suite.version,
            "baseline": "run://MIDGARD-CORE-QWEN36-35B-A3B-v1.0",
            "suites": [item.key for item in resolved],
        },
    )
    return Outcome(facts.run_id, Result.MOVED, f"suite {suite.key}", applied.state)


# ---------------------------------------------------------------------------
# EVALUATING -> MERGED or back to QUEUED
# ---------------------------------------------------------------------------


def judge(context: Context, facts: RunFacts) -> Outcome:
    """Judge the gates, and requeue within budget when one fails. AC-F7.

    The judgement is GLEIPNIR's: this measures, hands the numbers over, and
    records what came back. A worker that formed its own view about a margin
    would be a second implementation of the one rule the whole system turns on.
    """
    digest = _recorded(context, facts, "checkpoint_sha256")
    if digest is None:
        return Outcome(facts.run_id, Result.DEFERRED, "the chain records no checkpoint to judge")

    suite = raun_suites.default_registry().resolve("adapter")[0]
    try:
        result = _judged(
            context,
            facts,
            artefact_kind="adapter",
            digest=str(digest),
            workdir=context.workdir(facts.run_id),
            suite=suite,
        )
    except NoJudgementError as refusal:
        # Deferred, not failed. A run that could not be judged has not been
        # measured, and recording a gate failure against a model nobody
        # evaluated is a claim the chain would carry for as long as it exists.
        return Outcome(facts.run_id, Result.DEFERRED, f"the gates were not judged: {refusal}")

    if result.passed:
        applied = context.orchestrator.transition(
            facts.run_id,
            RunState.MERGED,
            facts={"failing_gates": list(result.failing)},
            payload={"gate_results": result.as_payload()},
        )
        return Outcome(facts.run_id, Result.MOVED, "gates pass", applied.state)

    failing = list(result.blocking_failures)
    if facts.budget_remaining <= 0:
        # Nowhere to go. SAD 6.1 has no transition out of EVALUATING except the
        # requeue and the pass, so a run that failed with no budget stays where
        # it is and waits for an operator. Saying so beats moving it somewhere
        # the table does not have.
        return Outcome(
            facts.run_id,
            Result.IDLE,
            (
                f"gates failed ({', '.join(failing)}) and the retry budget of "
                f"{facts.retry_budget} is exhausted: "
                f"{gleipnir_gates.describe(failing)}"
            ),
        )

    applied = context.orchestrator.transition(
        facts.run_id,
        RunState.QUEUED,
        facts={
            "failing_gates": failing,
            "retry_budget_remaining": facts.budget_remaining,
        },
        payload={
            "failing_gate": failing[0],
            "requeue_reason": (
                f"{gleipnir_gates.describe(failing[:1])}; "
                f"{facts.budget_remaining} of {facts.retry_budget} retries remaining"
            ),
            "failing_gates": failing,
            "gate_results": result.as_payload(),
        },
    )
    return Outcome(facts.run_id, Result.MOVED, f"requeued on {', '.join(failing)}", applied.state)


# ---------------------------------------------------------------------------
# MERGED -> QUANTISED
# ---------------------------------------------------------------------------


def merge_and_quantise(context: Context, facts: RunFacts) -> Outcome:
    """Sweep, wait for a choice, then build the formats from the chosen point.

    Three ticks' worth of work, each resumable from the chain (RF-27, AC-F8):

    * No sweep recorded: merge and re-gate every point, and record the sweep.
      This merged once and recorded how many points the sweep *had*, so the
      comparison S15 showed was invented and there was nothing to choose from.
    * A sweep and no selection: wait. Choosing among the points RAUN passed is
      S15's primary action, an operator's, and not the worker's to make.
    * A selection: quantise the chosen point's bytes, verified first.

    The transition is recorded last, after the quantised artefacts exist, so
    that the state QUANTISED is true of the run at the moment the chain says it
    is.
    """
    if not context.may_dispatch:
        return Outcome(facts.run_id, Result.DEFERRED, "the supply is on battery")

    adapter_digest = _recorded(context, facts, "checkpoint_sha256")
    if adapter_digest is None:
        return Outcome(facts.run_id, Result.DEFERRED, "the chain records no adapter to merge")

    workdir = context.workdir(facts.run_id)
    adapter = workdir / ARTEFACTS["adapter"]
    merged = workdir / ARTEFACTS["merged"]

    if not adapter.is_file():
        # The run was trained by something else -- an earlier `make procedure`,
        # or a worker with a different scratch -- and its weights are not where
        # this one would look. On the estate the artefact is a `hodd://` URI
        # the driver resolves and this cannot happen; here it can, and merging
        # a file that is not there would produce a digest of nothing.
        return Outcome(
            facts.run_id,
            Result.DEFERRED,
            (
                f"no {adapter.name} in {adapter.parent}: this run was trained "
                "somewhere other than this worker's scratch, so there is nothing here "
                "to merge"
            ),
        )

    # The bytes about to be merged are the bytes the chain says were trained.
    # RF-09, AC-S1: `integrity.verify_before_load` existed and nothing called
    # it, and this stage read `checkpoint_sha256` out of the chain, passed it
    # into the sweep as the adapter's identity, and never hashed the file --
    # so a merge recorded as being over one checkpoint could be over another,
    # and the sweep hash AC-F8 asks a reader to reconstruct from would be a
    # statement about bytes nobody checked. Here rather than earlier because
    # this is the last moment before an allocation is consumed.
    try:
        integrity.verify_before_load(
            adapter,
            artefact="adapter",
            expected=adapter_digest,
            at=datetime.now(UTC),
            run_id=str(facts.run_id),
        )
    except integrity.IntegrityError as refusal:
        return Outcome(facts.run_id, Result.DEFERRED, f"the adapter did not verify: {refusal}")

    from draupnir.brisingamen import sweep as sweeps

    recorded = sweeps.fold(context.orchestrator.history(facts.run_id))
    if recorded is None:
        base = hashlib.sha256(b"MIDGARD-CORE-QWEN36-35B-A3B-v1.0").hexdigest()
        return _evaluate_sweep(
            context,
            facts,
            linear(method="slerp", base_sha256=base, adapter_sha256=adapter_digest),
            adapter=adapter,
            workdir=workdir,
        )

    chosen = recorded.selected_point
    if chosen is None:
        return Outcome(
            facts.run_id,
            Result.IDLE,
            (
                f"{len(recorded.passing)} of {recorded.size} merge points clear every "
                "blocking gate; awaiting an operator's selection (S15)"
                if recorded.passing
                else (
                    f"no point of the {recorded.size}-point sweep clears every blocking gate, "
                    "so there is nothing to select"
                )
            ),
        )

    located = _sweep_file(context, facts, chosen.artefact_sha256 or "")
    if located is None:
        return Outcome(
            facts.run_id,
            Result.DEFERRED,
            (
                f"the selected merge point {chosen.label} was merged by a worker with a "
                "different scratch, so its bytes are not here to quantise"
            ),
        )
    merged, staged_record = located
    # The bytes about to be quantised are the bytes the selection was made on.
    try:
        integrity.verify_before_load(
            merged,
            artefact="merged",
            expected=chosen.artefact_sha256 or "",
            at=datetime.now(UTC),
            run_id=str(facts.run_id),
        )
    except integrity.IntegrityError as refusal:
        return Outcome(
            facts.run_id, Result.DEFERRED, f"the selected merge did not verify: {refusal}"
        )

    built: dict[str, str] = {}
    addresses: list[dict[str, Any]] = [staged_record]
    for fmt in FORMATS:
        target = workdir / f"{fmt}.bin"
        try:
            export_plan = _plan_for(
                context,
                facts,
                group="draupnir.export",
                output=target,
                inputs=[merged],
                workdir=workdir,
                partition=str(Partition.EXPORT),
            )
        except NoPlanError as refusal:
            return Outcome(facts.run_id, Result.DEFERRED, f"no {fmt} plan: {refusal}")

        try:
            outcome = execution.dispatch(
                context.scheduler,
                export_plan,
                timeout=context.timeout,
                leak_check=context.secrets,
            )
        except execution.DispatchError as refusal:
            return Outcome(facts.run_id, Result.DEFERRED, f"{fmt} not placed: {refusal}")
        if not outcome.succeeded:
            return Outcome(facts.run_id, Result.DEFERRED, f"{fmt} exited {outcome.exit_code}")
        if not target.is_file():
            return Outcome(
                facts.run_id,
                Result.DEFERRED,
                f"the {fmt} export exited zero and wrote no {target.name}",
            )
        try:
            staged_format = _stage(context, "quantised", facts.run_id, target, name=target.name)
        except NotStagedError as refusal:
            return Outcome(facts.run_id, Result.DEFERRED, str(refusal))
        built[fmt] = staged_format.sha256
        addresses.append(staged_format.as_record())

    applied = context.orchestrator.transition(
        facts.run_id,
        RunState.QUANTISED,
        facts={"failing_gates": list(chosen.evidence.failing) if chosen.evidence else []},
        payload={
            # The chosen point's configuration, and the whole comparison it was
            # chosen from, which is what the model card records (AC-F8).
            "merge_config_hash": chosen.config_hash(),
            "sweep_result": recorded.for_model_card(),
            "formats_built": built,
            "merged_sha256": chosen.artefact_sha256,
            # Every artefact this stage produced, each with its own address.
            # A list rather than one `artefact_uri` because a release run
            # produces several and a publication resolves one of them by its
            # digest -- see `Orchestrator._uri_for`, which matches on exactly
            # this shape and would otherwise find no location for two of the
            # three formats (RF-05, AC-S8).
            "artefacts": addresses,
        },
    )
    detail = f"{len(built)} format(s) built"
    if any(not item["uri"] for item in addresses):
        detail += ", none staged: this worker has no vault configured"
    return Outcome(facts.run_id, Result.MOVED, detail, applied.state)


# ---------------------------------------------------------------------------
# QUANTISED -> AWAITING_APPROVAL
# ---------------------------------------------------------------------------


def regate_formats(context: Context, facts: RunFacts) -> Outcome:
    """Re-gate every quantised build. AC-F9: nothing reaches approval unmeasured.

    Driven by what was built rather than by what was evaluated. Iterating the
    evidence would confirm that everything evaluated passed, which is true of
    an empty set and of a set missing the one format nobody ran.
    """
    built = _recorded(context, facts, "formats_built")
    if not isinstance(built, dict) or not built:
        return Outcome(facts.run_id, Result.DEFERRED, "the chain records no built format")

    suite = raun_suites.default_registry().resolve("quantised")[0]
    workdir = context.workdir(facts.run_id)
    results: dict[str, Any] = {}
    failing: list[str] = []
    for fmt, digest in sorted(built.items()):
        try:
            outcome = _judged(
                context,
                facts,
                artefact_kind="quantised",
                digest=str(digest),
                # Each format is evaluated in its own directory, because each
                # is its own evaluation: `collect_gates` reads one harness
                # result file, and pointing three formats at one would judge
                # all three on whichever ran last.
                workdir=workdir / fmt,
                suite=suite,
            )
        except NoJudgementError as refusal:
            return Outcome(facts.run_id, Result.DEFERRED, f"{fmt} was not re-gated: {refusal}")
        # Bound to the bytes, not to the format name. AC-F9 asks for evidence
        # per built format and RF-05's publication re-hash matches evidence to
        # a digest -- so the digest travels with the result rather than being
        # inferred from the key it sits under.
        results[fmt] = {
            **outcome.as_payload(),
            "artefactSha256": str(digest),
            "artefactKind": "quantised",
            "format": fmt,
            "suite": suite.key,
            "evaluatedAt": datetime.now(UTC).isoformat(),
            "passed": outcome.passed,
        }
        if not outcome.passed:
            failing.append(fmt)

    if failing:
        return Outcome(
            facts.run_id,
            Result.IDLE,
            f"format(s) failed re-gate and may not be published: {', '.join(failing)}",
        )

    applied = context.orchestrator.transition(
        facts.run_id,
        RunState.AWAITING_APPROVAL,
        facts={"formats_regated": sorted(built), "formats_failing": failing},
        payload={
            "format_gate_results": results,
            "artefact_sha256": str(built.get("nvfp4") or next(iter(built.values()))),
            "formats": sorted(built),
            "model": facts.name,
        },
    )
    return Outcome(facts.run_id, Result.MOVED, "awaiting a decision", applied.state)


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

#: One row per state the worker acts on. Everything else is somebody else's:
#: DRAFT through CURATED are the operator's corpus work, AWAITING_APPROVAL is
#: an approver's, and FAILED, RELEASED and QUARANTINED are terminal.
STAGES: Mapping[RunState, Stage] = {
    RunState.QUEUED: dispatch,
    RunState.TRAINING: observe,
    RunState.TRAINED: begin_evaluation,
    RunState.EVALUATING: judge,
    RunState.MERGED: merge_and_quantise,
    RunState.QUANTISED: regate_formats,
}


def advance(context: Context, facts: RunFacts) -> Outcome:
    """Do one thing about one run, or say there is nothing to do."""
    stage = STAGES.get(facts.state)
    if stage is None:
        return Outcome(facts.run_id, Result.IDLE, f"{facts.state} is not the worker's", facts.state)
    return stage(context, facts)


def actionable() -> frozenset[RunState]:
    """The states the worker acts on. Derived from the table, never listed."""
    return frozenset(STAGES)


# ---------------------------------------------------------------------------
# Reading what the chain recorded
# ---------------------------------------------------------------------------


def placement_of(context: Context, facts: RunFacts) -> dict[str, Any] | None:
    """The scheduler job the chain says this run was placed on.

    Public because the loop needs it too: the supply monitor is told which jobs
    are running so that a transfer to battery checkpoints them by name.

    Reconstructed rather than remembered. SAD 11.2 row 1 makes that the whole
    design: a worker that kept its handles in memory would lose the estate's
    work to a restart.
    """
    payload = _entry_payload(context, facts.run_id, "scheduler_job_id")
    if payload is None:
        return None
    placement = payload.get("placement")
    driver = placement.get("driver") if isinstance(placement, dict) else None
    return {
        "job_id": str(payload["scheduler_job_id"]),
        "driver": str(driver or "motsognir.local_subprocess/v1"),
        "node": payload.get("node"),
    }


def _recorded(context: Context, facts: RunFacts, key: str) -> Any:
    """The most recent value the chain recorded under `key` for this run."""
    payload = _entry_payload(context, facts.run_id, key)
    return None if payload is None else payload.get(key)


def _entry_payload(context: Context, run_id: UUID, key: str) -> dict[str, Any] | None:
    """The payload of the latest entry about `run_id` carrying `key`."""
    found: dict[str, Any] | None = None
    for entry in context.orchestrator.history(run_id):
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        if key in payload:
            found = payload
    return found


def _corpus_of(context: Context, facts: RunFacts, workdir: Path) -> Path:
    """The corpus this run trains on, staged into its scratch directory.

    A file rather than a `hodd://` URI because the executor is a local process.
    On the estate the driver resolves the URI and the vault is mounted; here the
    input is what the chain recorded about it, written down so the stand-in has
    real bytes to hash.
    """
    corpus = workdir / "corpus.bin"
    if not corpus.is_file():
        recorded = str(_recorded(context, facts, "output_sha256") or facts.spec_hash)
        corpus.write_bytes(_bytes_of(recorded) * 8)
    # Not verified against `output_sha256`, and deliberately not: these bytes
    # are *derived* from that digest rather than being the curated corpus, so
    # an integrity check here would compare a file against the hash of
    # something else and fail on every run. The check that means something on
    # this path is at the merge, where the chain's digest and the file are
    # about the same bytes because this worker wrote and hashed both. On the
    # estate the driver resolves a `hodd://` URI against the mounted vault and
    # the input carries its own manifest, which is where AC-S1 bites.
    return corpus


def _recorded_artefact(context: Context, facts: RunFacts, fallback: str) -> str:
    """What the chain says this run's job was expected to leave behind.

    Read back rather than assumed, because the filename belongs to whichever
    driver rendered the plan and a constant here would be a second answer to
    it. Falls back for a run placed before this was recorded.
    """
    recorded = _recorded(context, facts, "expected_artefacts")
    if isinstance(recorded, list) and recorded:
        return str(recorded[0])
    return fallback


def _digest(path: Path, *, block: int = 1 << 20) -> str:
    """SHA-256 of what is on disk. Never of what a job said it wrote."""
    running = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(block), b""):
            running.update(chunk)
    return running.hexdigest()


def _measurements(digest: str, gates: Sequence[str]) -> dict[str, float]:
    """Derive a measurement per gate from an artefact's digest.

    A stand-in for an evaluation harness, and deliberately a function of the
    artefact rather than a random number: the same bytes measure the same way
    twice, which is the property a real harness has and a fixture does not. E6
    is a contamination ceiling, so it is derived small.
    """
    values: dict[str, float] = {}
    for gate in gates:
        seed = int(hashlib.sha256(f"{digest}:{gate}".encode()).hexdigest()[:8], 16)
        fraction = seed / 0xFFFFFFFF
        values[gate] = round(0.0001 * fraction if gate == "E6" else 0.70 + 0.25 * fraction, 6)
    return values


def _baselines(gates: Sequence[str], measurements: Mapping[str, float]) -> dict[str, float]:
    """A baseline the measurements clear, so a healthy run reaches approval.

    The gates are judged for real against these numbers and the margins are in
    the ledger payload, so nothing is hidden. What is stood in for is the
    baseline capture: a real one is measured on the base model, and there is no
    base model here.
    """
    return {gate: round(measurements[gate] * 0.95, 6) for gate in gates if gate != "E6"}


def _bytes_of(value: str) -> bytes:
    """The bytes a digest names, or the text itself where it is not one."""
    try:
        return bytes.fromhex(value)
    except ValueError:
        return value.encode()


def _sweep_hash(sweep: Sweep) -> str:
    """A digest over the sweep's comparable form.

    Over the matrix rather than over the points, because the matrix is what
    AC-F8 asks a reader to be able to reconstruct: two sweeps that compare the
    same points on the same gates have the same hash.
    """
    return hashlib.sha256(json.dumps(sweep.matrix(), sort_keys=True).encode()).hexdigest()


__all__ = [
    "FORMATS",
    "STAGES",
    "Context",
    "NotStagedError",
    "Outcome",
    "Result",
    "Stage",
    "Staged",
    "actionable",
    "advance",
    "placement_of",
]


# ---------------------------------------------------------------------------
# The sweep, point by point. RF-27, AC-F8.
# ---------------------------------------------------------------------------


def _with_point(facts: RunFacts, parameters: Mapping[str, float]) -> RunFacts:
    """The run's facts, with one sweep point in the merge driver's parameters.

    The point travels in the specification the driver renders, because that is
    the only thing a driver renders from: a weight passed any other way reaches
    no merge, which is how five merges came to be one merge five times.
    """
    specification = facts.specification
    if not isinstance(specification, Mapping):
        return facts
    spec = specification.get("spec")
    train = spec.get("train") if isinstance(spec, Mapping) else None
    if not isinstance(spec, Mapping) or not isinstance(train, Mapping):
        return facts
    params = {**dict(train.get("params") or {}), "sweep_point": dict(parameters)}
    return replace(
        facts,
        specification={
            **dict(specification),
            "spec": {**dict(spec), "train": {**dict(train), "params": params}},
        },
    )


def _evaluate_sweep(
    context: Context, facts: RunFacts, sweep: Sweep, *, adapter: Path, workdir: Path
) -> Outcome:
    """Merge and re-gate every point, then record the sweep. Nothing is chosen here."""
    import math

    from draupnir.brisingamen import sweep as sweeps
    from draupnir.core.domain.evidence import Evidence

    suite = raun_suites.default_registry().resolve("merged")[0]
    files: dict[str, str] = {}
    artefacts: list[dict[str, Any]] = []

    for index, point in enumerate(sweep.points, start=1):
        pointdir = workdir / f"sweep-{index}"
        pointdir.mkdir(parents=True, exist_ok=True)
        try:
            plan = _plan_for(
                context,
                _with_point(facts, point.parameters),
                group="draupnir.merge",
                output=pointdir / ARTEFACTS["merged"],
                inputs=[adapter],
                workdir=pointdir,
                partition=str(Partition.EXPORT),
            )
        except NoPlanError as refusal:
            return Outcome(
                facts.run_id, Result.DEFERRED, f"no merge plan for {point.label}: {refusal}"
            )
        try:
            completed = execution.dispatch(
                context.scheduler, plan, timeout=context.timeout, leak_check=context.secrets
            )
        except execution.DispatchError as refusal:
            return Outcome(
                facts.run_id, Result.DEFERRED, f"merge {point.label} not placed: {refusal}"
            )
        if not completed.succeeded:
            return Outcome(
                facts.run_id,
                Result.DEFERRED,
                f"the merge at {point.label} exited {completed.exit_code}",
            )
        merged = _expected(plan, pointdir, ARTEFACTS["merged"])
        if not merged.is_file():
            # A job that exits zero and writes nothing wrote somewhere else.
            return Outcome(
                facts.run_id,
                Result.DEFERRED,
                f"the merge at {point.label} exited zero and wrote no {merged.name}",
            )
        try:
            staged = _stage(context, "merged", facts.run_id, merged, name=f"sweep-{index}")
        except NotStagedError as refusal:
            return Outcome(facts.run_id, Result.DEFERRED, str(refusal))
        try:
            result = _judged(
                context,
                facts,
                artefact_kind="merged",
                digest=staged.sha256,
                workdir=pointdir,
                suite=suite,
            )
        except NoJudgementError as refusal:
            return Outcome(
                facts.run_id,
                Result.DEFERRED,
                f"the merge at {point.label} was not re-gated: {refusal}",
            )

        sweep = sweep.with_result(
            point.parameters,
            artefact_sha256=staged.sha256,
            evidence=Evidence(
                artefact_sha256=staged.sha256,
                artefact_kind="merged",
                outcomes=tuple(result.outcomes),
                passed=result.passed,
                suite=suite.name,
                suite_version=result.suite_version,
                evaluated_at=datetime.now(UTC),
                measurements={
                    outcome.gate: outcome.value
                    for outcome in result.outcomes
                    if math.isfinite(outcome.value)
                },
            ),
        )
        files[staged.sha256] = merged.relative_to(workdir).as_posix()
        artefacts.append(staged.as_record())

    context.orchestrator.record(
        subject_type=sweeps.SWEEP_SUBJECT,
        subject_id=str(facts.run_id),
        transition=sweeps.EVALUATED,
        payload={**sweeps.record(sweep), "files": files, "artefacts": artefacts},
    )
    return Outcome(
        facts.run_id,
        Result.IDLE,
        (
            f"sweep evaluated: {len(sweep.passing)} of {sweep.size} points clear every "
            "blocking gate; awaiting an operator's selection (S15)"
            if sweep.passing
            else f"sweep evaluated: no point of {sweep.size} clears every blocking gate"
        ),
    )


def _sweep_file(
    context: Context, facts: RunFacts, digest: str
) -> tuple[Path, dict[str, Any]] | None:
    """Where the chosen point's bytes are in this worker's scratch, and their record."""
    payload = _entry_payload(context, facts.run_id, "files")
    if payload is None or not digest:
        return None
    relative = dict(payload.get("files") or {}).get(digest)
    if not relative:
        return None
    located = context.workdir(facts.run_id) / str(relative)
    if not located.is_file():
        return None
    record = next(
        (
            dict(item)
            for item in payload.get("artefacts", ())
            if isinstance(item, Mapping) and item.get("sha256") == digest
        ),
        {"uri": "", "sha256": digest},
    )
    return located, record
