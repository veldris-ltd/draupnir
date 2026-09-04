"""SAD 11.2 row 4's recovery column: "restore NFS, run reconciliation".

The behaviour half of that row is tested in `test_degraded_modes.py` -- the
vault is taken away for real and every read and write refuses. This is the
other half, and it was NOT BUILT until now: an operator who had restored the
mount had no command to run, and staging what the running jobs wrote was a
manual copy.

The chain here is built transition by transition rather than by driving the
worker, because what reconciliation reads is precisely the digests those
transitions record. Writing them out says what the vault command depends on.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, text

from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import RunState
from draupnir.core.infrastructure.orchestration import for_connection
from draupnir.core.infrastructure.repositories import LedgerRepository, set_site_scope
from draupnir.hodd import reconcile as hodd
from draupnir.hodd.ingest import Ingestor
from draupnir.hodd.register import LicenceRegister
from draupnir.hodd.stores import PosixStoreDriver, artefact_uri
from scripts import vault_admin

pytestmark = pytest.mark.integration

CURATOR = "curator@veldris.internal"
WEIGHTS = b"the bytes a running job wrote to local scratch" * 64


@pytest.fixture
def engine(migrated: str) -> Iterator[Engine]:
    """An engine that commits: the command opens its own connection."""
    made = create_engine(migrated, future=True)
    yield made
    made.dispose()


@pytest.fixture
def site(engine: Engine) -> str:
    """A forge of its own, per test.

    Unique rather than shared because reconciliation commits: two tests against
    one site would each see the other's runs, and an exit code that depends on
    what a previous test left is not a test of anything.
    """
    site_id = f"vault-test-{uuid4().hex[:8]}"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO site (id, name, location, timezone, control_plane_uri, "
                "anchor_state) VALUES (:id, 'Vault test', 'Belfast', 'Europe/London', "
                "'https://vault.veldris.internal', 'ANCHORED') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": site_id},
        )
    return site_id


def _train(engine: Engine, site_id: str, checkpoint: str) -> UUID:
    """Put one run in the chain at TRAINED, with `checkpoint` as its digest.

    Every transition of SAD 6.1 from DRAFT, in order, with the facts its guard
    reads and the fields its ledger entry must record. Nothing is skipped: a
    run that reached TRAINED by another route would not be one the chain
    describes the way reconciliation reads it.
    """
    run_id = uuid4()
    corpus = hashlib.sha256(b"curated corpus").hexdigest()
    spec = hashlib.sha256(b"specification").hexdigest()

    with engine.connect() as connection:
        transaction = connection.begin()
        orchestrator = for_connection(connection, SiteScope(site_id), actor=CURATOR)
        orchestrator.register(run_id, name="cim-gbr-v1.0", spec_hash=spec, kind="adapter")

        orchestrator.transition(
            run_id,
            RunState.CORPUS_REGISTERED,
            facts={"sources_without_declaration": []},
            payload={
                "sources": ["legislation.gov.uk"],
                "source_sha256": corpus,
                "curator": CURATOR,
            },
        )
        orchestrator.transition(
            run_id,
            RunState.LICENCE_CLEARED,
            facts={"sources_failing_policy": [], "base_model_cleared": True},
            payload={"policy_version": "2026.01", "evaluation_result": "PASS"},
        )
        orchestrator.transition(
            run_id,
            RunState.CURATED,
            facts={"curation_complete": True, "decontamination_confirmed": True},
            payload={"stage_retention": "24m", "output_sha256": corpus, "token_count": 1024},
        )
        orchestrator.transition(
            run_id,
            RunState.QUEUED,
            facts={"specification_hash": spec, "specification_valid": True},
            payload={"spec_hash": spec, "input_artefact_sha256": corpus},
        )
        orchestrator.transition(
            run_id,
            RunState.TRAINING,
            facts={"scheduler_job_id": "job-1"},
            payload={
                "scheduler_job_id": "job-1",
                "node": "dvalin",
                "placement": {"partition": "adapters", "nodes": 1},
            },
        )
        orchestrator.transition(
            run_id,
            RunState.TRAINED,
            facts={"exit_code": 0, "checkpoint_sha256": checkpoint},
            payload={"checkpoint_sha256": checkpoint, "steps": 100, "final_loss": 0.4},
        )
        transaction.commit()
    return run_id


def _entries(engine: Engine, site_id: str, transition: str) -> list[dict[str, Any]]:
    """Every payload in the chain carrying this transition."""
    scope = SiteScope(site_id)
    with engine.connect() as connection:
        transaction = connection.begin()
        set_site_scope(connection, scope, local=False)
        found = [
            entry.payload
            for entry in LedgerRepository(connection, scope).stream()
            if entry.transition == transition
        ]
        transaction.rollback()
    return found


def test_reconciliation_stages_what_a_running_job_wrote_and_records_it(
    engine: Engine, migrated: str, site: str, tmp_path: Path
) -> None:
    """The whole of row 4's recovery, from the outage to a settled vault."""
    checkpoint = hashlib.sha256(WEIGHTS).hexdigest()
    run_id = _train(engine, site, checkpoint)

    # What the job wrote while the vault was away.
    scratch = tmp_path / "scratch" / str(run_id)
    scratch.mkdir(parents=True)
    (scratch / "adapter.safetensors").write_bytes(WEIGHTS)

    vault = tmp_path / "vault"
    vault.mkdir()
    store = PosixStoreDriver(root=vault, local_site=site)
    hodd.initialise(store)

    # The outage. The directory goes, which is what a dropped NFS mount looks
    # like to a process holding a path into it.
    shutil.rmtree(vault)
    assert (
        vault_admin.run_reconcile(
            migrated,
            vault,
            site,
            tmp_path / "scratch",
            apply=True,
            actor="operator@veldris.internal",
            as_json=False,
        )
        == 1
    ), "reconciliation ran against a vault that was not there"

    # An operator who creates the mount point by hand gets a different refusal,
    # because it is a different problem with a different fix.
    vault.mkdir()
    assert (
        vault_admin.run_reconcile(
            migrated,
            vault,
            site,
            tmp_path / "scratch",
            apply=True,
            actor="operator@veldris.internal",
            as_json=False,
        )
        == 1
    )
    assert not (vault / site).exists(), "a refused reconciliation wrote to the vault anyway"

    # The mount is restored.
    hodd.initialise(store)

    # A dry run reports and changes nothing. Exit 2: something is still owed.
    assert (
        vault_admin.run_reconcile(
            migrated,
            vault,
            site,
            tmp_path / "scratch",
            apply=False,
            actor="operator@veldris.internal",
            as_json=False,
        )
        == 2
    )
    uri = artefact_uri(site, "adapter", str(run_id))
    assert not Path(store.resolve(uri)).exists(), "a dry run staged something"

    # And an applied one stages it, seals it, and settles.
    assert (
        vault_admin.run_reconcile(
            migrated,
            vault,
            site,
            tmp_path / "scratch",
            apply=True,
            actor="operator@veldris.internal",
            as_json=False,
        )
        == 0
    )

    held = Path(store.resolve(uri))
    assert (held / "adapter.safetensors").read_bytes() == WEIGHTS
    assert Ingestor(store, LicenceRegister()).verify(uri) == ()

    # The chain records that it happened, and what it staged. An operator has
    # to be able to establish afterwards that the reconciliation was run.
    staged = _entries(engine, site, hodd.STAGED)
    assert [item["uri"] for item in staged] == [uri]
    assert staged[0]["expectedSha256"] == checkpoint

    summary = _entries(engine, site, hodd.RECONCILED)[-1]
    assert summary["applied"] is True
    assert summary["settled"] is True
    assert summary["site"] == site


def test_scratch_that_does_not_hash_to_the_chain_is_refused_against_a_real_ledger(
    engine: Engine, migrated: str, site: str, tmp_path: Path
) -> None:
    """AC-S8, end to end. The bytes that arrived are what registers, or nothing does."""
    run_id = _train(engine, site, hashlib.sha256(WEIGHTS).hexdigest())

    scratch = tmp_path / "scratch" / str(run_id)
    scratch.mkdir(parents=True)
    (scratch / "adapter.safetensors").write_bytes(b"different bytes entirely")

    vault = tmp_path / "vault"
    vault.mkdir()
    store = PosixStoreDriver(root=vault, local_site=site)
    hodd.initialise(store)

    code = vault_admin.run_reconcile(
        migrated,
        vault,
        site,
        tmp_path / "scratch",
        apply=True,
        actor="operator@veldris.internal",
        as_json=False,
    )

    assert code == 2, "a vault with an unstageable artefact reported itself settled"
    assert not Path(store.resolve(artefact_uri(site, "adapter", str(run_id)))).exists()
    assert (scratch / "adapter.safetensors").is_file(), "the bytes were not left for an operator"

    summary = _entries(engine, site, hodd.RECONCILED)[-1]
    assert summary["settled"] is False
    assert [item["outcome"] for item in summary["findings"]] == ["UNSTAGEABLE"]
