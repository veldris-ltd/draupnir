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

**A job is a script, not an argument list.** This submitted with `--wrap` and
`--export` and both were wrong in ways that only appear against a real Slurm.

`--wrap` takes one shell command, and the plan's command is an argument vector:
joining it with spaces turned `sh -c 'a && b'` into words a shell read
separately, so a redirect happened in the wrong place and the configuration
file the trainer needed was never written. `--export` separates entries with
commas, and a training configuration is JSON, which is mostly commas -- so the
one variable carrying it arrived as several malformed ones. And naming
variables in `--export` without `ALL` propagates only those, which left the job
with no `PATH`: every batch script in VLD-INF-SINDRI-001 Part 5 begins by
sourcing a virtual environment, and none of them would have found it.

Writing a script removes all three questions rather than answering them. There
is no quoting problem because there is no argument to quote into; there is no
comma problem because a shell assignment is not a comma-separated list; and the
job inherits a usable environment because `sbatch` propagates one by default.
It also leaves the artefact an operator wants when asking what actually ran,
next to the log, in the run's own directory.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from draupnir.interfaces.types import JobHandle, JobPlan, JobState, JobStatus, NodeState

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
    sinfo: str = "sinfo"
    scontrol: str = "scontrol"
    #: Seconds before a Slurm command is presumed wedged. A control plane that
    #: blocks forever on `squeue` stops observing every other run too.
    timeout: float = 30.0
    #: Extra arguments every submission carries, e.g. `--account`.
    submit_arguments: tuple[str, ...] = ()

    #: Slurm's own default for `SchedulerParameters=max_script_size`. A script
    #: over it is truncated rather than refused, which produces a job that runs
    #: a prefix of what was meant; refusing here turns that into a submission
    #: that did not happen.
    max_script_bytes: int = 4 * 1024 * 1024

    #: Lines run before the command, after the plan's own variables are set.
    #: Site configuration: this is where an estate says how a job establishes
    #: the environment it needs, which at Sindri is
    #: `("source /forge/venv/bin/activate",)` -- the first line of every batch
    #: script in VLD-INF-SINDRI-001 Part 5.
    #:
    #: **It also decides where the job's environment comes from**, and the
    #: obvious answer is wrong here. `sbatch` propagates the submitting
    #: environment by default, and the submitter is the DRAUPNIR worker, which
    #: runs in a distroless container; the job runs on an appliance. Inheriting
    #: that PATH gives the job directories that exist in the container and on
    #: no machine the job can land on.
    #:
    #: So a site that says how to set a job up gets `--export=NONE` and an
    #: environment that is exactly what the script sets -- reproducible, and
    #: independent of who submitted it. A site that says nothing keeps the
    #: default, because that is what makes a development machine work.
    preamble: tuple[str, ...] = ()

    _logs: dict[str, str] = field(default_factory=dict, repr=False)

    # -- submit ------------------------------------------------------------

    def submit(self, plan: JobPlan, name: str = "") -> JobHandle:
        """Write the job as a script and submit it by path.

        By path rather than by `--wrap`, and by script rather than by
        `--export`: see the note at the top of this module for what each of
        those did to a real submission.
        """
        log_path = self._log_path(plan)
        script_path = self._script_path(plan)
        script = self.render_script(plan, log_path, name)

        if len(script.encode("utf-8")) > self.max_script_bytes:
            largest = max(plan.environment or {"": ""}, key=lambda k: len(plan.environment[k]))
            msg = (
                f"the job script is {len(script.encode('utf-8'))} bytes, over the "
                f"{self.max_script_bytes} byte limit Slurm applies. The largest environment "
                f"value is {largest!r}. Slurm truncates a script over its limit rather than "
                "refusing it, which produces a job that runs a prefix of what was meant, so "
                "this refuses instead. Nothing is queued."
            )
            raise SlurmError(msg)

        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script, encoding="utf-8", newline="\n")

        arguments = [self.sbatch, *self.submit_arguments, str(script_path)]
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

    # -- the cluster interface ---------------------------------------------

    def find(self, name: str) -> JobHandle | None:
        """The job this scheduler holds under `name`, or None.

        `squeue --name` covers a job that has not finished, which is the case
        that matters: it is what stops a restarted worker submitting a second
        copy of something it already submitted. A job that has finished is
        gone from the queue, and a caller that finds nothing here should be
        looking at what the chain recorded rather than resubmitting.
        """
        output = self._run(
            [self.squeue, "--name", name, "--noheader", "--format=%i|%N"], check=False
        )
        line = output.strip().splitlines()[0] if output.strip() else ""
        if not line:
            return None
        job_id, _, node = line.partition("|")
        # An array reports one row per element; the array's own identifier is
        # the part before the underscore, and that is what addresses all of it.
        return JobHandle(
            driver=self.name, job_id=job_id.strip().split("_")[0], node=node.strip() or None
        )

    def nodes(self) -> tuple[NodeState, ...]:
        """Every node the scheduler knows, and whether it can take work.

        `sinfo -N` lists one row per node. A node is available only in a state
        that will actually accept a job: DOWN, DRAIN, DRAINING, FAIL and
        MAINT will not, and the `*` suffix Slurm appends means it is not
        responding, which is the same answer.
        """
        output = self._run([self.sinfo, "--noheader", "--Node", "--format=%N|%T"], check=False)
        found: list[NodeState] = []
        for line in output.splitlines():
            name, _, state = line.partition("|")
            if not name.strip():
                continue
            word = state.strip()
            found.append(
                NodeState(name=name.strip(), state=word, available=_node_is_available(word))
            )
        return tuple(found)

    def requeue(self, handle: JobHandle, index: int) -> JobStatus:
        """Requeue one element of an array, leaving the other fifty five alone.

        AC-F6. `scontrol requeue` rather than a fresh submission: resubmitting
        makes a new job identifier, which severs the element from its array and
        loses both the throttle and the accounting record that ties them
        together.
        """
        element = f"{handle.job_id}_{index}"
        self._run([self.scontrol, "requeue", element], check=False)
        return self.poll(JobHandle(driver=self.name, job_id=element))

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

    def _script_path(self, plan: JobPlan) -> Path:
        """Where the job script is written. Beside the log, deliberately.

        It is kept after submission rather than cleaned up: it is the exact
        thing that ran, and the first question after a surprising result is
        what was actually submitted. A script in a temporary directory answers
        that question only until the job finishes.
        """
        return Path(plan.workdir or ".") / "draupnir-job.sbatch"

    def render_script(self, plan: JobPlan, log_path: Path, name: str = "") -> str:
        """The batch script for a plan.

        Public and separate from `submit` so an operator can be shown what will
        run before it runs, and so a test can read it without intercepting a
        subprocess. The directives are `#SBATCH` lines rather than command line
        arguments so the file is self contained: it is the same shape as the
        scripts in VLD-INF-SINDRI-001 Part 5, and it can be resubmitted by hand
        during an incident without reconstructing an argument list.
        """
        directives = [
            f"#SBATCH --chdir={plan.workdir or '.'}",
            f"#SBATCH --partition={plan.resources.partition}",
            f"#SBATCH --nodes={plan.resources.nodes}",
            f"#SBATCH --output={log_path}",
            "#SBATCH --open-mode=append",
        ]
        if plan.resources.gres:
            # The form the estate is configured for. VLD-INF-SINDRI-001
            # declares `Name=gpu Type=gb10` and every batch script in Part 5
            # asks for `gpu:gb10:1`; an untyped count against a typed GRES
            # resolves on some configurations and not others, and this cluster
            # has never been run with one.
            directives.append(f"#SBATCH --gres={plan.resources.gres}")
        elif plan.resources.gpus_per_node:
            directives.append(f"#SBATCH --gpus-per-node={plan.resources.gpus_per_node}")
        if plan.resources.time_limit_minutes:
            directives.append(f"#SBATCH --time={plan.resources.time_limit_minutes}")
        if plan.resources.array is not None:
            # AC-F5, and the estate's whole placement strategy: fifty six jobs
            # queued, three running, one per appliance. The `%M` is a throttle
            # rather than a count, so utilisation does not depend on the
            # control plane being awake to top a queue up (SAD 11.2).
            directives.append(f"#SBATCH --array={plan.resources.array.directive()}")
        if name:
            # What `find` looks for. A job the scheduler holds under a name
            # derived from the run is a job a restarted worker can recognise
            # without having recorded anything.
            directives.append(f"#SBATCH --job-name={name}")
        if self.preamble:
            # See the field's note: the submitting environment is a container's
            # and the job runs somewhere else, so a site that can establish its
            # own gets one rather than inheriting a PATH that does not resolve.
            directives.append("#SBATCH --export=NONE")

        # `shlex.quote` on every value. A training configuration is JSON, and
        # the characters that break an assignment -- quotes, spaces, dollars,
        # newlines -- are all of them present in one.
        exports = [
            f"export {key}={shlex.quote(value)}" for key, value in sorted(plan.environment.items())
        ]

        # `shlex.join` on the command. The plan is an argument vector and this
        # is the only place it becomes text; joining it with spaces is what
        # made `sh -c 'a && b'` fall apart.
        body = shlex.join(plan.command)

        lines = [
            "#!/bin/bash",
            *directives,
            "",
            "# Written by DRAUPNIR from a rendered JobPlan. Kept after submission:",
            "# it is the exact thing that ran.",
            "set -euo pipefail",
            "",
            *exports,
            *(["", *self.preamble] if self.preamble else []),
            "",
            body,
            "",
        ]
        return "\n".join(lines)

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


#: Node states that will not accept a job. A node in one of these is not part
#: of the estate for placement purposes, whatever the inventory says.
UNAVAILABLE_NODE_STATES: frozenset[str] = frozenset(
    {"DOWN", "DRAIN", "DRAINED", "DRAINING", "FAIL", "FAILING", "MAINT", "UNKNOWN"}
)


def _node_is_available(raw: str) -> bool:
    """Whether a node in this state can take work.

    Slurm suffixes a state with `*` when the node is not responding, and a
    node that is not responding is not going to run anything whatever the word
    in front of the asterisk says.
    """
    word = raw.strip().upper()
    if word.endswith("*"):
        return False
    return word.rstrip("$~#") not in UNAVAILABLE_NODE_STATES


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
