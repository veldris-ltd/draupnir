"""Checkpoint policy: never lose more than half an hour of training.

A checkpoint interval fixed in a specification is a guess about hardware. Set
it too high and a node failure eighteen hours in loses eighteen hours. Set it
too low and a 27B run spends its allocation writing 54 GB of optimiser state
to a filesystem instead of training.

So the interval is not authored, it is derived: measure how long a step
actually takes on the machine the job actually landed on, and choose the
largest interval that keeps unwritten work under `MAX_UNWRITTEN`.

The first estimate is made from the specification, before anything has run,
because a job needs a `save_steps` in its configuration at submission. It is
deliberately conservative. After `RECOMPUTE_AFTER` steps the observation
replaces the guess, and the driver rewrites the interval.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

#: The most work that may ever be unwritten. The number is the requirement:
#: "no more than thirty minutes of work is ever unwritten".
MAX_UNWRITTEN = timedelta(minutes=30)

#: How many steps to observe before trusting the measurement. Early steps
#: include compilation, allocator warm-up and the first optimiser state
#: allocation, and are not representative of the next eighteen hours.
RECOMPUTE_AFTER = 50

#: The interval used before anything has been observed. Assumes a slow step,
#: so the first estimate errs towards checkpointing too often rather than
#: losing more than the budget.
ASSUMED_STEP = timedelta(seconds=12)

#: Never checkpoint more often than this, whatever the measurement says. A
#: 27B checkpoint is tens of gigabytes; writing one every ten steps would
#: spend the allocation on the filesystem.
MIN_SAVE_STEPS = 10

#: Never less often than this, whatever the measurement says. Guards against a
#: pathologically fast measured step producing an interval so large that a
#: single failure loses a whole shard of work for reasons of arithmetic.
MAX_SAVE_STEPS = 2000


class CheckpointError(Exception):
    """Raised when a checkpoint interval cannot be derived."""


@dataclass(frozen=True, slots=True)
class Observation:
    """What has been measured about step time so far."""

    steps: int
    elapsed: timedelta

    @property
    def step_time(self) -> timedelta:
        """Mean wall clock time per step."""
        if self.steps <= 0:
            msg = "step time cannot be derived from zero steps"
            raise CheckpointError(msg)
        return self.elapsed / self.steps

    @property
    def sufficient(self) -> bool:
        """Whether enough steps have run for the measurement to be trusted."""
        return self.steps >= RECOMPUTE_AFTER


@dataclass(frozen=True, slots=True)
class Policy:
    """A checkpoint interval, and the reasoning that produced it."""

    save_steps: int
    #: The step time the interval was derived from.
    step_time: timedelta
    #: How much work is at risk between checkpoints, at that step time.
    exposure: timedelta
    #: True while the interval rests on `ASSUMED_STEP` rather than measurement.
    provisional: bool
    reason: str

    @property
    def within_budget(self) -> bool:
        """Whether the interval keeps unwritten work inside the budget."""
        return self.exposure <= MAX_UNWRITTEN

    @property
    def as_payload(self) -> dict[str, object]:
        """The ledger payload for a checkpoint policy decision."""
        return {
            "saveSteps": self.save_steps,
            "stepTimeSeconds": round(self.step_time.total_seconds(), 4),
            "exposureSeconds": round(self.exposure.total_seconds(), 1),
            "provisional": self.provisional,
            "reason": self.reason,
        }


def _clamp(save_steps: int) -> int:
    """Hold the interval inside the bounds that make it worth having."""
    return max(MIN_SAVE_STEPS, min(save_steps, MAX_SAVE_STEPS))


def derive(
    step_time: timedelta,
    *,
    budget: timedelta = MAX_UNWRITTEN,
    provisional: bool = False,
) -> Policy:
    """Return the largest interval that keeps unwritten work inside `budget`."""
    seconds = step_time.total_seconds()
    if seconds <= 0:
        msg = f"a step cannot take {seconds} seconds; the measurement is wrong"
        raise CheckpointError(msg)

    #: Floor, not round: rounding up would exceed the budget by design.
    ideal = int(budget.total_seconds() // seconds)
    save_steps = _clamp(ideal)
    exposure = timedelta(seconds=save_steps * seconds)

    if save_steps < ideal:
        reason = f"clamped to the {MAX_SAVE_STEPS} step ceiling"
    elif save_steps > ideal:
        reason = (
            f"raised to the {MIN_SAVE_STEPS} step floor: at {seconds:.1f}s per step "
            f"the budget allows only {ideal}, and checkpointing that often would "
            "cost more than the work it protects"
        )
    elif provisional:
        reason = (
            f"provisional: {seconds:.1f}s per step assumed, not measured. "
            f"Recomputed after {RECOMPUTE_AFTER} steps"
        )
    else:
        reason = (
            f"{save_steps} steps at {seconds:.1f}s is {exposure.total_seconds() / 60:.0f} "
            f"minutes of exposure, inside the {budget.total_seconds() / 60:.0f} minute budget"
        )

    return Policy(
        save_steps=save_steps,
        step_time=step_time,
        exposure=exposure,
        provisional=provisional,
        reason=reason,
    )


def initial(assumed_step: timedelta = ASSUMED_STEP) -> Policy:
    """The interval to submit with, before anything has been observed."""
    return derive(assumed_step, provisional=True)


def recompute(observation: Observation, current: Policy) -> Policy | None:
    """Return a revised interval once the measurement is trustworthy.

    Returns `None` while the observation is too short to trust, and when the
    revision would not change the interval -- a driver that rewrote its
    configuration on every poll would produce a ledger of noise.
    """
    if not observation.sufficient:
        return None

    revised = derive(observation.step_time)
    if revised.save_steps == current.save_steps and not current.provisional:
        return None
    return revised


def exposure_of(save_steps: int, step_time: timedelta) -> timedelta:
    """How much work an interval leaves unwritten at a given step time."""
    return timedelta(seconds=save_steps * step_time.total_seconds())


def check(save_steps: int, step_time: timedelta, *, budget: timedelta = MAX_UNWRITTEN) -> None:
    """Raise if an authored interval would leave too much work unwritten.

    A specification may name `save_steps` itself. It is still checked: an
    interval that loses more than the budget is refused at submission rather
    than discovered after a node failure.
    """
    exposure = exposure_of(save_steps, step_time)
    if exposure > budget:
        msg = (
            f"save_steps={save_steps} at {step_time.total_seconds():.1f}s per step leaves "
            f"{exposure.total_seconds() / 60:.0f} minutes of work unwritten, and the budget "
            f"is {budget.total_seconds() / 60:.0f}. A node failure would lose all of it."
        )
        raise CheckpointError(msg)
