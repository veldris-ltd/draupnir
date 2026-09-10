"""The event stream against a live PostgreSQL. RF-15.

What is proved here can only be proved here, because the defect RF-15 names is
not visible inside one process. The stream was module state fed by a single
handler with a per-process counter, so:

* a console connected to API process A never saw a change published in B, and
  SAD 5.1 runs two to four of them;
* the *worker*, which performs every state transition, every gate decision,
  every cancellation and every retry, is a separate process whose changes
  reached no stream at all;
* `Last-Event-ID` was answered against whichever process a client happened to
  reconnect to, so `id: 41` had as many meanings as there were processes.

Every one of those changes is already an entry in `ledger_entry`. Migration
0004 makes the append notify and `draupnir.api.ledger_events` listens, so the
chain is the source. These tests exercise that path end to end: a committed
append in another operating system process, through the trigger, across the
database, into two listeners standing for two API processes, and out as frames.

The appends here commit, which is why the site is its own. A notification is
delivered on commit and discarded on rollback -- that is the property being
relied on, so the `owner` fixture's rolled-back transaction cannot be used --
and `ledger_entry` refuses DELETE and TRUNCATE by design (SAD 11C), so what is
appended stays. A dedicated site keeps it out of the way of every test that
counts a chain's length.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from draupnir.api import ledger_events
from draupnir.api.events import EventKind, EventStream, ResynchroniseRequiredError, live_frames

pytestmark = pytest.mark.integration

#: Its own site, because these appends commit and cannot be removed.
SITE = "sindri-events"

#: AC-N3's budget: a state change reaches a console within five seconds. It is
#: asserted once, on the delivery itself, and measured from the moment the
#: appending process has committed -- so what is timed is the notification path
#: and not the cost of starting a Python interpreter.
BUDGET_SECONDS = 5.0

#: The ceiling that turns a hang into a failure, which is a different thing and
#: must not be confused with the budget above. These tests share a machine with
#: Docker, and a container under load can make a five second wait fail for
#: reasons that have nothing to do with the stream; a test that reports the
#: notification path as broken when the machine was merely busy is worse than
#: no test, because it teaches people to re-run it.
PATIENCE_SECONDS = 30.0

#: How long to wait before concluding that nothing is coming. Short, because
#: every use of it is paired with a positive assertion that the listener was
#: receiving -- otherwise the silence proves nothing.
SILENCE_SECONDS = 2.0

#: Appending from another operating system process, which is the claim. A
#: separate connection would exercise the trigger and the LISTEN, but the thing
#: RF-15 says was broken is that the worker is a different program, so the test
#: runs a different program.
APPEND_SCRIPT = """
import json, sys, uuid
from datetime import UTC, datetime
from sqlalchemy import create_engine, text
from draupnir.core.domain.ledger import GENESIS_HASH, compute_entry_hash

url, site, subject_type, subject_id, transition, seq = sys.argv[1:7]
engine = create_engine(url, future=True)
with engine.begin() as connection:
    previous = connection.execute(
        text("SELECT entry_hash FROM ledger_entry WHERE site_id = :s ORDER BY seq DESC LIMIT 1"),
        {"s": site},
    ).scalar()
    prev_hash = previous or GENESIS_HASH
    payload = {"from": "another process", "subject": subject_id, "seq": int(seq)}
    connection.execute(
        text(
            "INSERT INTO ledger_entry"
            " (id, site_id, seq, prev_hash, entry_hash, ts, actor,"
            "  subject_type, subject_id, transition, payload)"
            " VALUES (:id, :site, :seq, :prev, :hash, :ts, :actor,"
            "  :subject_type, :subject_id, :transition, CAST(:payload AS jsonb))"
        ),
        {
            "id": str(uuid.uuid4()),
            "site": site,
            "seq": int(seq),
            "prev": prev_hash,
            "hash": compute_entry_hash(prev_hash, payload),
            "ts": datetime.now(UTC),
            "actor": "worker:alviss",
            "subject_type": subject_type,
            "subject_id": subject_id,
            "transition": transition,
            "payload": json.dumps(payload),
        },
    )
engine.dispose()
"""


@pytest.fixture(scope="module")
def events_site(owner_engine: Engine) -> Iterator[str]:
    """A site of this file's own, registered and committed."""
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO site (id, name, location, timezone, control_plane_uri, anchor_state)"
                " VALUES (:id, :id, 'Nuneaton', 'Europe/London',"
                " 'https://alviss.example.internal', 'ANCHORED') ON CONFLICT DO NOTHING"
            ),
            {"id": SITE},
        )
    yield SITE


@pytest.fixture
def next_seq(owner_engine: Engine, events_site: str) -> Any:
    """Hand out sequence numbers that continue this site's chain.

    The entries stay, so a test cannot assume it starts at one. Reading the
    head is also what the worker does.
    """
    counter = {"value": 0}

    def allocate() -> int:
        if counter["value"] == 0:
            with owner_engine.connect() as connection:
                highest = connection.execute(
                    text("SELECT COALESCE(MAX(seq), 0) FROM ledger_entry WHERE site_id = :s"),
                    {"s": events_site},
                ).scalar_one()
            counter["value"] = int(highest)
        counter["value"] += 1
        return counter["value"]

    return allocate


def append_from_another_process(url: str, *, subject_id: str, seq: int, **named: str) -> None:
    """Commit one ledger entry from a separate program, and say so if it fails.

    `check=True` would raise with the exit status and nothing else; a failing
    append inside a subprocess is otherwise diagnosed by watching a listener
    receive nothing and time out, which says only that the stream is broken.
    """
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            APPEND_SCRIPT,
            url,
            SITE,
            named.get("subject_type", "run"),
            subject_id,
            named.get("transition", "QUEUED->TRAINING"),
            str(seq),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"the appending process failed:\n{result.stdout}\n{result.stderr}")


class Streams:
    """One API process's worth of per-site streams."""

    def __init__(self) -> None:
        self.by_site: dict[str, EventStream] = {}

    def __call__(self, site_id: str) -> EventStream:
        return self.by_site.setdefault(site_id, EventStream(site_id=site_id))


async def listening(dsn: str) -> tuple[ledger_events.LedgerListener, Streams]:
    """A started listener and the streams it feeds, ready to receive."""
    streams = Streams()
    listener = ledger_events.LedgerListener(dsn=dsn, stream_for=streams)
    listener.start()
    await asyncio.wait_for(listener.ready.wait(), timeout=PATIENCE_SECONDS)
    return listener, streams


async def wait_for_delta(stream: EventStream, *, seq: int, within: float = PATIENCE_SECONDS) -> Any:
    """Wait until the stream holds the delta for this ledger sequence.

    Polled rather than subscribed because the point is what a *late* consumer
    finds buffered as well as what a connected one is pushed; the run stream
    test below takes the pushed path.
    """
    async with asyncio.timeout(within):
        while True:
            for delta in stream.since(None):
                if delta.seq == seq:
                    return delta
            await asyncio.sleep(0.02)


@pytest.fixture
def dsn(migrated: str) -> str:
    """The plain PostgreSQL URL the listener connects with."""
    return ledger_events.dsn_of(migrated)


async def test_a_transition_from_another_process_reaches_a_stream(
    dsn: str, migrated: str, events_site: str, next_seq: Any
) -> None:
    """The headline claim of RF-15, and it needs three processes to state.

    The worker performs the transition, an API process serves the console, and
    PostgreSQL is the only thing between them. Before migration 0004 nothing
    the worker did reached a stream at all: AC-U4 and AC-N3 passed in
    end-to-end only because the test submitted through the same process that
    served the stream, so the one arrangement never exercised was the one that
    runs in production.
    """
    listener, streams = await listening(dsn)
    try:
        run_id = uuid4()
        seq = next_seq()
        append_from_another_process(migrated, subject_id=str(run_id), seq=seq)

        # Timed from here: the entry is committed, so everything that follows
        # is the notification path AC-N3 puts a budget on. Starting the clock
        # before the subprocess would time a Python interpreter starting up,
        # which is an artefact of the test and not of the system.
        committed_at = time.monotonic()
        delta = await wait_for_delta(streams(events_site), seq=seq)
        delivery = time.monotonic() - committed_at

        assert delivery < BUDGET_SECONDS, (
            f"the change took {delivery:.2f}s to reach the stream; AC-N3 allows "
            f"{BUDGET_SECONDS:.0f}s"
        )
        assert delta.kind is EventKind.RUN_STATE
        assert delta.run_id == run_id
        assert delta.changed["state"] == "TRAINING"
    finally:
        await listener.stop()


async def test_two_api_processes_both_receive_the_same_change(
    dsn: str, migrated: str, events_site: str, next_seq: Any
) -> None:
    """SAD 5.1 runs two to four API processes behind the proxy.

    A console is connected to one of them and has no say in which. The stream
    was per process, so which console saw a change was decided by which worker
    happened to serve the submission -- and nothing anywhere reported that the
    others had been left behind.
    """
    first, first_streams = await listening(dsn)
    second, second_streams = await listening(dsn)
    try:
        run_id = uuid4()
        seq = next_seq()
        append_from_another_process(migrated, subject_id=str(run_id), seq=seq)

        both = await asyncio.gather(
            wait_for_delta(first_streams(events_site), seq=seq),
            wait_for_delta(second_streams(events_site), seq=seq),
        )

        assert [delta.seq for delta in both] == [seq, seq]
        assert {delta.run_id for delta in both} == {run_id}

        # And a console that saw this event on the first process, then
        # reconnected to the second -- which is what happens when the proxy
        # picks a different upstream, and the client has no say in it -- is
        # answered against the same ordinal. With a per-process counter the
        # second process held its own meaning for the number and said nothing.
        moved = next_seq()
        append_from_another_process(migrated, subject_id=str(uuid4()), seq=moved)
        await wait_for_delta(second_streams(events_site), seq=moved)

        missed = second_streams(events_site).since(seq)

        assert [delta.seq for delta in missed] == [moved], (
            "reconnecting to another process with Last-Event-ID answered with the wrong events"
        )
    finally:
        await first.stop()
        await second.stop()


async def test_the_event_id_is_the_ledger_sequence_in_every_process(
    dsn: str, migrated: str, owner_engine: Engine, events_site: str, next_seq: Any
) -> None:
    """So `Last-Event-ID` survives reconnecting to a different process.

    This is the defect that told nobody. Two processes counting independently
    both produce plausible small integers, so a client that reconnected
    elsewhere with `Last-Event-ID: 41` was answered confidently with the wrong
    events, or with none, and had no way to detect either.

    The number on the wire is read back against the chain rather than compared
    to what the test asked for, because the claim is precisely that those are
    the same number.
    """
    listener, streams = await listening(dsn)
    try:
        seq = next_seq()
        append_from_another_process(migrated, subject_id=str(uuid4()), seq=seq)
        delta = await wait_for_delta(streams(events_site), seq=seq)

        announced = int(delta.render().splitlines()[0].removeprefix("id: "))
        with owner_engine.connect() as connection:
            in_the_chain = connection.execute(
                text("SELECT transition FROM ledger_entry WHERE site_id = :s AND seq = :q"),
                {"s": events_site, "q": announced},
            ).scalar_one_or_none()

        assert in_the_chain == "QUEUED->TRAINING", (
            f"the frame announced id {announced}, which names no entry in the chain"
        )
    finally:
        await listener.stop()


async def test_a_run_stream_carries_only_that_run_and_stays_open(
    dsn: str, migrated: str, events_site: str, next_seq: Any
) -> None:
    """`streamRunEvents` took `run_id` and used it only in a log line (RF-15).

    So a console watching one run received every other run's changes and had to
    filter client side, which nothing told it to do; and it yielded the backlog
    and closed, so it was a page of history to poll rather than a stream.

    Both halves are asserted against the real notification path: the frames
    come from committed appends made by another process, not from a publisher
    called by hand.
    """
    listener, streams = await listening(dsn)
    watched = uuid4()
    other = uuid4()
    try:
        frames = live_frames(streams(events_site), since=None, only_run=watched)
        assert await anext(frames) == "retry: 3000\n\n"

        append_from_another_process(migrated, subject_id=str(other), seq=next_seq())
        append_from_another_process(migrated, subject_id=str(watched), seq=next_seq())

        # The other run's append happened first, so a stream that did not
        # filter would answer with it here.
        received = await asyncio.wait_for(anext(frames), timeout=PATIENCE_SECONDS)

        assert str(watched) in received
        assert str(other) not in received, "another run's event appeared on this run's stream"

        # And it is still open: a generator that had returned would raise
        # `StopAsyncIteration` rather than wait for the keep-alive.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(frames), timeout=0.5)

        await frames.aclose()
    finally:
        await listener.stop()


async def test_a_rolled_back_append_notifies_nobody(
    dsn: str, migrated: str, owner_engine: Engine, events_site: str, next_seq: Any
) -> None:
    """The reason the trigger is AFTER INSERT and `pg_notify` is transactional.

    A console that showed a run as TRAINING because the transaction that said
    so was rolled back would be reporting something that never happened, and
    would keep reporting it until somebody reloaded.
    """
    listener, streams = await listening(dsn)
    try:
        seq = next_seq()
        with owner_engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text(
                    "INSERT INTO ledger_entry"
                    " (id, site_id, seq, prev_hash, entry_hash, ts, actor,"
                    "  subject_type, subject_id, transition, payload)"
                    " VALUES (:id, :site, :seq, :h, :h, now(), 'operator:akuma',"
                    "  'run', :subject, 'QUEUED->TRAINING', '{}'::jsonb)"
                ),
                {
                    "id": str(uuid4()),
                    "site": events_site,
                    "seq": seq,
                    "h": "0" * 64,
                    "subject": str(uuid4()),
                },
            )
            transaction.rollback()

        with pytest.raises(TimeoutError):
            await wait_for_delta(streams(events_site), seq=seq, within=SILENCE_SECONDS)

        # And the silence was the rollback rather than a listener that was
        # never receiving: a committed append now arrives. Without this the
        # test passes just as well against no trigger at all.
        committed = next_seq()
        append_from_another_process(migrated, subject_id=str(uuid4()), seq=committed)
        assert (await wait_for_delta(streams(events_site), seq=committed)).seq == committed
    finally:
        await listener.stop()


async def test_an_entry_the_listener_cannot_translate_does_not_stop_the_stream(
    dsn: str, migrated: str, events_site: str, next_seq: Any
) -> None:
    """One unrecognised entry must not stop a board updating.

    `Delta` refuses an event with no changed fields, which is what stops a
    publisher sending a refresh instruction instead of a delta. That refusal
    has to be survivable here: a listener that let it escape would lose its
    connection and take every console's stream with it, over one entry.
    """
    listener, streams = await listening(dsn)
    try:
        # A subject type with no transition string to unpack. The listener
        # falls back to the transition itself, which is a changed field, so
        # this arrives -- the assertion that matters is the one after it.
        odd = next_seq()
        append_from_another_process(
            migrated,
            subject_id="GBR",
            seq=odd,
            subject_type="corpus",
            transition="INGESTED",
        )
        delta = await wait_for_delta(streams(events_site), seq=odd)
        assert delta.changed["transition"] == "INGESTED"

        # And the listener is still listening.
        run_id = uuid4()
        after = next_seq()
        append_from_another_process(migrated, subject_id=str(run_id), seq=after)
        following = await wait_for_delta(streams(events_site), seq=after)

        assert following.run_id == run_id
    finally:
        await listener.stop()


async def test_a_client_asking_for_a_dropped_point_is_still_told_to_resynchronise(
    dsn: str, migrated: str, events_site: str, next_seq: Any
) -> None:
    """The buffer is bounded, and a silent gap is the thing to avoid.

    Now that the sequences are the ledger's rather than a counter starting at
    one, a `Last-Event-ID` far below the buffer's oldest entry is the ordinary
    case for a console that was away -- so the refusal has to be driven by what
    the buffer holds, not by how small the number is.
    """
    listener, streams = await listening(dsn)
    try:
        stream = streams(events_site)
        stream.capacity = 2
        for _ in range(3):
            append_from_another_process(migrated, subject_id=str(uuid4()), seq=next_seq())
        oldest = await asyncio.wait_for(_oldest_of(stream, held=2), timeout=PATIENCE_SECONDS)

        # `oldest - 1` is the last point that can still be served: a client
        # holding it is asking for the oldest buffered event onwards, and
        # nothing between the two has been dropped.
        assert stream.since(oldest - 1)[0].seq == oldest

        with pytest.raises(ResynchroniseRequiredError):
            stream.since(oldest - 2)
    finally:
        await listener.stop()


async def _oldest_of(stream: EventStream, *, held: int) -> int:
    """The lowest sequence still buffered, once the buffer has filled."""
    while True:
        buffered = stream.since(None)
        if len(buffered) >= held:
            return int(buffered[0].seq)
        await asyncio.sleep(0.02)


def test_the_notification_carries_identity_and_not_the_payload(
    owner_engine: Engine, events_site: str
) -> None:
    """The notification names the entry rather than carrying it.

    `pg_notify` refuses a payload over 8000 bytes and a ledger payload has no
    bound. Sending the row would work in testing and fail on the first merge
    configuration, and a notification that is sometimes delivered is worse than
    one that never is. So this asserts what the trigger builds, by asking the
    function to build it rather than by reading its source.
    """
    with owner_engine.connect() as connection:
        body = connection.execute(
            text(
                "SELECT json_build_object("
                " 'siteId', CAST(:site AS text), 'seq', 1, 'subjectType', 'run',"
                " 'subjectId', CAST(:subject AS text),"
                " 'transition', 'QUEUED->TRAINING',"
                " 'actor', 'worker:alviss',"
                " 'at', to_char(now() AT TIME ZONE 'UTC',"
                " 'YYYY-MM-DD\"T\"HH24:MI:SS.USOF'))::text"
            ),
            {"site": events_site, "subject": str(uuid4())},
        ).scalar_one()

    notification = ledger_events.Notification.from_json(body)

    assert notification.site_id == events_site
    assert notification.at.tzinfo is not None, "SAD 11E.2: instants are offset aware"
    assert "payload" not in json.loads(body), (
        "the notification carries the payload, which pg_notify will silently refuse"
    )
    assert len(body.encode()) < 8000


def test_the_subject_of_a_named_thing_is_stable(events_site: str) -> None:
    """A corpus is `GBR` and an array is `cim-56-adapters`, not UUIDs.

    `Delta.subject_id` is a UUID, so those are derived. Derived and not
    generated: a console keys on the identifier, and one that changed per
    notification would render every corpus event as a new object.
    """
    del events_site
    body = {
        "siteId": SITE,
        "seq": 7,
        "subjectType": "corpus",
        "subjectId": "GBR",
        "transition": "INGESTED",
        "actor": "operator:akuma",
        "at": datetime.now(UTC).isoformat(),
    }
    once = ledger_events.subject_of(ledger_events.Notification.from_json(json.dumps(body)))
    again = ledger_events.subject_of(ledger_events.Notification.from_json(json.dumps(body)))

    assert once == again
    assert isinstance(once, UUID)
