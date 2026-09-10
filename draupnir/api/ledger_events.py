"""Fan the ledger's notifications into this process's event stream. RF-15.

The stream was fed by one publisher in one process, so a console connected to
API process A never saw anything published in process B -- and the *worker*,
which performs every state transition, every gate decision, every cancellation
and every retry, is a separate process whose changes reached no stream at all.

Every one of those changes is an entry in `ledger_entry`. Migration 0004 makes
the append notify; this listens and turns each notification into a delta.

**The translation is here and not in SQL.** What kind of event a transition is,
and what a console should merge, is application vocabulary: SAD 6.1's states,
the run board's fields. A trigger that knew them would be a second place they
are written down, and the one that drifts is always the one in the database.

**Sequence numbers are the ledger's.** That is the point of doing it this way:
`id: 41` means entry 41 of this site's chain in every process, so a client
reconnecting to a different one with `Last-Event-ID: 41` gets what it missed
rather than a different forty-one events. A per-process counter produced two
meanings for one number and told nobody.

**A listener that dies takes the stream down with it, so it says so.** The
console degrades to whatever it last held, which looks like a system that has
stopped. Losing the connection is logged at error level and the task retries
with a delay -- reconnecting immediately in a loop against a database that is
down is how a control plane turns an outage into two.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from draupnir.api.events import EventKind, EventStream, StreamError

logger = structlog.get_logger(__name__)

#: The channel migration 0004 notifies on.
CHANNEL = "draupnir_ledger"

#: How long to wait before reconnecting a listener whose connection dropped.
#: Long enough that a database restart is one reconnection rather than a
#: thousand; short enough that a board is stale for seconds and not minutes.
RECONNECT_SECONDS = 2.0

#: How a subject type maps onto what a console renders. The stream's kinds are
#: what a browser adds a listener for, so this is the join between SAD 7.1's
#: entities and the screens in the UX specification.
_KINDS: Mapping[str, EventKind] = {
    "run": EventKind.RUN_STATE,
    "corpus": EventKind.SITE_STATUS,
    "array": EventKind.ARRAY_ELEMENT,
    "site": EventKind.SITE_STATUS,
    "baseline": EventKind.GATE_RESULT,
    "source": EventKind.SITE_STATUS,
}

#: Transitions that are a decision about a gate rather than a move through the
#: lifecycle. A console renders them on the approval screen rather than on the
#: board, so they carry their own kind.
_GATE_TRANSITIONS = frozenset({"AWAITING_APPROVAL->RELEASED", "AWAITING_APPROVAL->QUARANTINED"})


@dataclass(frozen=True, slots=True)
class Notification:
    """One ledger append, as the trigger describes it."""

    site_id: str
    seq: int
    subject_type: str
    subject_id: str
    transition: str
    actor: str
    at: datetime

    @classmethod
    def from_json(cls, body: str) -> Notification:
        """Parse a notification payload, or raise saying what was wrong."""
        try:
            found = json.loads(body)
        except json.JSONDecodeError as error:
            msg = f"a ledger notification was not JSON: {body[:120]!r}"
            raise StreamError(msg) from error

        try:
            return cls(
                site_id=str(found["siteId"]),
                seq=int(found["seq"]),
                subject_type=str(found["subjectType"]),
                subject_id=str(found["subjectId"]),
                transition=str(found["transition"]),
                actor=str(found.get("actor") or ""),
                at=_moment(found.get("at")),
            )
        except (KeyError, TypeError, ValueError) as error:
            msg = f"a ledger notification is missing a field it needs: {found!r}"
            raise StreamError(msg) from error


def _moment(raw: Any) -> datetime:
    """The instant an entry was appended, always offset-aware (SAD 11E.2)."""
    try:
        found = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return datetime.now(UTC)
    return found if found.tzinfo else found.replace(tzinfo=UTC)


def kind_of(notification: Notification) -> EventKind:
    """What sort of change this is, in the vocabulary a console listens for."""
    if notification.transition in _GATE_TRANSITIONS:
        return EventKind.GATE_RESULT
    return _KINDS.get(notification.subject_type, EventKind.SITE_STATUS)


def changed_by(notification: Notification) -> dict[str, Any]:
    """What moved, as a console should merge it.

    A delta and never a representation: a consumer merges these fields into
    what it holds, and one that treated the event as the whole resource would
    delete every field it was not told about.

    A run transition carries its target state, which is in the transition
    string -- `QUEUED->TRAINING` -- so the common case needs no second query.
    Everything else carries the transition itself, which is what a console
    keys its rendering on and what an operator reading the audit view sees.
    """
    if notification.subject_type == "run" and "->" in notification.transition:
        _before, _, after = notification.transition.partition("->")
        if after:
            return {"state": after, "transition": notification.transition}

    return {"transition": notification.transition, "actor": notification.actor}


def run_of(notification: Notification) -> UUID | None:
    """The run this concerns, where the subject is one.

    `None` for a corpus, an array or the site itself. The per-run stream
    filters on it, and a subject that is not a run must not appear on one.
    """
    if notification.subject_type != "run":
        return None
    try:
        return UUID(notification.subject_id)
    except ValueError:
        return None


def subject_of(notification: Notification) -> UUID:
    """The identifier the delta names.

    `Delta.subject_id` is a UUID, and a corpus is `GBR` while an array is
    `cim-56-adapters`. Those are derived here as UUIDv5 over the subject's
    name, so a console can key on a stable identifier and the same corpus
    always produces the same one -- rather than the alternative, which is
    widening the delta's type and making every consumer handle two shapes.
    """
    import uuid

    try:
        return UUID(notification.subject_id)
    except ValueError:
        return uuid.uuid5(
            uuid.NAMESPACE_URL, f"draupnir:{notification.subject_type}:{notification.subject_id}"
        )


def publish(notification: Notification, stream: EventStream) -> None:
    """Turn one notification into one delta on this process's stream.

    The ledger's sequence, so `Last-Event-ID` means the same thing in every
    process. An entry that produces no changed fields is refused by `Delta`
    itself and skipped here rather than dropping the whole connection: the
    refusal exists to stop a publisher sending a refresh instruction, and one
    unrecognised entry must not stop a board updating.
    """
    try:
        stream.publish(
            kind_of(notification),
            subject_id=subject_of(notification),
            at=notification.at,
            changed=changed_by(notification),
            run_id=run_of(notification),
            seq=notification.seq,
        )
    except StreamError as refused:
        logger.warning(
            "events.notification.skipped",
            siteId=notification.site_id,
            seq=notification.seq,
            reason=str(refused),
        )


@dataclass
class LedgerListener:
    """One `LISTEN` per API process, fanned into the per-site streams.

    Started by the application's lifespan and cancelled when it stops. Holds
    its own connection rather than borrowing one from the pool: a listening
    connection is occupied for the life of the process, and returning one to a
    pool that then hands it to a query is how a `LISTEN` quietly stops.
    """

    dsn: str
    #: How this process finds the stream for a site.
    stream_for: Callable[[str], EventStream]
    #: Set once the listener is actually listening, so a test can wait for it
    #: rather than sleeping and hoping.
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task[None] | None = field(default=None, repr=False)

    def start(self) -> None:
        """Begin listening, in the background."""
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="draupnir-ledger-listener")

    async def stop(self) -> None:
        """Stop listening and wait for the task to finish."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self.ready.clear()

    async def _run(self) -> None:
        """Listen, reconnecting on failure until cancelled."""
        while True:
            try:
                await self._listen()
            except asyncio.CancelledError:
                raise
            except Exception as lost:
                # Loud. Without the stream a console shows whatever it last
                # held, which looks like a system that has stopped rather than
                # one whose notifications have.
                logger.exception("events.listener.lost", reason=str(lost))
                self.ready.clear()
                await asyncio.sleep(RECONNECT_SECONDS)

    async def _listen(self) -> None:
        """One connection's worth of listening."""
        import asyncpg

        connection = await asyncpg.connect(self.dsn)
        try:
            await connection.add_listener(CHANNEL, self._on_notify)
            self.ready.set()
            logger.info("events.listener.ready", channel=CHANNEL)
            # Nothing to do but stay connected: `add_listener` dispatches on
            # the connection's own reader task. Sleeping forever is the shape
            # asyncpg's listener API expects, and cancellation unwinds it.
            while True:
                await asyncio.sleep(3600)
        finally:
            self.ready.clear()
            with contextlib.suppress(Exception):
                await connection.remove_listener(CHANNEL, self._on_notify)
            with contextlib.suppress(Exception):
                await connection.close()

    def _on_notify(self, _connection: Any, _pid: int, _channel: str, body: str) -> None:
        """Dispatch one notification. Called on asyncpg's reader task."""
        try:
            notification = Notification.from_json(body)
        except StreamError as malformed:
            logger.warning("events.notification.malformed", reason=str(malformed))
            return
        publish(notification, self.stream_for(notification.site_id))


def dsn_of(url: str) -> str:
    """The plain PostgreSQL URL asyncpg wants, from SQLAlchemy's."""
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


__all__ = [
    "CHANNEL",
    "RECONNECT_SECONDS",
    "LedgerListener",
    "Notification",
    "changed_by",
    "dsn_of",
    "kind_of",
    "publish",
    "run_of",
    "subject_of",
]
