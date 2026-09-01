"""Rebuilding the run registry from the ledger.

Prompt 1 requires `rebuild_projection()` to replay the ledger from zero and
produce byte-identical table contents. "Byte-identical" is taken literally
here: the comparison is an MD5 over the rendered rows in identifier order,
computed by PostgreSQL from the stored tuples, so a difference in a timestamp's
microseconds or a numeric's scale would show up. Comparing Python objects would
not catch either.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, text

from draupnir.core.domain.ledger import LedgerEntry, append
from draupnir.core.domain.projector import REGISTRATION
from draupnir.core.domain.sites import SiteScope, UnscopedQueryError
from draupnir.core.domain.states import RunState
from draupnir.core.infrastructure.repositories import LedgerRepository, RunProjection

pytestmark = pytest.mark.integration

EPOCH = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
RUN_A = "019cb993-d800-777d-aae5-c00a7ce43658"
RUN_B = "019cb993-d800-777d-aae5-c00a7ce43659"

SPINE = (
    "DRAFT->CORPUS_REGISTERED",
    "CORPUS_REGISTERED->LICENCE_CLEARED",
    "LICENCE_CLEARED->CURATED",
    "CURATED->QUEUED",
    "QUEUED->TRAINING",
    "TRAINING->TRAINED",
    "TRAINED->EVALUATING",
)


def fingerprint(connection: Connection, site_id: str) -> str | None:
    """An MD5 over the run table's rendered rows, in identifier order."""
    return connection.execute(
        text(
            "SELECT md5(string_agg(r::text, '|' ORDER BY r.id)) FROM run r "
            "WHERE r.site_id = :site_id"
        ),
        {"site_id": site_id},
    ).scalar_one_or_none()


@pytest.fixture
def sindri(owner: Connection) -> Connection:
    owner.execute(
        text(
            "INSERT INTO site (id, name, location, timezone, control_plane_uri, anchor_state) "
            "VALUES ('sindri', 'Sindri', 'Nuneaton', 'Europe/London', "
            "'https://alviss.example.internal', 'ANCHORED') ON CONFLICT DO NOTHING"
        )
    )
    return owner


def write_chain(connection: Connection, transitions: tuple[str, ...] = SPINE) -> int:
    """Write a registration plus `transitions` for two runs. Returns the length."""
    entries: list[LedgerEntry] = []
    previous: LedgerEntry | None = None
    step = 0

    def add(subject_id: str, transition: str, payload: dict[str, object]) -> None:
        nonlocal previous, step
        step += 1
        previous = append(
            previous=previous,
            site_id="sindri",
            ts=EPOCH + timedelta(minutes=step),
            actor="system:test",
            subject_type="run",
            subject_id=subject_id,
            transition=transition,
            payload=payload,
        )
        entries.append(previous)

    for run_id, name, kind in (
        (RUN_A, "cim-gbr-v0.1", "adapter"),
        (RUN_B, "cim-irl-v0.1", "merge"),
    ):
        add(run_id, REGISTRATION, {"name": name, "spec_hash": "a" * 64, "kind": kind})

    for transition in transitions:
        for run_id in (RUN_A, RUN_B):
            payload: dict[str, object] = {}
            if transition.endswith("->TRAINING"):
                payload = {"scheduler_job_id": "421337", "node": "dvalin"}
            add(run_id, transition, payload)

    LedgerRepository(connection, SiteScope("sindri")).append_many(entries)
    return len(entries)


def test_a_rebuild_builds_the_registry_from_nothing(sindri: Connection) -> None:
    length = write_chain(sindri)
    report = RunProjection(sindri, SiteScope("sindri")).rebuild()

    assert report.entries_read == length
    assert report.rows_written == 2
    assert report.last_seq == length
    assert report.rebuilt

    runs = RunProjection(sindri, SiteScope("sindri")).read()
    assert {run.id for run in runs} == {RUN_A, RUN_B}
    assert {run.state for run in runs} == {RunState.EVALUATING}
    assert all(run.started_at is not None for run in runs)


def test_rebuilding_twice_produces_byte_identical_contents(sindri: Connection) -> None:
    write_chain(sindri)
    projection = RunProjection(sindri, SiteScope("sindri"))

    projection.rebuild()
    first = fingerprint(sindri, "sindri")

    projection.rebuild()
    second = fingerprint(sindri, "sindri")

    assert first is not None
    assert first == second


def test_a_rebuild_discards_a_row_the_chain_does_not_account_for(
    sindri: Connection,
) -> None:
    write_chain(sindri)
    projection = RunProjection(sindri, SiteScope("sindri"))
    projection.rebuild()
    expected = fingerprint(sindri, "sindri")

    # Somebody edits the registry by hand. The chain is the source of truth,
    # so a rebuild must erase the edit rather than preserve or merge it.
    sindri.execute(
        text(
            "INSERT INTO run (id, site_id, name, spec_hash, kind, state) VALUES "
            "('019cb993-d800-777d-aae5-c00a7ce4365a', 'sindri', 'invented', "
            "'ff', 'adapter', 'RELEASED')"
        )
    )
    sindri.execute(text("UPDATE run SET state = 'RELEASED' WHERE id = :id"), {"id": RUN_A})
    assert fingerprint(sindri, "sindri") != expected

    projection.rebuild()
    assert fingerprint(sindri, "sindri") == expected


def test_a_rebuild_from_zero_equals_the_incremental_result(sindri: Connection) -> None:
    write_chain(sindri, SPINE[:4])
    projection = RunProjection(sindri, SiteScope("sindri"))
    projection.rebuild()

    # More of the chain arrives, and the projection catches up rather than
    # being rebuilt.
    ledger = LedgerRepository(sindri, SiteScope("sindri"))
    head = ledger.head()
    assert head is not None
    previous = head
    entries: list[LedgerEntry] = []
    for index, transition in enumerate(SPINE[4:]):
        for run_id in (RUN_A, RUN_B):
            payload: dict[str, object] = (
                {"scheduler_job_id": "421337", "node": "dvalin"}
                if transition.endswith("->TRAINING")
                else {}
            )
            previous = append(
                previous=previous,
                site_id="sindri",
                ts=EPOCH + timedelta(hours=2, minutes=index),
                actor="system:test",
                subject_type="run",
                subject_id=run_id,
                transition=transition,
                payload=payload,
            )
            entries.append(previous)
    ledger.append_many(entries)

    projection.catch_up()
    incremental = fingerprint(sindri, "sindri")

    projection.rebuild()
    assert fingerprint(sindri, "sindri") == incremental


def test_the_checkpoint_records_how_far_the_projection_has_read(
    sindri: Connection,
) -> None:
    length = write_chain(sindri)
    projection = RunProjection(sindri, SiteScope("sindri"))
    assert projection.checkpoint() == 0

    projection.rebuild()
    assert projection.checkpoint() == length

    # Nothing new to read.
    report = projection.catch_up()
    assert report.entries_read == 0
    assert report.last_seq == length


def test_the_projection_cannot_be_built_without_a_scope(sindri: Connection) -> None:
    with pytest.raises(UnscopedQueryError):
        RunProjection(sindri, None)


def test_an_empty_chain_projects_an_empty_registry(sindri: Connection) -> None:
    report = RunProjection(sindri, SiteScope("sindri")).rebuild()
    assert report.entries_read == 0
    assert report.rows_written == 0
    assert report.last_seq == 0
    assert RunProjection(sindri, SiteScope("sindri")).read() == ()
