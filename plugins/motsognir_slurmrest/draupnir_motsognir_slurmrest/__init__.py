"""A ScheduleDriver for Slurm, over `slurmrestd`.

The same interface as `motsognir.slurm/v1` and a different way of reaching the
scheduler. That driver shells out to `sbatch`, `squeue`, `sacct` and `scancel`,
which needs those binaries and a `munge` key on whatever host the control plane
runs on. At Sindri it has neither: VLD-INF-SINDRI-001 Rev 3.3 installs
`slurm-wlm` on REGIN and `slurmd` on the three appliances, and ALVISS -- the
host SAD Decision S3 puts the control plane on -- gets neither. The control
plane also runs in a distroless container, which by construction has no shell
to run them from.

So the two are alternative installations of one interface, which is what the
plug-in system is for. A host with the binaries installs the shim; a host
without them installs this. Nothing in the core changes either way, and
MOTSOGNIR still owns placement, array concurrency and retry (SAD 5.2).

**Four things the code below is shaped by.**

*The token is never held.* It arrives as a callable that is asked for a value
at the moment a request is made, so this object can be constructed, logged,
repr'd and pickled without a credential being anywhere in it. SVALINN issues it
as a lease with an expiry (SAD 9.4), and a driver that cached the string would
outlive the lease and turn an expiry into a 401 nobody could explain.

*The HTTP client is injected.* An outbound call has to declare a destination, a
purpose, a run and an approving policy, and that broker is SVALINN's -- which
this may not import, by the `drivers-depend-on-interfaces-only` contract. So
the caller supplies the client, and where that client came from is the
composition root's business. The default is a plain one, for a test and for a
development machine.

*`squeue` forgets and so does the REST surface.* `GET /slurm/.../job/{id}`
answers about the queue, and a job that finished more than `MinJobAge` ago is
no longer in it. The accounting database remembers, at
`GET /slurmdb/.../job/{id}`, and only a job neither knows is unknown. This is
the same two-step the shim performs against `squeue` and `sacct`, for the same
reason.

*An array element is `<job>_<index>`.* That is the identifier throughout.
Passing the array's own identifier where an element's was meant cancels fifty
six jobs instead of one.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

from draupnir.interfaces.types import JobHandle, JobPlan, JobState, JobStatus, NodeState

#: The versioned entry point name. Must match the key in pyproject.toml.
NAME: Final = "motsognir.slurmrest/v1"

#: The same capabilities as the shim: it is the same scheduler. `array` is the
#: one that matters, because the core refuses to plan an array against a driver
#: that has not said it can run one (AC-F5).
CAPABILITIES: Final = frozenset({"slurm", "array", "multinode", "gpu"})

#: The `slurmrestd` API version this speaks. Named rather than discovered: an
#: OpenAPI version is a contract, and picking whichever the server happens to
#: offer would make a scheduler upgrade change what this sends without anybody
#: choosing to.
API_VERSION: Final = "v0.0.40"

#: How Slurm's job states map onto the five of SAD 8.2. Anything not listed is
#: treated as failed rather than as running, so an unrecognised state stops a
#: pipeline instead of hanging it.
#:
#: The same table as the shim's, because it is a fact about Slurm rather than
#: about a transport. The two are separate distributions and neither may import
#: the other, so `tests/contract/test_reference_drivers.py` asserts they agree
#: rather than leaving two copies to drift.
STATES: Final[Mapping[str, JobState]] = {
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


#: Node states that will not accept a job. The same set the sbatch shim uses,
#: because it is a fact about Slurm rather than about a transport; the two are
#: separate distributions and a contract test asserts they agree.
UNAVAILABLE_NODE_STATES: Final[frozenset[str]] = frozenset(
    {
        "DOWN",
        "DRAIN",
        "DRAINED",
        "DRAINING",
        "FAIL",
        "FAILING",
        "MAINT",
        "UNKNOWN",
        "NOT_RESPONDING",
    }
)


class SlurmRestError(RuntimeError):
    """Raised when slurmrestd cannot be reached or refuses a request."""


class Response(Protocol):
    """The part of an HTTP response this driver needs.

    Structural, so a test's stub is a stub rather than a subclass of somebody
    else's client library.
    """

    status_code: int

    def json(self) -> Any:
        """The decoded body."""
        ...

    @property
    def text(self) -> str:
        """The raw body, for an error this could not decode."""
        ...


class Client(Protocol):
    """The part of an HTTP client this needs.

    `httpx.Client` satisfies it, and so does whatever the composition root
    wraps one in to put the call through SVALINN's egress broker.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = ...,
        json: Any = ...,
    ) -> Response:
        """Perform one request."""
        ...


def _default_client(timeout: float) -> Client:
    """A plain client, for a development machine and for the harness."""
    import httpx

    return httpx.Client(timeout=timeout)


@dataclass
class SlurmRestDriver:
    """Submits `JobPlan`s to Slurm through `slurmrestd`."""

    #: Where slurmrestd answers. VLD-INF-SINDRI-001 puts REGIN on the
    #: management fabric at 10.10.0.5 and on Fabric 2 at 10.20.0.5; ALVISS
    #: reaches it on the latter.
    base_url: str = "http://regin.sindri.veldris.internal:6820"

    #: Asked for the token at the moment of the request, never before. Returns
    #: the empty string on a development machine, where slurmrestd runs with
    #: `AuthType=auth/local` and wants no token at all.
    #:
    #: Kept out of the repr. A callable is usually a closure, whose repr says
    #: nothing, but `functools.partial(fetch, "the-token")` is a perfectly
    #: natural way to wire one and reprs the value it carries.
    token: Callable[[], str] = field(default=lambda: "", repr=False)

    #: The account the token was issued for. Slurm requires it alongside the
    #: token: the JWT says who, and this says who to act as.
    user_name: str = ""

    api_version: str = API_VERSION
    timeout: float = 30.0

    #: Also kept out of the repr: a client is an object of somebody else's,
    #: and what it holds is not this driver's to publish. A test double that
    #: records requests holds every header it was sent.
    client: Client | None = field(default=None, repr=False)

    name: str = NAME
    capabilities: frozenset[str] = CAPABILITIES

    _logs: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """Build a client if the caller did not supply one."""
        if self.client is None:
            self.client = _default_client(self.timeout)

    # -- submit ------------------------------------------------------------

    def submit(self, plan: JobPlan, name: str = "") -> JobHandle:
        """Submit the plan and return the handle Slurm's identifier gives."""
        log_path = self._log_path(plan)
        body = {
            "script": self._script(plan),
            "job": self._job(plan, log_path, name),
        }

        payload = self._call("POST", f"/slurm/{self.api_version}/job/submit", body=body)
        job_id = payload.get("job_id")
        if job_id is None:
            errors = payload.get("errors") or payload.get("warnings") or []
            msg = (
                f"slurmrestd accepted the request and returned no job identifier. It said: "
                f"{json.dumps(errors)[:400]}. The submission is presumed to have failed; "
                "nothing is queued."
            )
            raise SlurmRestError(msg)

        identifier = str(job_id)
        self._logs[identifier] = str(log_path)
        return JobHandle(driver=self.name, job_id=identifier)

    # -- observe -----------------------------------------------------------

    def poll(self, handle: JobHandle) -> JobStatus:
        """Return the job's current status, from the queue or the accounting.

        The queue first because it is cheap and current, then the accounting
        database, because the queue forgets a finished job and its silence must
        not be read as "no such job".
        """
        live = self._from_queue(handle.job_id)
        if live is not None:
            return live

        recorded = self._from_accounting(handle.job_id)
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
        """Return whatever the job has written so far.

        The REST surface has no log endpoint, so this reads the file the job
        was told to write. That works when the plan's working directory is on
        the shared vault, which is where VLD-INF-SINDRI-001 puts run output
        (`/forge/runs`); it returns nothing when the directory is local to an
        appliance, and nothing is the honest answer rather than an error,
        because the job may equally not have written anything yet.
        """
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

        self._call("DELETE", f"/slurm/{self.api_version}/job/{handle.job_id}", tolerate=True)
        settled = self.poll(handle)
        if settled.state in {JobState.PENDING, JobState.RUNNING}:
            # The cancellation is asynchronous. The job is going, and saying so
            # is more useful than reporting it as still running.
            return JobStatus(
                state=JobState.CANCELLED,
                node=settled.node,
                message="slurmrestd accepted the cancellation; Slurm has not yet reaped the job",
            )
        return settled

    # -- the cluster interface ---------------------------------------------

    def find(self, name: str) -> JobHandle | None:
        """The job this scheduler holds under `name`, or None.

        slurmrestd has no lookup by name, so this reads the queue and filters.
        That is one request per call and the queue is small -- fifty six
        elements at Sindri -- so it costs nothing worth optimising, and the
        alternative is the control plane remembering something it is designed
        not to.
        """
        try:
            payload = self._call("GET", f"/slurm/{self.api_version}/jobs")
        except SlurmRestError:
            return None

        for record in payload.get("jobs") or []:
            if not isinstance(record, Mapping) or record.get("name") != name:
                continue
            # An array reports one record per element; the array's own
            # identifier is what addresses all of it.
            job_id = str(record.get("array_job_id") or record.get("job_id") or "").split("_")[0]
            if not job_id or job_id == "0":
                job_id = str(record.get("job_id") or "")
            if job_id:
                return JobHandle(driver=self.name, job_id=job_id, node=record.get("nodes") or None)
        return None

    def nodes(self) -> tuple[NodeState, ...]:
        """Every node the scheduler knows, and whether it can take work."""
        try:
            payload = self._call("GET", f"/slurm/{self.api_version}/nodes")
        except SlurmRestError:
            return ()

        found: list[NodeState] = []
        for record in payload.get("nodes") or []:
            if not isinstance(record, Mapping) or not record.get("name"):
                continue
            states = record.get("state")
            words = states if isinstance(states, list) else [states]
            spelled = "+".join(str(word).upper() for word in words if word)
            found.append(
                NodeState(
                    name=str(record["name"]),
                    state=spelled,
                    available=not (
                        {str(word).upper() for word in words if word} & UNAVAILABLE_NODE_STATES
                    ),
                )
            )
        return tuple(found)

    def requeue(self, handle: JobHandle, index: int) -> JobStatus:
        """Not available over this transport, and said rather than approximated.

        AC-F6 asks for one element to be retried without disturbing the other
        fifty five, and the mechanism for that is `scontrol requeue
        <job>_<index>`. slurmrestd v0.0.40 exposes no requeue: it can submit,
        read and cancel a job, and it cannot put one back on the queue.

        The approximations are both worse than a refusal. Cancelling the
        element and submitting a replacement gives it a new job identifier,
        which severs it from the array and loses the throttle and the
        accounting record that ties the fifty six together. Requeuing the array
        restarts all of them, discarding the compute of the ones that
        succeeded -- which is the exact failure AC-F6 exists to prevent.

        So this refuses and names what to do instead. See RF-E24.
        """
        del index
        msg = (
            f"slurmrestd {self.api_version} exposes no requeue, so element retry is not "
            f"available over this transport (job {handle.job_id}). Requeue it from REGIN with "
            "`scontrol requeue <job>_<index>`, or install `motsognir.slurm/v1` on a host with "
            "the Slurm client tools. Resubmitting the element instead would give it a new job "
            "identifier and sever it from its array (AC-F6)."
        )
        raise SlurmRestError(msg)

    # -- internals ---------------------------------------------------------

    def _script(self, plan: JobPlan) -> str:
        """The batch script. One command, quoted so a shell reassembles it.

        `shlex.join` rather than `" ".join`: a plan's command is an argument
        vector, and joining it with spaces turns `sh -c 'a && b'` into
        something a shell reads as four words. The shim had exactly that bug.
        """
        return "#!/bin/bash\n" + shlex.join(plan.command) + "\n"

    def _job(self, plan: JobPlan, log_path: Path, name: str = "") -> dict[str, Any]:
        """The job description slurmrestd expects."""
        job: dict[str, Any] = {
            "partition": plan.resources.partition,
            "nodes": str(plan.resources.nodes),
            "current_working_directory": plan.workdir or ".",
            "standard_output": str(log_path),
            "standard_error": str(log_path),
            # A list of `K=V`, and it must not be empty: slurmrestd refuses a
            # submission with no environment, and the refusal names neither the
            # field nor the reason.
            "environment": [f"{key}={value}" for key, value in sorted(plan.environment.items())]
            or ["DRAUPNIR=1"],
        }

        if plan.resources.gres:
            # The estate's own form, from the placement. `tres_per_node` wants
            # the `gres/` prefix that `--gres` does not, and that difference is
            # this driver's to know: the plan carries what the estate declares
            # and each transport spells it the way its own surface expects.
            job["tres_per_node"] = f"gres/{plan.resources.gres}"
        elif plan.resources.gpus_per_node:
            job["tres_per_node"] = f"gres/gpu:{plan.resources.gpus_per_node}"

        if plan.resources.time_limit_minutes:
            job["time_limit"] = {"set": True, "number": plan.resources.time_limit_minutes}

        if plan.resources.array is not None:
            # AC-F5: fifty six queued, three running. The throttle is part of
            # the string here as it is on the command line.
            job["array"] = plan.resources.array.directive()

        if name:
            # What `find` looks for. A restarted worker asks the scheduler
            # whether it already submitted this run rather than remembering.
            job["name"] = name

        return job

    def _log_path(self, plan: JobPlan) -> Path:
        # `%A` is the array job's identifier and `%a` the element index,
        # expanded by Slurm rather than here: the index is not known at
        # submission.
        return Path(plan.workdir or ".") / "slurm-%A_%a.out"

    def _headers(self) -> dict[str, str]:
        """The authentication headers, built at the moment of the call.

        The token is read here and nowhere else, and it is never stored on the
        instance: a driver that cached it would outlive the lease.
        """
        headers = {"Accept": "application/json"}
        token = self.token()
        if token:
            headers["X-SLURM-USER-TOKEN"] = token
        if self.user_name:
            headers["X-SLURM-USER-NAME"] = self.user_name
        return headers

    def _call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        tolerate: bool = False,
    ) -> dict[str, Any]:
        """One request, with the refusal spelled out.

        `tolerate` is for a cancellation: Slurm answers a cancel of a job it
        has already reaped with an error, and that is not a failure of the
        cancellation.
        """
        assert self.client is not None  # noqa: S101 -- __post_init__ established it
        url = f"{self.base_url.rstrip('/')}{path}"
        try:
            response = self.client.request(method, url, headers=self._headers(), json=body)
        except Exception as error:
            msg = (
                f"slurmrestd at {self.base_url} could not be reached: "
                f"{type(error).__name__}: {error}"
            )
            raise SlurmRestError(msg) from error

        if response.status_code == 401:
            msg = (
                f"slurmrestd refused the token ({method} {path}). The lease may have expired, "
                "or the JWT key on REGIN may have been rotated."
            )
            raise SlurmRestError(msg)

        if response.status_code >= 400 and not tolerate:
            msg = (
                f"slurmrestd returned {response.status_code} for {method} {path}: "
                f"{response.text[:400]}"
            )
            raise SlurmRestError(msg)

        try:
            decoded = response.json()
        except Exception:
            if tolerate:
                return {}
            msg = f"slurmrestd returned a body that is not JSON for {method} {path}"
            raise SlurmRestError(msg) from None

        return decoded if isinstance(decoded, dict) else {}

    def _from_queue(self, job_id: str) -> JobStatus | None:
        try:
            payload = self._call("GET", f"/slurm/{self.api_version}/job/{job_id}")
        except SlurmRestError:
            return None
        return _status_of(payload.get("jobs") or [])

    def _from_accounting(self, job_id: str) -> JobStatus | None:
        try:
            payload = self._call("GET", f"/slurmdb/{self.api_version}/job/{job_id}")
        except SlurmRestError:
            return None
        return _status_of(payload.get("jobs") or [])


def _status_of(jobs: list[Any]) -> JobStatus | None:
    """Read one job record into a status, or None when there is no record."""
    if not jobs:
        return None
    record = jobs[0]
    if not isinstance(record, Mapping):
        return None

    return JobStatus(
        state=state_of(_state_word(record.get("job_state"))),
        exit_code=_exit_code(record.get("exit_code")),
        node=(record.get("nodes") or None),
        message=_state_word(record.get("job_state")) or None,
    )


def _state_word(raw: Any) -> str:
    """The state, from either shape slurmrestd uses.

    v0.0.40 reports `job_state` as a list of state words; earlier versions
    report a bare string. Both are read rather than one being assumed, because
    the difference is silent: a list read as a string yields no match, and an
    unmatched state maps to FAILED.
    """
    if isinstance(raw, str):
        return raw.strip().upper()
    if isinstance(raw, list) and raw:
        first = raw[0]
        return first.strip().upper() if isinstance(first, str) else ""
    return ""


def state_of(word: str) -> JobState:
    """Map a Slurm state word onto one of the five states of SAD 8.2."""
    return STATES.get(word.strip().upper(), JobState.FAILED)


def _exit_code(raw: Any) -> int | None:
    """Read the job's exit code from slurmrestd's nested shape.

    v0.0.40 reports `exit_code` as `{"status": ["SUCCESS"], "return_code":
    {"set": true, "number": 0}, "signal": {...}}`. A job killed by a signal
    reports a zero return code, and reading only that would call it a success,
    so the signal is returned as `128 + n` -- the shell convention the retry
    policy already understands.
    """
    if isinstance(raw, int):
        return raw
    if not isinstance(raw, Mapping):
        return None

    signal = raw.get("signal")
    if isinstance(signal, Mapping):
        number = signal.get("signal_id", signal.get("number"))
        if isinstance(number, int) and number:
            return 128 + number

    code = raw.get("return_code")
    if isinstance(code, Mapping):
        number = code.get("number")
        return number if isinstance(number, int) else None
    return code if isinstance(code, int) else None


#: The entry point. One instance, constructed with the defaults; a deployment
#: replaces it through the loader with one that has a token and a brokered
#: client.
driver = SlurmRestDriver()

__all__ = [
    "API_VERSION",
    "CAPABILITIES",
    "NAME",
    "STATES",
    "UNAVAILABLE_NODE_STATES",
    "Client",
    "Response",
    "SlurmRestDriver",
    "SlurmRestError",
    "driver",
    "state_of",
]
