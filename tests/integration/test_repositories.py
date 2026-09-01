"""Repositories against a live PostgreSQL.

Two of Prompt 1's exit conditions are proved here: an unscoped query raises
rather than defaulting, and `verify_chain(from_seq, to_seq)` returns the first
divergent sequence number. The third, that a direct UPDATE on the ledger table
is refused, is in `test_schema_constraints.py` where it attacks the database
with raw SQL and no application code in the way.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, text

from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry, append, canonical
from draupnir.core.domain.sites import (
    AnchorState,
    SiteScope,
    UnknownSiteError,
    UnscopedQueryError,
)
from draupnir.core.infrastructure.repositories import (
    LedgerRepository,
    SiteRepository,
    clear_site_scope,
    current_site_scope,
)

pytestmark = pytest.mark.integration

EPOCH = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def make_site(connection: Connection, site_id: str, anchor: str = "UNANCHORED") -> None:
    connection.execute(
        text(
            "INSERT INTO site (id, name, location, timezone, control_plane_uri, anchor_state) "
            "VALUES (:id, :id, 'Nuneaton', 'Europe/London', "
            "'https://alviss.example.internal', :anchor) ON CONFLICT DO NOTHING"
        ),
        {"id": site_id, "anchor": anchor},
    )


def chain(site_id: str, length: int) -> list[LedgerEntry]:
    entries: list[LedgerEntry] = []
    previous: LedgerEntry | None = None
    for index in range(length):
        previous = append(
            previous=previous,
            site_id=site_id,
            ts=EPOCH + timedelta(minutes=index),
            actor="system:test",
            subject_type="run",
            subject_id=f"run-{index // 4:04d}",
            transition="QUEUED->TRAINING",
            payload={"scheduler_job_id": str(400000 + index), "i": index},
        )
        entries.append(previous)
    return entries


@pytest.fixture
def sindri(owner: Connection) -> Connection:
    """A rolled-back transaction with two sites registered."""
    make_site(owner, "sindri", "ANCHORED")
    make_site(owner, "brokkr")
    return owner


# ---------------------------------------------------------------------------
# The scope is never inferred
# ---------------------------------------------------------------------------


def test_a_repository_cannot_be_built_without_a_scope(sindri: Connection) -> None:
    with pytest.raises(UnscopedQueryError) as raised:
        LedgerRepository(sindri, None)
    assert "LedgerRepository" in str(raised.value)


def test_a_scope_naming_an_unregistered_site_is_refused(sindri: Connection) -> None:
    registry = SiteRepository(sindri).registry(local="sindri")
    with pytest.raises(UnknownSiteError):
        registry.scope("eitri")


def test_a_repository_refuses_entries_belonging_to_another_site(sindri: Connection) -> None:
    ledger = LedgerRepository(sindri, SiteScope("sindri"))
    with pytest.raises(UnscopedQueryError):
        ledger.append_many(chain("brokkr", 1))


def test_building_a_repository_sets_the_scope_the_policies_read(
    sindri: Connection,
) -> None:
    LedgerRepository(sindri, SiteScope("brokkr"))
    assert current_site_scope(sindri) == "brokkr"


def test_clearing_the_scope_really_clears_it(sindri: Connection) -> None:
    # Transaction-locally, matching how it is set. Clearing at session level
    # would leave the SET LOCAL in force and the scope would look cleared
    # while still being applied.
    LedgerRepository(sindri, SiteScope("sindri"))
    clear_site_scope(sindri)
    assert current_site_scope(sindri) == ""


def test_a_scoped_repository_reads_only_its_own_site(sindri: Connection) -> None:
    # The queries carry an explicit site predicate as well as relying on the
    # row level security policy. This connection is the schema owner, and a
    # superuser bypasses every policy -- so without the predicate this test
    # would see both chains, and so would anything else that ever ran as one.
    LedgerRepository(sindri, SiteScope("sindri")).append_many(chain("sindri", 3))
    LedgerRepository(sindri, SiteScope("brokkr")).append_many(chain("brokkr", 5))

    assert LedgerRepository(sindri, SiteScope("sindri")).length() == 3
    assert LedgerRepository(sindri, SiteScope("brokkr")).length() == 5

    heads = {
        site: LedgerRepository(sindri, SiteScope(site)).export_head()
        for site in ("sindri", "brokkr")
    }
    assert heads["sindri"] is not None
    assert heads["brokkr"] is not None
    assert heads["sindri"].seq == 3
    assert heads["brokkr"].seq == 5
    assert heads["sindri"].entry_hash != heads["brokkr"].entry_hash


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------


def test_an_empty_chain_has_no_head(sindri: Connection) -> None:
    ledger = LedgerRepository(sindri, SiteScope("sindri"))
    assert ledger.head() is None
    assert ledger.export_head() is None
    assert ledger.verify_chain() is None


def test_appended_entries_come_back_in_order(sindri: Connection) -> None:
    ledger = LedgerRepository(sindri, SiteScope("sindri"))
    written = chain("sindri", 12)
    ledger.append_many(written)

    read = list(ledger.stream())
    assert [entry.seq for entry in read] == list(range(1, 13))
    assert [entry.entry_hash for entry in read] == [entry.entry_hash for entry in written]
    assert read[0].prev_hash == GENESIS_HASH
    assert read[3].payload["i"] == 3


def test_the_head_is_the_highest_sequence(sindri: Connection) -> None:
    ledger = LedgerRepository(sindri, SiteScope("sindri"))
    written = chain("sindri", 7)
    ledger.append_many(written)

    head = ledger.head()
    assert head is not None
    assert head.seq == 7

    exported = ledger.export_head()
    assert exported is not None
    assert exported.site_id == "sindri"
    assert exported.seq == 7
    assert exported.entry_hash == written[-1].entry_hash
    assert exported.as_anchor() == (7, written[-1].entry_hash)


def test_a_whole_intact_chain_verifies(sindri: Connection) -> None:
    ledger = LedgerRepository(sindri, SiteScope("sindri"))
    ledger.append_many(chain("sindri", 40))
    assert ledger.verify_chain() is None
    assert ledger.verify_chain(10, 20) is None


def test_verification_returns_the_first_divergent_sequence(sindri: Connection) -> None:
    ledger = LedgerRepository(sindri, SiteScope("sindri"))
    ledger.append_many(chain("sindri", 20))

    # The table refuses UPDATE, so a rewrite is simulated the only way an
    # attacker could: by restoring from a doctored dump. The trigger is
    # dropped for the length of this transaction, which rolls back.
    sindri.execute(text("ALTER TABLE ledger_entry DISABLE TRIGGER trg_ledger_entry_append_only"))
    sindri.execute(
        text("UPDATE ledger_entry SET payload = :p WHERE seq = 9"),
        {"p": '{"i": "rewritten"}'},
    )
    sindri.execute(text("ALTER TABLE ledger_entry ENABLE TRIGGER trg_ledger_entry_append_only"))

    assert ledger.verify_chain() == 9
    # A window that ends before the damage still verifies.
    assert ledger.verify_chain(1, 8) is None
    # A window starting after it does too, and that is correct rather than a
    # gap: rewriting a payload without its entry_hash leaves the links intact,
    # so only re-hashing entry 9 can catch it. This is why verification runs
    # from sequence 1 rather than from wherever it last left off.
    assert ledger.verify_chain(10, 20) is None


def test_a_rewrite_that_relinks_the_chain_is_caught_at_the_next_entry(
    sindri: Connection,
) -> None:
    ledger = LedgerRepository(sindri, SiteScope("sindri"))
    ledger.append_many(chain("sindri", 20))

    # The thorough forgery: rewrite the payload and its hash together, so
    # entry 9 is internally consistent. Entry 10 still points at the old hash.
    # The hash is computed over the canonical form, which is what the domain
    # hashes: sorted keys and no whitespace.
    canonical_text = canonical({"i": "rewritten"}).decode()
    sindri.execute(text("ALTER TABLE ledger_entry DISABLE TRIGGER trg_ledger_entry_append_only"))
    sindri.execute(
        text(
            "UPDATE ledger_entry SET payload = CAST(:doc AS jsonb), entry_hash = encode("
            "sha256(decode(prev_hash, 'hex') || convert_to(:doc_text, 'UTF8')), 'hex') "
            "WHERE seq = 9"
        ),
        {"doc": canonical_text, "doc_text": canonical_text},
    )
    sindri.execute(text("ALTER TABLE ledger_entry ENABLE TRIGGER trg_ledger_entry_append_only"))

    assert ledger.verify_chain() == 10
    assert ledger.verify_chain(10, 20) == 10


def test_verification_of_a_window_past_the_end_reports_the_gap(sindri: Connection) -> None:
    ledger = LedgerRepository(sindri, SiteScope("sindri"))
    ledger.append_many(chain("sindri", 5))
    assert ledger.verify_chain(9, 12) == 9


def test_batched_verification_crosses_batch_boundaries(
    sindri: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-N5 verifies 100,000 entries in slices; the link has to be carried
    # across a slice boundary, so the boundary is made small and tested.
    import draupnir.core.infrastructure.repositories as module

    monkeypatch.setattr(module, "BATCH", 3)
    ledger = module.LedgerRepository(sindri, SiteScope("sindri"))
    ledger.append_many(chain("sindri", 25))
    assert ledger.verify_chain() is None


# ---------------------------------------------------------------------------
# The forge registry
# ---------------------------------------------------------------------------


def test_the_registry_loads_from_the_site_table(sindri: Connection) -> None:
    registry = SiteRepository(sindri).registry(local="sindri")
    assert registry.ids == ("brokkr", "sindri")
    assert registry.local.anchor_state is AnchorState.ANCHORED
    assert registry.local_scope() == SiteScope("sindri")


def test_anchor_state_is_recorded(sindri: Connection) -> None:
    sites = SiteRepository(sindri)
    anchored_at = EPOCH + timedelta(days=1)
    sites.set_anchor_state("brokkr", AnchorState.ANCHORED, anchored_at=anchored_at)

    registry = sites.registry(local="sindri")
    assert registry.get("brokkr").anchor_state is AnchorState.ANCHORED
    assert registry.get("brokkr").last_anchored_at == anchored_at


def test_a_partitioned_site_may_not_release(sindri: Connection) -> None:
    sites = SiteRepository(sindri)
    sites.set_anchor_state("sindri", AnchorState.PARTITIONED)
    registry = sites.registry(local="sindri")

    from draupnir.core.domain.sites import ReleaseBlockedError

    with pytest.raises(ReleaseBlockedError):
        registry.assert_may_release(SiteScope("sindri"))
