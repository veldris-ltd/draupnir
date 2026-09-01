"""The seed writes, and what it wrote verifies.

AC-Q9 depends on this: `make dev` claims to leave a running stack with seeded
data, and the only honest way to hold that claim is to write the dataset into a
real PostgreSQL with the real migrations and the real row level security, then
read it back.

Reads go through the unprivileged application role, not the schema owner. A
PostgreSQL superuser bypasses every row level security policy, so a count taken
as one would silently ignore the site scope and pass whatever the seed had
written. That is the same trap the development compose file avoids by creating
a NOSUPERUSER role for the application.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import Connection, Engine, text

from draupnir.core.domain.ledger import GENESIS_HASH, compute_entry_hash
from draupnir.core.domain.states import RUN_PHASE_STATES
from scripts.seed import (
    TARGET_LEDGER_ENTRIES,
    TARGET_RELEASES,
    TARGET_RUNS,
    TARGET_SOURCES,
    build,
    write,
)
from tests.integration.conftest import reset_schema

pytestmark = pytest.mark.integration

SITES = ("sindri", "brokkr")


@pytest.fixture(scope="session")
def seeded(migrated: str) -> str:
    """Reset the schema, migrate it again, and seed it.

    The reset is not tidiness. The constraint tests commit site rows, because
    proving that one connection cannot see another site's data requires two
    connections and therefore a commit. The seed refuses to run against a
    database that already holds sites, which is the behaviour those same tests
    would otherwise defeat, so this fixture takes the schema down and rebuilds
    it first -- exactly what `make reset-db && make seed` does.
    """
    reset_schema(migrated)
    write(build(), migrated)
    return migrated


def _scoped(connection: Connection, site_id: str) -> None:
    connection.execute(
        text("SELECT set_config('draupnir.site_id', :site, false)"), {"site": site_id}
    )


def test_the_counts_match_the_specification(seeded: str, app_engine: Engine) -> None:
    with app_engine.connect() as connection:
        counts = {
            table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
            for table in ("site", "source", "release")
        }
        per_site: dict[str, int] = {}
        runs = 0
        for site_id in SITES:
            _scoped(connection, site_id)
            per_site[site_id] = connection.execute(
                text("SELECT count(*) FROM ledger_entry")
            ).scalar_one()
            runs += connection.execute(text("SELECT count(*) FROM run")).scalar_one()

    assert counts["site"] == 2
    assert counts["source"] == TARGET_SOURCES
    assert counts["release"] == TARGET_RELEASES
    assert runs == TARGET_RUNS
    assert sum(per_site.values()) == TARGET_LEDGER_ENTRIES
    # Both chains carry entries; a dataset that put all 400 on one site would
    # not exercise federation at all.
    assert all(count > 0 for count in per_site.values()), per_site


def test_the_site_scope_partitions_what_a_reader_sees(seeded: str, app_engine: Engine) -> None:
    with app_engine.connect() as connection:
        for site_id in SITES:
            _scoped(connection, site_id)
            visible = {
                row[0]
                for row in connection.execute(text("SELECT DISTINCT site_id FROM ledger_entry"))
            }
            assert visible == {site_id}


def test_every_run_state_is_present(seeded: str, app_engine: Engine) -> None:
    states: set[str] = set()
    with app_engine.connect() as connection:
        for site_id in SITES:
            _scoped(connection, site_id)
            states |= {row[0] for row in connection.execute(text("SELECT DISTINCT state FROM run"))}

    assert states == {str(state) for state in RUN_PHASE_STATES}


def test_every_stored_chain_verifies(seeded: str, app_engine: Engine) -> None:
    with app_engine.connect() as connection:
        for site_id in SITES:
            _scoped(connection, site_id)
            rows = connection.execute(
                text("SELECT seq, prev_hash, entry_hash, payload FROM ledger_entry ORDER BY seq")
            ).all()

            assert rows, f"{site_id} has no ledger entries"
            expected_prev = GENESIS_HASH
            for index, (seq, prev_hash, entry_hash, payload) in enumerate(rows, start=1):
                assert seq == index, f"{site_id} sequence break at {index}"
                assert prev_hash == expected_prev, f"{site_id} chain break at seq {seq}"
                stored = payload if isinstance(payload, dict) else json.loads(payload)
                assert compute_entry_hash(prev_hash, stored) == entry_hash
                expected_prev = entry_hash


def test_every_release_is_bound_to_an_approval(seeded: str, app_engine: Engine) -> None:
    with app_engine.connect() as connection:
        orphaned = connection.execute(
            text(
                "SELECT count(*) FROM release r "
                "LEFT JOIN approval a ON a.id = r.approval_id "
                "WHERE a.id IS NULL OR a.decision <> 'APPROVED'"
            )
        ).scalar_one()
    assert orphaned == 0


def test_the_seed_refuses_to_run_twice(seeded: str) -> None:
    with pytest.raises(SystemExit, match="already holds sites"):
        write(build(), seeded)


def test_the_smoke_ledger_check_passes_against_the_seeded_database(seeded: str) -> None:
    from scripts.smoke import check_ledger

    assert check_ledger(seeded) is True
