"""A ScheduleDriver for Slurm.

SAD 8.2 lists Slurm as an implementation of `draupnir.schedule`. This is that
implementation, and like every schedule driver it only knows how to submit,
observe, cancel and read logs. Placement, array concurrency and retry are
MOTSOGNIR's, not a driver's (SAD 5.2), so nothing here decides where work goes
or whether a failure is worth another attempt: it receives a plan and runs it.

Three things about Slurm that the code below is shaped by.

`squeue` forgets. A job that finished more than `MinJobAge` seconds ago is
gone from the queue, and asking `squeue` about it returns nothing at all --
which is indistinguishable, to a naive reader, from a job that was never
submitted. So a job `squeue` does not know is looked up in `sacct`, which
remembers, and only a job neither knows is reported as unknown.

`sacct` reports several rows per job: the job itself and its `.batch` and
`.extern` steps. The job's own row is the one whose identifier has no dot in
it, and taking the first row instead gives the batch step's state, which
diverges from the job's exactly when something interesting has happened.

An array element is `<job>_<index>`, and that is the identifier throughout.
Passing the array's own identifier where an element's was meant cancels
fifty six jobs instead of one.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from draupnir.interfaces.types import JobHandle, JobPlan, JobState, JobStatus

#: The versioned entry point name. Must match the key in pyproject.toml.
NAME = "motsognir.slurm/v1"

#: `array` is the one that matters: AC-F5 submits fifty six elements as one
#: action, and the core refuses to plan an array against a driver that has not
#: said it can run one.
CAPABILITIES = frozenset({"slurm", "array", "multinode", "gpu"})

#: How Slurm's job states map onto the five of SAD 8.2. Anything not listed is
#: treated as failed rather than as running, so an unrecognised state stops a
#: pipeline instead of hanging it.
_STATES: dict[str, JobState] = {
    "PENDING": JobState.PENDING,
    "CONFIGURING": JobState.PENDING,
    "REQUEUED": JobState.PENDING,
    "RESIZING": JobState.PENDING,
    "SUSPENDED": JobState.PENDING,
    "RUNNING": JobState.RUNNING,
    "COMPLETING": JobState.RUNNING,
    "COMPLETED": JobState.COMPLETED,
    "CANCELLED": JobState.CANCELLED,
    "FAILED": JobState.FAILED,
    "TIMEOUT": JobState.FAILED,
    "NODE_FAIL": JobState.FAILED,
    "OUT_OF_MEMORY": JobState.FAILED,
    "PREEMPTED": JobState.FAILED,
    "BOOT_FAIL": JobState.FAILED,
    "DEADLINE": JobState.FAILED,
}

#: `sbatch` says "Submitted batch job 12345". The number is the whole payload.
_SUBMITTED = re.compile(r"Submitted batch job (\d+)")

#: `sacct` decorates a cancelled state with who cancelled it: "CANCELLED by 1000".
_STATE_WORD = re.compile(r"^([A-Z_]+)")


class SlurmError(RuntimeError):
    """Raised when Slurm cannot be reached or rejects a submission."""


@dataclass
class SlurmDriver:
    """Submits `JobPlan`s to Slurm and reports what becomes of them."""

    name: str = NAME
    capabilities: frozenset[str] = CAPABILITIES
    #: Overridable so that a test can point at a stub, and so that a site with
    #: Slurm somewhere other than the path can say where.
    sbatch: str = "sbatch"
    squeue: str = "squeue"
    sacct: str = "sacct"
    scancel: str = "scancel"
    #: Seconds before a Slurm command is presumed wedged. A control plane that
    #: blocks forever on `squeue` stops observing every other run too.
    timeout: float = 30.0
    #: Extra arguments every submission carries, e.g. `--account`.
    submit_arguments: tuple[str, ...] = ()

    _logs: dict[str, str] = field(default_factory=dict, repr=False)

    # -- submit ------------------------------------------------------------

    def submit(self, plan: JobPlan) -> JobHandle:
        """Submit the plan and return the handle Slurm's identifier gives."""
        log_path = self._log_path(plan)
        arguments = [
            self.sbatch,
            f"--chdir={plan.workdir or '.'}",
            f"--partition={plan.resources.partition}",
            f"--nodes={plan.resources.nodes}",
            f"--output={log_path}",
            "--open-mode=append",
        ]
        if plan.resources.gpus_per_node:
            arguments.append(f"--gpus-per-node={plan.resources.gpus_per_node}")
        if plan.resources.time_limit_minutes:
            arguments.append(f"--time={plan.resources.time_limit_minutes}")
        # The environment is the plan's. `--export` with an explicit list keeps
        # the login environment of whoever started the control plane out of the
        # job, so a run does not depend on who submitted it.
        if plan.environment:
            exported = ",".join(f"{key}={value}" for key, value in sorted(plan.environment.items()))
            arguments.append(f"--export={exported}")
        arguments.extend(self.submit_arguments)
        arguments.append("--wrap=" + " ".join(plan.command))

        result = self._run(arguments)
        match = _SUBMITTED.search(result)
        if match is None:
            msg = (
                f"sbatch did not report a job identifier. It said: {result.strip()!r}. "
                "The submission is presumed to have failed; nothing is queued."
            )
            raise SlurmError(msg)

        job_id = match.group(1)
        self._logs[job_id] = str(log_path)
        return JobHandle(driver=self.name, job_id=job_id)

    # -- observe -----------------------------------------------------------

    def poll(self, handle: JobHandle) -> JobStatus:
        """Return the job's current status, from the queue or the accounting.

        Asks `squeue` first because it is cheap and current, then `sacct`
        because `squeue` forgets a finished job and its silence must not be
        read as "no such job".
        """
        live = self._from_squeue(handle.job_id)
        if live is not None:
            return live

        recorded = self._from_sacct(handle.job_id)
        if recorded is not None:
            return recorded

        return JobStatus(
            state=JobState.FAILED,
            message=(
                f"Slurm knows nothing of job {handle.job_id}: it is neither queued nor "
                "in the accounting database. Either it was purged, or it was never "
                "accepted."
            ),
        )

    def logs(self, handle: JobHandle) -> str:
        """Return whatever the job has written so far."""
        recorded = self._logs.get(handle.job_id)
        if recorded is None:
            return ""
        path = Path(recorded)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    # -- cancel ------------------------------------------------------------

    def cancel(self, handle: JobHandle) -> JobStatus:
        """Cancel the job and return the state it settled in. AC-F13.

        Cancelling something that has already finished is not an error; the
        status returned is the one it actually reached, because rewriting a
        completed job as cancelled would be a less true record.
        """
        current = self.poll(handle)
        if current.state in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}:
            return current

        self._run([self.scancel, handle.job_id], check=False)
        settled = self.poll(handle)
        if settled.state in {JobState.PENDING, JobState.RUNNING}:
            # scancel is asynchronous. The job is going, and saying so is more
            # useful than reporting it as still running.
            return JobStatus(
                state=JobState.CANCELLED,
                node=settled.node,
                message="scancel accepted; Slurm has not yet reaped the job",
            )
        return settled

    # -- internals ---------------------------------------------------------

    def _from_squeue(self, job_id: str) -> JobStatus | None:
        output = self._run(
            [self.squeue, "--job", job_id, "--noheader", "--format=%T|%N"], check=False
        )
        line = output.strip().splitlines()[0] if output.strip() else ""
        if not line:
            return None
        state, _, node = line.partition("|")
        return JobStatus(state=_state_of(state), node=node.strip() or None)

    def _from_sacct(self, job_id: str) -> JobStatus | None:
        output = self._run(
            [
                self.sacct,
                "--jobs",
                job_id,
                "--noheader",
                "--parsable2",
                "--format=JobID,State,ExitCode,NodeList",
            ],
            check=False,
        )
        for line in output.splitlines():
            fields = line.split("|")
            if len(fields) < 4:
                continue
            # The job's own row, not its `.batch` or `.extern` step. Those
            # carry a different state exactly when something went wrong.
            if "." in fields[0]:
                continue
            return JobStatus(
                state=_state_of(fields[1]),
                exit_code=_exit_code(fields[2]),
                node=fields[3].strip() or None,
                message=fields[1].strip() or None,
            )
        return None

    def _log_path(self, plan: JobPlan) -> Path:
        # `%j` is Slurm's job identifier and `%a` the array index, expanded by
        # Slurm rather than here: the index is not known at submission.
        return Path(plan.workdir or ".") / "slurm-%A_%a.out"

    def _run(self, arguments: list[str], *, check: bool = True) -> str:
        executable = shutil.which(arguments[0])
        if executable is None:
            msg = (
                f"{arguments[0]} is not on the path. This driver talks to Slurm through "
                "its command line tools, and needs to run somewhere they exist -- a "
                "submit host, not a container without them."
            )
            raise SlurmError(msg)

        try:
            completed = subprocess.run(  # noqa: S603
                [executable, *arguments[1:]],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as expired:
            msg = f"{arguments[0]} did not answer within {self.timeout}s"
            raise SlurmError(msg) from expired

        if check and completed.returncode != 0:
            msg = (
                f"{arguments[0]} exited {completed.returncode}: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
            raise SlurmError(msg)
        return completed.stdout


def _state_of(raw: str) -> JobState:
    """Map a Slurm state word onto one of the five states of SAD 8.2."""
    match = _STATE_WORD.match(raw.strip())
    word = match.group(1) if match else raw.strip().upper()
    return _STATES.get(word, JobState.FAILED)


def _exit_code(raw: str) -> int | None:
    """Read the job's exit code from Slurm's `code:signal` pair.

    A job killed by a signal reports `0:9`, and reading only the first field
    would call that a success. The signal is returned as `128 + n`, which is
    the shell convention the retry policy already understands.
    """
    code, _, signal_number = raw.strip().partition(":")
    if not code.isdigit():
        return None
    if signal_number.isdigit() and int(signal_number) != 0:
        return 128 + int(signal_number)
    return int(code)


#: The object the entry point resolves to.
driver = SlurmDriver()

__all__ = ["CAPABILITIES", "NAME", "SlurmDriver", "SlurmError", "driver"]
