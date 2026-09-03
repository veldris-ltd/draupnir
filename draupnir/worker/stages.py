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
digests go in the chain, which is what makes the directory disposable: a
scratch tree that is lost costs the run, and a chain that is lost costs
everything.

**On the executors.** The plans are placed through the real schedule driver and
run as real processes with real exit codes. What they run is the development
executor of `motsognir.execution`, because there is no GPU in a control plane;
the driver-rendered plan is recorded beside the result so that what a real
estate would have run is in the chain even where it did not run here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from draupnir.brisingamen.sweep import Sweep, linear
from draupnir.core.application.orchestrator import Orchestrator, RunFacts
from draupnir.core.domain.states import RunState
from draupnir.gleipnir import gates as gleipnir_gates
from draupnir.interfaces.types import JobState
from draupnir.motsognir import execution
from draupnir.motsognir.placement import Estate, Partition, PlacementError
from draupnir.motsognir.placement import plan as place
from draupnir.raun import suites as raun_suites

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


# ---------------------------------------------------------------------------
# QUEUED -> TRAINING
# ---------------------------------------------------------------------------


def dispatch(context: Context, facts: RunFacts) -> Outcome:
    """Place a queued run, and record the allocation it was given.

    Placement first, submission second, transition third, and the transition
    records the scheduler's own identifier for the job -- which is what a later
    tick, or a later worker, uses to find it again.
    """
    if not context.may_dispatch:
        return Outcome(
            facts.run_id,
            Result.DEFERRED,
            "the supply is on battery; queued work will not finish before it does",
        )

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

    plan = execution.stand_in_plan(
        output,
        [corpus],
        workdir=workdir,
        partition=str(placement.partition),
        nodes=placement.nodes_per_element,
    )

    try:
        handle = execution.submit(context.scheduler, plan)
    except execution.DispatchError as refusal:
        # Dispatch suspends; the run stays QUEUED. SAD 11.2 row 2: a queued run
        # is not marked failed, because nothing about the run failed.
        return Outcome(facts.run_id, Result.DEFERRED, f"dispatch suspended: {refusal}")

    applied = context.orchestrator.transition(
        facts.run_id,
        RunState.TRAINING,
        facts={"scheduler_job_id": handle.job_id},
        payload={
            "scheduler_job_id": handle.job_id,
            "node": handle.node or (placement.appliances[0] if placement.appliances else None),
            "placement": {
                "partition": str(placement.partition),
                "nodes": placement.nodes_per_element,
                "driver": handle.driver,
            },
        },
    )
    return Outcome(facts.run_id, Result.PLACED, f"job {handle.job_id}", applied.state)


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
    produced = workdir / ARTEFACTS["adapter"]

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

    digest = _digest(produced)
    applied = context.orchestrator.transition(
        facts.run_id,
        RunState.TRAINED,
        facts={"exit_code": completed.exit_code, "checkpoint_sha256": digest},
        payload={
            "checkpoint_sha256": digest,
            "steps": 1,
            "final_loss": 0.0,
            "executor": "stand-in",
            "node": completed.node,
        },
    )
    return Outcome(facts.run_id, Result.MOVED, f"checkpoint {digest[:12]}", applied.state)


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
    measurements = _measurements(digest, suite.gates)
    result = gleipnir_gates.evaluate(
        measurements, _baselines(suite.gates, measurements), suite_version=suite.version
    )

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
    """Merge with a sweep, re-gate the merge, and build the formats.

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

    base = hashlib.sha256(b"MIDGARD-CORE-QWEN36-35B-A3B-v1.0").hexdigest()
    sweep = linear(method="slerp", base_sha256=base, adapter_sha256=adapter_digest)

    try:
        completed = execution.dispatch(
            context.scheduler,
            execution.stand_in_plan(merged, [adapter], workdir=workdir, partition="export"),
            timeout=context.timeout,
        )
    except execution.DispatchError as refusal:
        return Outcome(facts.run_id, Result.DEFERRED, f"merge not placed: {refusal}")
    if not completed.succeeded:
        return Outcome(facts.run_id, Result.DEFERRED, f"the merge exited {completed.exit_code}")
    if not merged.is_file():
        # A job that exits zero and writes nothing is a job that wrote
        # somewhere else. Saying so beats hashing a file that is not there.
        return Outcome(
            facts.run_id,
            Result.DEFERRED,
            f"the merge exited zero and wrote no {merged.name} in {merged.parent}",
        )

    merged_digest = _digest(merged)
    suite = raun_suites.default_registry().resolve("merged")[0]
    measurements = _measurements(merged_digest, suite.gates)
    regate = gleipnir_gates.evaluate(
        measurements, _baselines(suite.gates, measurements), suite_version=suite.version
    )
    if not regate.passed:
        return Outcome(
            facts.run_id,
            Result.IDLE,
            f"the merged artefact failed re-gate: {', '.join(regate.blocking_failures)}",
        )

    built: dict[str, str] = {}
    for fmt in FORMATS:
        target = workdir / f"{fmt}.bin"
        try:
            outcome = execution.dispatch(
                context.scheduler,
                execution.stand_in_plan(target, [merged], workdir=workdir, partition="export"),
                timeout=context.timeout,
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
        built[fmt] = _digest(target)

    applied = context.orchestrator.transition(
        facts.run_id,
        RunState.QUANTISED,
        facts={"failing_gates": list(regate.failing)},
        payload={
            "merge_config_hash": _sweep_hash(sweep),
            "sweep_result": {"points": len(sweep.points), "method": sweep.method},
            "formats_built": built,
            "merged_sha256": merged_digest,
        },
    )
    return Outcome(facts.run_id, Result.MOVED, f"{len(built)} format(s) built", applied.state)


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
    results: dict[str, Any] = {}
    failing: list[str] = []
    for fmt, digest in sorted(built.items()):
        measurements = _measurements(str(digest), suite.gates)
        outcome = gleipnir_gates.evaluate(
            measurements, _baselines(suite.gates, measurements), suite_version=suite.version
        )
        results[fmt] = outcome.as_payload()
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
    return corpus


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
    "Outcome",
    "Result",
    "Stage",
    "actionable",
    "advance",
    "placement_of",
]
