"""Pre-flight capacity: refuse a run at planning rather than partway through it.

AC-S10: "A run whose projected output exceeds the vault free space is refused
at planning rather than failing partway." The word that matters is *planning*.
A run refused after an allocation has been consumed has already spent the
scarce resource on this estate to learn something that was knowable before it
started, and it leaves a half-written checkpoint behind.

The estimate is deliberately crude and deliberately pessimistic. It is not
trying to predict a checkpoint size to the megabyte; it is trying to be
confident that the run cannot fill the vault. An estimate that is too small
lets a run fail at hour nine, and an estimate that is too large refuses a run
that would have fitted -- so the arithmetic is stated plainly here rather than
tuned, and every number is somewhere an operator can read and change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from draupnir.hodd.stores import readable_size
from draupnir.interfaces.types import RunSpec

#: Keep this much of the vault free at all times. A vault at 100 per cent is
#: not merely full: it fails writes that are already in flight, and on a
#: copy-on-write filesystem it can fail deletes too.
DEFAULT_RESERVE_FRACTION = 0.10

#: What one adapter checkpoint costs, before the multipliers below. A LoRA
#: adapter of rank 64 over a 35B parameter base is a few hundred megabytes;
#: this is rounded well up, because being wrong in this direction refuses a
#: run and being wrong in the other loses one.
ADAPTER_CHECKPOINT_BYTES = 2 * 1024**3

#: A full or substrate run writes the model, not an adapter.
DENSE_CHECKPOINT_BYTES = 80 * 1024**3

#: Checkpoints are kept, not overwritten: a run that cannot be resumed from an
#: intermediate checkpoint is a run that restarts from zero.
CHECKPOINTS_RETAINED = 3

#: Logs, metrics, evaluation reports and the quantised outputs of the release
#: route. A fraction of the checkpoint estimate rather than its own model.
OVERHEAD_FRACTION = 0.25


class Store(Protocol):
    """What a quota check needs from a store driver."""

    def free_bytes(self) -> int:
        """Bytes currently available."""
        ...

    def total_bytes(self) -> int:
        """Total capacity."""
        ...


class QuotaExceededError(Exception):
    """Raised when a run would breach the reserve threshold.

    Carries the arithmetic. A refusal an operator cannot check is a refusal
    they will work around.
    """

    def __init__(self, estimate: Estimate) -> None:
        """Record the estimate that produced the refusal."""
        self.estimate = estimate
        super().__init__(
            f"the run is projected to write {readable_size(estimate.projected_bytes)} and "
            f"the vault has {readable_size(estimate.free_bytes)} free, of which "
            f"{readable_size(estimate.reserve_bytes)} is reserved. "
            f"That leaves {readable_size(estimate.usable_bytes)} usable, "
            f"{readable_size(estimate.shortfall)} short. "
            "Refused at planning rather than partway through (AC-S10)."
        )


@dataclass(frozen=True, slots=True)
class Estimate:
    """What a run is projected to write, and what the vault can take."""

    projected_bytes: int
    free_bytes: int
    total_bytes: int
    reserve_fraction: float
    #: How the projection was reached, so an operator can see the assumption
    #: rather than the conclusion.
    workings: tuple[str, ...] = ()

    @property
    def reserve_bytes(self) -> int:
        """The floor the vault is kept above."""
        return int(self.total_bytes * self.reserve_fraction)

    @property
    def usable_bytes(self) -> int:
        """Free space above the reserve. Never negative."""
        return max(self.free_bytes - self.reserve_bytes, 0)

    @property
    def fits(self) -> bool:
        """Whether the run can be admitted."""
        return self.projected_bytes <= self.usable_bytes

    @property
    def shortfall(self) -> int:
        """How much more room the run would need. Zero when it fits."""
        return max(self.projected_bytes - self.usable_bytes, 0)

    def explain(self) -> str:
        """The arithmetic, one step per line."""
        return "\n".join(
            [
                *self.workings,
                f"projected      {readable_size(self.projected_bytes)}",
                f"free           {readable_size(self.free_bytes)}",
                f"reserve ({self.reserve_fraction:.0%})   {readable_size(self.reserve_bytes)}",
                f"usable         {readable_size(self.usable_bytes)}",
                f"verdict        {'fits' if self.fits else 'refused'}",
            ]
        )


def project(spec: RunSpec) -> tuple[int, tuple[str, ...]]:
    """Estimate what a run will write, and show the working."""
    dense = spec.train.method in {"full", "substrate", "pretrain"} or spec.kind in {"SubstrateRun"}
    per_checkpoint = DENSE_CHECKPOINT_BYTES if dense else ADAPTER_CHECKPOINT_BYTES
    kind = "dense" if dense else "adapter"

    checkpoints = per_checkpoint * CHECKPOINTS_RETAINED
    # An array runs several elements concurrently, and each writes its own.
    concurrent = max(spec.placement.max_concurrent, 1)
    subtotal = checkpoints * concurrent
    overhead = int(subtotal * OVERHEAD_FRACTION)

    workings = (
        f"method         {spec.train.method} ({kind})",
        f"checkpoint     {readable_size(per_checkpoint)}",
        f"retained       {CHECKPOINTS_RETAINED}",
        f"concurrent     {concurrent}",
        f"overhead ({OVERHEAD_FRACTION:.0%})  {readable_size(overhead)}",
    )
    return subtotal + overhead, workings


def estimate(
    spec: RunSpec, store: Store, *, reserve_fraction: float = DEFAULT_RESERVE_FRACTION
) -> Estimate:
    """Project a run's output against what the vault can take."""
    if not 0.0 <= reserve_fraction < 1.0:
        msg = f"the reserve fraction is a proportion of the vault, not {reserve_fraction}"
        raise ValueError(msg)

    projected, workings = project(spec)
    return Estimate(
        projected_bytes=projected,
        free_bytes=store.free_bytes(),
        total_bytes=store.total_bytes(),
        reserve_fraction=reserve_fraction,
        workings=workings,
    )


def check(
    spec: RunSpec, store: Store, *, reserve_fraction: float = DEFAULT_RESERVE_FRACTION
) -> Estimate:
    """Raise `QuotaExceededError` unless the run fits. Called at planning."""
    projection = estimate(spec, store, reserve_fraction=reserve_fraction)
    if not projection.fits:
        raise QuotaExceededError(projection)
    return projection
