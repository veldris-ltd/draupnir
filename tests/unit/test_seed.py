"""The seeded dataset is exactly what Prompt 0 asks for, and it is reproducible.

These assertions run without a database, so a change that quietly drops a run
state or breaks determinism fails in the unit stage rather than being noticed
by a developer whose screenshots stopped matching everyone else's.
"""

from __future__ import annotations

from draupnir.core.domain.ledger import GENESIS_HASH, compute_entry_hash
from draupnir.core.domain.states import RUN_PHASE_STATES
from scripts.seed import (
    TARGET_LEDGER_ENTRIES,
    TARGET_RELEASES,
    TARGET_RUNS,
    TARGET_SOURCES,
    build,
    projected,
)


def test_the_dataset_has_the_specified_shape() -> None:
    dataset = build()
    assert len(dataset["sites"]) == 2
    assert len(dataset["sources"]) == TARGET_SOURCES == 6
    assert len(dataset["runs"]) == TARGET_RUNS == 14
    assert len(projected(dataset).releases) == TARGET_RELEASES == 1
    assert sum(chain.seq for chain in dataset["chains"].values()) == TARGET_LEDGER_ENTRIES == 400


def test_the_runs_cover_every_run_state() -> None:
    states = {run["state"] for run in build()["runs"]}
    assert states == {str(state) for state in RUN_PHASE_STATES}


def test_the_dataset_is_reproducible() -> None:
    first, second = build(), build()
    for key in ("sites", "sources", "runs"):
        assert first[key] == second[key], f"{key} is not deterministic"
    assert projected(first) == projected(second), "the projected releases are not deterministic"
    for site_id, chain in first["chains"].items():
        assert chain.rows == second["chains"][site_id].rows


def test_the_gates_are_folded_from_evaluations_recorded_in_the_worker_s_shape() -> None:
    """RF-41. The seed inserts no gate row; every one is projected from the chain."""
    import json

    from draupnir.core.domain import gate_results
    from draupnir.core.domain.ledger import LedgerEntry

    dataset = build()
    assert "gate_results" not in dataset, "the seed still carries gate rows beside the chain"

    rows = []
    for chain in dataset["chains"].values():
        rows.extend(
            gate_results.fold(
                LedgerEntry(**{**row, "payload": json.loads(row["payload"])})
                for row in chain.rows or []
            )
        )

    past_evaluation = {
        run["id"]
        for run in dataset["runs"]
        if run["state"] in {"MERGED", "QUANTISED", "AWAITING_APPROVAL", "RELEASED", "QUARANTINED"}
    }
    assert {row.run_id for row in rows} == past_evaluation
    assert all(row.baseline_value is not None and row.margin is not None for row in rows)


def test_every_site_chain_verifies() -> None:
    for site_id, chain in build()["chains"].items():
        expected_prev = GENESIS_HASH
        rows = chain.rows or []
        assert rows, f"{site_id} has no entries"
        for index, row in enumerate(rows, start=1):
            assert row["seq"] == index
            assert row["prev_hash"] == expected_prev
            # The stored payload is JSON text; the chain hashes the structure.
            import json

            assert (
                compute_entry_hash(row["prev_hash"], json.loads(row["payload"]))
                == (row["entry_hash"])
            )
            expected_prev = row["entry_hash"]


def test_the_chains_are_split_across_both_sites() -> None:
    chains = build()["chains"]
    assert set(chains) == {"sindri", "brokkr"}
    assert all(chain.seq > 0 for chain in chains.values())


def test_every_release_names_an_approval_and_an_artefact() -> None:
    """RF-33: folded from the chain, so a release binds to its own run's approval."""
    folded = projected(build())
    approvals = {approval.id: approval for approval in folded.approvals}
    artefacts = {artefact.uri: artefact for artefact in folded.artefacts}
    assert folded.releases, "the seed publishes nothing"
    for release in folded.releases:
        approval = approvals[release.approval_id]
        assert approval.decision == "APPROVED"
        assert approval.artefact_sha256 == release.artefact_sha256
        assert artefacts[release.artefact_uri].sha256 == release.artefact_sha256
        assert release.anchored_at is not None
        # SAD 9A: the two Article 53 artefacts are part of the release record.
        assert release.documents["training-summary"]
        assert release.documents["copyright-policy"]


def test_a_source_carrying_personal_data_names_its_dpia() -> None:
    for source in build()["sources"]:
        if source["personal_data"]:
            assert source["dpia_ref"], source["url"]


def test_identifiers_are_uuid_v7() -> None:
    dataset = build()
    for run in dataset["runs"]:
        assert run["id"].version == 7
    for release in projected(dataset).releases:
        assert release.id.version == 7


def test_the_summary_names_every_entity() -> None:
    from scripts.seed import summarise

    summary = summarise(build())
    for label in ("sites", "sources", "runs", "releases", "ledger entries"):
        assert label in summary
    assert "WARNING" not in summary
