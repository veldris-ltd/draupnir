"""Idempotency keys across processes. RF-14.

`deps.STORE` was an `IdempotencyStore()` backed by a `dict`, and there was no
table. SAD 5.1 specifies two to four API processes, so a key reserved in
process A was unknown to process B: the documented behaviour -- "a second click
while the first request is still running is refused rather than acting twice" --
failed precisely under the concurrency the control exists for, and every
reservation was lost on restart.

These tests use two independent stores over one database, which is what two API
processes are. The in-memory store cannot pass any of them, and that is the
point: the three refusals are a property of the rule, and the rule has to hold
across the processes SAD 5.1 runs.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text

from draupnir.api.idempotency import TTL, InFlightError, KeyReusedError
from draupnir.api.idempotency_store import DatabaseIdempotencyStore

pytestmark = pytest.mark.integration

#: A forge of its own. The sweep test ticks a worker, and a worker tick
#: records duties to that site's chain -- at `sindri` those commits collide
#: with the fixed sequence numbers `test_repositories` and `test_projection`
#: build their chains from. A site is an installation, and a test estate is an
#: installation like any other (Decision S12).
SITE = "sindri-idempotency-test"
ACTOR = "operator@veldris.internal"
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
BODY = {"specification": {"metadata": {"name": "cim-gbr-v1.0"}}}


@pytest.fixture
def engine(migrated: str) -> Iterator[Engine]:
    """An engine that commits: two stores have to see each other's writes."""
    made = create_engine(migrated, future=True)
    yield made
    made.dispose()


@pytest.fixture
def site(engine: Engine) -> str:
    """The forge these keys belong to."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO site (id, name, location, timezone, control_plane_uri, "
                "anchor_state) VALUES (:id, 'Idempotency test', 'Belfast', 'Europe/London', "
                "'https://sindri.veldris.internal', 'ANCHORED') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": SITE},
        )
    return SITE


def _store(engine: Engine) -> DatabaseIdempotencyStore:
    """One API process's view of the store."""
    return DatabaseIdempotencyStore(engine=engine)


def _key(name: str) -> str:
    """A key nothing else in this module uses."""
    import uuid

    return f"{name}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Two processes
# ---------------------------------------------------------------------------


def test_a_key_reserved_in_one_process_replays_in_another(engine: Engine, site: str) -> None:
    """The finding. Two independent stores are two API processes.

    A dictionary in one of them could not answer this: the second process
    would see a fresh key and act, which is the exact double action the key
    exists to prevent.
    """
    del site
    key = _key("replay")
    first, second = _store(engine), _store(engine)

    assert first.reserve(key, site_id=SITE, actor=ACTOR, payload=BODY, now=NOW) is None
    first.complete(key, site_id=SITE, actor=ACTOR, status=202, body={"runId": "abc"})

    replayed = second.reserve(key, site_id=SITE, actor=ACTOR, payload=BODY, now=NOW)

    assert replayed is not None
    assert replayed.status == 202
    assert replayed.body == {"runId": "abc"}


def test_a_key_in_flight_in_one_process_is_refused_in_another(engine: Engine, site: str) -> None:
    """409 rather than a second run.

    This is the case the control exists for and the one a process-local store
    could never answer: the first request has reserved and has not finished, so
    the second must be told to wait rather than allowed to act.
    """
    del site
    key = _key("in-flight")
    first, second = _store(engine), _store(engine)

    assert first.reserve(key, site_id=SITE, actor=ACTOR, payload=BODY, now=NOW) is None

    with pytest.raises(InFlightError):
        second.reserve(key, site_id=SITE, actor=ACTOR, payload=BODY, now=NOW)


def test_two_processes_reserving_at_once_produce_one_winner(engine: Engine, site: str) -> None:
    """The reservation is one statement, so the database decides.

    A read-then-write would be a race between the read and the write -- the
    failure the key exists to prevent, reintroduced by the fix for it.
    """
    del site
    key = _key("race")
    stores = [_store(engine) for _ in range(4)]
    outcomes: list[Any] = []

    for store in stores:
        try:
            outcomes.append(store.reserve(key, site_id=SITE, actor=ACTOR, payload=BODY, now=NOW))
        except InFlightError:
            outcomes.append("refused")

    assert outcomes.count(None) == 1, "more than one caller claimed the key"
    assert outcomes.count("refused") == 3


def test_a_different_body_under_one_key_is_refused(engine: Engine, site: str) -> None:
    """422, and the first response is not returned.

    Returning it would tell the caller a request they did not make had
    succeeded, which is worse than either alternative.
    """
    del site
    key = _key("reused")
    first, second = _store(engine), _store(engine)

    first.reserve(key, site_id=SITE, actor=ACTOR, payload=BODY, now=NOW)
    first.complete(key, site_id=SITE, actor=ACTOR, status=202, body={"runId": "abc"})

    with pytest.raises(KeyReusedError):
        second.reserve(key, site_id=SITE, actor=ACTOR, payload={"other": True}, now=NOW)


def test_a_released_key_is_available_again(engine: Engine, site: str) -> None:
    """A request that errored has not acted, so its key must not be held.

    A client retrying after a 500 would otherwise be told its request was
    already in flight for the whole twenty-four hour window.
    """
    del site
    key = _key("released")
    first, second = _store(engine), _store(engine)

    first.reserve(key, site_id=SITE, actor=ACTOR, payload=BODY, now=NOW)
    first.release(key, site_id=SITE, actor=ACTOR)

    assert second.reserve(key, site_id=SITE, actor=ACTOR, payload=BODY, now=NOW) is None


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_two_actors_may_use_the_same_obvious_key(engine: Engine, site: str) -> None:
    """`retry-1` is what two operators will both type."""
    del site
    key = _key("shared")
    store = _store(engine)

    assert store.reserve(key, site_id=SITE, actor="akuma", payload=BODY, now=NOW) is None
    assert store.reserve(key, site_id=SITE, actor="brokkr", payload=BODY, now=NOW) is None


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_an_expired_key_is_taken_over_rather_than_replayed(engine: Engine, site: str) -> None:
    """A key held for ever turns a resubmission a week later into a no-op.

    It would return a stale run identifier, which looks exactly like the system
    ignoring the operator.
    """
    del site
    key = _key("stale")
    store = _store(engine)

    store.reserve(key, site_id=SITE, actor=ACTOR, payload=BODY, now=NOW)
    store.complete(key, site_id=SITE, actor=ACTOR, status=202, body={"runId": "old"})

    later = NOW + TTL + timedelta(minutes=1)
    assert store.reserve(key, site_id=SITE, actor=ACTOR, payload=BODY, now=later) is None


def test_a_live_key_is_not_taken_over(engine: Engine, site: str) -> None:
    """The other side of the same statement: inside the window it replays."""
    del site
    key = _key("live")
    store = _store(engine)

    store.reserve(key, site_id=SITE, actor=ACTOR, payload=BODY, now=NOW)
    store.complete(key, site_id=SITE, actor=ACTOR, status=202, body={"runId": "current"})

    replayed = store.reserve(
        key, site_id=SITE, actor=ACTOR, payload=BODY, now=NOW + timedelta(hours=23)
    )

    assert replayed is not None
    assert replayed.body == {"runId": "current"}


def test_the_worker_sweeps_expired_records(engine: Engine, site: str, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """On the timetable rather than on a request path. RF-14.

    A sweep on the request path makes one unlucky caller pay for everybody
    else's expired keys, and does nothing at all on a quiet estate -- which is
    exactly when the table grows without anybody looking.
    """
    from draupnir.worker.duties import Duty, Timetable
    from draupnir.worker.loop import Worker, WorkerSettings

    key = _key("sweepable")
    store = _store(engine)
    old = NOW - TTL - timedelta(hours=1)
    store.reserve(key, site_id=site, actor=ACTOR, payload=BODY, now=old)
    store.complete(key, site_id=site, actor=ACTOR, status=202, body={"runId": "ancient"})

    worker = Worker(
        WorkerSettings(site_id=site, scratch=tmp_path / "worker", interval=0.05, stand_in=True),
        engine=engine,
    )
    worker.timetable = Timetable()
    report = worker.run_once()

    swept = [item for item in report.findings if item.duty is Duty.KEYS]
    assert swept, "the sweep did not run"
    assert swept[0].measurements["removed"] >= 1

    # And the key is available again, which is what the sweep is for.
    assert store.reserve(key, site_id=site, actor=ACTOR, payload=BODY, now=NOW) is None
