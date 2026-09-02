"""The conventions of SAD 11E.2, which are not optional.

AC-B1 idempotency, AC-B2 problem documents, AC-B3 cursor pagination, AC-B4
conditional writes, AC-B8 UUIDv7, AC-B9 202 for long operations.

These are unit tests over the mechanisms. The tests that exercise them through
a real request are in `tests/contract/test_api_surface.py`; both matter,
because a convention that works in isolation and is not wired to a route is a
convention the API does not have.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from draupnir.api import concurrency, events, idempotency, pagination, telemetry
from draupnir.api.concurrency import (
    PreconditionFailedError,
    PreconditionRequiredError,
    etag,
)
from draupnir.api.events import EventKind, EventStream, ResynchroniseRequiredError, StreamError
from draupnir.api.idempotency import (
    IdempotencyStore,
    InFlightError,
    KeyReusedError,
)
from draupnir.api.pagination import Cursor, PaginationError, paginate
from draupnir.core.domain.identifiers import new_id

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# AC-B1: idempotency
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> IdempotencyStore:
    return IdempotencyStore()


def test_a_replay_returns_the_original_result_and_does_not_act_twice(
    store: IdempotencyStore,
) -> None:
    """AC-B1, stated directly."""
    payload = {"specification": {"kind": "AdapterRun"}}

    assert store.reserve("k1", site_id="sindri", actor="a", payload=payload, now=AT) is None
    store.complete("k1", site_id="sindri", actor="a", status=202, body={"runId": "r1"})

    replay = store.reserve("k1", site_id="sindri", actor="a", payload=payload, now=AT)

    assert replay is not None
    assert replay.body == {"runId": "r1"}
    assert replay.status == 202


def test_a_replay_while_the_first_request_is_running_is_refused(
    store: IdempotencyStore,
) -> None:
    """Two clicks on submit. Returning "not found" would let the second act."""
    store.reserve("k1", site_id="sindri", actor="a", payload={"x": 1}, now=AT)

    with pytest.raises(InFlightError, match="already in progress"):
        store.reserve("k1", site_id="sindri", actor="a", payload={"x": 1}, now=AT)


def test_the_same_key_with_a_different_body_is_refused(store: IdempotencyStore) -> None:
    """Replaying the first response would say a request they did not make succeeded."""
    store.reserve("k1", site_id="sindri", actor="a", payload={"x": 1}, now=AT)
    store.complete("k1", site_id="sindri", actor="a", status=202, body={"runId": "r1"})

    with pytest.raises(KeyReusedError, match="a different request body"):
        store.reserve("k1", site_id="sindri", actor="a", payload={"x": 2}, now=AT)


def test_keys_are_scoped_per_site_and_per_actor(store: IdempotencyStore) -> None:
    """Two operators using `retry-1` must not collide."""
    store.reserve("retry-1", site_id="sindri", actor="a", payload={"x": 1}, now=AT)

    assert store.reserve("retry-1", site_id="sindri", actor="b", payload={"x": 1}, now=AT) is None
    assert store.reserve("retry-1", site_id="brokkr", actor="a", payload={"x": 1}, now=AT) is None


def test_a_failed_request_releases_its_key(store: IdempotencyStore) -> None:
    """A request that errored has not acted, so its key must not be held."""
    store.reserve("k1", site_id="sindri", actor="a", payload={"x": 1}, now=AT)
    store.release("k1", site_id="sindri", actor="a")

    assert store.reserve("k1", site_id="sindri", actor="a", payload={"x": 1}, now=AT) is None


def test_a_key_expires_so_a_later_resubmission_is_a_new_request(
    store: IdempotencyStore,
) -> None:
    """A key held forever turns next week's resubmission into a silent no-op."""
    store.reserve("k1", site_id="sindri", actor="a", payload={"x": 1}, now=AT)
    store.complete("k1", site_id="sindri", actor="a", status=202, body={"runId": "r1"})

    later = AT + timedelta(days=2)

    assert store.reserve("k1", site_id="sindri", actor="a", payload={"x": 1}, now=later) is None


def test_completing_a_key_that_was_never_reserved_is_refused(store: IdempotencyStore) -> None:
    with pytest.raises(idempotency.IdempotencyError, match="complete follows reserve"):
        store.complete("k1", site_id="sindri", actor="a", status=200)


def test_purging_drops_only_expired_records(store: IdempotencyStore) -> None:
    store.reserve("old", site_id="sindri", actor="a", payload={}, now=AT)
    store.reserve("new", site_id="sindri", actor="a", payload={}, now=AT + timedelta(days=2))

    assert store.purge(AT + timedelta(days=2)) == 1
    assert len(store) == 1


# ---------------------------------------------------------------------------
# AC-B3: cursor pagination
# ---------------------------------------------------------------------------


def rows(count: int, *, start: int = 0) -> list[tuple[datetime, UUID]]:
    """Rows in `(created_at, id)` order."""
    return [(AT + timedelta(seconds=start + n), UUID(int=start + n)) for n in range(count)]


def test_pagination_over_an_inserted_row_skips_and_duplicates_nothing() -> None:
    """AC-B3, and the whole reason cursors exist.

    Page one is taken, a row is inserted *before* the boundary, and page two is
    taken from the cursor. With offset pagination the row at index 50 would
    have shifted to 51 and never been returned.
    """
    everything = rows(100)

    first = paginate(everything[:11], limit=10, key=lambda r: r)
    assert first.next_cursor is not None

    # A row arrives, earlier than the cursor.
    inserted = (AT + timedelta(seconds=5, microseconds=1), UUID(int=999))
    grown = sorted([*everything, inserted])

    cursor = Cursor.decode(first.next_cursor)
    remaining = [row for row in grown if (row[0], row[1]) > (cursor.created_at, cursor.id)]
    second = paginate(remaining[:11], limit=10, key=lambda r: r)

    returned = [*first.items, *second.items]
    assert len(returned) == len(set(returned)), "a row was returned twice"
    # The row that offset pagination would have skipped is still coming.
    assert everything[10] in second.items


def test_a_cursor_names_exactly_one_row() -> None:
    """UUIDv7 makes `(created_at, id)` a total order, so there are no ties."""
    encoded = Cursor(created_at=AT, id=UUID(int=7)).encode()

    decoded = Cursor.decode(encoded)

    assert (decoded.created_at, decoded.id) == (AT, UUID(int=7))


def test_a_cursor_we_did_not_issue_is_refused() -> None:
    """A silent reset to page one loops a paginating client forever."""
    with pytest.raises(PaginationError, match="not a cursor this API issued"):
        Cursor.decode("not-a-cursor")


def test_the_last_page_carries_a_null_cursor() -> None:
    page = paginate(rows(3), limit=10, key=lambda r: r)

    assert page.next_cursor is None
    assert not page.has_more


def test_a_page_size_is_clamped_and_a_nonsensical_one_is_refused() -> None:
    assert pagination.clamp(None) == pagination.DEFAULT_LIMIT
    assert pagination.clamp(10_000) == pagination.MAX_LIMIT

    with pytest.raises(PaginationError, match="at least 1"):
        pagination.clamp(0)


def test_the_cursor_predicate_is_a_row_comparison() -> None:
    """One index scan rather than an OR that PostgreSQL cannot satisfy from it."""
    fragment, params = pagination.predicate(Cursor(created_at=AT, id=UUID(int=1)))

    assert "(created_at, id) > (:cursor_created_at, :cursor_id)" in fragment
    assert set(params) == {"cursor_created_at", "cursor_id"}
    assert pagination.predicate(None) == ("", {})


# ---------------------------------------------------------------------------
# AC-B4: conditional writes
# ---------------------------------------------------------------------------


def test_a_stale_conditional_write_returns_a_precondition_failure() -> None:
    """AC-B4. Two operators, one run, and the retry must not undo the cancel."""
    state = {"id": "run-1", "state": "TRAINING"}
    read_tag = etag(state)

    moved = {"id": "run-1", "state": "CANCELLED"}

    with pytest.raises(PreconditionFailedError) as raised:
        concurrency.require("run run-1", moved, read_tag)

    assert raised.value.expected == read_tag
    assert raised.value.current == etag(moved)
    assert "refused rather than applied over somebody else's change" in str(raised.value)


def test_a_write_with_no_precondition_is_refused() -> None:
    """428, and not a permissive default.

    Optional concurrency control is control the one client that needed it did
    not use.
    """
    with pytest.raises(PreconditionRequiredError, match="carries no If-Match"):
        concurrency.require("run run-1", {"id": "run-1"}, None)


def test_a_current_tag_permits_the_write() -> None:
    state = {"id": "run-1", "state": "TRAINING"}

    assert concurrency.require("run run-1", state, etag(state)) == etag(state)


def test_the_wildcard_asserts_existence_rather_than_opting_out() -> None:
    state = {"id": "run-1"}

    assert concurrency.matches(etag(state), "*")


def test_a_list_of_tags_matches_if_any_member_does() -> None:
    """RFC 9110 allows `If-Match: "a", "b"`."""
    state = {"id": "run-1"}
    current = etag(state)

    assert concurrency.matches(current, f'"deadbeef", {current}')


def test_the_tag_changes_when_the_state_does() -> None:
    assert etag({"state": "TRAINING"}) != etag({"state": "TRAINED"})


# ---------------------------------------------------------------------------
# Server-sent events: deltas, not refreshes
# ---------------------------------------------------------------------------


def test_an_event_carries_only_what_changed() -> None:
    """The requirement: state deltas, not full list refreshes."""
    stream = EventStream(site_id="sindri")
    subject = new_id()

    delta = stream.publish(
        EventKind.RUN_STATE, subject_id=subject, at=AT, changed={"state": "TRAINING"}
    )

    assert delta.as_payload()["changed"] == {"state": "TRAINING"}
    assert "items" not in delta.as_payload()


def test_an_event_that_carries_no_delta_is_refused() -> None:
    """An event with nothing changed is a refresh instruction."""
    stream = EventStream(site_id="sindri")

    with pytest.raises(StreamError, match="this stream carries deltas"):
        stream.publish(EventKind.RUN_STATE, subject_id=new_id(), at=AT, changed={})


def test_a_reconnecting_client_receives_only_what_it_missed() -> None:
    stream = EventStream(site_id="sindri")
    for index in range(5):
        stream.publish(EventKind.RUN_PROGRESS, subject_id=new_id(), at=AT, changed={"step": index})

    missed = stream.since(3)

    assert [item.seq for item in missed] == [4, 5]


def test_a_client_asking_for_a_dropped_point_is_told_to_resynchronise() -> None:
    """A silent gap leaves the client wrong with nothing to detect it."""
    stream = EventStream(site_id="sindri", capacity=3)
    for index in range(10):
        stream.publish(EventKind.RUN_PROGRESS, subject_id=new_id(), at=AT, changed={"step": index})

    with pytest.raises(ResynchroniseRequiredError, match="no longer buffered"):
        stream.since(1)


def test_the_frame_carries_the_sequence_as_the_event_id() -> None:
    """`id:` is what makes Last-Event-ID work."""
    stream = EventStream(site_id="sindri")
    delta = stream.publish(
        EventKind.RUN_STATE, subject_id=new_id(), at=AT, changed={"state": "QUEUED"}
    )

    frame = delta.render()

    assert frame.startswith(f"id: {delta.seq}\nevent: run.state\ndata: ")
    assert frame.endswith("\n\n")


def test_a_delta_is_computed_rather_than_assembled_by_hand() -> None:
    """So a publisher cannot accidentally send a whole record."""
    before = {"state": "TRAINING", "step": 100, "name": "cim-gbr"}
    after = {"state": "TRAINED", "step": 100, "name": "cim-gbr"}

    assert events.deltas_between(before, after) == {"state": "TRAINED"}
    assert events.deltas_between(before, before) == {}


def test_an_unparseable_last_event_id_is_refused() -> None:
    with pytest.raises(StreamError, match="must be an event sequence"):
        events.parse_last_event_id("latest")

    assert events.parse_last_event_id(None) is None


# ---------------------------------------------------------------------------
# AC-B8: identifiers
# ---------------------------------------------------------------------------


def test_identifiers_are_uuid7_and_sort_by_creation_time() -> None:
    """AC-B8."""
    issued = [new_id() for _ in range(200)]

    assert all(item.version == 7 for item in issued)
    assert [str(item) for item in issued] == sorted(str(item) for item in issued)


# ---------------------------------------------------------------------------
# Logging: no secret, and always the three fields
# ---------------------------------------------------------------------------


def test_a_credential_is_redacted_by_field_name() -> None:
    line = telemetry.build_line("call", access_token="supersecret-value-here")  # noqa: S106

    assert telemetry.REDACTED in str(line.as_payload())
    assert "supersecret-value-here" not in str(line.as_payload())


def test_a_credential_is_redacted_by_shape_wherever_it_appears() -> None:
    """The token that arrived inside a message somebody was helpfully including."""
    line = telemetry.build_line("call", message="failed with hf_" + "a" * 34)

    assert "hf_" not in str(line.as_payload())


def test_a_corpus_excerpt_is_truncated_rather_than_logged() -> None:
    """A long string in a field called `text` looks exactly like a diagnostic."""
    line = telemetry.build_line("curated", text="the corpus said " * 60)

    rendered = str(line.as_payload()["text"])
    assert "truncated" in rendered
    assert len(rendered) < 400


def test_bytes_are_never_logged() -> None:
    line = telemetry.build_line("read", payload=b"\x00\x01\x02")

    assert line.as_payload()["payload"] == "<3 bytes>"


def test_a_nested_credential_is_redacted() -> None:
    line = telemetry.build_line("call", headers={"Authorization": "Bearer " + "a" * 40})

    assert "aaaa" not in str(line.as_payload())


def test_a_caller_cannot_overwrite_the_context_fields() -> None:
    """Context wins, so a field named `actor` cannot forge one."""
    from draupnir.api import context as request_context
    from draupnir.core.domain.sites import SiteScope

    built = request_context.build(scope=SiteScope(site_id="sindri"))
    token = request_context.bind(built)
    try:
        line = telemetry.build_line("call", actor="somebody-else", siteId="brokkr")
    finally:
        request_context.unbind(token)

    assert line.as_payload()["actor"] == "anonymous"
    assert line.as_payload()["siteId"] == "sindri"


def test_spans_run_from_edge_through_orchestrator_to_driver() -> None:
    """The three boundaries the requirement names."""
    tracer = telemetry.Tracer()

    with (
        tracer.span("POST /v1/runs", telemetry.EDGE),
        tracer.span("submit", telemetry.ORCHESTRATOR),
        tracer.span("render", telemetry.DRIVER),
    ):
        pass

    assert tracer.layers() == (telemetry.EDGE, telemetry.ORCHESTRATOR, telemetry.DRIVER)


def test_a_span_attribute_is_scrubbed_like_a_log_line() -> None:
    tracer = telemetry.Tracer()

    with tracer.span("submit", telemetry.EDGE, token="AKIAIOSFODNN7EXAMPLE") as span:  # noqa: S106
        span.set(note="fine")

    assert tracer.roots[0].attributes["token"] == telemetry.REDACTED
    assert tracer.roots[0].attributes["note"] == "fine"
