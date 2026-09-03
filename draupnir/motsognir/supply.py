"""Mains loss, and what DRAUPNIR does about it. SAD 11.2, last row.

"The supply signals over USB. DRAUPNIR forces an immediate checkpoint on every
running job, then drains the queue and halts cleanly at the low battery
threshold. Recovery: restore mains, resume from the forced checkpoint."

**What is fitted and what is not.** SAD 11.3 lists the mains and battery signal
as coming from the "uninterruptible supply over USB, *once fitted*". It is not
fitted, and this module does not pretend otherwise: it does not open a USB
device, and there is no code here that talks to a supply. What it does is read
the status file the supply's daemon writes -- the shape `upsc` prints and NUT's
`upsmon` maintains -- and decide what to do about it. That decision is the half
DRAUPNIR owns, and it is the half that has to be right before the hardware
arrives, because the first time it runs will be during a power cut.

**Why one checkpoint and not one per poll.** A transfer to battery forces a
checkpoint on every running job. The next poll is still on battery, and a
monitor that acted on state rather than on *transition* would force another,
and another, until the battery it is racing ran out writing checkpoints. So the
forced checkpoint is bound to the edge, and the monitor remembers which edge it
has already acted on.

**Why drain rather than kill.** A job that is already running has consumed its
allocation; killing it wastes the work and the power spent on it. A job that
has not started yet will not finish before the battery does. So the queue stops
dispatching and the running work is checkpointed, which is the ordering that
loses least.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

#: Below this, the supply cannot be relied on to outlast a checkpoint, so the
#: estate halts rather than being cut off mid-write. Twenty per cent of a
#: commissioned runtime, not of a nameplate rating: a battery at twenty per
#: cent has minutes, and a halt takes one of them.
LOW_BATTERY_PERCENT = 20.0

#: What NUT reports. `OL` on line, `OB` on battery, `LB` low battery, and they
#: combine -- a supply on battery and low reports `OB LB`.
_ON_BATTERY = "OB"
_LOW_BATTERY = "LB"


class SupplyState(StrEnum):
    """Where the estate's power is coming from."""

    MAINS = "MAINS"
    BATTERY = "BATTERY"
    LOW_BATTERY = "LOW_BATTERY"


class ActionKind(StrEnum):
    """What the monitor is telling the dispatcher to do."""

    #: Force an immediate checkpoint on one running job.
    CHECKPOINT = "CHECKPOINT"
    #: Stop dispatching queued work. Running work is untouched.
    DRAIN = "DRAIN"
    #: Stop the estate cleanly. The battery will not outlast the queue.
    HALT = "HALT"
    #: Mains restored. Dispatch again; running jobs resume from their forced
    #: checkpoint rather than from the last periodic one.
    RESUME = "RESUME"


class SupplyError(Exception):
    """Raised when a supply reading cannot be read or believed."""


@dataclass(frozen=True, slots=True)
class Reading:
    """One observation of the supply."""

    state: SupplyState
    charge_percent: float
    at: datetime
    #: What the daemon actually said, kept so an operator can see the raw line.
    raw: str = ""

    def __post_init__(self) -> None:
        """Refuse a reading nobody can act on."""
        if self.at.tzinfo is None:
            msg = "supply readings carry an explicit offset (SAD 11E.2)"
            raise SupplyError(msg)
        if not 0.0 <= self.charge_percent <= 100.0:
            msg = f"{self.charge_percent} is not a battery charge percentage"
            raise SupplyError(msg)


@dataclass(frozen=True, slots=True)
class Action:
    """One instruction, with the reason an operator will read in the log."""

    kind: ActionKind
    reason: str
    #: The scheduler job this applies to, for `CHECKPOINT`. Empty otherwise.
    job_id: str = ""

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, for the ledger and the console."""
        return {"kind": str(self.kind), "reason": self.reason, "jobId": self.job_id or None}


def parse_status(text: str, *, at: datetime) -> Reading:
    """Read a `upsc`-style status block into a `Reading`.

    The format is `key: value` per line. Two keys matter and the rest is
    ignored: a parser that required the whole block would break on a supply
    model that reports one field differently.
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()

    status = fields.get("ups.status", "")
    if not status:
        msg = "the supply status block carries no `ups.status`; nothing can be concluded from it"
        raise SupplyError(msg)

    flags = status.split()
    if _LOW_BATTERY in flags:
        state = SupplyState.LOW_BATTERY
    elif _ON_BATTERY in flags:
        state = SupplyState.BATTERY
    else:
        state = SupplyState.MAINS

    try:
        charge = float(fields.get("battery.charge", "100"))
    except ValueError as error:
        msg = f"battery.charge {fields.get('battery.charge')!r} is not a number"
        raise SupplyError(msg) from error

    return Reading(state=state, charge_percent=charge, at=at, raw=status)


def read_status_file(path: Path, *, at: datetime) -> Reading:
    """Read the status file the supply's daemon maintains.

    A file rather than a device, because that is the interface that exists: the
    daemon owns the USB link and publishes what it found. It also means this
    can be exercised for real without a supply, by writing the file the daemon
    would have written.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        msg = (
            f"the supply status at {path} cannot be read: {error}. A monitor that "
            "assumed mains when it could not tell would be silent through the one "
            "event it exists for."
        )
        raise SupplyError(msg) from error
    return parse_status(text, at=at)


@dataclass
class SupplyMonitor:
    """Decides what a change in the supply means for the estate.

    Holds one piece of state -- the last state it acted on -- because the
    forced checkpoint belongs to the transfer and not to the condition.
    """

    low_battery_percent: float = LOW_BATTERY_PERCENT
    #: `None` until the first reading. The first reading on battery is a
    #: transfer: a control plane that started up already on battery is a
    #: control plane in the middle of a power cut.
    last_state: SupplyState | None = None
    history: list[Action] = field(default_factory=list)
    #: False while draining. Private, and read through `may_dispatch()`: these
    #: are what the monitor concluded, not settings a caller adjusts.
    _dispatching: bool = True
    _halted: bool = False

    def may_dispatch(self) -> bool:
        """Whether the dispatcher may place queued work, as of the last reading.

        A method rather than an attribute because the answer changes with every
        observation. An attribute reads as a fact about the monitor, and a
        caller that cached one during a power cut would place work the battery
        cannot finish.
        """
        return self._dispatching

    def is_halted(self) -> bool:
        """Whether the estate has been told to stop, as of the last reading."""
        return self._halted

    def observe(self, reading: Reading, running: Sequence[str] = ()) -> tuple[Action, ...]:
        """Return what to do about this reading. Idempotent within a state."""
        actions: list[Action] = []
        previous = self.last_state
        self.last_state = reading.state

        if reading.state is SupplyState.MAINS:
            if previous is not None and previous is not SupplyState.MAINS:
                self._dispatching = True
                self._halted = False
                actions.append(
                    Action(
                        ActionKind.RESUME,
                        "mains restored; dispatch resumes and running jobs resume from "
                        "the forced checkpoint",
                    )
                )
            self.history.extend(actions)
            return tuple(actions)

        transferred = previous is None or previous is SupplyState.MAINS
        if transferred:
            # Every running job, once, at the edge. A job list that is empty is
            # not a problem: draining still matters, because the queue is what
            # would otherwise start work the battery cannot finish.
            actions.extend(
                Action(
                    ActionKind.CHECKPOINT,
                    f"supply transferred to battery at {reading.charge_percent:.0f} per cent",
                    job_id=job_id,
                )
                for job_id in running
            )
            self._dispatching = False
            actions.append(
                Action(
                    ActionKind.DRAIN,
                    "queued work will not finish before the battery does; dispatch stops "
                    "and running work is left to checkpoint",
                )
            )

        if self._low(reading) and not self._halted:
            self._halted = True
            self._dispatching = False
            actions.append(
                Action(
                    ActionKind.HALT,
                    f"battery at {reading.charge_percent:.0f} per cent, at or below the "
                    f"{self.low_battery_percent:.0f} per cent threshold; halting cleanly "
                    "while there is still power to halt with",
                )
            )

        self.history.extend(actions)
        return tuple(actions)

    def _low(self, reading: Reading) -> bool:
        """Whether this reading is at or below the halt threshold.

        Either signal is enough. The supply's own `LB` flag knows the battery's
        discharge curve better than a percentage does, and the percentage
        catches a supply that never raises `LB`.
        """
        return (
            reading.state is SupplyState.LOW_BATTERY
            or reading.charge_percent <= self.low_battery_percent
        )

    def checkpointed(self) -> tuple[str, ...]:
        """Every job this monitor has forced a checkpoint on."""
        return tuple(
            action.job_id for action in self.history if action.kind is ActionKind.CHECKPOINT
        )


def describe(actions: Iterable[Action]) -> str:
    """Render actions for an operator's log line."""
    return "; ".join(
        f"{action.kind}{f' {action.job_id}' if action.job_id else ''}: {action.reason}"
        for action in actions
    )


__all__ = [
    "LOW_BATTERY_PERCENT",
    "Action",
    "ActionKind",
    "Reading",
    "SupplyError",
    "SupplyMonitor",
    "SupplyState",
    "describe",
    "parse_status",
    "read_status_file",
]
