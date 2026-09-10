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


# ---------------------------------------------------------------------------
# Venues. RF-E08: `export` was a Slurm partition the estate does not have.
# ---------------------------------------------------------------------------


def test_every_partition_has_a_policy() -> None:
    """The table is the definition, so nothing may be missing from it.

    Adding a partition and forgetting its row would give it a venue by
    KeyError at the moment it was first planned, which is the worst place to
    find out.
    """
    assert set(placement.POLICY) == set(placement.Partition)


def test_all_or_nothing_is_derived_from_the_table() -> None:
    """Two declarations of one fact is how one of them becomes false."""
    assert (
        frozenset({p for p, policy in placement.POLICY.items() if policy.all_or_nothing})
        == placement.ALL_OR_NOTHING
    )
    assert frozenset({placement.Partition.RING}) == placement.ALL_OR_NOTHING


def test_export_is_not_a_partition_the_scheduler_is_asked_for() -> None:
    """The finding itself.

    VLD-INF-SINDRI-001 section 34 declares `adapters` and `ring`, both over
    `dvalin,durin,dain`. There is no `export` partition and ALVISS is not a
    Slurm node, so `sbatch --partition=export` is answered with `Invalid
    partition name specified`.
    """
    assert placement.cluster_partitions() == (
        placement.Partition.ADAPTERS,
        placement.Partition.RING,
    )
    assert placement.Partition.EXPORT not in placement.cluster_partitions()
    assert placement.policy_for(placement.Partition.EXPORT).venue is placement.Venue.CONTROL_PLANE


def test_export_work_runs_with_the_whole_estate_switched_off() -> None:
    """The consequence of the venue, and the reason it is worth having.

    Merge, quantisation and MLX evaluation happen on the control plane; they
    have no appliances to lose. An operator who has powered the estate down
    can still finish packaging a release, which is exactly when they want to.
    """
    dead = placement.Estate().without("dvalin", "durin", "dain")

    placed = placement.plan(partition=placement.Partition.EXPORT, estate=dead)

    assert placed.appliances == (), "an export placement named an appliance"
    assert placed.concurrency == 1
    assert "control plane" in placed.notes[0]


def test_a_cluster_partition_still_refuses_when_the_estate_is_down() -> None:
    """The venue changes nothing for the two partitions that are on it."""
    dead = placement.Estate().without("dvalin", "durin", "dain")

    with pytest.raises(placement.NoCapacityError):
        placement.plan(partition=placement.Partition.ADAPTERS, estate=dead)


def test_a_scheduler_missing_a_partition_is_refused_by_name() -> None:
    """A commissioning failure rather than a 48-hour queue wait.

    A run submitted to a partition that has never existed spends its budget
    queued and is then rejected, which reads as a scheduling problem days
    later rather than as the configuration mistake it is.
    """
    placement.verify_partitions(["adapters", "ring"])
    placement.verify_partitions(["adapters", "ring", "debug"])

    with pytest.raises(placement.UnknownPartitionError) as refusal:
        placement.verify_partitions(["adapters"])

    assert "ring" in str(refusal.value)
    assert "adapters" in str(refusal.value), "the refusal does not say what the scheduler does have"


def test_a_scheduler_is_not_asked_for_the_export_partition() -> None:
    """A scheduler is never asked for the export partition.

    The two the manual declares are enough. If `export` were still expected of
    the scheduler, a correctly built Sindri would fail this check.
    """
    placement.verify_partitions(["adapters", "ring"])


def test_a_run_longer_than_its_partition_allows_is_refused_with_the_limit() -> None:
    """VLD-INF-SINDRI-001 section 34: `MaxTime` is 48 hours on `adapters`.

    Slurm rejects such a submission itself, but only after the run has been
    registered and has waited. Refusing here names the limit, and names it
    before anything is written to the chain.
    """
    with pytest.raises(placement.TimeLimitError) as refusal:
        placement.plan(
            partition=placement.Partition.ADAPTERS,
            estate=placement.Estate(),
            time_limit_minutes=72 * 60,
        )

    assert refusal.value.limit == 48 * 60
    assert "48 hours" in str(refusal.value)
    assert "slurm.conf" in str(refusal.value), "the refusal does not say whose limit it is"


def test_the_ring_allows_the_longer_limit_the_estate_gives_it() -> None:
    """336 hours, because a substrate run is a fortnight of compute."""
    placed = placement.plan(
        partition=placement.Partition.RING,
        estate=placement.Estate(),
        nodes_per_element=3,
        time_limit_minutes=300 * 60,
    )
    assert placed.nodes_per_element == 3


def test_the_control_plane_partition_has_no_time_limit_to_exceed() -> None:
    """It is not a Slurm partition, so there is no `MaxTime` to enforce.

    Inventing one here would be DRAUPNIR making up a constraint the estate
    does not have.
    """
    assert placement.policy_for(placement.Partition.EXPORT).max_minutes is None

    placed = placement.plan(
        partition=placement.Partition.EXPORT,
        estate=placement.Estate(),
        time_limit_minutes=10_000 * 60,
    )
    assert placed.partition is placement.Partition.EXPORT


def test_every_export_kind_lands_on_the_control_plane() -> None:
    """Every export kind lands on the control plane.

    `partition_for` and the venue table have to agree about the work the manual
    does by hand on ALVISS.
    """
    for kind in ("MergeRun", "ExportRun", "EvalRun"):
        partition = placement.partition_for(kind)
        assert placement.policy_for(partition).venue is placement.Venue.CONTROL_PLANE, (
            f"{kind} is placed on {partition}, which is not on the control plane"
        )

    for kind in ("SubstrateRun", "AdapterRun"):
        partition = placement.partition_for(kind)
        assert placement.policy_for(partition).venue is placement.Venue.CLUSTER


# ---------------------------------------------------------------------------
# The accelerator. RF-E09: the estate's hardware, not the specification's.
# ---------------------------------------------------------------------------


def test_the_estate_declares_the_accelerator_it_is_configured_with() -> None:
    """VLD-INF-SINDRI-001 section 34: `Name=gpu Type=gb10` in gres.conf.

    From configuration rather than from the module constant. `SINDRI` names the
    machines and their ranks, which are the estate; the accelerator type is
    `gres.conf` on REGIN and differs between forges, so a second forge is a
    variable rather than an edit.
    """
    sindri = placement.estate_for("sindri", "gb10")
    assert sindri.accelerator == "gb10"
    assert sindri.gres(1) == "gpu:gb10:1"

    unconfigured = placement.estate_for("sindri")
    assert unconfigured.accelerator == ""
    assert unconfigured.gres(1) == "", "an unconfigured forge invented an accelerator"


def test_a_placement_carries_the_gres_into_the_ledger() -> None:
    """What the estate offered is part of what was decided, so it is recorded.

    A run whose payload does not say what it asked for cannot be compared with
    one submitted after the hardware changed.
    """
    placed = placement.plan(
        partition=placement.Partition.ADAPTERS,
        estate=placement.estate_for("sindri", "gb10"),
    )
    assert placed.gres == "gpu:gb10:1"
    assert placed.as_payload["gres"] == "gpu:gb10:1"


def test_an_estate_that_declares_no_accelerator_asks_for_none() -> None:
    """A development machine. The drivers fall back to an untyped count."""
    bare = placement.Estate(appliances=(placement.Appliance(name="dev"),))
    assert bare.accelerator == ""
    assert bare.gres(1) == ""


def test_a_mixed_estate_refuses_to_guess_an_accelerator() -> None:
    """Two kinds of machine have no single answer.

    Inventing one would submit a job asking for hardware half the appliances
    do not have, and Slurm would place it on whichever it happened to pick. An
    estate like that needs a partition per accelerator, which is a
    configuration decision rather than something to paper over here.
    """
    mixed = placement.Estate(
        appliances=(
            placement.Appliance(name="a", accelerator="gb10"),
            placement.Appliance(name="b", accelerator="h100"),
        )
    )
    assert mixed.accelerator == ""
    assert mixed.gres(1) == ""


def test_a_partly_declared_estate_refuses_too() -> None:
    """One machine declaring and one not is not agreement.

    The set of declared types has one member, which a careless check would
    read as unanimity while one appliance has said nothing at all.
    """
    partial = placement.Estate(
        appliances=(
            placement.Appliance(name="a", accelerator="gb10"),
            placement.Appliance(name="b"),
        )
    )
    assert partial.accelerator == ""


def test_a_control_plane_placement_asks_for_no_accelerator() -> None:
    """Export work runs where there is no GPU to request."""
    placed = placement.plan(
        partition=placement.Partition.EXPORT,
        estate=placement.estate_for("sindri", "gb10"),
    )
    assert placed.gres == ""


def test_a_run_that_wants_no_gpu_asks_for_no_accelerator() -> None:
    placed = placement.plan(
        partition=placement.Partition.ADAPTERS,
        estate=placement.estate_for("sindri", "gb10"),
        gpus_per_node=0,
    )
    assert placed.gres == ""


# ---------------------------------------------------------------------------
# The declared ring. RF-E21.
# ---------------------------------------------------------------------------


def test_a_two_node_ring_plans() -> None:
    """VLD-WIR-SINDRI-001 section 7.4's recovery configuration.

    On an appliance failure the two survivors are recabled as a direct pair and
    the ring partition is set to two nodes. Before this, `place()` refused
    against `len(appliances)` and there was no way to tell DRAUPNIR the ring
    had changed — so the configuration the wiring document specifies could not
    be represented at all.
    """
    estate = placement.estate_for("sindri", "gb10", ring_members=("durin", "dain"))

    placed = placement.plan(partition=placement.Partition.RING, estate=estate, nodes_per_element=2)

    assert placed.appliances == ("durin", "dain")
    assert placed.nodes_per_element == 2


def test_a_three_node_specification_is_refused_naming_the_declared_size() -> None:
    """The acceptance criterion, and the refusal that must not be a fault."""
    estate = placement.estate_for("sindri", "gb10", ring_members=("durin", "dain"))

    with pytest.raises(placement.RingSizeError) as raised:
        placement.plan(partition=placement.Partition.RING, estate=estate, nodes_per_element=3)

    assert raised.value.requested == 3
    assert raised.value.declared == 2
    assert raised.value.site == "sindri"
    assert "configuration rather than a fault" in str(raised.value)


def test_a_declared_ring_is_a_different_refusal_from_a_degraded_one() -> None:
    """The distinction is the point of the finding.

    A degraded ring sends somebody to a rack. A declared ring should not: no
    appliance is necessarily down, the forge is simply configured as a smaller
    ring, and nothing at the rack will change that.
    """
    declared = placement.estate_for("sindri", "gb10", ring_members=("durin", "dain"))
    degraded = placement.estate_for("sindri", "gb10").without("dain")

    with pytest.raises(placement.RingSizeError):
        placement.plan(partition=placement.Partition.RING, estate=declared, nodes_per_element=3)

    with pytest.raises(placement.DegradedRingError):
        placement.plan(partition=placement.Partition.RING, estate=degraded, nodes_per_element=3)


def test_the_ring_keeps_its_members_when_the_third_appliance_returns() -> None:
    """Why members and not a size, which is where a count would have failed.

    DVALIN fails, DURIN and DAIN are recabled as a pair, and DVALIN is later
    repaired. The estate then has three appliances, all three usable for
    adapter work, and a ring of exactly DURIN and DAIN — because ring
    membership is which machines have a DAC cable between them. A declared
    *size* of two would have picked DVALIN and DURIN by rank, which is a run
    submitted across a cable that does not exist.
    """
    estate = placement.estate_for("sindri", "gb10", ring_members=("durin", "dain"))

    repaired = estate.with_all_available()
    placed = placement.plan(
        partition=placement.Partition.RING, estate=repaired, nodes_per_element=2
    )

    assert placed.appliances == ("durin", "dain")
    assert "dvalin" not in placed.appliances


def test_a_declared_ring_whose_member_is_down_is_degraded() -> None:
    """Two idle machines outside the ring do not make it whole.

    The ring is DURIN and DAIN; DAIN is down. DVALIN being racked, powered and
    available is irrelevant, because it is not cabled to either of them.
    """
    estate = placement.estate_for("sindri", "gb10", ring_members=("durin", "dain"))

    with pytest.raises(placement.DegradedRingError) as raised:
        placement.plan(
            partition=placement.Partition.RING,
            estate=estate.without("dain"),
            nodes_per_element=2,
        )

    assert "durin" in str(raised.value)


def test_the_placement_says_the_ring_was_declared() -> None:
    """An operator reading a placement should not have to infer it."""
    estate = placement.estate_for("sindri", "gb10", ring_members=("durin", "dain"))

    placed = placement.plan(partition=placement.Partition.RING, estate=estate, nodes_per_element=2)

    assert any("declares a ring of durin, dain" in note for note in placed.notes)
    assert any("7.4" in note for note in placed.notes)


def test_an_undeclared_ring_is_the_whole_estate() -> None:
    """The ordinary case is unchanged, and says nothing extra."""
    estate = placement.estate_for("sindri", "gb10")

    assert estate.ring_size == 3
    assert estate.ring_is_declared is False

    placed = placement.plan(partition=placement.Partition.RING, estate=estate, nodes_per_element=3)
    assert placed.appliances == ("dvalin", "durin", "dain")
    assert not any("declares a ring" in note for note in placed.notes)


def test_a_ring_of_one_is_refused() -> None:
    """A collective over one rank measures nothing."""
    estate = placement.estate_for("sindri", "gb10")

    with pytest.raises(placement.PlacementError, match="is not a ring"):
        estate.with_ring("dvalin")


def test_a_ring_naming_a_machine_the_forge_does_not_have_is_refused() -> None:
    """A typo in draupnir.env during a recovery must not become a silent ring."""
    estate = placement.estate_for("sindri", "gb10")

    with pytest.raises(placement.PlacementError, match="durrin"):
        estate.with_ring("durrin", "dain")


def test_the_declared_ring_survives_the_accelerator_being_stamped_on() -> None:
    """`estate_for` does two things; neither may undo the other."""
    estate = placement.estate_for("sindri", "gb10", ring_members=("durin", "dain"))

    assert estate.accelerator == "gb10"
    assert estate.ring_members == ("durin", "dain")
