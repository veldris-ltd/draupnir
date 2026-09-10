"""Placement policy: where work runs, and when it must not run at all.

SAD 5.2 gives MOTSOGNIR "scheduler drivers, placement policy, array
concurrency, retry and backoff" and forbids it from knowing what a job
computes. Nothing here reads a corpus, names a base model or inspects a
checkpoint. It answers one question: given the appliances that are up, may
this run be placed, and how.

Three partitions across two venues, and they behave differently when the estate
is short of a machine.

The `adapters` partition runs independent single-node jobs on the appliances,
so losing one reduces throughput. Concurrency follows the appliances that are
actually available, and the array runs slower.

The `ring` partition runs one job across all three appliances over the BAUGR
ring, with NCCL ranks 0 to 2. Losing an appliance does not make it slower; it
makes it a different job. A two-node run of a three-node specification
produces a model that is not the model the specification describes, and it
would be discovered at evaluation after days of compute. So a ring job with
an appliance down **refuses to plan** rather than running degraded.

`export` is not on the appliances at all, and this is the correction that
`Venue` exists to hold. It was written as a third Slurm partition, and
VLD-INF-SINDRI-001 Rev 3.3 section 34 declares two: `adapters` and `ring`, both
over `dvalin,durin,dain`. There is no `export` partition, ALVISS is not a Slurm
node, and `sbatch --partition=export` is answered with `Invalid partition name
specified`. What the manual actually does with merge, quantisation and MLX
evaluation is run them on ALVISS by hand (Procedures M7, M9 and section 32) --
so `export` names the control plane, and a placement in it resolves to a local
scheduler driver rather than to the cluster.

That distinction is `Venue`. It also decides what a partition means when the
estate is down: an export job has no appliances to lose, so merge and
quantisation keep working with every appliance switched off, which is exactly
when an operator wants them to.

**Time limits are the estate's, not ours.** `MaxTime` is 48 hours on `adapters`
and 336 on `ring`. A plan asking for more is rejected by Slurm at submission,
after the run has been recorded and queued; rejecting it here means the
refusal names the limit and arrives before anything is written.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Final


class Partition(StrEnum):
    """Where a run is placed. SAD 6.2 names `adapters`; 11.1 the ring.

    Two of these are Slurm partitions and one is not; `POLICY` says which.
    """

    #: Independent single-node jobs. One element per appliance, in parallel.
    ADAPTERS = "adapters"
    #: One job across every appliance over the BAUGR ring. All or nothing.
    RING = "ring"
    #: Evaluation, merge and quantisation. On the control plane, not the estate.
    EXPORT = "export"


class Venue(StrEnum):
    """Where a partition's work actually runs.

    The distinction that was missing. A partition is not only a name to pass
    to a scheduler: it says which scheduler, and `export` has a different
    answer from the other two.
    """

    #: Slurm, on the appliances. The name is a real Slurm partition.
    CLUSTER = "cluster"
    #: The host the control plane runs on. No Slurm, no appliances, and the
    #: partition name is DRAUPNIR's own rather than something to submit.
    CONTROL_PLANE = "control-plane"


@dataclass(frozen=True, slots=True)
class PartitionPolicy:
    """What is true of one partition. One row, read by everything below."""

    venue: Venue
    #: The estate's `MaxTime`, in minutes, or None where there is no limit.
    #: VLD-INF-SINDRI-001 section 34: 48 hours on `adapters`, 336 on `ring`.
    max_minutes: int | None
    #: Whether a job here needs every appliance or none.
    all_or_nothing: bool = False
    #: The entry point group member a placement here is submitted through.
    #: `export` runs locally, which is what makes the local subprocess driver
    #: a production component rather than only a development one.
    driver_capability: str = "slurm"


#: The table. Everything else about a partition is derived from this row, so a
#: fourth partition is a line here rather than a branch in five places.
POLICY: Mapping[Partition, PartitionPolicy] = {
    Partition.ADAPTERS: PartitionPolicy(Venue.CLUSTER, max_minutes=48 * 60),
    Partition.RING: PartitionPolicy(Venue.CLUSTER, max_minutes=336 * 60, all_or_nothing=True),
    Partition.EXPORT: PartitionPolicy(
        Venue.CONTROL_PLANE, max_minutes=None, driver_capability="local"
    ),
}

#: Partitions whose jobs span the whole estate and cannot run short-handed.
#: Derived rather than declared, so it cannot disagree with the table.
#: Below this a ring is one machine, and a collective over one rank measures
#: nothing. Section 7.4's recovery configuration is exactly two.
_SMALLEST_RING: Final = 2

ALL_OR_NOTHING: frozenset[Partition] = frozenset(
    partition for partition, policy in POLICY.items() if policy.all_or_nothing
)


def estate_for(site: str, accelerator: str = "", ring_members: Sequence[str] = ()) -> Estate:
    """The estate at one forge, with the accelerator its scheduler declares.

    The one place an `Estate` is built from configuration. `SINDRI` names the
    machines; `accelerator` comes from `DRAUPNIR_ACCELERATOR`, which
    `install.sh` writes from the site's `gres.conf`. Keeping the type out of the
    module constant is what lets a second forge be a variable rather than a
    patch.
    """
    appliances = SINDRI
    if accelerator:
        appliances = tuple(replace(item, accelerator=accelerator) for item in SINDRI)
    estate = Estate(appliances=appliances, site=site)
    return estate.with_ring(*ring_members) if ring_members else estate


def policy_for(partition: Partition) -> PartitionPolicy:
    """The row for one partition."""
    return POLICY[partition]


def cluster_partitions() -> tuple[Partition, ...]:
    """The partitions a Slurm controller has to actually have.

    `export` is not among them, which is the whole of RF-E08: a partition name
    DRAUPNIR uses internally is not a name to send to a scheduler.
    """
    return tuple(partition for partition, policy in POLICY.items() if policy.venue is Venue.CLUSTER)


def verify_partitions(known: Iterable[str]) -> None:
    """Refuse a scheduler that does not have the partitions this expects.

    A run submitted to a partition that has never existed spends its budget
    queued and is then rejected, which reads as a scheduling problem days
    later. This turns it into a commissioning failure, which is where a
    configuration mistake belongs.
    """
    present = {str(name) for name in known}
    missing = tuple(item for item in cluster_partitions() if item.value not in present)
    if missing:
        raise UnknownPartitionError(missing, sorted(present))


class PlacementError(Exception):
    """Raised when a run cannot be placed."""


class UnknownPartitionError(PlacementError):
    """Raised when the scheduler does not have a partition this expects."""

    def __init__(self, missing: Sequence[Partition], present: Sequence[str]) -> None:
        """Name what is missing and what the scheduler does have."""
        self.missing = tuple(missing)
        self.present = tuple(present)
        super().__init__(
            f"the scheduler has no partition named {', '.join(item.value for item in missing)}. "
            f"It has: {', '.join(present) or 'none'}. A run submitted to a partition that does "
            "not exist queues and is then rejected, so this is refused at commissioning "
            "instead (VLD-INF-SINDRI-001 section 34 declares the partitions)."
        )


class TimeLimitError(PlacementError):
    """Raised when a run asks for longer than its partition allows.

    Slurm rejects such a submission itself, but only after the run has been
    registered and queued. Refusing here means the limit is named, and named
    before anything is written to the chain.
    """

    def __init__(self, partition: Partition, requested: int, limit: int) -> None:
        """Name the partition, what was asked for and what is allowed."""
        self.partition = partition
        self.requested = requested
        self.limit = limit
        super().__init__(
            f"the {partition} partition allows {limit} minutes ({limit / 60:.0f} hours) and "
            f"this run asks for {requested} ({requested / 60:.0f} hours). The limit is the "
            "estate's, in slurm.conf; raising it is a change to the cluster rather than to "
            "the specification."
        )


class RingSizeError(PlacementError):
    """Raised when a specification asks for more ring nodes than the forge has.

    Distinct from `DegradedRingError`, and the distinction is the whole point
    of RF-E21. A degraded ring is a fault: an appliance is down and somebody
    should go and look. A declared ring is a configuration: the forge is a
    two-node ring because its third machine failed and there was no spare
    QSFP56 cable to recable around it (gap G6), and a substrate run written for
    three nodes cannot be placed here until it is rewritten or the estate is
    repaired.

    Reporting the second as the first sends an operator to a rack to find
    nothing wrong.
    """

    def __init__(self, requested: int, declared: int, site: str) -> None:
        """Name the specification's size, the forge's, and which forge."""
        self.requested = requested
        self.declared = declared
        self.site = site
        super().__init__(
            f"this specification asks for a ring of {requested} and {site} declares a "
            f"ring of {declared}. This is the forge's configuration rather than a "
            "fault -- no appliance is necessarily down -- so nothing here will fix "
            "it: either the specification is placed at a forge with a larger ring, or "
            "the ring is restored and the declared size raised with it."
        )


class DegradedRingError(PlacementError):
    """Raised when a ring job is planned with an appliance down.

    The refusal is the point. A ring run that quietly dropped to two nodes
    would train a different model from the one its specification describes,
    and nobody would find out until evaluation -- after the compute had been
    spent.
    """

    def __init__(self, required: int, available: Sequence[str], down: Sequence[str]) -> None:
        """Name what the run needs, what is up, and what is not."""
        self.required = required
        self.available = tuple(available)
        self.down = tuple(down)
        super().__init__(
            f"the {Partition.RING} partition needs {required} appliances and "
            f"{len(available)} are available ({', '.join(available) or 'none'}); "
            f"{', '.join(down) or 'none'} unavailable. A ring run does not degrade "
            "gracefully: two nodes of a three node specification is a different "
            "model, discovered at evaluation. Refused at planning."
        )


class NoCapacityError(PlacementError):
    """Raised when no appliance at all can take the work."""

    def __init__(self, partition: Partition) -> None:
        """Name the partition with nothing behind it."""
        self.partition = partition
        super().__init__(
            f"no appliance is available for the {partition} partition. The run is "
            "refused at planning rather than queued against an empty estate."
        )


class ResidencyError(PlacementError):
    """Raised when a residency constrained corpus may not be worked on here.

    SAD 11C: "Where a corpus is residency constrained, work on it is planned
    only at a permitted site, and the constraint is checked at planning rather
    than at execution."
    """

    def __init__(self, site: str, permitted: Sequence[str]) -> None:
        """Name where the work was planned and where it is permitted."""
        self.site = site
        self.permitted = tuple(permitted)
        super().__init__(
            f"this corpus may be held only at {', '.join(permitted)} and the run was "
            f"planned at {site}. Residency is checked at planning, not at execution "
            "(SAD 11C)."
        )


@dataclass(frozen=True, slots=True)
class Appliance:
    """One machine within a forge. A node, never a site (Decision S12)."""

    name: str
    gpus: int = 1
    #: Whether the scheduler currently reports it as usable.
    available: bool = True
    #: Its NCCL rank in the ring, where it has one.
    rank: int | None = None
    #: The generic resource type Slurm knows this machine's accelerator by.
    #: VLD-INF-SINDRI-001 section 34 declares `Name=gpu Type=gb10` in
    #: `gres.conf`, and every batch script in Part 5 asks for `gpu:gb10:1`.
    #:
    #: A property of the machine rather than of a run. A specification is the
    #: unit of reproduction (SAD 6.2) and has to be portable across the Forge
    #: Matrix; a specification that named `gb10` would not run at a forge with
    #: different hardware, which is the opposite of what portability is for.
    accelerator: str = ""


#: The estate at Sindri. SAD 5.1: executor shims run on DVALIN, DURIN, DAIN.
#:
#: The machines and their ranks are here because they are the estate. The
#: accelerator type is not: it is `gres.conf` on REGIN, it differs between
#: forges, and a second forge in the Forge Matrix must be a setting rather than
#: an edit. `estate_for` stamps it on.
SINDRI: tuple[Appliance, ...] = (
    Appliance(name="dvalin", gpus=1, rank=0),
    Appliance(name="durin", gpus=1, rank=1),
    Appliance(name="dain", gpus=1, rank=2),
)


def _rank_order(item: Appliance) -> tuple[int, str]:
    """Ring order: by NCCL rank, then by name for an appliance without one."""
    return (item.rank if item.rank is not None else 99, item.name)


@dataclass(frozen=True, slots=True)
class Estate:
    """The appliances a forge has, and which are up."""

    appliances: tuple[Appliance, ...] = SINDRI
    site: str = "sindri"

    #: The appliances cabled into the ring, when the forge declares them.
    #: Empty means all of them, which is the ordinary case.
    #:
    #: It is declared rather than counted because the recovery configuration
    #: exists. VLD-WIR-SINDRI-001 section 7.4: on an appliance failure the two
    #: survivors are recabled as a direct pair and "the ring partition [is set]
    #: to two nodes". The estate is then three machines with one down *and* a
    #: two-node ring, and those are different facts -- counting `appliances`
    #: cannot tell them apart, and G6 (zero spare QSFP56 cables) means the
    #: recovery configuration is the one the estate will actually be in.
    #:
    #: **Members rather than a size**, which is a deliberate departure from how
    #: RF-E21 framed this. A size cannot say *which* two, and the answer is not
    #: derivable: ring membership is which machines have a DAC cable between
    #: them. Consider DVALIN failing, DURIN and DAIN being recabled as a pair,
    #: and DVALIN later being repaired. The estate then has three appliances,
    #: all three available for adapter work, and a ring of exactly DURIN and
    #: DAIN -- and a size of two would have picked DVALIN and DURIN by rank,
    #: which is a run submitted across a cable that does not exist. Slurm's own
    #: recovery configuration names them (`PartitionName=ring Nodes=durin,dain`)
    #: and this mirrors it.
    #:
    #: A run refused against a declared ring is also a different refusal from
    #: one refused against a degraded estate, which is the other half of the
    #: point: "this forge is a two-node ring" sends nobody looking for a fault,
    #: and "an appliance is down" sends somebody to the rack.
    ring_members: tuple[str, ...] = ()

    @property
    def available(self) -> tuple[Appliance, ...]:
        """Appliances the scheduler reports as usable, in ring order."""
        return tuple(sorted((i for i in self.appliances if i.available), key=_rank_order))

    @property
    def down(self) -> tuple[str, ...]:
        """Appliances that are not usable."""
        return tuple(sorted(item.name for item in self.appliances if not item.available))

    @property
    def size(self) -> int:
        """How many appliances the forge has when everything is up."""
        return len(self.appliances)

    @property
    def ring(self) -> tuple[Appliance, ...]:
        """The appliances a ring job spans, in rank order.

        The declared members where there are any, the whole estate otherwise.
        """
        if not self.ring_members:
            return tuple(sorted(self.appliances, key=_rank_order))
        wanted = set(self.ring_members)
        return tuple(sorted((i for i in self.appliances if i.name in wanted), key=_rank_order))

    @property
    def ring_size(self) -> int:
        """How many appliances a ring job spans."""
        return len(self.ring)

    @property
    def ring_is_declared(self) -> bool:
        """Whether this forge has been told which machines are cabled together."""
        return bool(self.ring_members) and len(self.ring_members) != self.size

    def with_ring(self, *names: str) -> Estate:
        """The same estate with the ring declared to be exactly `names`.

        Refuses a name the estate does not have, and a ring below two: a
        one-node "ring" is a single machine, and a collective over one rank
        measures nothing. Section 7.4's recovery configuration is two.
        """
        declared = tuple(dict.fromkeys(names))
        if len(declared) < _SMALLEST_RING:
            msg = (
                f"a ring of {len(declared)} is not a ring. Two is the smallest the "
                "recovery configuration of VLD-WIR-SINDRI-001 section 7.4 describes; "
                "below that a collective spans one rank and measures nothing."
            )
            raise PlacementError(msg)
        unknown = sorted(set(declared) - {item.name for item in self.appliances})
        if unknown:
            msg = (
                f"the ring was declared over {', '.join(unknown)}, which {self.site} does "
                f"not have. Its appliances are "
                f"{', '.join(sorted(item.name for item in self.appliances))}."
            )
            raise PlacementError(msg)
        return replace(self, ring_members=declared)

    def with_all_available(self) -> Estate:
        """The same estate with every appliance back.

        The inverse of `without`, and needed for the same reason: an appliance
        that has returned has to be able to return. Without it an estate could
        only ever shrink, and the first drained node would take a ring run out
        of reach until the worker restarted.
        """
        if not self.down:
            return self
        return replace(
            self,
            appliances=tuple(replace(item, available=True) for item in self.appliances),
        )

    @property
    def accelerator(self) -> str:
        """The accelerator type the estate's appliances agree on.

        Empty when they do not, and deliberately: a heterogeneous estate has
        no single answer, and inventing one would submit a job asking for
        hardware that half the appliances do not have. An estate like that
        needs a partition per accelerator, which is a configuration decision
        rather than something to paper over here.
        """
        declared = {item.accelerator for item in self.appliances if item.accelerator}
        if len(declared) != 1 or len(declared) != len(
            {item.accelerator for item in self.appliances}
        ):
            return ""
        return declared.pop()

    def gres(self, gpus_per_node: int) -> str:
        """The generic resource string a job on this estate asks for.

        `gpu:gb10:1` at Sindri. Empty where the estate declares no accelerator,
        which is what a development machine looks like, and the drivers then
        fall back to an untyped count.
        """
        if not gpus_per_node or not self.accelerator:
            return ""
        return f"gpu:{self.accelerator}:{gpus_per_node}"

    def without(self, *names: str) -> Estate:
        """The same estate with these appliances marked down.

        Used by the tests, and by an operator modelling a maintenance window
        before they take a machine out.
        """
        lost = set(names)
        from dataclasses import replace

        return replace(
            self,
            appliances=tuple(
                replace(item, available=False) if item.name in lost else item
                for item in self.appliances
            ),
        )


@dataclass(frozen=True, slots=True)
class Placement:
    """A decision about where and how a run executes."""

    partition: Partition
    #: Appliances the work will actually use.
    appliances: tuple[str, ...]
    #: How many array elements may run at once. One per appliance.
    concurrency: int
    #: How many nodes each element occupies.
    nodes_per_element: int = 1
    #: True when concurrency was reduced because the estate is short.
    reduced: bool = False
    #: The generic resource this placement asks the scheduler for, e.g.
    #: `gpu:gb10:1`. Empty on a control-plane placement and on an estate that
    #: declares no accelerator.
    gres: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def as_payload(self) -> dict[str, object]:
        """The ledger payload for a QUEUED to TRAINING transition."""
        return {
            "partition": str(self.partition),
            "appliances": list(self.appliances),
            "concurrency": self.concurrency,
            "nodesPerElement": self.nodes_per_element,
            "reduced": self.reduced,
            "gres": self.gres,
            "notes": list(self.notes),
        }


def plan(
    *,
    partition: Partition,
    estate: Estate,
    requested_concurrency: int = 1,
    nodes_per_element: int = 1,
    residency_constraint: Iterable[str] = (),
    time_limit_minutes: int | None = None,
    gpus_per_node: int = 1,
) -> Placement:
    """Decide where a run executes, or refuse.

    Concurrency is capped at one element per available appliance. SAD 6.2 asks
    for three concurrent adapter elements against three appliances; asking for
    more would queue work behind itself and asking for fewer would idle a
    machine, so the cap is the estate.
    """
    permitted = tuple(residency_constraint)
    if permitted and estate.site not in permitted:
        raise ResidencyError(estate.site, permitted)

    policy = policy_for(partition)

    # The estate's own limit, refused before anything is recorded. Slurm would
    # refuse it too, after the run had been registered and had waited.
    if (
        time_limit_minutes is not None
        and policy.max_minutes is not None
        and time_limit_minutes > policy.max_minutes
    ):
        raise TimeLimitError(partition, time_limit_minutes, policy.max_minutes)

    if policy.venue is Venue.CONTROL_PLANE:
        # Nothing about the estate applies. Export work runs on the host the
        # control plane runs on, so it has no appliances to lose and no ring to
        # be short of: merge and quantisation keep working with every appliance
        # switched off, which is exactly when an operator wants them to.
        return Placement(
            partition=partition,
            appliances=(),
            concurrency=1,
            nodes_per_element=1,
            notes=("runs on the control plane, not on the estate",),
        )

    available = estate.available
    if not available:
        raise NoCapacityError(partition)

    if policy.all_or_nothing:
        # A ring job needs the whole ring, and the ring is what the forge
        # declares it to be rather than how many machines are racked. See
        # `Estate.ring_size`: a two-node recovery ring is a configuration, and
        # a three-appliance forge with one machine down is a fault, and the
        # two must not produce the same refusal.
        required = estate.ring_size
        if nodes_per_element > required:
            raise RingSizeError(nodes_per_element, required, estate.site)

        # The ring's own members, not whatever happens to be up. A third
        # appliance that is racked and available but not cabled into the ring
        # is not a ring node, and a two-node ring whose members are down is
        # degraded however many other machines are idle.
        members = tuple(item for item in estate.ring if item.available)
        if len(members) < required:
            raise DegradedRingError(required, [item.name for item in members], estate.down)

        placed = [f"ranks {', '.join(str(item.rank) for item in members)}"]
        if estate.ring_is_declared:
            placed.append(
                f"{estate.site} declares a ring of {', '.join(estate.ring_members)} "
                f"-- {required} of its {estate.size} appliances "
                "(VLD-WIR-SINDRI-001 section 7.4)"
            )
        return Placement(
            partition=partition,
            appliances=tuple(item.name for item in members),
            concurrency=1,
            nodes_per_element=required,
            gres=estate.gres(gpus_per_node),
            notes=tuple(placed),
        )

    if nodes_per_element > len(available):
        raise DegradedRingError(nodes_per_element, [item.name for item in available], estate.down)

    # One element per appliance. Losing one reduces throughput and nothing else.
    capacity = len(available) // max(nodes_per_element, 1)
    concurrency = max(min(requested_concurrency, capacity), 1)
    reduced = concurrency < requested_concurrency

    notes: list[str] = []
    if reduced:
        notes.append(
            f"concurrency reduced from {requested_concurrency} to {concurrency}: "
            f"{', '.join(estate.down)} unavailable"
        )

    return Placement(
        partition=partition,
        appliances=tuple(item.name for item in available),
        concurrency=concurrency,
        nodes_per_element=nodes_per_element,
        reduced=reduced,
        gres=estate.gres(gpus_per_node),
        notes=tuple(notes),
    )


def partition_for(kind: str) -> Partition:
    """Which partition a run of this kind belongs to.

    A substrate run spans the estate and goes to the ring; an adapter run is
    independent and goes to `adapters`. Stated as a table so that adding a
    kind is a line here rather than a branch somewhere else.
    """
    return {
        "SubstrateRun": Partition.RING,
        "AdapterRun": Partition.ADAPTERS,
        "MergeRun": Partition.EXPORT,
        "ExportRun": Partition.EXPORT,
        "EvalRun": Partition.EXPORT,
    }.get(kind, Partition.ADAPTERS)
