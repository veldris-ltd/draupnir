"""The Slurm driver, against the output Slurm actually produces.

Slurm is not installed on a developer machine and will not be in CI, so the
command layer is replaced and everything above it is exercised for real. The
canned output below is copied from the formats the driver asks for -- not
invented -- because the three defects this driver is shaped around are all
defects of reading that output:

* a finished job vanishes from `squeue`, and its silence is not "no such job";
* `sacct` returns the job and its `.batch` and `.extern` steps, and the batch
  step's state diverges from the job's exactly when something went wrong;
* a job killed by a signal reports `0:9`, and reading the first field alone
  calls that a success.
"""

from __future__ import annotations

import pathlib
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from draupnir_motsognir_slurm import SlurmDriver, SlurmError

from draupnir.interfaces.types import (
    ArraySpec,
    JobHandle,
    JobPlan,
    JobState,
    ResourceRequest,
)

pytestmark = pytest.mark.unit


@dataclass
class FakeSlurm(SlurmDriver):
    """A Slurm driver whose command layer answers from a script."""

    #: `command word -> output`, consumed in order for repeated calls.
    responses: dict[str, list[str]] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)

    def _run(self, arguments: list[str], *, check: bool = True) -> str:
        self.calls.append(arguments)
        queue = self.responses.get(arguments[0])
        if not queue:
            return ""
        return queue.pop(0) if len(queue) > 1 else queue[0]


@pytest.fixture
def plan(tmp_path) -> JobPlan:  # type: ignore[no-untyped-def]
    return JobPlan(
        command=("llamafactory-cli", "train", "config.json"),
        environment={"PYTHONHASHSEED": "0"},
        workdir=str(tmp_path),
        resources=ResourceRequest(partition="adapters", nodes=1, gpus_per_node=1),
    )


# -- submit -----------------------------------------------------------------


def test_submit_returns_the_identifier_sbatch_reports(plan: JobPlan) -> None:
    driver = FakeSlurm(responses={"sbatch": ["Submitted batch job 4815162342\n"]})

    handle = driver.submit(plan)

    assert handle.job_id == "4815162342"
    assert handle.driver == "motsognir.slurm/v1"


def test_the_submission_carries_the_partition_nodes_and_environment(
    plan: JobPlan,
) -> None:
    """The directives are in the script, and the script is what is submitted."""
    driver = FakeSlurm(responses={"sbatch": ["Submitted batch job 1\n"]})
    driver.submit(plan)

    arguments = driver.calls[0]
    assert arguments[0] == "sbatch"
    assert arguments[-1].endswith("draupnir-job.sbatch"), (
        f"sbatch was given something other than a script path: {arguments}"
    )

    script = pathlib.Path(arguments[-1]).read_text(encoding="utf-8")
    assert "#SBATCH --partition=adapters" in script
    assert "#SBATCH --nodes=1" in script
    assert "#SBATCH --gpus-per-node=1" in script
    assert "export PYTHONHASHSEED=0" in script


def test_the_command_survives_a_shell(plan: JobPlan, tmp_path: Path) -> None:
    """RF-E06. `--wrap` took one shell command; a plan is an argument vector.

    Joining it with spaces turned `sh -c 'a && b'` into words a shell read
    separately: the redirect happened in the wrong place, the configuration
    file was never written, and the trainer ran against something that was not
    there. Nothing about that was visible without a real Slurm.

    Round-tripped rather than pattern-matched: the property is that a shell
    reassembles exactly the vector that went in.
    """
    hostile = (
        "sh",
        "-c",
        'printf "%s" "$CONFIG" > lf.json && llamafactory-cli train lf.json',
        "a b",
        "quote'\"both",
        "$NOT_EXPANDED",
    )
    driver = FakeSlurm(responses={"sbatch": ["Submitted batch job 1\n"]})
    driver.submit(
        JobPlan(
            command=hostile,
            workdir=str(tmp_path),
            resources=ResourceRequest(partition="adapters"),
        )
    )

    script = pathlib.Path(driver.calls[0][-1]).read_text(encoding="utf-8")
    body = [
        line for line in script.splitlines() if line and not line.startswith(("#", "export", "set"))
    ]
    assert shlex.split(body[-1]) == list(hostile), (
        f"the script does not reassemble into the plan's command:\n{body[-1]}"
    )


def test_an_environment_value_full_of_commas_arrives_intact(tmp_path: Path) -> None:
    """RF-E07. `--export` separates entries with commas.

    A training configuration is JSON, which is mostly commas, so the one
    variable carrying it was split into dozens of malformed ones and the job
    received something that was not what was rendered.
    """
    configuration = '{"stage":"sft","lora_rank":64,"targets":["q","k","v"],"lr":1.0e-4}'
    driver = FakeSlurm(responses={"sbatch": ["Submitted batch job 1\n"]})
    driver.submit(
        JobPlan(
            command=("true",),
            environment={"LF_CONFIG": configuration},
            workdir=str(tmp_path),
            resources=ResourceRequest(partition="adapters"),
        )
    )

    script = pathlib.Path(driver.calls[0][-1]).read_text(encoding="utf-8")
    line = next(x for x in script.splitlines() if x.startswith("export LF_CONFIG="))
    _, _, quoted = line.partition("=")
    assert shlex.split(quoted) == [configuration], (
        f"the configuration did not survive quoting:\n{quoted}"
    )


def test_the_job_keeps_a_usable_path(plan: JobPlan) -> None:
    """RF-E07, the half that would have stopped every job on the estate.

    `--export` naming variables without `ALL` propagates only those, so the job
    ran with no `PATH`. Every batch script in VLD-INF-SINDRI-001 Part 5 begins
    by sourcing a virtual environment, and `llamafactory-cli` lives in it;
    none of them would have been found.

    Asserted as an absence: `sbatch` propagates the submitting environment by
    default, so what must not be there is a flag narrowing it.
    """
    driver = FakeSlurm(responses={"sbatch": ["Submitted batch job 1\n"]})
    driver.submit(plan)

    narrowing = [a for a in driver.calls[0] if a.startswith("--export")]
    assert not narrowing, (
        f"the submission narrows the environment with {narrowing}, which drops PATH"
    )


def test_the_script_is_kept_where_an_operator_will_look(plan: JobPlan) -> None:
    """The job script is kept where an operator will look.

    It is the exact thing that ran, and the first question after a surprising
    result is what was actually submitted.

    Beside the log, in the run's own directory, and still there afterwards: a
    script in a temporary directory answers that question only until the job
    finishes.
    """
    driver = FakeSlurm(responses={"sbatch": ["Submitted batch job 1\n"]})
    driver.submit(plan)

    script = Path(plan.workdir) / "draupnir-job.sbatch"
    assert script.is_file(), "the job script was not left in the run's working directory"
    assert script.read_text(encoding="utf-8").startswith("#!/bin/bash")


def test_a_script_over_slurms_limit_is_refused_rather_than_truncated(tmp_path: Path) -> None:
    """Slurm truncates a script over `max_script_size` rather than refusing it.

    A truncated script is a job that runs a prefix of what was meant, which is
    a far worse outcome than a submission that did not happen. The refusal
    names the largest environment value, because that is what one is.
    """
    driver = FakeSlurm(responses={"sbatch": ["Submitted batch job 1\n"]}, max_script_bytes=1024)

    with pytest.raises(SlurmError) as refusal:
        driver.submit(
            JobPlan(
                command=("true",),
                environment={"SMALL": "x", "ENORMOUS": "y" * 4096},
                workdir=str(tmp_path),
                resources=ResourceRequest(partition="adapters"),
            )
        )

    assert "ENORMOUS" in str(refusal.value)
    assert "Nothing is queued" in str(refusal.value)
    assert driver.calls == [], "a script over the limit was submitted anyway"


def test_a_submission_sbatch_does_not_acknowledge_is_a_failure(plan: JobPlan) -> None:
    """Nothing is queued, and pretending otherwise loses the run."""
    driver = FakeSlurm(responses={"sbatch": ["sbatch: error: Batch job submission failed\n"]})

    with pytest.raises(SlurmError, match="did not report a job identifier"):
        driver.submit(plan)


# -- poll -------------------------------------------------------------------


def test_a_queued_job_is_read_from_squeue() -> None:
    driver = FakeSlurm(responses={"squeue": ["PENDING|\n"]})

    status = driver.poll(JobHandle(driver=driver.name, job_id="7"))

    assert status.state is JobState.PENDING


def test_a_running_job_reports_the_node_it_landed_on() -> None:
    driver = FakeSlurm(responses={"squeue": ["RUNNING|dvalin\n"]})

    status = driver.poll(JobHandle(driver=driver.name, job_id="7"))

    assert status.state is JobState.RUNNING
    assert status.node == "dvalin"


def test_a_finished_job_squeue_has_forgotten_is_found_in_sacct() -> None:
    """The defect this fall-through exists to prevent."""
    driver = FakeSlurm(
        responses={
            "squeue": [""],
            "sacct": ["7|COMPLETED|0:0|dvalin\n7.batch|COMPLETED|0:0|dvalin\n"],
        }
    )

    status = driver.poll(JobHandle(driver=driver.name, job_id="7"))

    assert status.state is JobState.COMPLETED
    assert status.exit_code == 0
    assert status.node == "dvalin"


def test_the_jobs_own_row_is_read_not_its_batch_step() -> None:
    """The batch step's state diverges from the job's when it matters."""
    driver = FakeSlurm(
        responses={
            "squeue": [""],
            "sacct": [
                "7.batch|COMPLETED|0:0|dvalin\n7.extern|COMPLETED|0:0|dvalin\n7|FAILED|1:0|dvalin\n"
            ],
        }
    )

    status = driver.poll(JobHandle(driver=driver.name, job_id="7"))

    assert status.state is JobState.FAILED
    assert status.exit_code == 1


def test_a_job_killed_by_a_signal_is_not_reported_as_a_success() -> None:
    """`0:9` is a kill, and 137 is what the retry policy understands."""
    driver = FakeSlurm(responses={"squeue": [""], "sacct": ["7|OUT_OF_MEMORY|0:9|dvalin\n"]})

    status = driver.poll(JobHandle(driver=driver.name, job_id="7"))

    assert status.state is JobState.FAILED
    assert status.exit_code == 137


def test_a_job_neither_squeue_nor_sacct_knows_is_reported_as_unknown() -> None:
    driver = FakeSlurm(responses={"squeue": [""], "sacct": [""]})

    status = driver.poll(JobHandle(driver=driver.name, job_id="7"))

    assert status.state is JobState.FAILED
    assert status.message is not None
    assert "knows nothing of job 7" in status.message


def test_an_unrecognised_state_stops_a_pipeline_rather_than_hanging_it() -> None:
    driver = FakeSlurm(responses={"squeue": ["SPECIAL_NEW_STATE|dvalin\n"]})

    status = driver.poll(JobHandle(driver=driver.name, job_id="7"))

    assert status.state is JobState.FAILED


def test_a_cancelled_state_decorated_with_a_user_is_still_cancelled() -> None:
    """`sacct` writes "CANCELLED by 1000"."""
    driver = FakeSlurm(responses={"squeue": [""], "sacct": ["7|CANCELLED by 1000|0:15|dvalin\n"]})

    status = driver.poll(JobHandle(driver=driver.name, job_id="7"))

    assert status.state is JobState.CANCELLED


# -- cancel: AC-F13 ---------------------------------------------------------


def test_cancelling_a_running_job_leaves_it_cancelled() -> None:
    driver = FakeSlurm(
        responses={
            "squeue": ["RUNNING|dvalin\n", ""],
            "sacct": ["7|CANCELLED by 1000|0:15|dvalin\n"],
            "scancel": [""],
        }
    )

    status = driver.cancel(JobHandle(driver=driver.name, job_id="7"))

    assert status.state is JobState.CANCELLED
    assert ["scancel", "7"] in driver.calls


def test_cancelling_an_already_finished_job_is_not_an_error() -> None:
    """AC-F13: a defined state, and the true one rather than a uniform one."""
    driver = FakeSlurm(responses={"squeue": [""], "sacct": ["7|COMPLETED|0:0|dvalin\n"]})

    status = driver.cancel(JobHandle(driver=driver.name, job_id="7"))

    assert status.state is JobState.COMPLETED
    assert not any(call[0] == "scancel" for call in driver.calls)


def test_a_job_slurm_has_not_yet_reaped_is_reported_as_cancelled() -> None:
    """Scancel is asynchronous; "still running" would be the misleading answer."""
    driver = FakeSlurm(responses={"squeue": ["RUNNING|dvalin\n"], "sacct": [""], "scancel": [""]})

    status = driver.cancel(JobHandle(driver=driver.name, job_id="7"))

    assert status.state is JobState.CANCELLED
    assert status.message is not None
    assert "not yet reaped" in status.message


# -- logs -------------------------------------------------------------------


def test_logs_are_empty_for_a_job_this_driver_did_not_submit() -> None:
    driver = FakeSlurm()

    assert driver.logs(JobHandle(driver=driver.name, job_id="7")) == ""


def test_logs_are_read_from_the_file_the_submission_named(plan: JobPlan, tmp_path) -> None:  # type: ignore[no-untyped-def]
    driver = FakeSlurm(responses={"sbatch": ["Submitted batch job 7\n"]})
    handle = driver.submit(plan)
    # Slurm expands %A and %a; here the pattern stands in for the real name.
    (tmp_path / "slurm-%A_%a.out").write_text("step 1/100\n", encoding="utf-8")

    assert driver.logs(handle) == "step 1/100\n"


# -- conformance ------------------------------------------------------------


def test_the_driver_declares_a_versioned_name_and_capabilities() -> None:
    from draupnir.interfaces.testing.harness import check_driver

    assert check_driver(SlurmDriver()) == []


def test_it_declares_the_array_capability_that_ac_f5_requires() -> None:
    """The core refuses to plan an array against a driver that has not said so."""
    assert "array" in SlurmDriver().capabilities
    assert "multinode" in SlurmDriver().capabilities


def test_a_configured_site_gets_its_own_environment(tmp_path: Path) -> None:
    """RF-E07's decision, and the reason the obvious answer is wrong here.

    `sbatch` propagates the submitting environment by default. The submitter is
    the DRAUPNIR worker, which runs in a distroless container, and the job runs
    on an appliance: inheriting that PATH gives the job directories that exist
    in the container and on no machine it can land on.

    So a site that says how to set a job up gets `--export=NONE` and an
    environment that is exactly what the script sets.
    """
    driver = FakeSlurm(
        responses={"sbatch": ["Submitted batch job 1\n"]},
        preamble=("source /forge/venv/bin/activate",),
    )
    driver.submit(
        JobPlan(
            command=("llamafactory-cli", "train", "lf.json"),
            workdir=str(tmp_path),
            resources=ResourceRequest(partition="adapters"),
        )
    )

    script = pathlib.Path(driver.calls[0][-1]).read_text(encoding="utf-8")
    assert "#SBATCH --export=NONE" in script
    assert "source /forge/venv/bin/activate" in script

    lines = script.splitlines()
    assert lines.index("source /forge/venv/bin/activate") < lines.index(
        "llamafactory-cli train lf.json"
    ), "the preamble runs after the command, so the command cannot use what it sets up"


def test_an_unconfigured_site_keeps_the_inherited_environment(plan: JobPlan) -> None:
    """A development machine has no venv to source and no container in the way.

    Narrowing the environment there would break the default configuration to
    protect against a problem it does not have.
    """
    driver = FakeSlurm(responses={"sbatch": ["Submitted batch job 1\n"]})
    driver.submit(plan)

    script = pathlib.Path(driver.calls[0][-1]).read_text(encoding="utf-8")
    assert "--export" not in script


# ---------------------------------------------------------------------------
# RF-E10, E11, E12: the array, the estate and what a pending job is.
# ---------------------------------------------------------------------------


@dataclass
class ClusterSlurm(FakeSlurm):
    """A Slurm that queues, and can be asked about jobs and nodes."""

    known: dict[str, str] = field(default_factory=dict)
    node_states: dict[str, str] = field(default_factory=dict)


def test_fifty_six_elements_are_one_submission(tmp_path: Path) -> None:
    """AC-F5, and the estate's whole placement strategy.

    VLD-INF-SINDRI-001 Procedure M6: "fifty six jobs are queued, exactly three
    execute at any moment, one per appliance". One `sbatch`, not fifty six, and
    the `%3` is a throttle rather than a count -- so utilisation does not depend
    on the control plane being awake to top a queue up.
    """
    driver = FakeSlurm(responses={"sbatch": ["Submitted batch job 900\n"]})
    driver.submit(
        JobPlan(
            command=("true",),
            workdir=str(tmp_path),
            resources=ResourceRequest(partition="adapters", array=ArraySpec(size=56, throttle=3)),
        )
    )

    assert sum(1 for call in driver.calls if call[0] == "sbatch") == 1, (
        "fifty six elements were submitted as more than one job"
    )
    script = pathlib.Path(driver.calls[0][-1]).read_text(encoding="utf-8")
    assert "#SBATCH --array=0-55%3" in script


def test_an_array_element_is_requeued_and_the_others_are_untouched() -> None:
    """AC-F6, and the mechanism matters.

    `scontrol requeue <job>_<index>` keeps the element inside its array. A
    fresh submission would give it a new job identifier, severing it from the
    array and losing both the throttle and the accounting record that ties the
    fifty six together.
    """
    driver = FakeSlurm(responses={"squeue": ["PENDING|"]})
    driver.requeue(JobHandle(driver="motsognir.slurm/v1", job_id="900"), 17)

    requeues = [call for call in driver.calls if call[0] == "scontrol"]
    assert requeues == [["scontrol", "requeue", "900_17"]]
    assert not any("sbatch" in call[0] for call in driver.calls), (
        "the element was resubmitted rather than requeued, which severs it from its array"
    )


def test_an_array_element_is_addressed_by_index_not_by_the_array() -> None:
    """An element is addressed by index, never by the array.

    Passing the array's identifier where an element's was meant cancels fifty
    six jobs instead of one.
    """
    array = ArraySpec(size=56, throttle=3)
    assert array.element("900", 17) == "900_17"
    assert array.directive() == "0-55%3"

    with pytest.raises(ValueError, match="no index 56"):
        array.element("900", 56)


def test_an_array_that_would_never_start_is_refused() -> None:
    """A throttle of zero queues fifty six jobs and runs none of them."""
    with pytest.raises(ValueError, match="at least one element"):
        ArraySpec(size=0, throttle=3)
    with pytest.raises(ValueError, match="would never start"):
        ArraySpec(size=56, throttle=0)


def test_the_scheduler_is_asked_which_nodes_can_take_work() -> None:
    """RF-E11. A drained node cannot, whatever the inventory says.

    Slurm suffixes a state with `*` when the node is not responding, and a
    node that is not responding will not run anything whatever the word in
    front of the asterisk says.
    """
    driver = FakeSlurm(responses={"sinfo": ["dvalin|idle\ndurin|drain\ndain|allocated*\n"]})
    reported = {node.name: node.available for node in driver.nodes()}

    assert reported == {"dvalin": True, "durin": False, "dain": False}


def test_a_job_is_found_again_by_the_name_the_run_gave_it() -> None:
    """RF-E12's other half, and what makes dispatch idempotent.

    A worker holds nothing between ticks (SAD 11.2 row 1). Without this it
    would have to record an allocation the moment it submitted -- which is how
    a job Slurm had merely accepted came to be recorded as TRAINING.
    """
    driver = FakeSlurm(responses={"squeue": ["900_12|dvalin\n900_13|durin\n"]})

    found = driver.find("draupnir-abc")
    assert found is not None
    assert found.job_id == "900", (
        "an array element's identifier was returned where the array's was meant"
    )

    assert FakeSlurm(responses={"squeue": [""]}).find("draupnir-abc") is None
