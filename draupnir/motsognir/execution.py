"""Submitting a plan and waiting for it. The dispatch half of SAD 5.2.

MOTSOGNIR owns job dispatch, and this is the part every caller needs and none
should write twice: hand a plan to a schedule driver, poll until it settles,
and come back with the exit code and the tail of the log. The procedure runner
and the worker both call it, which is why it is here rather than in either.

Nothing here knows what a job computes. It knows a plan, a driver, and how long
to wait -- and a `JobState`, which is the scheduler's vocabulary rather than the
lifecycle's. Turning a settled job into a run transition is the caller's, and it
has to be: the same exit code means "requeue" during evaluation and "failed"
during training.

**On the stand-in.** `stand_in_plan` builds a plan that reads its inputs and
writes a file whose bytes are a function of them. It is a development executor
and it is named as one: there is no GPU, no LLaMA-Factory and no mergekit in a
control plane, by design (SAD 5.2 requires a specification to validate without
one). What it buys is a dispatch path that runs for real -- a real process, a
real exit code, a real artefact with a real digest -- so that everything around
the executor is exercised rather than mocked. What it does not buy is a model,
and nothing in this repository pretends otherwise.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from draupnir.interfaces.types import JobHandle, JobPlan, JobState, ResourceRequest

#: How long to wait for a job before giving up on it. A bound rather than a
#: guess: a dispatcher that waits forever is a dispatcher one stuck job stops.
DEFAULT_TIMEOUT_SECONDS = 900.0

#: How often to ask. Slurm's `squeue` is a fork and a round trip to REGIN, so
#: this is a compromise between load on the controller and latency on the
#: board. The board itself does not wait on it: it reads the event stream.
POLL_SECONDS = 0.25

#: How much of the log to carry back. SAD 6.1 requires TRAINING -> FAILED to
#: record `last_log_lines`, and an operator diagnosing a failure needs the end
#: of the log rather than all of it.
LOG_TAIL = 20

#: The development executor. It hashes what it was given and writes the result,
#: so the artefact is a function of the inputs and two runs over one corpus
#: produce identical bytes -- which is what makes a digest worth recording.
STAND_IN = (
    "import hashlib,json,pathlib,sys\n"
    "out=pathlib.Path(sys.argv[1]); ins=[pathlib.Path(p) for p in sys.argv[2:]]\n"
    "d=hashlib.sha256()\n"
    "for p in ins:\n"
    "    d.update(p.read_bytes() if p.is_file() else p.name.encode())\n"
    "out.parent.mkdir(parents=True,exist_ok=True)\n"
    "out.write_bytes(d.digest()*64)\n"
    "print(json.dumps({'wrote': str(out), 'bytes': out.stat().st_size}))\n"
)


class DispatchError(Exception):
    """Raised when a job could not be placed or could not be waited for."""


class Scheduler(Protocol):
    """What dispatch needs from a `draupnir.schedule` driver."""

    def submit(self, plan: JobPlan) -> JobHandle:
        """Place the plan and return a handle that identifies it."""
        ...

    def poll(self, handle: JobHandle) -> Any:
        """Return the job's current status. Safe to call repeatedly."""
        ...

    def cancel(self, handle: JobHandle) -> Any:
        """Stop the job. Cancelling a finished one is not an error."""
        ...

    def logs(self, handle: JobHandle) -> str:
        """Return whatever the job has written so far."""
        ...


@dataclass(frozen=True, slots=True)
class Completed:
    """A job that has stopped, however it stopped."""

    handle: JobHandle
    state: JobState
    exit_code: int
    node: str | None
    #: The last lines of the log. What SAD 6.1 asks a failure to record.
    tail: tuple[str, ...]

    @property
    def succeeded(self) -> bool:
        """Whether the executor exited zero, which is the guard's question."""
        return self.state is JobState.COMPLETED and self.exit_code == 0


def stand_in_plan(
    output: Path,
    inputs: Iterable[Path],
    *,
    workdir: Path,
    partition: str = "adapters",
    nodes: int = 1,
) -> JobPlan:
    """A plan that runs the development executor over real files."""
    return JobPlan(
        command=(sys.executable, "-c", STAND_IN, str(output), *[str(item) for item in inputs]),
        environment={"PYTHONHASHSEED": "0"},
        workdir=str(workdir),
        resources=ResourceRequest(partition=partition, nodes=nodes),
        expected_artefacts=(output.name,),
    )


def submit(scheduler: Scheduler, plan: JobPlan) -> JobHandle:
    """Place a plan, or raise with what the driver said.

    Separate from `wait` because the two happen at different moments: a
    dispatcher submits and records the allocation, and something else -- a
    later tick, a restart, another process -- waits for it. A dispatcher that
    could only submit by blocking is a dispatcher that loses its work to a
    restart.
    """
    try:
        return scheduler.submit(plan)
    except Exception as error:
        msg = f"the scheduler refused the plan: {error}"
        raise DispatchError(msg) from error


def wait(
    scheduler: Scheduler,
    handle: JobHandle,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll: float = POLL_SECONDS,
) -> Completed:
    """Poll until the job settles, then cancel it if it never does.

    Cancelling on timeout rather than abandoning it: a job nobody is waiting
    for is a job holding an allocation nobody is watching, and an allocation on
    this estate is the scarce resource.
    """
    deadline = time.monotonic() + timeout
    status = scheduler.poll(handle)
    while status.state in {JobState.PENDING, JobState.RUNNING}:
        if time.monotonic() > deadline:
            scheduler.cancel(handle)
            msg = f"job {handle.job_id} did not settle within {timeout:.0f}s and was cancelled"
            raise DispatchError(msg)
        time.sleep(poll)
        status = scheduler.poll(handle)

    return observe(scheduler, handle, status)


def observe(scheduler: Scheduler, handle: JobHandle, status: Any) -> Completed:
    """Turn a settled status into a result, with the tail of its log.

    Exposed so a caller that polls on its own schedule -- a worker tick, which
    must not block -- reads a finished job the same way `wait` does.
    """
    return Completed(
        handle=handle,
        state=status.state,
        # A completed job with no reported code exited zero; anything else that
        # stopped without one did not. Guessing zero for a failure would turn a
        # failed run into a trained one.
        exit_code=_exit_code(status),
        node=getattr(status, "node", None),
        tail=tail(scheduler, handle),
    )


def settled(status: Any) -> bool:
    """Whether a status is one a job does not leave."""
    return status.state not in {JobState.PENDING, JobState.RUNNING}


def _exit_code(status: Any) -> int:
    code = getattr(status, "exit_code", None)
    if code is not None:
        return int(code)
    return 0 if status.state is JobState.COMPLETED else 1


def tail(scheduler: Scheduler, handle: JobHandle, *, lines: int = LOG_TAIL) -> tuple[str, ...]:
    """The last lines of a job's log, or nothing if it wrote none."""
    try:
        written = scheduler.logs(handle)
    except Exception:
        # A log that cannot be read is not a failure of the job. The exit code
        # is the fact; the tail is the explanation, and losing the explanation
        # must not lose the fact.
        return ()
    return tuple(written.strip().splitlines()[-lines:])


def dispatch(
    scheduler: Scheduler,
    plan: JobPlan,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Completed:
    """Submit and wait. The blocking form, for a caller that is a script."""
    return wait(scheduler, submit(scheduler, plan), timeout=timeout)


def handle_for(driver: str, job_id: str, node: str | None = None) -> JobHandle:
    """Rebuild a handle from what the ledger recorded about a job.

    SAD 11.2 row 1: a restarted control plane reconstructs its state from the
    chain. A handle is the scheduler's name for a job, and the chain records it
    at QUEUED -> TRAINING, so a worker that has just started can find the jobs
    the one before it placed.
    """
    return JobHandle(driver=driver, job_id=job_id, node=node)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "STAND_IN",
    "Completed",
    "DispatchError",
    "Scheduler",
    "dispatch",
    "handle_for",
    "observe",
    "settled",
    "stand_in_plan",
    "submit",
    "tail",
    "wait",
]
