"""Server-sent events carrying state deltas, not list refreshes.

The requirement is one line -- "Server-sent events carry state deltas, not full
list refreshes" -- and the reason is the run board. Fifty-six adapter runs,
each emitting a step event every few seconds; a full list refresh on every one
is fifty-six run records serialised per event, which is megabytes a minute per
connected console and a UI that re-renders every row to change one.

So an event says what changed about one subject. A client applies it to the
state it already holds.

That makes two things load-bearing.

**Every event carries a monotonic sequence.** A client that reconnects sends
`Last-Event-ID` and gets what it missed. Without the sequence, a reconnect can
only be answered by a full refresh, which is the thing this exists to avoid --
and worse, the client cannot tell whether it missed anything, so it would have
to refresh on every reconnect.

**A gap is stated rather than papered over.** If a client asks for events after
a sequence the buffer no longer holds, it is told to resynchronise. Silently
sending from the oldest available event would leave the client's state
permanently wrong in a way nothing detects.

Events are scoped to a site, like everything else: a stream is opened under a
request context and carries only that site's subjects.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

#: How many events to keep for reconnecting clients. Roughly a minute of the
#: fifty-six element array at full rate, which is longer than a page reload
#: and far shorter than a client could usefully catch up on.
BUFFER = 2_048

#: SSE reconnection hint, milliseconds. Long enough not to hammer the edge when
#: it restarts; short enough that a run board is not visibly stale.
RETRY_MS: Final = 3_000


class EventKind(StrEnum):
    """What changed. One per thing a console renders."""

    #: A run moved between the states of SAD 6.1.
    RUN_STATE = "run.state"
    #: Training progress: step, loss, checkpoint. From HAMARR's fold.
    RUN_PROGRESS = "run.progress"
    #: One array element changed.
    ARRAY_ELEMENT = "array.element"
    #: A gate result was recorded.
    GATE_RESULT = "gate.result"
    #: Something entered or left the approval queue.
    APPROVAL_QUEUE = "approval.queue"
    #: A site's capacity or federation link changed.
    SITE_STATUS = "site.status"


class StreamError(Exception):
    """Raised when a stream cannot be served from where a client asked."""


class ResynchroniseRequiredError(StreamError):
    """Raised when a client asks for events the buffer no longer holds.

    Told rather than silently served from the oldest event. A client resuming
    from a gap it does not know about holds state that is wrong for as long as
    the page is open, and nothing detects it.
    """

    def __init__(self, asked_for: int, oldest: int) -> None:
        """Name the gap."""
        self.asked_for = asked_for
        self.oldest = oldest
        super().__init__(
            f"events after {asked_for} are no longer buffered; the oldest held is "
            f"{oldest}. Re-read the collection and reconnect without Last-Event-ID. "
            "Serving from the oldest available event would leave your state "
            "permanently wrong with nothing to detect it."
        )


@dataclass(frozen=True, slots=True)
class Delta:
    """One change to one subject. Never a collection.

    `changed` holds only the fields that moved. A client merges it into what it
    holds; it is not a partial representation of the whole resource, and a
    consumer that treats it as one will delete fields it was not told about.
    """

    seq: int
    kind: EventKind
    site_id: str
    subject_id: UUID
    at: datetime
    changed: Mapping[str, Any] = field(default_factory=dict)
    #: The run this concerns, where the subject is not itself a run.
    run_id: UUID | None = None

    def __post_init__(self) -> None:
        """Refuse an event that says nothing or cannot be ordered."""
        if self.seq < 1:
            msg = f"event sequences start at 1, not {self.seq}"
            raise StreamError(msg)
        if not self.changed:
            msg = (
                f"a {self.kind} event for {self.subject_id} lists no changed fields. "
                "An event that carries no delta is a refresh instruction, and this "
                "stream carries deltas."
            )
            raise StreamError(msg)
        if self.at.tzinfo is None:
            msg = "event timestamps carry an explicit offset (SAD 11E.2)"
            raise StreamError(msg)

    def as_payload(self) -> dict[str, Any]:
        """The JSON body of one event."""
        return {
            "seq": self.seq,
            "kind": str(self.kind),
            "siteId": self.site_id,
            "subjectId": str(self.subject_id),
            "runId": str(self.run_id) if self.run_id else None,
            "at": self.at.isoformat(),
            "changed": dict(self.changed),
        }

    def render(self) -> str:
        """The wire form: an SSE frame.

        `id:` is the sequence, which is what makes `Last-Event-ID` work, and
        `event:` is the kind, so a browser can add a listener per kind rather
        than switching inside one handler.
        """
        body = json.dumps(self.as_payload(), separators=(",", ":"), ensure_ascii=False)
        return f"id: {self.seq}\nevent: {self.kind}\ndata: {body}\n\n"


def comment(text: str) -> str:
    """An SSE comment frame. Used as a keep-alive.

    A proxy that sees no bytes for its idle timeout closes the connection, and
    the client reconnects, and on a quiet system that is the only traffic there
    is. A comment is ignored by every SSE client and keeps the socket warm.
    """
    return f": {text}\n\n"


def retry(milliseconds: int = RETRY_MS) -> str:
    """The reconnection hint frame, sent once when a stream opens."""
    return f"retry: {milliseconds}\n\n"


@dataclass
class EventStream:
    """A per-site ring of recent deltas, and the sequence that orders them."""

    site_id: str
    capacity: int = BUFFER
    _events: list[Delta] = field(default_factory=list, repr=False)
    _next_seq: int = 1
    #: Live subscribers. A queue each, so one slow console cannot hold up the
    #: others and cannot hold up the publisher.
    _subscribers: set[asyncio.Queue[Delta]] = field(default_factory=set, repr=False)

    def publish(
        self,
        kind: EventKind,
        *,
        subject_id: UUID,
        at: datetime,
        changed: Mapping[str, Any],
        run_id: UUID | None = None,
    ) -> Delta:
        """Record one change and return the event that describes it."""
        delta = Delta(
            seq=self._next_seq,
            kind=kind,
            site_id=self.site_id,
            subject_id=subject_id,
            at=at,
            changed=dict(changed),
            run_id=run_id,
        )
        self._next_seq += 1
        self._events.append(delta)
        if len(self._events) > self.capacity:
            del self._events[: len(self._events) - self.capacity]
        for queue in self._subscribers:
            # `put_nowait` on a bounded queue, and a full queue drops the
            # event for that subscriber alone. The alternative -- awaiting a
            # slow consumer -- would let one wedged console stall every other
            # console and the request that published. A dropped event is
            # recoverable: the subscriber's next `Last-Event-ID` reconnect
            # asks for what it missed, and is told to resynchronise if the
            # buffer has moved past it.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(delta)
        return delta

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Delta]]:
        """Register a live subscriber for the duration of a connection.

        The board needs this and the buffer cannot provide it: `frames()`
        answers "what did I miss", and a client that only ever asked that
        would have to ask repeatedly, which is the polling this stream exists
        to avoid (AC-U4).
        """
        queue: asyncio.Queue[Delta] = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscribers(self) -> int:
        """How many live subscribers this stream has."""
        return len(self._subscribers)

    @property
    def latest_seq(self) -> int:
        """The sequence of the most recent event, or 0 if none."""
        return self._events[-1].seq if self._events else 0

    @property
    def oldest_seq(self) -> int:
        """The sequence of the oldest buffered event, or 0 if none."""
        return self._events[0].seq if self._events else 0

    def since(self, last_event_id: int | None) -> tuple[Delta, ...]:
        """Events after `last_event_id`, or everything buffered from a fresh start.

        Raises when the client asks for a point the buffer has dropped, rather
        than serving what happens to remain.
        """
        if last_event_id is None:
            return tuple(self._events)
        if last_event_id < 0:
            msg = f"Last-Event-ID is a sequence; {last_event_id} is not one"
            raise StreamError(msg)
        if self._events and last_event_id < self.oldest_seq - 1:
            raise ResynchroniseRequiredError(last_event_id, self.oldest_seq)
        return tuple(item for item in self._events if item.seq > last_event_id)

    def frames(self, last_event_id: int | None = None) -> Iterator[str]:
        """The SSE frames to send a client connecting from `last_event_id`."""
        yield retry()
        for delta in self.since(last_event_id):
            yield delta.render()

    def __len__(self) -> int:
        """How many events are buffered."""
        return len(self._events)


def parse_last_event_id(header: str | None) -> int | None:
    """Read the `Last-Event-ID` header, refusing anything that is not one."""
    if header is None or header == "":
        return None
    try:
        return int(header)
    except ValueError as error:
        msg = f"Last-Event-ID must be an event sequence; got {header!r}"
        raise StreamError(msg) from error


def deltas_between(
    before: Mapping[str, Any], after: Mapping[str, Any], *, fields: Iterable[str] | None = None
) -> dict[str, Any]:
    """The fields that actually changed between two states.

    Used so a publisher cannot accidentally send a whole record: it computes
    the difference and sends that, and if nothing differs there is no event to
    send rather than an event that says everything is as it was.
    """
    keys = tuple(fields) if fields is not None else tuple(sorted({*before, *after}))
    return {key: after[key] for key in keys if key in after and before.get(key) != after[key]}


async def live_frames(
    stream: EventStream,
    *,
    since: int | None,
    keepalive_seconds: float = 15.0,
    only_run: UUID | None = None,
    disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    """Backlog, then live events, until the client goes away.

    Two halves, and both are necessary. The backlog answers "what did I miss
    while I was reconnecting"; the live half is what makes AC-N3's five second
    budget achievable without polling, since a state change is pushed the
    moment it is published rather than found by the next request.

    The keep-alive is a comment frame. A proxy that sees no bytes for its idle
    timeout closes the connection, and on a quiet estate there may be no events
    for minutes; without this the console would reconnect continuously and look
    exactly like the polling it is not doing.

    `disconnected` is checked rather than waiting for the generator to be
    finalised. A subscriber is removed by the `finally` of `subscribe()`, and
    that runs when this generator *returns*; abandoning it and leaving the
    cleanup to garbage collection means a queue per departed console, held for
    as long as the collector takes to notice. Returning on an observed
    disconnect makes the removal happen at the moment the client leaves.
    """
    async with stream.subscribe() as queue:
        for frame in stream.frames(since):
            yield frame
        while True:
            if disconnected is not None and await disconnected():
                return
            try:
                delta = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
            except TimeoutError:
                yield comment("keep-alive")
                continue
            if only_run is not None and delta.run_id != only_run and delta.subject_id != only_run:
                continue
            yield delta.render()
