"""The worker's corpus half, without a database. RF-43.

A run submitted through the API stayed at DRAFT, because only the demonstration
procedure took SAD 6.1's corpus steps. These pin what the two stages do for each
thing the chain can hold: registered sources or none, a recorded specification
or none, an installed policy driver or none, and each of the policy's answers.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from draupnir.core.application.orchestrator import RunFacts
from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry
from draupnir.core.domain.states import RunState
from draupnir.gleipnir.licence import CURRENT, driver
from draupnir.hamarr import tiers
from draupnir.worker import stages
from draupnir.worker.loop import DEFAULT_POLICY_DRIVER, WorkerSettings
from tests.specs import submittable_mapping

pytestmark = pytest.mark.unit

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def specification(jurisdiction: str) -> dict[str, Any]:
    spec = submittable_mapping()
    spec["metadata"] = {**spec["metadata"], "jurisdiction": jurisdiction}
    spec["spec"]["base"] = {**spec["spec"]["base"], "artefact": tiers.base_artefact(jurisdiction)}
    return spec


def run(state: RunState, jurisdiction: str = "NZL", *, recorded: bool = True) -> RunFacts:
    return RunFacts(
        run_id=uuid4(),
        name=f"cim-{jurisdiction.lower()}-v1.0",
        state=state,
        submitter="operator@veldris.internal",
        spec_hash="a" * 64,
        specification=specification(jurisdiction) if recorded else None,
    )


def registration(jurisdiction: str, licence: str, *, personal_data: bool = False) -> LedgerEntry:
    return LedgerEntry(
        id=uuid4(),
        site_id="sindri",
        seq=1,
        prev_hash=GENESIS_HASH,
        entry_hash="b" * 64,
        ts=AT,
        actor="curator@veldris.internal",
        subject_type="source",
        subject_id=str(uuid4()),
        transition="registered",
        payload={
            "jurisdiction": jurisdiction,
            "url": f"https://{jurisdiction.lower()}.example/{licence}",
            "licence_spdx": licence,
            "attribution_required": True,
            "personal_data": personal_data,
            "dpia_ref": "DPIA-1" if personal_data else None,
            "sha256": uuid4().hex * 2,
            "retrieved_at": AT.isoformat(),
        },
    )


class Orchestrator:
    """Records the transitions a stage asks for, and holds the chain's sources."""

    actor = "worker@veldris.internal"

    def __init__(self, *registered: LedgerEntry) -> None:
        self.registered = registered
        self.moved: list[tuple[UUID, RunState, dict[str, Any], dict[str, Any]]] = []

    def entries_of_type(self, subject_type: str) -> tuple[LedgerEntry, ...]:
        return self.registered if subject_type == "source" else ()

    def transition(
        self, run_id: UUID, target: RunState, *, facts: dict[str, Any], payload: dict[str, Any]
    ) -> Any:
        self.moved.append((run_id, target, facts, payload))
        return SimpleNamespace(state=target)


def context(orchestrator: Orchestrator, *, policy: Any = driver) -> stages.Context:
    return stages.Context(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        scheduler=None,
        scratch=None,  # type: ignore[arg-type]
        policy=policy,
    )


def test_a_draft_run_with_registered_sources_registers_its_corpus() -> None:
    orchestrator = Orchestrator(registration("NZL", "CC-BY-4.0"), registration("FJI", "MIT"))
    facts = run(RunState.DRAFT)

    outcome = stages.advance(context(orchestrator), facts)

    assert outcome.result is stages.Result.MOVED
    assert outcome.state is RunState.CORPUS_REGISTERED
    ((_, target, recorded, payload),) = orchestrator.moved
    assert target is RunState.CORPUS_REGISTERED
    assert recorded == {"sources_without_declaration": []}
    assert len(payload["sources"]) == 1, "another jurisdiction's source was registered too"
    assert payload["curator"] == Orchestrator.actor


def test_a_draft_run_whose_jurisdiction_has_no_source_waits_recording_nothing() -> None:
    orchestrator = Orchestrator(registration("FJI", "MIT"))
    outcome = stages.advance(context(orchestrator), run(RunState.DRAFT))

    assert outcome.result is stages.Result.IDLE
    assert "no source is registered for NZL" in outcome.detail
    assert orchestrator.moved == []


@pytest.mark.parametrize("state", [RunState.DRAFT, RunState.CORPUS_REGISTERED])
def test_a_run_with_no_recorded_specification_is_left_alone(state: RunState) -> None:
    orchestrator = Orchestrator(registration("NZL", "CC-BY-4.0"))
    outcome = stages.advance(context(orchestrator), run(state, recorded=False))

    assert outcome.result is stages.Result.IDLE
    assert orchestrator.moved == []


def test_an_unreadable_specification_is_left_alone() -> None:
    broken = replace(run(RunState.DRAFT), specification={"metadata": {}})
    outcome = stages.advance(context(Orchestrator(registration("NZL", "MIT"))), broken)
    assert outcome.result is stages.Result.IDLE


def test_a_permitted_corpus_is_cleared_under_the_installed_driver_s_version() -> None:
    orchestrator = Orchestrator(registration("NZL", "CC-BY-4.0"))
    outcome = stages.advance(context(orchestrator), run(RunState.CORPUS_REGISTERED))

    assert outcome.state is RunState.LICENCE_CLEARED
    ((_, target, recorded, payload),) = orchestrator.moved
    assert target is RunState.LICENCE_CLEARED
    assert recorded == {"sources_failing_policy": [], "base_model_cleared": True}
    assert payload["policy_version"] == CURRENT.version
    assert payload["decisions"][-1]["subject"] == "base_model"


def test_a_refused_licence_quarantines_the_run_naming_the_rule() -> None:
    orchestrator = Orchestrator(registration("NZL", "CC-BY-NC-4.0"))
    outcome = stages.advance(context(orchestrator), run(RunState.CORPUS_REGISTERED))

    assert outcome.state is RunState.QUARANTINED
    assert orchestrator.moved[0][3]["rule"] == "licence-refused"


def test_a_tier_a_run_is_quarantined_on_its_declared_base() -> None:
    orchestrator = Orchestrator(registration("GBR", "OGL-UK-3.0"))
    outcome = stages.advance(context(orchestrator), run(RunState.CORPUS_REGISTERED, "GBR"))

    assert outcome.state is RunState.QUARANTINED
    assert orchestrator.moved[0][3]["failing_source"] == tiers.base_artefact("GBR")


def test_a_decision_owed_an_approval_defers_recording_nothing() -> None:
    orchestrator = Orchestrator(registration("NZL", "CC-BY-4.0", personal_data=True))
    outcome = stages.advance(context(orchestrator), run(RunState.CORPUS_REGISTERED))

    assert outcome.result is stages.Result.DEFERRED
    assert "requires an approval" in outcome.detail
    assert orchestrator.moved == []


def test_no_installed_policy_driver_defers_the_decision() -> None:
    orchestrator = Orchestrator(registration("NZL", "CC-BY-4.0"))
    outcome = stages.advance(context(orchestrator, policy=None), run(RunState.CORPUS_REGISTERED))

    assert outcome.result is stages.Result.DEFERRED
    assert "draupnir.policy" in outcome.detail
    assert orchestrator.moved == []


def test_the_policy_driver_is_named_by_the_environment_and_defaults_to_gleipnir_s() -> None:
    """Asserted against the installer's text too, as RF-28 taught (see test_worker.py)."""
    installer = (Path(__file__).parents[2] / "deploy" / "install.sh").read_text(encoding="utf-8")
    assert "DRAUPNIR_POLICY_DRIVER=${POLICY_DRIVER}" in installer
    assert DEFAULT_POLICY_DRIVER == "gleipnir.licence/v1"
    assert WorkerSettings.from_environment({}).policy_driver == DEFAULT_POLICY_DRIVER
    named = WorkerSettings.from_environment({"DRAUPNIR_POLICY_DRIVER": " gleipnir.spdx/v1 "})
    assert named.policy_driver == "gleipnir.spdx/v1"
