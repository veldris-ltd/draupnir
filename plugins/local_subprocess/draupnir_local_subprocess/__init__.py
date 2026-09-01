"""A ScheduleDriver that runs a job plan as a local child process.

SAD 8.2 lists "local subprocess for development" as an implementation of
`draupnir.schedule`, beside Slurm and Ray. This is that implementation, and it
is also the worked example: everything here is what SAD 5.2 permits a schedule
driver to do -- submit, observe, cancel, read logs -- and nothing here knows
what the job computes.

Two things are worth copying from it.

The driver holds no state that matters. A handle carries the job identifier,
and everything else lives in the process table and a log file, so a driver
restarted mid-run can still find what it started. A driver that kept its only
record of a submitted job in memory would lose the estate's work to a restart.

Cancelling an already finished job is not an error. AC-F13 requires a
cancelled run to be left in a defined state, never an ambiguous one, and the
simplest way to get that wrong is to raise when the race is lost.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from draupnir.interfaces.types import JobHandle, JobPlan, JobState, JobStatus

#: The versioned entry point name. It must match the key in pyproject.toml:
#: the core resolves what a specification names, and a driver whose declared
#: name differs from its registration is refused at load.
NAME = "motsognir.local_subprocess/v1"

#: `local` says what this is. `array` says it can run several at once, which
#: it can, because each submission is its own process.
CAPABILITIES = frozenset({"local", "array"})


@dataclass
class _Job:
    """One submitted process and where its output is going."""

    process: subprocess.Popen[bytes]
    log_path: Path
    log_file: object
    cancelled: bool = False


@dataclass
class LocalSubprocessDriver:
    """Runs a `JobPlan` as a child process on this machine."""

    name: str = NAME
    capabilities: frozenset[str] = CAPABILITIES
    #: Where log files go when a plan does not name a usable working directory.
    log_root: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "draupnir-local")

    _jobs: dict[str, _Job] = field(default_factory=dict, repr=False)

    # -- submit ------------------------------------------------------------

    def submit(self, plan: JobPlan) -> JobHandle:
        """Start the plan's command and return a handle that identifies it."""
        job_id = uuid.uuid4().hex[:16]
        workdir = self._workdir(plan)
        log_path = self._log_path(job_id)
        log_file = log_path.open("wb")

        # The environment is the plan's, not this process's. A plan that
        # inherited the control plane's environment would render differently
        # depending on who started the control plane, which is the same class
        # of irreproducibility Decision S5 forbids in `render`.
        process = subprocess.Popen(  # noqa: S603
            list(plan.command),
            cwd=str(workdir),
            env={**_base_environment(), **dict(plan.environment)},
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        self._jobs[job_id] = _Job(process=process, log_path=log_path, log_file=log_file)
        return JobHandle(driver=self.name, job_id=job_id, node="localhost")

    # -- observe -----------------------------------------------------------

    def poll(self, handle: JobHandle) -> JobStatus:
        """Return the job's current state. Safe to call repeatedly."""
        job = self._jobs.get(handle.job_id)
        if job is None:
            return JobStatus(
                state=JobState.FAILED,
                message=f"no job {handle.job_id!r} was submitted through this driver",
            )

        code = job.process.poll()
        if code is None:
            return JobStatus(state=JobState.RUNNING, node="localhost")

        self._close(job)
        if job.cancelled:
            return JobStatus(state=JobState.CANCELLED, exit_code=code, node="localhost")
        state = JobState.COMPLETED if code == 0 else JobState.FAILED
        return JobStatus(state=state, exit_code=code, node="localhost")

    def logs(self, handle: JobHandle) -> str:
        """Return whatever the job has written so far."""
        job = self._jobs.get(handle.job_id)
        if job is None:
            return ""
        if not job.log_path.exists():
            return ""
        return job.log_path.read_text(encoding="utf-8", errors="replace")

    # -- cancel ------------------------------------------------------------

    def cancel(self, handle: JobHandle) -> JobStatus:
        """Stop the job and return its resulting state.

        Cancelling a job that has already finished is not an error: it returns
        the state the job actually reached. AC-F13 wants a defined state, and
        rewriting a completed job as cancelled would be a less true one.
        """
        job = self._jobs.get(handle.job_id)
        if job is None:
            return JobStatus(
                state=JobState.FAILED,
                message=f"no job {handle.job_id!r} was submitted through this driver",
            )

        if job.process.poll() is not None:
            return self.poll(handle)

        job.cancelled = True
        _terminate(job.process)
        try:
            job.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            job.process.kill()
            job.process.wait(timeout=10)

        self._close(job)
        return JobStatus(
            state=JobState.CANCELLED,
            exit_code=job.process.returncode,
            node="localhost",
            message="cancelled by request",
        )

    # -- internals ---------------------------------------------------------

    def _workdir(self, plan: JobPlan) -> Path:
        candidate = Path(plan.workdir or ".")
        if candidate.is_dir():
            return candidate
        self.log_root.mkdir(parents=True, exist_ok=True)
        return self.log_root

    def _log_path(self, job_id: str) -> Path:
        self.log_root.mkdir(parents=True, exist_ok=True)
        return self.log_root / f"{job_id}.log"

    @staticmethod
    def _close(job: _Job) -> None:
        handle = job.log_file
        if handle is not None and not getattr(handle, "closed", True):
            handle.close()  # type: ignore[attr-defined]


def _base_environment() -> dict[str, str]:
    """The minimum a child process needs to start on this platform."""
    keep = ("PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "HOME", "LANG", "LC_ALL")
    return {name: os.environ[name] for name in keep if name in os.environ}


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """Ask the process to stop, in whatever way this platform provides."""
    if os.name == "nt":
        process.terminate()
        return
    # It may have finished between the poll and here; that is the race
    # cancelling always has, and losing it is not an error.
    with contextlib.suppress(ProcessLookupError):
        process.send_signal(signal.SIGTERM)


#: The object the entry point resolves to. A module-level instance rather than
#: the class, so that a driver holding scheduler state holds one lot of it.
driver = LocalSubprocessDriver()

__all__ = ["CAPABILITIES", "NAME", "LocalSubprocessDriver", "driver"]
