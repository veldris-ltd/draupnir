"""The three constraints of SAD 11C are enforced by the database.

"A constraint that lives only in the application is a constraint that a
migration script can bypass." These tests attack the database directly, with
raw SQL and no application code in the way, because that is exactly how a
future migration or an operator with psql would attack it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from draupnir.core.domain.identifiers import new_id
from draupnir.core.domain.ledger import GENESIS_HASH, compute_entry_hash

#: The tables carrying a site scope, per the bold attributes of SAD 7.1.
SITE_SCOPED_TABLES = ("ledger_entry", "artefact", "run")

pytestmark = pytest.mark.integration


def _site(connection: Connection, site_id: str) -> None:
    connection.execute(
        text(
            "INSERT INTO site (id, name, location, timezone, control_plane_uri, anchor_state) "
            "VALUES (:id, :id, 'Nuneaton', 'Europe/London', "
            "'https://alviss.example.internal', 'UNANCHORED') ON CONFLICT DO NOTHING"
        ),
        {"id": site_id},
    )


def _set_site(connection: Connection, site_id: str) -> None:
    connection.execute(
        text("SELECT set_config('draupnir.site_id', :site_id, true)"), {"site_id": site_id}
    )


def _ledger_entry(connection: Connection, site_id: str, seq: int = 1) -> UUID:
    payload = {"transition": "QUEUED->TRAINING", "seq": seq}
    entry_id = new_id()
    connection.execute(
        text(
            "INSERT INTO ledger_entry "
            "(id, site_id, seq, prev_hash, entry_hash, ts, actor, subject_type, "
            " subject_id, transition, payload) "
            "VALUES (:id, :site_id, :seq, :prev_hash, :entry_hash, :ts, :actor, "
            " 'run', :subject_id, 'QUEUED->TRAINING', :payload)"
        ),
        {
            "id": entry_id,
            "site_id": site_id,
            "seq": seq,
            "prev_hash": GENESIS_HASH,
            "entry_hash": compute_entry_hash(GENESIS_HASH, payload),
            "ts": datetime.now(tz=UTC),
            "actor": "system:test",
            "subject_id": str(new_id()),
            "payload": json.dumps(payload),
        },
    )
    return entry_id


# -- Constraint 1: ledger_entry accepts INSERT only -------------------------


def test_the_ledger_accepts_an_insert(owner: Connection) -> None:
    _site(owner, "sindri")
    _set_site(owner, "sindri")
    _ledger_entry(owner, "sindri")
    count = owner.execute(
        text("SELECT count(*) FROM ledger_entry WHERE site_id = :s"), {"s": "sindri"}
    ).scalar_one()
    assert count == 1


def test_the_ledger_refuses_an_update(owner: Connection) -> None:
    _site(owner, "sindri")
    _set_site(owner, "sindri")
    _ledger_entry(owner, "sindri")

    with pytest.raises(DBAPIError, match="append only"):
        owner.execute(text("UPDATE ledger_entry SET actor = 'someone-else'"))


def test_the_ledger_refuses_a_delete(owner: Connection) -> None:
    _site(owner, "sindri")
    _set_site(owner, "sindri")
    _ledger_entry(owner, "sindri")

    with pytest.raises(DBAPIError, match="append only"):
        owner.execute(text("DELETE FROM ledger_entry"))


def test_the_ledger_refuses_a_truncate(owner: Connection) -> None:
    _site(owner, "sindri")
    _set_site(owner, "sindri")
    _ledger_entry(owner, "sindri")

    with pytest.raises(DBAPIError, match="append only"):
        owner.execute(text("TRUNCATE ledger_entry"))


def test_a_site_chain_cannot_reuse_a_sequence_number(owner: Connection) -> None:
    _site(owner, "sindri")
    _set_site(owner, "sindri")
    _ledger_entry(owner, "sindri", seq=1)

    with pytest.raises(IntegrityError):
        _ledger_entry(owner, "sindri", seq=1)


def test_a_non_hex_hash_is_refused(owner: Connection) -> None:
    _site(owner, "sindri")
    _set_site(owner, "sindri")

    with pytest.raises(IntegrityError):
        owner.execute(
            text(
                "INSERT INTO ledger_entry "
                "(id, site_id, seq, prev_hash, entry_hash, ts, actor, subject_type, "
                " subject_id, transition, payload) "
                "VALUES (:id, 'sindri', 1, 'not-a-hash', :entry_hash, now(), 'a', 'run', "
                " 'b', 'X->Y', '{}')"
            ),
            {"id": new_id(), "entry_hash": GENESIS_HASH},
        )


# -- Constraint 2: a release requires an approval ---------------------------


def test_a_release_without_an_approval_is_refused(owner: Connection) -> None:
    _site(owner, "sindri")
    _set_site(owner, "sindri")
    artefact_id = new_id()
    owner.execute(
        text(
            "INSERT INTO artefact (id, site_id, locality, kind, uri, sha256_manifest, size) "
            "VALUES (:id, 'sindri', ARRAY['sindri'], 'quantised', :uri, :sha, 1024)"
        ),
        {"id": artefact_id, "uri": f"hodd://sindri/{artefact_id}", "sha": "a" * 64},
    )

    with pytest.raises((IntegrityError, DBAPIError)):
        owner.execute(
            text(
                "INSERT INTO release (id, artefact_id, approval_id, model_card_uri, "
                " sbom_uri, lineage_uri, training_summary_uri, copyright_policy_uri, signature) "
                "VALUES (:id, :artefact_id, NULL, 'a', 'b', 'c', 'd', 'e', 'f')"
            ),
            {"id": new_id(), "artefact_id": artefact_id},
        )


def test_a_release_naming_an_unknown_approval_is_refused(owner: Connection) -> None:
    _site(owner, "sindri")
    _set_site(owner, "sindri")
    artefact_id = new_id()
    owner.execute(
        text(
            "INSERT INTO artefact (id, site_id, locality, kind, uri, sha256_manifest, size) "
            "VALUES (:id, 'sindri', ARRAY['sindri'], 'quantised', :uri, :sha, 1024)"
        ),
        {"id": artefact_id, "uri": f"hodd://sindri/{artefact_id}", "sha": "b" * 64},
    )

    with pytest.raises(IntegrityError):
        owner.execute(
            text(
                "INSERT INTO release (id, artefact_id, approval_id, model_card_uri, "
                " sbom_uri, lineage_uri, training_summary_uri, copyright_policy_uri, signature) "
                "VALUES (:id, :artefact_id, :approval_id, 'a', 'b', 'c', 'd', 'e', 'f')"
            ),
            {"id": new_id(), "artefact_id": artefact_id, "approval_id": new_id()},
        )


# -- Constraint 3: site scope by row level security -------------------------


def test_a_write_without_the_site_variable_is_refused(
    app: Connection, owner_engine: Engine
) -> None:
    with owner_engine.begin() as setup:
        _site(setup, "sindri")

    with pytest.raises(DBAPIError):
        _ledger_entry(app, "sindri")


def test_a_write_for_another_site_is_refused(app: Connection, owner_engine: Engine) -> None:
    with owner_engine.begin() as setup:
        _site(setup, "sindri")
        _site(setup, "brokkr")

    _set_site(app, "brokkr")
    with pytest.raises(DBAPIError):
        _ledger_entry(app, "sindri")


def test_a_read_sees_only_the_scoped_site(app: Connection, owner_engine: Engine) -> None:
    with owner_engine.begin() as setup:
        _site(setup, "sindri")
        _site(setup, "brokkr")
        _set_site(setup, "sindri")
        _ledger_entry(setup, "sindri", seq=901)
        _set_site(setup, "brokkr")
        _ledger_entry(setup, "brokkr", seq=902)

    _set_site(app, "sindri")
    sites = {row[0] for row in app.execute(text("SELECT DISTINCT site_id FROM ledger_entry")).all()}
    assert sites == {"sindri"}


def test_force_row_level_security_is_set_on_every_scoped_table(owner: Connection) -> None:
    """Without FORCE, the table owner is exempt and the policy is decorative.

    A PostgreSQL superuser bypasses row level security whatever this says,
    which is why the deployment role is created NOSUPERUSER (see
    docker/initdb/01-application-role.sql). This asserts the half of the
    protection that lives in the schema; `app` above asserts the other half by
    connecting as an ordinary role.
    """
    rows = owner.execute(
        text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = ANY(:tables)"
        ),
        {"tables": list(SITE_SCOPED_TABLES)},
    ).all()

    found = {name: (enabled, forced) for name, enabled, forced in rows}
    assert set(found) == set(SITE_SCOPED_TABLES)
    for table, (enabled, forced) in sorted(found.items()):
        assert enabled, f"{table} does not have row level security enabled"
        assert forced, f"{table} does not FORCE row level security"


def test_every_scoped_table_carries_the_site_isolation_policy(owner: Connection) -> None:
    policies = {
        row[0]
        for row in owner.execute(
            text("SELECT tablename FROM pg_policies WHERE policyname = 'site_isolation'")
        )
    }
    assert set(SITE_SCOPED_TABLES) <= policies


# -- The source register ----------------------------------------------------


def test_a_personal_data_source_must_name_a_dpia(owner: Connection) -> None:
    with pytest.raises(IntegrityError):
        owner.execute(
            text(
                "INSERT INTO source (id, jurisdiction, url, licence_spdx, "
                " attribution_required, retrieved_at, sha256, personal_data, state) "
                "VALUES (:id, 'GBR', 'https://example.invalid', 'CC-BY-4.0', true, now(), "
                " :sha, true, 'CORPUS_REGISTERED')"
            ),
            {"id": new_id(), "sha": "c" * 64},
        )


def test_a_source_cannot_hold_a_run_only_state(owner: Connection) -> None:
    with pytest.raises(IntegrityError):
        owner.execute(
            text(
                "INSERT INTO source (id, jurisdiction, url, licence_spdx, "
                " attribution_required, retrieved_at, sha256, personal_data, state) "
                "VALUES (:id, 'GBR', 'https://example.invalid', 'CC-BY-4.0', true, now(), "
                " :sha, false, 'TRAINING')"
            ),
            {"id": new_id(), "sha": "d" * 64},
        )
