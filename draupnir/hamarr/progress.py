"""Structured progress: what the UI is allowed to know about a running job.

The requirement is that "progress parsing produces structured events, never
regex-scraped strings in the UI layer". Two halves of that, in two places.

The regex belongs to the driver. A `TrainDriver.parse_progress` turns one line
of LLaMA-Factory output into a `ProgressEvent`, and the pattern that does it
lives with the tool whose output it matches -- so an upgrade that changes the
log format is a plug-in version bump, not a change to the control plane.

This module is the other half: it folds those events into a `Progress` record
with numbers in it. The API serves that record. Nothing downstream ever sees a
line of executor output, so no template, no component and no dashboard can
come to depend on the wording of a log message.

`Progress` is derived, never stored. Fold the events again and you get the
same record, which is the same property the run projection has (SAD 7.1).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from draupnir.interfaces.types import ProgressEvent, ProgressKind

#: How many recent losses to keep. Enough to draw a sparkline and to notice a
#: divergence; not so many that a week long run accumulates a log by stealth.
LOSS_WINDOW = 128


class ProgressError(Exception):
    """Raised when an event cannot be folded into a progress record."""


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """One checkpoint the executor reported writing."""

    step: int
    path: str | None = None


@dataclass(frozen=True, slots=True)
class Progress:
    """Where a run has got to. Every field is a number, a name or a time.

    Deliberately contains no free text from the executor beyond `warnings`,
    which are quoted verbatim because a warning's whole value is its wording,
    and are marked as such so that no consumer mistakes one for a field.
    """

    step: int = 0
    total: int | None = None
    loss: float | None = None
    losses: tuple[float, ...] = ()
    metrics: tuple[tuple[str, float], ...] = ()
    checkpoints: tuple[Checkpoint, ...] = ()
    warnings: tuple[str, ...] = ()
    #: Wall clock at the first and most recent observed step.
    started_at: datetime | None = None
    observed_at: datetime | None = None
    #: Steps counted since `started_at`, for the step time measurement.
    observed_steps: int = 0

    @property
    def fraction(self) -> float | None:
        """How far through, between 0 and 1, when the total is known."""
        if not self.total:
            return None
        return min(self.step / self.total, 1.0)

    @property
    def elapsed(self) -> timedelta:
        """Wall clock across the observed steps."""
        if self.started_at is None or self.observed_at is None:
            return timedelta(0)
        return self.observed_at - self.started_at

    @property
    def step_time(self) -> timedelta | None:
        """Mean time per observed step, or `None` before two steps.

        Divided by the intervals between observations, not by the count of
        them: `n` steps stamped as they finish span `n - 1` gaps, and dividing
        by `n` reports every job as faster than it is.
        """
        if self.observed_steps < 2:
            return None
        return self.elapsed / (self.observed_steps - 1)

    @property
    def eta(self) -> timedelta | None:
        """How long the rest is expected to take."""
        step_time = self.step_time
        if step_time is None or not self.total:
            return None
        return step_time * max(self.total - self.step, 0)

    @property
    def last_checkpoint(self) -> Checkpoint | None:
        """The most recent checkpoint written."""
        return self.checkpoints[-1] if self.checkpoints else None

    @property
    def unwritten_steps(self) -> int:
        """Steps computed since the last checkpoint. Lost if the node dies."""
        last = self.last_checkpoint
        return self.step - (last.step if last else 0)

    @property
    def unwritten(self) -> timedelta | None:
        """How much work would be lost right now, in wall clock time."""
        step_time = self.step_time
        if step_time is None:
            return None
        return step_time * self.unwritten_steps

    @property
    def as_payload(self) -> dict[str, object]:
        """The shape the API serves. Numbers, names and ISO 8601 timestamps."""
        step_time = self.step_time
        eta = self.eta
        unwritten = self.unwritten
        return {
            "step": self.step,
            "total": self.total,
            "fraction": self.fraction,
            "loss": self.loss,
            "losses": list(self.losses),
            "metrics": dict(self.metrics),
            "checkpoints": [{"step": item.step, "path": item.path} for item in self.checkpoints],
            "warnings": list(self.warnings),
            "startedAt": self.started_at.isoformat() if self.started_at else None,
            "observedAt": self.observed_at.isoformat() if self.observed_at else None,
            "stepTimeSeconds": step_time.total_seconds() if step_time else None,
            "etaSeconds": eta.total_seconds() if eta else None,
            "unwrittenSteps": self.unwritten_steps,
            "unwrittenSeconds": unwritten.total_seconds() if unwritten else None,
        }


def advance(progress: Progress, event: ProgressEvent, *, at: datetime) -> Progress:
    """Fold one event into a progress record.

    `at` is supplied rather than read from the clock. `parse_progress` is a
    pure translation and carries no timestamp (SAD 8.2), so replaying a
    captured log with its captured times reproduces the record exactly.
    """
    if at.tzinfo is None:
        msg = "progress timestamps carry an explicit offset (SAD 11E.2)"
        raise ProgressError(msg)

    match event.kind:
        case ProgressKind.STEP:
            if event.step is None:
                msg = "a step event without a step number says nothing"
                raise ProgressError(msg)
            return replace(
                progress,
                step=max(event.step, progress.step),
                total=event.total if event.total is not None else progress.total,
                started_at=progress.started_at or at,
                observed_at=at,
                observed_steps=progress.observed_steps + 1,
            )

        case ProgressKind.LOSS:
            if event.value is None:
                return progress
            return replace(
                progress,
                loss=event.value,
                losses=(*progress.losses, event.value)[-LOSS_WINDOW:],
                step=max(event.step or 0, progress.step),
                observed_at=at,
            )

        case ProgressKind.CHECKPOINT:
            step = event.step if event.step is not None else progress.step
            return replace(
                progress,
                checkpoints=(*progress.checkpoints, Checkpoint(step=step, path=event.message)),
                observed_at=at,
            )

        case ProgressKind.METRIC:
            if event.message is None or event.value is None:
                return progress
            others = tuple(item for item in progress.metrics if item[0] != event.message)
            return replace(
                progress,
                metrics=tuple(sorted((*others, (event.message, event.value)))),
                observed_at=at,
            )

        case ProgressKind.WARNING:
            if event.message is None:
                return progress
            return replace(
                progress,
                warnings=(*progress.warnings, event.message),
                observed_at=at,
            )


def fold(
    events: Iterable[tuple[ProgressEvent, datetime]], *, initial: Progress | None = None
) -> Progress:
    """Fold a sequence of stamped events into one record."""
    progress = initial or Progress()
    for event, at in events:
        progress = advance(progress, event, at=at)
    return progress


@dataclass(frozen=True, slots=True)
class Stream:
    """A running fold, for a poller that receives events as they arrive."""

    progress: Progress = field(default_factory=Progress)

    def consume(self, event: ProgressEvent, *, at: datetime) -> Stream:
        """Return the stream advanced by one event."""
        return Stream(progress=advance(self.progress, event, at=at))

    def consume_all(self, events: Sequence[tuple[ProgressEvent, datetime]]) -> Stream:
        """Return the stream advanced by several events."""
        return Stream(progress=fold(events, initial=self.progress))
