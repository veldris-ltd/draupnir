"""The slurmrestd ScheduleDriver, against a stubbed scheduler.

RF-E05. `motsognir.slurm/v1` shells out to `sbatch`, `squeue`, `sacct` and
`scancel`, and at Sindri the control plane has none of them: VLD-INF-SINDRI-001
Rev 3.3 installs `slurm-wlm` on REGIN and `slurmd` on the appliances, ALVISS
gets neither, and the container is distroless. So there is a second driver for
the same interface, and this is where it is held to it.

The whole published conformance suite runs here, which it cannot for the shim.
The only thing the shim needs a live Slurm for is the command layer, and the
only thing this needs one for is the HTTP client -- and a client is an argument.
Injecting one that answers the way slurmrestd answers puts submit, poll, cancel
and logs through the real code path rather than skipping them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from draupnir.interfaces.testing import ScheduleDriverConformance
from draupnir.interfaces.types import JobHandle, JobPlan, JobState, ResourceRequest
from draupnir_motsognir_slurmrest import (
    NAME,
    STATES,
    SlurmRestDriver,
    SlurmRestError,
)

# ---------------------------------------------------------------------------
# A stub that answers the way slurmrestd does
# ---------------------------------------------------------------------------


@dataclass
class Reply:
    """What the stub gives back. Structural, matching the driver's protocol."""

    status_code: int
    body: Any

    def json(self) -> Any:
        return self.body

    @property
    def text(self) -> str:
        return json.dumps(self.body)


@dataclass
class FakeSlurmRest:
    """slurmrestd, as far as this driver can tell.

    Records every request so a test can assert on what was actually sent,
    which is the only way to check that a token reached the header and not the
    job payload.
    """

    #: The states a job walks through on successive polls. The default settles,
    #: so the conformance suite's `_wait` terminates.
    walk: list[str] = field(default_factory=lambda: ["PENDING", "RUNNING", "COMPLETED"])
    submit_status: int = 200
    submit_body: Any = None
    queue_forgets_after: int | None = None

    requests: list[dict[str, Any]] = field(default_factory=list)
    _polls: int = 0
    _cancelled: bool = False

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Any = None,
        json: Any = None,
    ) -> Reply:
        self.requests.append(
            {"method": method, "url": url, "headers": dict(headers or {}), "json": json}
        )

        if method == "POST" and "/job/submit" in url:
            body = self.submit_body if self.submit_body is not None else {"job_id": 4242}
            return Reply(self.submit_status, body)

        if method == "DELETE":
            self._cancelled = True
            return Reply(200, {})

        if method == "GET" and "/slurmdb/" in url:
            return Reply(200, {"jobs": [self._record(self.walk[-1])]})

        if method == "GET":
            if self.queue_forgets_after is not None and self._polls >= self.queue_forgets_after:
                return Reply(200, {"jobs": []})
            word = (
                "CANCELLED" if self._cancelled else self.walk[min(self._polls, len(self.walk) - 1)]
            )
            self._polls += 1
            return Reply(200, {"jobs": [self._record(word)]})

        return Reply(404, {})

    def _record(self, word: str) -> dict[str, Any]:
        """v0.0.40's shape: a state list, and a nested exit code."""
        return {
            "job_state": [word],
            "nodes": "dvalin",
            "exit_code": {
                "return_code": {"set": True, "number": 0},
                "signal": {"set": False, "signal_id": 0},
            },
        }


def a_driver(**overrides: Any) -> SlurmRestDriver:
    client = overrides.pop("client", None) or FakeSlurmRest()
    settings: dict[str, Any] = {
        "base_url": "http://regin.sindri.veldris.internal:6820",
        "client": client,
        "user_name": "draupnir",
    }
    settings.update(overrides)
    return SlurmRestDriver(**settings)


# ---------------------------------------------------------------------------
# The published suite, in full
# ---------------------------------------------------------------------------


class TestSlurmRestConformance(ScheduleDriverConformance):
    """The whole suite, not the half the shim can manage.

    A driver whose only untestable dependency is an argument is a driver that
    can be held to its own contract.
    """

    @pytest.fixture
    def driver(self) -> Any:
        return a_driver()


# ---------------------------------------------------------------------------
# What the scheduler is actually sent
# ---------------------------------------------------------------------------


def a_plan(tmp_path: Any, **resources: Any) -> JobPlan:
    settings: dict[str, Any] = {"partition": "adapters", "nodes": 1}
    settings.update(resources)
    return JobPlan(
        command=("sh", "-c", 'printf "%s" "$CONFIG" > lf.json && llamafactory-cli train lf.json'),
        environment={"CONFIG": '{"a":1,"b":2}', "PYTHONHASHSEED": "0"},
        workdir=str(tmp_path),
        resources=ResourceRequest(**settings),
    )


def submitted(client: FakeSlurmRest) -> dict[str, Any]:
    """The one submission body the stub received."""
    posts = [r for r in client.requests if r["method"] == "POST"]
    assert len(posts) == 1, f"expected one submission, saw {len(posts)}"
    body = posts[0]["json"]
    assert isinstance(body, dict)
    return body


def test_the_command_survives_a_shell(tmp_path: Any) -> None:
    """The defect the shim has, not repeated here.

    A plan's command is an argument vector. Joining it with spaces turns
    `sh -c 'a && b'` into words a shell reads separately, and the redirect then
    happens in the wrong place: the configuration file is never written and the
    trainer runs against something that is not there.
    """
    import shlex

    client = FakeSlurmRest()
    plan = a_plan(tmp_path)
    a_driver(client=client).submit(plan)

    script = submitted(client)["script"]
    body = script.splitlines()[-1]
    assert shlex.split(body) == list(plan.command), (
        f"the script does not reassemble into the plan's command:\n{body}"
    )


def test_the_environment_survives_a_comma(tmp_path: Any) -> None:
    """The other defect the shim has.

    `sbatch --export` separates entries with commas, and the training
    configuration is JSON, which is mostly commas. Here the environment is a
    list, so a value containing one is a value rather than several.
    """
    client = FakeSlurmRest()
    a_driver(client=client).submit(a_plan(tmp_path))

    environment = submitted(client)["job"]["environment"]
    assert 'CONFIG={"a":1,"b":2}' in environment, environment


def test_an_empty_environment_is_never_sent(tmp_path: Any) -> None:
    """An empty environment is never sent.

    Slurmrestd refuses a submission with none, and names neither the field nor
    the reason when it does.
    """
    client = FakeSlurmRest()
    plan = JobPlan(
        command=("true",),
        workdir=str(tmp_path),
        resources=ResourceRequest(partition="adapters"),
    )
    a_driver(client=client).submit(plan)

    assert submitted(client)["job"]["environment"], "an empty environment was sent"


def test_the_typed_gres_the_estate_declares_is_what_is_asked_for(tmp_path: Any) -> None:
    """The GPU request uses the type the estate declares.

    VLD-INF-SINDRI-001 declares `Name=gpu Type=gb10` and every batch script in
    Part 5 asks for `gpu:gb10:1`. An untyped count against a typed GRES
    resolves on some configurations and not others, and this estate has never
    been run with one.

    The string comes from the placement rather than from driver configuration:
    the accelerator is a fact about the machines, and MOTSOGNIR is what knows
    the machines. The `gres/` prefix is this transport's own -- `tres_per_node`
    wants it and `--gres` does not.
    """
    client = FakeSlurmRest()
    a_driver(client=client).submit(a_plan(tmp_path, gpus_per_node=1, gres="gpu:gb10:1"))

    assert submitted(client)["job"]["tres_per_node"] == "gres/gpu:gb10:1"


def test_an_estate_that_declares_no_accelerator_still_gets_a_gpu(tmp_path: Any) -> None:
    """A development machine, where an untyped count is all there is to ask for."""
    client = FakeSlurmRest()
    a_driver(client=client).submit(a_plan(tmp_path, gpus_per_node=1))
    assert submitted(client)["job"]["tres_per_node"] == "gres/gpu:1"


def test_the_partition_nodes_and_working_directory_are_the_plans(tmp_path: Any) -> None:
    client = FakeSlurmRest()
    a_driver(client=client).submit(a_plan(tmp_path, partition="ring", nodes=3))

    job = submitted(client)["job"]
    assert job["partition"] == "ring"
    assert job["nodes"] == "3"
    assert job["current_working_directory"] == str(tmp_path)


def test_a_time_limit_is_sent_in_the_shape_the_api_expects(tmp_path: Any) -> None:
    client = FakeSlurmRest()
    a_driver(client=client).submit(a_plan(tmp_path, time_limit_minutes=2880))
    assert submitted(client)["job"]["time_limit"] == {"set": True, "number": 2880}


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------


def test_the_token_is_asked_for_at_the_moment_of_the_call(tmp_path: Any) -> None:
    """The token is read per call, not at construction.

    A lease has an expiry, and a driver that cached the string would outlive it
    and turn an expiry into a 401 nobody could explain.
    """
    asked: list[int] = []

    def token() -> str:
        asked.append(1)
        return "jwt-value"

    client = FakeSlurmRest()
    driver = a_driver(client=client, token=token)
    assert asked == [], "the token was read at construction"

    driver.submit(a_plan(tmp_path))
    assert asked, "the token was never read"


def test_the_token_reaches_the_header_and_nothing_else(tmp_path: Any) -> None:
    """The token reaches the header and nothing else.

    It belongs in `X-SLURM-USER-TOKEN` and in no job payload, no URL and no
    repr. A credential in a job description is a credential in Slurm's
    accounting database.
    """
    secret = "jwt-that-must-not-leak"
    client = FakeSlurmRest()
    driver = a_driver(client=client, token=lambda: secret)
    handle = driver.submit(a_plan(tmp_path))
    driver.poll(handle)

    for request in client.requests:
        assert request["headers"].get("X-SLURM-USER-TOKEN") == secret
        assert secret not in request["url"]
        assert secret not in json.dumps(request["json"] or {}), (
            "the token reached the job payload, and therefore Slurm's accounting database"
        )

    assert secret not in repr(driver), "the token is recoverable from the driver's repr"

    # The injected client is excluded, and deliberately: this stub records every
    # request it was handed, so of course it holds the header. What is being
    # asserted is that the *driver* keeps nothing -- it asks the callable at the
    # moment of the call and does not remember the answer.
    own = {name: value for name, value in vars(driver).items() if name != "client"}
    assert secret not in str(own), f"the token was stored on the driver: {own}"


def test_a_refused_token_says_which_of_the_two_things_happened(tmp_path: Any) -> None:
    """A refused token says which of the two things happened.

    A 401 is a lease that expired or a key that was rotated, and an operator
    told only "401" has to guess which.
    """

    @dataclass
    class Refusing(FakeSlurmRest):
        def request(self, method: str, url: str, **kwargs: Any) -> Reply:
            super().request(method, url, **kwargs)
            return Reply(401, {"errors": ["invalid token"]})

    with pytest.raises(SlurmRestError) as refusal:
        a_driver(client=Refusing()).submit(a_plan(tmp_path))

    assert "lease may have expired" in str(refusal.value)
    assert "rotated" in str(refusal.value)


def test_no_token_is_sent_when_there_is_none(tmp_path: Any) -> None:
    """A development slurmrestd runs `AuthType=auth/local` and wants none.

    Sending an empty header would be refused, which would make the default
    configuration the one that cannot work.
    """
    client = FakeSlurmRest()
    a_driver(client=client).submit(a_plan(tmp_path))
    assert "X-SLURM-USER-TOKEN" not in client.requests[0]["headers"]


# ---------------------------------------------------------------------------
# Observing
# ---------------------------------------------------------------------------


def test_a_job_the_queue_has_forgotten_is_looked_up_in_the_accounting() -> None:
    """A forgotten job is looked up in the accounting database.

    The queue drops a job `MinJobAge` after it finishes, and its silence is not
    evidence that the job never existed.
    """
    client = FakeSlurmRest(queue_forgets_after=0)
    driver = a_driver(client=client)
    status = driver.poll(JobHandle(driver=NAME, job_id="4242"))

    assert status.state is JobState.COMPLETED
    assert any("/slurmdb/" in r["url"] for r in client.requests), (
        "the accounting database was never asked"
    )


def test_a_job_neither_knows_is_reported_as_such() -> None:
    @dataclass
    class Amnesiac(FakeSlurmRest):
        def request(self, method: str, url: str, **kwargs: Any) -> Reply:
            super().request(method, url, **kwargs)
            return Reply(200, {"jobs": []})

    status = a_driver(client=Amnesiac()).poll(JobHandle(driver=NAME, job_id="9"))
    assert status.state is JobState.FAILED
    assert status.message is not None
    assert "knows nothing of job 9" in status.message


def test_a_signal_is_not_read_as_a_success() -> None:
    """A signal is not read as a success.

    A job killed by a signal reports return code zero. Reading only that would
    call a killed job successful; the signal comes back as 128 + n, which is the
    convention the retry policy already understands.
    """

    @dataclass
    class Killed(FakeSlurmRest):
        def _record(self, word: str) -> dict[str, Any]:
            record = super()._record(word)
            record["job_state"] = ["FAILED"]
            record["exit_code"] = {
                "return_code": {"set": True, "number": 0},
                "signal": {"set": True, "signal_id": 9},
            }
            return record

    status = a_driver(client=Killed()).poll(JobHandle(driver=NAME, job_id="1"))
    assert status.exit_code == 137, "a SIGKILLed job was reported as exit zero"


def test_a_bare_state_string_is_read_as_well_as_a_list() -> None:
    """v0.0.40 reports `job_state` as a list; earlier versions report a string.

    The difference is silent: a list read as a string matches nothing, and an
    unmatched state maps to FAILED, so a healthy job would read as a failure.
    """

    @dataclass
    class Older(FakeSlurmRest):
        def _record(self, word: str) -> dict[str, Any]:
            record = super()._record(word)
            record["job_state"] = word
            return record

    status = a_driver(client=Older(walk=["RUNNING"])).poll(JobHandle(driver=NAME, job_id="1"))
    assert status.state is JobState.RUNNING


def test_an_unrecognised_state_fails_rather_than_hanging() -> None:
    """An unrecognised state fails rather than hanging.

    Anything unlisted stops a pipeline instead of leaving it running for ever
    against a state nobody modelled.
    """

    @dataclass
    class Strange(FakeSlurmRest):
        def _record(self, word: str) -> dict[str, Any]:
            record = super()._record(word)
            record["job_state"] = ["SOMETHING_NEW"]
            return record

    status = a_driver(client=Strange()).poll(JobHandle(driver=NAME, job_id="1"))
    assert status.state is JobState.FAILED


# ---------------------------------------------------------------------------
# Submitting and cancelling
# ---------------------------------------------------------------------------


def test_a_submission_with_no_job_identifier_is_a_refusal(tmp_path: Any) -> None:
    """A submission with no job identifier is a refusal.

    Silence about the identifier means nothing is queued, and a driver that
    returned a handle anyway would have the core polling a job that does not
    exist.
    """
    client = FakeSlurmRest(submit_body={"errors": [{"error": "Invalid partition name"}]})
    with pytest.raises(SlurmRestError) as refusal:
        a_driver(client=client).submit(a_plan(tmp_path))
    assert "Invalid partition name" in str(refusal.value)
    assert "nothing is queued" in str(refusal.value)


def test_an_unreachable_scheduler_names_where_it_tried(tmp_path: Any) -> None:
    @dataclass
    class Down(FakeSlurmRest):
        def request(self, method: str, url: str, **kwargs: Any) -> Reply:
            msg = "connection refused"
            raise OSError(msg)

    with pytest.raises(SlurmRestError) as refusal:
        a_driver(client=Down()).submit(a_plan(tmp_path))
    assert "regin.sindri.veldris.internal:6820" in str(refusal.value)


def test_cancelling_a_finished_job_reports_what_it_reached() -> None:
    """Cancelling a finished job reports what it reached, per AC-F13.

    Rewriting a completed job as cancelled would be a less true record, and the
    ledger keeps whichever this returns.
    """
    driver = a_driver(client=FakeSlurmRest(walk=["COMPLETED"]))
    status = driver.cancel(JobHandle(driver=NAME, job_id="4242"))
    assert status.state is JobState.COMPLETED


def test_cancelling_a_running_job_says_so_before_slurm_has_reaped_it() -> None:
    client = FakeSlurmRest(walk=["RUNNING"])
    status = a_driver(client=client).cancel(JobHandle(driver=NAME, job_id="4242"))

    assert status.state is JobState.CANCELLED
    assert any(r["method"] == "DELETE" for r in client.requests)


# ---------------------------------------------------------------------------
# The two drivers are two views of one scheduler
# ---------------------------------------------------------------------------


def test_both_slurm_drivers_map_every_state_the_same_way() -> None:
    """Both drivers map every Slurm state the same way.

    Two distributions, neither of which may import the other, and one fact
    about Slurm between them.

    A state that meant RUNNING through one transport and FAILED through the
    other would make a run's history depend on which host submitted it.
    """
    shim = pytest.importorskip(
        "draupnir_motsognir_slurm", reason="the sbatch driver is not installed"
    )

    assert dict(STATES) == dict(shim._STATES), (
        "the two Slurm drivers disagree about what a Slurm state means"
    )


def test_both_slurm_drivers_declare_the_same_capabilities() -> None:
    """Both drivers declare the same capabilities.

    They are the same scheduler. A core that refused an array against one and
    allowed it against the other would be deciding on the transport.
    """
    shim = pytest.importorskip("draupnir_motsognir_slurm")
    from draupnir_motsognir_slurmrest import CAPABILITIES

    assert CAPABILITIES == shim.CAPABILITIES


def test_both_slurm_drivers_ask_for_the_same_accelerator(tmp_path: Any) -> None:
    """RF-E09. Two drivers, one request, and they used to spell it differently.

    The shim sent `--gpus-per-node=1` and this one sent a typed
    `tres_per_node`, so the same run asked for different things depending on
    which host submitted it. Neither is wrong in isolation; what was wrong was
    that nothing compared them.

    The two surfaces genuinely differ -- `--gres` takes `gpu:gb10:1` and
    `tres_per_node` wants `gres/` in front -- so this asserts they carry the
    same estate string rather than that they produce identical text.
    """
    from draupnir.motsognir.placement import Partition, estate_for
    from draupnir.motsognir.placement import plan as place

    shim = pytest.importorskip("draupnir_motsognir_slurm")

    placed = place(partition=Partition.ADAPTERS, estate=estate_for("sindri", "gb10"))
    assert placed.gres == "gpu:gb10:1", "the placement no longer carries the accelerator"

    plan = a_plan(tmp_path, gpus_per_node=1, gres=placed.gres)

    client = FakeSlurmRest()
    a_driver(client=client).submit(plan)
    over_rest = submitted(client)["job"]["tres_per_node"]

    script = shim.SlurmDriver().render_script(plan, tmp_path / "out")
    over_sbatch = next(
        line.split("=", 1)[1] for line in script.splitlines() if line.startswith("#SBATCH --gres=")
    )

    assert over_sbatch == placed.gres
    assert over_rest == f"gres/{placed.gres}"


# ---------------------------------------------------------------------------
# Element requeue is not available over this transport. RF-E24.
# ---------------------------------------------------------------------------


def test_element_requeue_is_refused_rather_than_approximated() -> None:
    """AC-F6's mechanism does not exist in slurmrestd v0.0.40.

    It can submit a job, read one and cancel one, and it cannot put one back on
    the queue. Both approximations are worse than a refusal: cancelling the
    element and submitting a replacement gives it a new job identifier, which
    severs it from its array and loses the throttle and the accounting record
    tying the fifty six together; requeuing the array restarts all of them and
    discards the compute of the ones that succeeded, which is the exact failure
    AC-F6 exists to prevent.
    """
    driver = a_driver()

    with pytest.raises(SlurmRestError) as raised:
        driver.requeue(JobHandle(driver="motsognir.slurmrest/v1", job_id="4471"), 17)

    assert "exposes no requeue" in str(raised.value)


def test_the_refusal_names_what_to_do_instead() -> None:
    """A refusal an operator cannot act on is a refusal that gets worked around.

    Both routes are named because they cost differently: one is a command on
    REGIN now, the other is a driver swap that needs Slurm client tools on the
    host — and RF-E05 established ALVISS has none.
    """
    driver = a_driver()

    with pytest.raises(SlurmRestError) as raised:
        driver.requeue(JobHandle(driver="motsognir.slurmrest/v1", job_id="4471"), 17)

    message = str(raised.value)
    assert "scontrol requeue" in message, "the refusal does not name the manual route"
    assert "motsognir.slurm/v1" in message, "the refusal does not name the other driver"
    assert "4471" in message, "the refusal does not name the job it was asked about"
    assert "AC-F6" in message


def test_the_refusal_makes_no_request() -> None:
    """It must not cancel the element on its way to refusing.

    The dangerous near-miss: a driver that cancels first and then discovers it
    cannot requeue has destroyed the element and reported a failure.
    """
    client = FakeSlurmRest()
    driver = a_driver(client=client)

    with pytest.raises(SlurmRestError):
        driver.requeue(JobHandle(driver="motsognir.slurmrest/v1", job_id="4471"), 17)

    assert client.requests == [], f"the refusal reached the scheduler: {client.requests}"


def test_the_console_offers_no_control_this_driver_cannot_perform() -> None:
    """RF-E24: "the console must not offer a requeue that raises".

    It does not today — the console's retry is a *run* retry, which is a
    lifecycle transition through the state machine and has nothing to do with
    array elements. This asserts that stays true, because the screen where
    somebody would add an element retry is the failure-diagnosis screen, and
    the control would look identical to the one already there.

    If element requeue is ever offered, this test is the prompt to make it
    unavailable with the reason rather than a button that throws.
    """
    from pathlib import Path

    console = Path(__file__).resolve().parents[2] / "web" / "apps" / "console" / "src"
    offered = [
        path.as_posix()
        for path in sorted(console.rglob("*.tsx"))
        if "requeueElement" in path.read_text(encoding="utf-8")
        or "requeue_element" in path.read_text(encoding="utf-8")
    ]

    assert not offered, (
        f"{', '.join(offered)} offers an element requeue. slurmrestd exposes none "
        "(AC-F6 is DEVIATED for this reason), so the control must be unavailable with "
        "the reason shown rather than a button that raises."
    )
