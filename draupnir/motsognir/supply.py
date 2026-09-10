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

The contract
------------

`SCHEMA` names it, so a site can be told what to produce and a later revision
can add a required key without silently changing what an existing file means.

**v1 is the block `upsc` prints**, deliberately, because that is a format the
daemon already produces. NUT with `usbhid-ups` writes it; a site running NUT
needs a one-line timer and no translation layer, and a translation layer nobody
needs is a translation layer nobody notices has stopped running. `adapters`
converts the other common daemon's output for a site that has one.

Lines are `key: value`. Two keys are read and the rest ignored, because a
parser that required the whole block would break on a supply model that reports
one field differently:

| Key | Required | Meaning |
|---|---|---|
| `ups.status` | yes | NUT flags: `OL` on line, `OB` on battery, `LB` low. They combine |
| `battery.charge` | no, defaults to 100 | Percentage remaining |
| `draupnir.schema` | no | The contract version. Absent means v1, so a bare `upsc` dump is valid |

**Staleness is part of the contract and is the failure that matters.** A file
whose daemon died reads exactly like a healthy one reporting mains: `OL`, one
hundred per cent, no error. Nothing about it is malformed. A monitor without a
freshness check concludes mains for as long as the file sits there, which
includes the whole of the outage it exists for -- and the file it is reading
was last written before the power went out, which is precisely when it was
still true.

So the file carries its own recency in its modification time, and a reading
older than `MAX_AGE_SECONDS` raises rather than being believed. The estate then
has no supply signal, which is a worse position than having one and a much
better position than having a wrong one.
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

#: The status file contract. Versioned so that a later revision can require a
#: key without changing what an existing file means, and so that a site can be
#: told what to produce by name rather than by example.
SCHEMA = "draupnir/supply-status/v1"

#: The optional key naming the version. Optional because v1 *is* `upsc` output,
#: and requiring a marker would mean a site running NUT could not simply
#: redirect the command it already has.
SCHEMA_KEY = "draupnir.schema"

#: How old a reading may be before it is refused. Three minutes is four missed
#: polls at the sixty-second interval the deployment configures, which is long
#: enough not to alarm on a slow host and short enough that a dead daemon is
#: found before the next power cut rather than during it.
MAX_AGE_SECONDS = 180.0

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
    #: The status file could not be read or believed. Nothing is done to the
    #: estate -- see `signal_lost` for why not -- and an operator is told.
    SIGNAL_LOST = "SIGNAL_LOST"


class SupplyError(Exception):
    """Raised when a supply reading cannot be read or believed."""


class StaleSupplyError(SupplyError):
    """Raised when the status file stopped being written.

    Its own type because the response differs. An unreadable file is a
    configuration fault -- wrong path, wrong permissions -- and is found the
    first time the worker starts. A stale one means the daemon was running and
    is not any more, which is a fault that appears later and looks like
    nothing at all.
    """

    def __init__(self, path: Path, age_seconds: float, limit: float) -> None:
        """Name the file, its age, and what would have been acceptable."""
        self.path = path
        self.age_seconds = age_seconds
        super().__init__(
            f"the supply status at {path} was last written {age_seconds:.0f}s ago, past "
            f"the {limit:.0f}s limit. A file whose daemon died reads exactly like a "
            "healthy one reporting mains, so it is refused rather than believed: no "
            "supply signal is a worse position than a working one and a much better "
            "position than a wrong one."
        )


class SchemaError(SupplyError):
    """Raised when the file declares a contract version this cannot read."""

    def __init__(self, declared: str) -> None:
        """Name what was declared and what is understood."""
        self.declared = declared
        super().__init__(
            f"the supply status declares {declared!r} and this reads {SCHEMA!r}. A file "
            "written to a later contract may mean something different by the same key, "
            "and guessing is how a monitor concludes mains during a power cut."
        )


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

    declared = fields.get(SCHEMA_KEY, "")
    if declared and declared != SCHEMA:
        raise SchemaError(declared)

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


def read_status_file(
    path: Path, *, at: datetime, max_age_seconds: float = MAX_AGE_SECONDS
) -> Reading:
    """Read the status file the supply's daemon maintains.

    A file rather than a device, because that is the interface that exists: the
    daemon owns the USB link and publishes what it found. It also means this
    can be exercised for real without a supply, by writing the file the daemon
    would have written.

    Refuses a file older than `max_age_seconds`. See the module docstring for
    why that check is not optional: a stale file is well formed, reports mains,
    and is wrong in exactly the circumstance the monitor exists for.
    """
    try:
        text = path.read_text(encoding="utf-8")
        modified = path.stat().st_mtime
    except OSError as error:
        msg = (
            f"the supply status at {path} cannot be read: {error}. A monitor that "
            "assumed mains when it could not tell would be silent through the one "
            "event it exists for."
        )
        raise SupplyError(msg) from error

    age = at.timestamp() - modified
    if age > max_age_seconds:
        raise StaleSupplyError(path, age, max_age_seconds)

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


def signal_lost(error: SupplyError) -> Action:
    """The supply cannot be read. Say so, and change nothing.

    **Deliberately not a drain.** The tempting response is to stop dispatching
    until the signal returns, on the grounds that running blind during a power
    cut is what this module exists to prevent. It is the wrong response: a
    daemon restart, a slow host or a rotated file would then stop the estate,
    the drain would outlast the cause, and within a month somebody would set
    the path to empty to make it stop.

    So an unreadable supply is reported and not acted on. The estate runs as it
    would with no supply fitted, which is the state it was in before one was --
    and the operator is told, once per tick, that the protection they think
    they have is not there.
    """
    return Action(
        ActionKind.SIGNAL_LOST,
        f"the supply signal cannot be read: {error}. Dispatch continues, because a "
        "drain that outlasts a daemon restart is a drain somebody disables.",
    )


def describe(actions: Iterable[Action]) -> str:
    """Render actions for an operator's log line."""
    return "; ".join(
        f"{action.kind}{f' {action.job_id}' if action.job_id else ''}: {action.reason}"
        for action in actions
    )


__all__ = [
    "LOW_BATTERY_PERCENT",
    "MAX_AGE_SECONDS",
    "SCHEMA",
    "SCHEMA_KEY",
    "Action",
    "ActionKind",
    "Reading",
    "SchemaError",
    "StaleSupplyError",
    "SupplyError",
    "SupplyMonitor",
    "SupplyState",
    "describe",
    "parse_status",
    "read_status_file",
    "signal_lost",
]
