"""AC-N5: ledger chain verification over 100,000 entries, under 60 seconds.

This is measured against a real PostgreSQL rather than a list in memory,
because the number that matters is the one an operator sees when they ask
whether the forge's history is intact. Reading the rows is most of the cost,
which is exactly why `LedgerRepository.verify_chain` streams in batches
instead of loading the chain.

The chain is built inside a transaction that rolls back. The ledger refuses
DELETE and TRUNCATE by design, so a benchmark that committed a hundred
thousand rows would have no way to clear them and every test module that ran
afterwards would inherit them.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, Engine, text

from draupnir.core.domain.identifiers import id_at
from draupnir.core.domain.ledger import GENESIS_HASH, compute_entry_hash
from draupnir.core.domain.sites import SiteScope
from draupnir.core.infrastructure.repositories import LedgerRepository

pytestmark = pytest.mark.integration

#: The figure AC-N5 names.
ENTRIES = 100_000
BUDGET_SECONDS = 60.0

SITE = "benchmark"
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def build_rows(count: int, site_id: str) -> list[dict[str, object]]:
    """Build a genuine hash chain of `count` entries, linked and hashed."""
    rows: list[dict[str, object]] = []
    prev_hash = GENESIS_HASH
    for index in range(count):
        payload = {"i": index, "scheduler_job_id": str(400000 + index)}
        entry_hash = compute_entry_hash(prev_hash, payload)
        moment = EPOCH + timedelta(seconds=index)
        rows.append(
            {
                "id": id_at(moment, index.to_bytes(10, "big")),
                "site_id": site_id,
                "seq": index + 1,
                "prev_hash": prev_hash,
                "entry_hash": entry_hash,
                "ts": moment,
                "actor": "system:benchmark",
                "subject_type": "run",
                "subject_id": f"run-{index // 50:05d}",
                "transition": "QUEUED->TRAINING",
                "payload": f'{{"i":{index},"scheduler_job_id":"{400000 + index}"}}',
            }
        )
        prev_hash = entry_hash
    return rows


@pytest.fixture(scope="module")
def long_chain(owner_engine: Engine) -> Iterator[Connection]:
    """A 100,000 entry chain, built once and rolled back afterwards."""
    with owner_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "INSERT INTO site (id, name, location, timezone, control_plane_uri, "
                    "anchor_state) VALUES (:id, 'Benchmark', 'nowhere', 'UTC', "
                    "'https://example.invalid', 'UNANCHORED') ON CONFLICT DO NOTHING"
                ),
                {"id": SITE},
            )
            connection.execute(text("SELECT set_config('draupnir.site_id', :s, true)"), {"s": SITE})

            rows = build_rows(ENTRIES, SITE)
            started = time.perf_counter()
            for offset in range(0, len(rows), 10_000):
                connection.execute(
                    text(
                        "INSERT INTO ledger_entry (id, site_id, seq, prev_hash, entry_hash, "
                        "ts, actor, subject_type, subject_id, transition, payload) VALUES "
                        "(:id, :site_id, :seq, :prev_hash, :entry_hash, :ts, :actor, "
                        ":subject_type, :subject_id, :transition, :payload)"
                    ),
                    rows[offset : offset + 10_000],
                )
            print(f"\n  insert {ENTRIES:,} entries: {time.perf_counter() - started:.1f}s")  # noqa: T201
            yield connection
        finally:
            transaction.rollback()


def test_verification_of_100_000_entries_is_within_the_budget(long_chain: Connection) -> None:
    """AC-N5."""
    ledger = LedgerRepository(long_chain, SiteScope(SITE))
    assert ledger.length() == ENTRIES

    started = time.perf_counter()
    divergence = ledger.verify_chain()
    elapsed = time.perf_counter() - started

    print(  # noqa: T201
        f"  AC-N5: verified {ENTRIES:,} entries in {elapsed:.2f}s "
        f"({ENTRIES / elapsed:,.0f} entries/s), budget {BUDGET_SECONDS:.0f}s"
    )
    assert divergence is None, f"the benchmark chain diverges at {divergence}"
    assert elapsed < BUDGET_SECONDS, (
        f"AC-N5 requires verification of {ENTRIES:,} entries in under "
        f"{BUDGET_SECONDS:.0f}s; took {elapsed:.1f}s"
    )


def test_a_divergence_deep_in_a_long_chain_is_found(long_chain: Connection) -> None:
    """Verification is not merely fast; it still finds a single bad entry."""
    target = 73_501

    # A savepoint, so the damage is undone without discarding the chain the
    # other tests in this module are using.
    savepoint = long_chain.begin_nested()
    try:
        long_chain.execute(
            text("ALTER TABLE ledger_entry DISABLE TRIGGER trg_ledger_entry_append_only")
        )
        long_chain.execute(
            text(
                "UPDATE ledger_entry SET payload = CAST(:doc AS jsonb) "
                "WHERE site_id = :s AND seq = :seq"
            ),
            {"doc": '{"i":"rewritten"}', "s": SITE, "seq": target},
        )
        assert LedgerRepository(long_chain, SiteScope(SITE)).verify_chain() == target
    finally:
        savepoint.rollback()


def test_a_window_of_a_long_chain_verifies_without_reading_the_rest(
    long_chain: Connection,
) -> None:
    """A window is verified against the entry before it, so a slice is sound."""
    ledger = LedgerRepository(long_chain, SiteScope(SITE))

    started = time.perf_counter()
    assert ledger.verify_chain(90_000, 90_500) is None
    elapsed = time.perf_counter() - started

    # Five hundred entries out of a hundred thousand should be immediate. If
    # it is not, the read is not being bounded by the window.
    assert elapsed < 2.0, f"a 500 entry window took {elapsed:.2f}s"


def test_the_exported_head_names_the_end_of_the_chain(long_chain: Connection) -> None:
    head = LedgerRepository(long_chain, SiteScope(SITE)).export_head()
    assert head is not None
    assert head.seq == ENTRIES
    assert head.site_id == SITE
