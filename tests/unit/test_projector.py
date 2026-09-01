"""The run registry is a fold over the chain, and folds are testable in memory.

Everything here runs without a database. The same fold is exercised against
PostgreSQL in `tests/integration/test_projection.py`, which is where the
byte-identical rebuild of AC-Q6's sibling requirement is proved; what is proved
here is that the fold itself is total, deterministic, and refuses a chain that
records something the state machine forbids.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from draupnir.core.domain.ledger import LedgerEntry, append
from draupnir.core.domain.projector import (
    REGISTRATION,
    ProjectedRun,
    ProjectionError,
    as_rows,
    project,
)
from draupnir.core.domain.states import RunState

EPOCH = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
RUN = "019cb993-d800-777d-aae5-c00a7ce43658"


class ChainBuilder:
    """Builds a site chain the way the orchestrator would."""

    def __init__(self, site_id: str = "sindri") -> None:
        self.site_id = site_id
        self.entries: list[LedgerEntry] = []

    def add(
        self,
        transition: str,
        payload: dict[str, Any] | None = None,
        *,
        subject_id: str = RUN,
        subject_type: str = "run",
        minutes: int = 10,
    ) -> ChainBuilder:
        previous = self.entries[-1] if self.entries else None
        self.entries.append(
            append(
                previous=previous,
                site_id=self.site_id,
                ts=EPOCH + timedelta(minutes=minutes * (len(self.entries) + 1)),
                actor="system:test",
                subject_type=subject_type,
                subject_id=subject_id,
                transition=transition,
                payload=payload or {},
            )
        )
        return self

    def register(self, name: str = "cim-gbr-v0.1", **overrides: Any) -> ChainBuilder:
        payload = {"name": name, "spec_hash": "a" * 64, "kind": "adapter", **overrides}
        return self.add(REGISTRATION, payload)

    def to_curated(self) -> ChainBuilder:
        return (
            self.add("DRAFT->CORPUS_REGISTERED")
            .add("CORPUS_REGISTERED->LICENCE_CLEARED")
            .add("LICENCE_CLEARED->CURATED")
        )

    def to_training(self, job: str = "421337", node: str = "dvalin") -> ChainBuilder:
        return (
            self.to_curated()
            .add("CURATED->QUEUED")
            .add("QUEUED->TRAINING", {"scheduler_job_id": job, "node": node})
        )

    def one(self) -> ProjectedRun:
        runs = project(self.entries)
        return runs[RUN]


def test_a_registration_alone_projects_a_draft_run() -> None:
    run = ChainBuilder().register().one()
    assert run.state is RunState.DRAFT
    assert run.name == "cim-gbr-v0.1"
    assert run.kind == "adapter"
    assert run.started_at is None
    assert run.ended_at is None
    assert run.retry_count == 0


def test_placement_sets_the_start_time_and_the_scheduler_identity() -> None:
    run = ChainBuilder().register().to_training(job="900001", node="durin").one()
    assert run.state is RunState.TRAINING
    assert run.scheduler_job_id == "900001"
    assert run.node == "durin"
    assert run.started_at is not None
    assert run.ended_at is None


def test_a_terminal_state_sets_the_end_time() -> None:
    run = ChainBuilder().register().to_training().add("TRAINING->FAILED", {"exit_code": 137}).one()
    assert run.state is RunState.FAILED
    assert run.ended_at is not None


def test_a_non_terminal_state_leaves_the_end_time_unset() -> None:
    run = ChainBuilder().register().to_training().add("TRAINING->TRAINED").one()
    assert run.state is RunState.TRAINED
    assert run.ended_at is None


def test_only_the_requeue_transition_spends_retry_budget() -> None:
    chain = (
        ChainBuilder()
        .register()
        .to_training()
        .add("TRAINING->TRAINED")
        .add("TRAINED->EVALUATING")
        .add("EVALUATING->QUEUED", {"failing_gate": "E3"})
    )
    assert chain.one().retry_count == 1

    chain.add("QUEUED->TRAINING").add("TRAINING->TRAINED").add("TRAINED->EVALUATING")
    assert chain.one().retry_count == 1

    chain.add("EVALUATING->QUEUED", {"failing_gate": "E5"})
    assert chain.one().retry_count == 2


def test_the_whole_spine_projects_to_released() -> None:
    run = (
        ChainBuilder()
        .register()
        .to_training()
        .add("TRAINING->TRAINED")
        .add("TRAINED->EVALUATING")
        .add("EVALUATING->MERGED")
        .add("MERGED->QUANTISED")
        .add("QUANTISED->AWAITING_APPROVAL")
        .add("AWAITING_APPROVAL->RELEASED")
        .one()
    )
    assert run.state is RunState.RELEASED
    assert run.started_at is not None
    assert run.ended_at is not None


def test_entries_about_other_subjects_are_ignored() -> None:
    chain = (
        ChainBuilder()
        .register()
        .add("ANCHOR_SUBMITTED", subject_type="site", subject_id="sindri")
        .add("PLUGIN_VERIFIED", subject_type="plugin", subject_id="hamarr.llamafactory")
        .to_curated()
    )
    runs = project(chain.entries)
    assert len(runs) == 1
    assert runs[RUN].state is RunState.CURATED


def test_two_runs_project_independently() -> None:
    other = "019cb993-d800-777d-aae5-c00a7ce43659"
    chain = ChainBuilder().register()
    chain.add(REGISTRATION, {"name": "b", "spec_hash": "b" * 64, "kind": "merge"}, subject_id=other)
    chain.to_curated()

    runs = project(chain.entries)
    assert runs[RUN].state is RunState.CURATED
    assert runs[other].state is RunState.DRAFT
    assert runs[other].kind == "merge"


def test_the_fold_is_deterministic() -> None:
    chain = ChainBuilder().register().to_training()
    assert as_rows(project(chain.entries).values()) == as_rows(project(chain.entries).values())


def test_rows_are_ordered_by_identifier() -> None:
    chain = ChainBuilder().register()
    for suffix in ("a", "b", "c"):
        chain.add(
            REGISTRATION,
            {"name": suffix, "spec_hash": "c" * 64, "kind": "adapter"},
            subject_id=f"0000000{suffix}",
        )
    rows = as_rows(project(chain.entries).values())
    assert [row["id"] for row in rows] == sorted(row["id"] for row in rows)


# ---------------------------------------------------------------------------
# A chain that records the impossible
# ---------------------------------------------------------------------------


def test_a_transition_on_an_unregistered_run_is_refused() -> None:
    chain = ChainBuilder().add("DRAFT->CORPUS_REGISTERED")
    with pytest.raises(ProjectionError, match="never registered"):
        project(chain.entries)


def test_registering_the_same_run_twice_is_refused() -> None:
    chain = ChainBuilder().register().register()
    with pytest.raises(ProjectionError, match="registered twice"):
        project(chain.entries)


def test_a_registration_without_its_fields_is_refused() -> None:
    chain = ChainBuilder().add(REGISTRATION, {"name": "cim-gbr-v0.1"})
    with pytest.raises(ProjectionError, match="spec_hash"):
        project(chain.entries)


def test_a_transition_not_in_the_table_is_refused() -> None:
    chain = ChainBuilder().register().add("DRAFT->RELEASED")
    with pytest.raises(ProjectionError, match="not a"):
        project(chain.entries)


def test_a_transition_from_the_wrong_state_is_refused() -> None:
    # The chain says the run went CURATED -> QUEUED while it is still in DRAFT.
    chain = ChainBuilder().register().add("CURATED->QUEUED")
    with pytest.raises(ProjectionError, match=r"which is in (RunState\.)?DRAFT"):
        project(chain.entries)


def test_an_unparseable_transition_is_refused() -> None:
    chain = ChainBuilder().register().add("NONSENSE")
    with pytest.raises(ProjectionError):
        project(chain.entries)


def test_an_empty_chain_projects_nothing() -> None:
    assert project([]) == {}
    assert as_rows([]) == []
