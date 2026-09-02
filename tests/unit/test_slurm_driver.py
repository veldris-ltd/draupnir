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

from dataclasses import dataclass, field

import pytest
from draupnir_motsognir_slurm import SlurmDriver, SlurmError

from draupnir.interfaces.types import JobHandle, JobPlan, JobState, ResourceRequest

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
    driver = FakeSlurm(responses={"sbatch": ["Submitted batch job 7\n"]})

    driver.submit(plan)
    arguments = driver.calls[0]

    assert "--partition=adapters" in arguments
    assert "--nodes=1" in arguments
    assert "--gpus-per-node=1" in arguments
    assert "--export=PYTHONHASHSEED=0" in arguments
    assert arguments[-1] == "--wrap=llamafactory-cli train config.json"


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
