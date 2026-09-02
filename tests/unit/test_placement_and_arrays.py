"""AC-F4, AC-F5, AC-F6 and AC-F13 at the level where the decisions are made.

AC-F4: a substrate run executes across all three appliances through the `ring`
partition.

AC-F5: a fifty six element adapter array is submitted as one action and
executes exactly three concurrently, one per appliance.

AC-F6: a failed array element is retried individually without disturbing the
other elements.

AC-F13: cancelling leaves a defined state, never an ambiguous one.
"""

from __future__ import annotations

import pytest

from draupnir.hamarr import tiers
from draupnir.motsognir import arrays, placement
from draupnir.motsognir.arrays import ElementState
from draupnir.motsognir.placement import (
    DegradedRingError,
    Estate,
    NoCapacityError,
    Partition,
    ResidencyError,
)

# -- AC-F4: the ring --------------------------------------------------------


def test_a_substrate_run_is_placed_across_all_three_appliances() -> None:
    """AC-F4: the ring partition, all three, ranks in order."""
    result = placement.plan(partition=Partition.RING, estate=Estate())

    assert result.partition is Partition.RING
    assert result.appliances == ("dvalin", "durin", "dain")
    assert result.nodes_per_element == 3
    assert result.concurrency == 1
    assert result.notes == ("ranks 0, 1, 2",)


def test_a_ring_run_refuses_to_plan_with_an_appliance_down() -> None:
    """Decision: a ring job does not degrade, it refuses.

    Two nodes of a three node specification produces a different model, and it
    would be discovered at evaluation, after the compute was spent.
    """
    with pytest.raises(DegradedRingError) as raised:
        placement.plan(partition=Partition.RING, estate=Estate().without("dain"))

    assert raised.value.required == 3
    assert raised.value.available == ("dvalin", "durin")
    assert raised.value.down == ("dain",)
    assert "does not degrade gracefully" in str(raised.value)


def test_a_substrate_run_is_routed_to_the_ring_partition() -> None:
    assert placement.partition_for("SubstrateRun") is Partition.RING
    assert placement.partition_for("AdapterRun") is Partition.ADAPTERS


# -- concurrency reduces on loss of an appliance ----------------------------


def test_losing_an_appliance_reduces_adapter_concurrency() -> None:
    """An adapter array runs slower rather than refusing: it is independent work."""
    result = placement.plan(
        partition=Partition.ADAPTERS, estate=Estate().without("dain"), requested_concurrency=3
    )

    assert result.concurrency == 2
    assert result.reduced is True
    assert "dain unavailable" in result.notes[0]


def test_a_full_estate_is_not_marked_reduced() -> None:
    result = placement.plan(partition=Partition.ADAPTERS, estate=Estate(), requested_concurrency=3)

    assert result.concurrency == 3
    assert result.reduced is False
    assert result.notes == ()


def test_an_empty_estate_refuses_rather_than_queueing() -> None:
    with pytest.raises(NoCapacityError):
        placement.plan(
            partition=Partition.ADAPTERS, estate=Estate().without("dvalin", "durin", "dain")
        )


def test_residency_is_checked_at_planning_not_at_execution() -> None:
    """SAD 11C, and the failure it exists to prevent."""
    with pytest.raises(ResidencyError, match="planned at sindri"):
        placement.plan(
            partition=Partition.ADAPTERS,
            estate=Estate(),
            residency_constraint=("brokkr",),
        )


# -- AC-F5: fifty six elements, three at a time -----------------------------


@pytest.fixture
def full_array() -> arrays.ArrayPlan:
    """The CIM-56 adapter array, on a healthy estate."""
    return arrays.build(
        tiers.ALL,
        placement.plan(partition=Partition.ADAPTERS, estate=Estate(), requested_concurrency=3),
        retry_budget=2,
        name="cim-adapters",
    )


def test_fifty_six_elements_submit_as_one_action_and_run_three_at_a_time(
    full_array: arrays.ArrayPlan,
) -> None:
    """AC-F5, exactly as written."""
    assert full_array.size == 56
    assert full_array.concurrency == 3
    assert full_array.slurm_array() == "0-55%3"


def test_the_submission_carries_the_throttle_and_the_partition(
    full_array: arrays.ArrayPlan,
) -> None:
    assert full_array.slurm_arguments() == (
        "--job-name=cim-adapters",
        "--array=0-55%3",
        "--partition=adapters",
        "--nodes=1",
    )


def test_concurrency_follows_the_estate_when_an_appliance_is_lost() -> None:
    """The throttle is the estate's, so losing a machine changes the submission."""
    reduced = arrays.build(
        tiers.ALL,
        placement.plan(
            partition=Partition.ADAPTERS,
            estate=Estate().without("durin"),
            requested_concurrency=3,
        ),
    )

    assert reduced.slurm_array() == "0-55%2"


def test_an_array_with_a_gap_in_its_indices_is_refused() -> None:
    """Slurm addresses elements by index; a gap addresses nothing."""
    spot = placement.plan(partition=Partition.ADAPTERS, estate=Estate())

    with pytest.raises(arrays.ArrayError, match="contiguous"):
        arrays.ArrayPlan(
            elements=(
                arrays.Element(index=0, subject="GBR"),
                arrays.Element(index=2, subject="KEN"),
            ),
            placement=spot,
        )


# -- AC-F6: individual retry ------------------------------------------------


def test_a_failed_element_is_retried_alone(full_array: arrays.ArrayPlan) -> None:
    """AC-F6: `--array=<index>`, not a resubmission of the array."""
    running = full_array
    for index in (0, 1, 2):
        running = arrays.observe(running, index, state=ElementState.RUNNING)
    running = arrays.observe(running, 0, state=ElementState.COMPLETED)
    running = arrays.observe(running, 1, state=ElementState.FAILED, exit_code=1)

    pending = arrays.due_for_retry(running)

    assert len(pending) == 1
    assert pending[0].index == 1
    assert pending[0].slurm_arguments(running) == (
        "--job-name=cim-adapters-retry-1",
        "--array=1",
        "--partition=adapters",
        "--nodes=1",
    )


def test_retrying_one_element_disturbs_no_other(full_array: arrays.ArrayPlan) -> None:
    """The other fifty five are the objects they were, not copies of them."""
    before = full_array
    after = arrays.observe(before, 7, state=ElementState.FAILED, exit_code=1)

    assert after.element(7).state is ElementState.AWAITING_RETRY
    for index in range(56):
        if index == 7:
            continue
        assert after.element(index) is before.element(index)


def test_a_failure_within_budget_is_awaiting_retry_not_failed(
    full_array: arrays.ArrayPlan,
) -> None:
    """In the run board, FAILED must mean "will not be tried again"."""
    once = arrays.observe(full_array, 3, state=ElementState.FAILED, exit_code=1)
    twice = arrays.observe(once, 3, state=ElementState.FAILED, exit_code=1)
    thrice = arrays.observe(twice, 3, state=ElementState.FAILED, exit_code=1)

    assert once.element(3).state is ElementState.AWAITING_RETRY
    assert twice.element(3).state is ElementState.AWAITING_RETRY
    assert thrice.element(3).state is ElementState.EXHAUSTED
    assert thrice.element(3).attempts == 3
    assert arrays.due_for_retry(thrice) == ()


def test_progress_counts_only_completed_elements(full_array: arrays.ArrayPlan) -> None:
    advanced = arrays.observe(full_array, 0, state=ElementState.COMPLETED)
    advanced = arrays.observe(advanced, 1, state=ElementState.FAILED, exit_code=1)

    assert advanced.progress == (1, 56)
    assert advanced.settled is False
    assert arrays.summarise(advanced) == {
        "AWAITING_RETRY": 1,
        "COMPLETED": 1,
        "PENDING": 54,
    }


# -- AC-F13: cancellation leaves a defined state ----------------------------


def test_cancelling_leaves_every_element_in_a_defined_state(
    full_array: arrays.ArrayPlan,
) -> None:
    """AC-F13, at the array level."""
    running = arrays.observe(full_array, 0, state=ElementState.COMPLETED)
    running = arrays.observe(running, 1, state=ElementState.RUNNING)

    cancelled = arrays.cancel(running)

    assert cancelled.settled is True
    assert all(item.state in arrays.TERMINAL for item in cancelled.elements)


def test_cancelling_does_not_rewrite_a_completed_element(
    full_array: arrays.ArrayPlan,
) -> None:
    """A defined state is asked for, not a uniform one: the result is kept."""
    running = arrays.observe(full_array, 0, state=ElementState.COMPLETED)

    cancelled = arrays.cancel(running)

    assert cancelled.element(0).state is ElementState.COMPLETED
    assert cancelled.element(1).state is ElementState.CANCELLED


def test_cancelling_a_subset_leaves_the_rest_alone(full_array: arrays.ArrayPlan) -> None:
    cancelled = arrays.cancel(full_array, indices=[4, 5])

    assert cancelled.element(4).state is ElementState.CANCELLED
    assert cancelled.element(6).state is ElementState.PENDING


def test_an_empty_array_is_refused() -> None:
    spot = placement.plan(partition=Partition.ADAPTERS, estate=Estate())

    with pytest.raises(arrays.ArrayError, match="at least one element"):
        arrays.ArrayPlan(elements=(), placement=spot)


def test_addressing_an_element_that_does_not_exist_is_refused(
    full_array: arrays.ArrayPlan,
) -> None:
    with pytest.raises(arrays.ArrayError, match="there is no index 56"):
        full_array.element(56)


def test_the_running_elements_are_the_ones_the_scheduler_is_executing(
    full_array: arrays.ArrayPlan,
) -> None:
    running = arrays.observe(full_array, 0, state=ElementState.RUNNING, node="dvalin")
    running = arrays.observe(running, 1, state=ElementState.RUNNING, node="durin")

    assert {item.index for item in running.running} == {0, 1}
    assert running.element(0).node == "dvalin"


def test_an_adapter_element_needing_more_nodes_than_are_up_is_refused() -> None:
    """A two node element cannot run on one appliance, however patient we are."""
    with pytest.raises(DegradedRingError):
        placement.plan(
            partition=Partition.ADAPTERS,
            estate=Estate().without("durin", "dain"),
            nodes_per_element=2,
        )


def test_the_placement_is_recorded_as_a_ledger_payload() -> None:
    payload = placement.plan(partition=Partition.RING, estate=Estate()).as_payload

    assert payload["partition"] == "ring"
    assert payload["appliances"] == ["dvalin", "durin", "dain"]
    assert payload["nodesPerElement"] == 3
