"""Placement policy: where work runs, and when it must not run at all.

SAD 5.2 gives MOTSOGNIR "scheduler drivers, placement policy, array
concurrency, retry and backoff" and forbids it from knowing what a job
computes. Nothing here reads a corpus, names a base model or inspects a
checkpoint. It answers one question: given the appliances that are up, may
this run be placed, and how.

Two partitions, and they behave differently when the estate is short of a
machine.

The `adapters` partition runs independent single-node jobs, so losing an
appliance reduces throughput. Concurrency follows the appliances that are
actually available, and the array runs slower.

The `ring` partition runs one job across all three appliances over the BAUGR
ring, with NCCL ranks 0 to 2. Losing an appliance does not make it slower; it
makes it a different job. A two-node run of a three-node specification
produces a model that is not the model the specification describes, and it
would be discovered at evaluation after days of compute. So a ring job with
an appliance down **refuses to plan** rather than running degraded.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum


class Partition(StrEnum):
    """Slurm partitions at Sindri. SAD 6.2 names `adapters`; 11.1 the ring."""

    #: Independent single-node jobs. One element per appliance, in parallel.
    ADAPTERS = "adapters"
    #: One job across every appliance over the BAUGR ring. All or nothing.
    RING = "ring"
    #: Evaluation, merge and quantisation on ALVISS.
    EXPORT = "export"


#: Partitions whose jobs span the whole estate and cannot run short-handed.
ALL_OR_NOTHING: frozenset[Partition] = frozenset({Partition.RING})


class PlacementError(Exception):
    """Raised when a run cannot be placed."""


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


#: The estate at Sindri. SAD 5.1: executor shims run on DVALIN, DURIN, DAIN.
SINDRI: tuple[Appliance, ...] = (
    Appliance(name="dvalin", gpus=1, rank=0),
    Appliance(name="durin", gpus=1, rank=1),
    Appliance(name="dain", gpus=1, rank=2),
)


@dataclass(frozen=True, slots=True)
class Estate:
    """The appliances a forge has, and which are up."""

    appliances: tuple[Appliance, ...] = SINDRI
    site: str = "sindri"

    @property
    def available(self) -> tuple[Appliance, ...]:
        """Appliances the scheduler reports as usable, in ring order."""
        return tuple(
            sorted(
                (item for item in self.appliances if item.available),
                key=lambda item: (item.rank if item.rank is not None else 99, item.name),
            )
        )

    @property
    def down(self) -> tuple[str, ...]:
        """Appliances that are not usable."""
        return tuple(sorted(item.name for item in self.appliances if not item.available))

    @property
    def size(self) -> int:
        """How many appliances the forge has when everything is up."""
        return len(self.appliances)

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
            "notes": list(self.notes),
        }


def plan(
    *,
    partition: Partition,
    estate: Estate,
    requested_concurrency: int = 1,
    nodes_per_element: int = 1,
    residency_constraint: Iterable[str] = (),
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

    available = estate.available
    if not available:
        raise NoCapacityError(partition)

    if partition in ALL_OR_NOTHING:
        # A ring job needs every appliance, and the specification's node count
        # is what it needs, not what happens to be up.
        required = max(nodes_per_element, estate.size)
        if len(available) < required:
            raise DegradedRingError(required, [item.name for item in available], estate.down)
        return Placement(
            partition=partition,
            appliances=tuple(item.name for item in available),
            concurrency=1,
            nodes_per_element=required,
            notes=(f"ranks {', '.join(str(item.rank) for item in available)}",),
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
