"""Gate definitions, and the Article 53 copyright policy.

GLEIPNIR defines a gate; RAUN executes it (SAD 5.2). So what is tested here is
what passing means, not what is measured.

The copyright policy is the artefact SAD 9A.2 requires GLEIPNIR to produce:
"Machine readable copyright policy, versioned, referenced by every release."
Decision S11 requires it to be generated from the pipeline record rather than
authored, and the test that matters is that it cannot disagree with the policy
the engine actually applies -- because a compliance document that has drifted
from the system is worse than none, and nobody re-reads it to find out.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from draupnir.gleipnir import copyright, gates, licence
from draupnir.gleipnir.gates import Comparison, Gate, GateError
from draupnir.interfaces.types import Verdict

ISSUED = datetime(2026, 1, 1, tzinfo=UTC)
SUITE_VERSION = "raun-suite/2026.02"


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def test_the_suite_is_e1_to_e6() -> None:
    assert [gate.id for gate in gates.SUITE] == ["E1", "E2", "E3", "E4", "E5", "E6"]


def test_every_gate_in_the_suite_blocks() -> None:
    # SAD 6.1: EVALUATING to MERGED requires that gates E1 to E6 pass, without
    # qualification.
    assert all(gate.blocking for gate in gates.SUITE)


def test_every_gate_states_what_it_requires() -> None:
    for gate in gates.SUITE:
        assert gate.statement
        assert gate.comparison in set(Comparison)


def test_a_regression_within_the_margin_passes() -> None:
    e1 = gates.get("E1")
    assert e1.holds(value=0.69, baseline=0.70)  # 0.01 down, margin 0.02
    assert not e1.holds(value=0.67, baseline=0.70)  # 0.03 down


def test_an_improvement_gate_requires_actual_improvement() -> None:
    e2 = gates.get("E2")
    assert e2.holds(value=0.72, baseline=0.70)
    assert not e2.holds(value=0.70, baseline=0.70)  # equal is not better


def test_an_absolute_floor_ignores_the_baseline() -> None:
    e4 = gates.get("E4")
    assert e4.holds(value=0.61, baseline=None)
    assert not e4.holds(value=0.59, baseline=None)


def test_an_absolute_ceiling_catches_contamination() -> None:
    e6 = gates.get("E6")
    assert e6.holds(value=0.0005, baseline=None)
    assert not e6.holds(value=0.01, baseline=None)


def test_a_relative_gate_without_a_baseline_is_an_error_not_a_pass() -> None:
    # A gate nobody could compare is an unknown, and an unknown is not a pass.
    with pytest.raises(GateError, match="not a pass"):
        gates.get("E1").holds(value=0.70, baseline=None)


def test_an_outcome_records_the_margin() -> None:
    outcome = gates.get("E1").evaluate(0.69, 0.70, SUITE_VERSION)
    assert outcome.gate == "E1"
    assert outcome.suite_version == SUITE_VERSION
    assert outcome.margin == pytest.approx(-0.01)
    assert outcome.passed


def test_a_full_pass_admits_the_merge() -> None:
    measurements = {"E1": 0.70, "E2": 0.72, "E3": 0.70, "E4": 0.65, "E5": 0.80, "E6": 0.0}
    baselines = {"E1": 0.70, "E2": 0.70, "E3": 0.70, "E5": 0.80}

    result = gates.evaluate(measurements, baselines, suite_version=SUITE_VERSION)
    assert result.passed
    assert result.failing == ()
    assert len(result.as_payload()["gates"]) == 6


def test_one_failing_gate_stops_the_merge_and_is_named() -> None:
    measurements = {"E1": 0.60, "E2": 0.72, "E3": 0.70, "E4": 0.65, "E5": 0.80, "E6": 0.0}
    baselines = {"E1": 0.70, "E2": 0.70, "E3": 0.70, "E5": 0.80}

    result = gates.evaluate(measurements, baselines, suite_version=SUITE_VERSION)
    assert not result.passed
    assert result.failing == ("E1",)
    assert result.blocking_failures == ("E1",)
    assert "general capability" in gates.describe(result.failing)


def test_a_gate_nobody_ran_is_a_failure_not_an_omission() -> None:
    result = gates.evaluate({"E1": 0.70}, {"E1": 0.70}, suite_version=SUITE_VERSION)
    assert not result.passed
    assert set(result.failing) == {"E2", "E3", "E4", "E5", "E6"}


def test_an_unknown_gate_is_refused_naming_the_suite() -> None:
    with pytest.raises(GateError, match="E1, E2"):
        gates.get("E99")


def test_a_jurisdiction_suite_registers_its_own_gate() -> None:
    """SAD 10.1: a bespoke evaluation is a configuration change, not a core one."""
    bespoke = Gate(
        id="E7-cim-gbr",
        statement="jurisdiction citation accuracy reaches the floor",
        comparison=Comparison.AT_LEAST,
        margin=0.75,
        blocking=False,
    )
    gates.register(bespoke)
    try:
        assert gates.get("E7-cim-gbr") is bespoke
        assert any(item["id"] == "E7-cim-gbr" for item in gates.registry())

        with pytest.raises(GateError, match="already defined"):
            gates.register(bespoke)

        # Non-blocking: recorded and reported, but it does not stop a run.
        # That is how a new evaluation is introduced before anyone is willing
        # to fail a release on it.
        result = gates.evaluate(
            {"E7-cim-gbr": 0.10}, {}, suite_version=SUITE_VERSION, gates=[bespoke]
        )
        assert result.failing == ("E7-cim-gbr",)
        assert result.blocking_failures == ()
        assert result.passed
    finally:
        gates.BY_ID.pop("E7-cim-gbr", None)


# ---------------------------------------------------------------------------
# The copyright policy
# ---------------------------------------------------------------------------


def test_the_policy_is_machine_readable_and_versioned() -> None:
    policy = copyright.issue("copyright/2026.01", ISSUED)
    document = json.loads(policy.to_json())

    assert document["schema"] == copyright.SCHEMA
    assert document["version"] == "copyright/2026.01"
    assert document["instrument"].startswith("Regulation (EU) 2024/1689")
    assert document["issuedAt"] == ISSUED.isoformat()


def test_the_policy_states_the_text_and_data_mining_reservation() -> None:
    # SAD 9A.2 names it explicitly, and a policy that only lists licences does
    # not answer the question Article 4(3) asks.
    document = copyright.issue("copyright/2026.01", ISSUED).as_mapping()
    reservation = document["textAndDataMiningReservation"]
    assert "Article 4(3)" in reservation
    assert "not ingested" in reservation


def test_the_policy_cannot_disagree_with_the_engine() -> None:
    """Decision S11: generated from the record, never authored separately.

    The clauses are rendered from the same `Policy` object the engine
    evaluates. There is no second copy to fall out of step.
    """
    policy = copyright.issue("copyright/2026.01", ISSUED)
    rendered = policy.as_mapping()["licencePolicy"]

    assert rendered["version"] == licence.CURRENT.version
    assert {clause["id"] for clause in rendered["clauses"]} == {
        rule.id for rule in licence.CURRENT.rules
    }
    assert set(rendered["permitted"]) == {
        item
        for rule in licence.CURRENT.rules
        if rule.verdict is Verdict.PERMIT
        for item in rule.licences
    }


def test_the_digest_changes_when_the_policy_does() -> None:
    # A version that can be rewritten says nothing, so a release records both.
    current = copyright.issue("copyright/2026.01", ISSUED)
    previous = copyright.issue("copyright/2025.11", ISSUED, licence_policy=licence.PREVIOUS)
    assert current.digest() != previous.digest()
    assert len(current.digest()) == 64


def test_the_digest_is_stable_for_the_same_policy() -> None:
    first = copyright.issue("copyright/2026.01", ISSUED)
    second = copyright.issue("copyright/2026.01", ISSUED)
    assert first.digest() == second.digest()


def test_a_release_references_the_policy_by_version_and_digest() -> None:
    reference = copyright.issue("copyright/2026.01", ISSUED).reference(
        "hodd://sindri/releases/cim-gbr-v0.1"
    )
    assert reference["version"] == "copyright/2026.01"
    assert reference["uri"].endswith("copyright-policy-copyright/2026.01.json")
    assert len(reference["sha256"]) == 64


def test_a_historical_release_keeps_the_policy_in_force_at_its_date() -> None:
    """SAD 10.2: existing releases keep the version in force at their release date."""
    historical = copyright.for_release("gleipnir-licence/2025.11", ISSUED)
    assert historical.licence_policy is licence.PREVIOUS

    current = copyright.for_release("gleipnir-licence/2026.01", ISSUED)
    assert current.licence_policy is licence.CURRENT

    # Re-rendering a historical release under the current policy would quietly
    # restate its compliance position, so it is refused rather than guessed.
    assert historical.digest() != current.digest()


def test_an_unknown_policy_version_is_refused() -> None:
    with pytest.raises(KeyError, match="never"):
        copyright.for_release("gleipnir-licence/2019.01", ISSUED)


def test_a_naive_issue_date_is_refused() -> None:
    with pytest.raises(copyright.CopyrightPolicyError, match="explicit offset"):
        copyright.issue("copyright/2026.01", datetime(2026, 1, 1))  # noqa: DTZ001


def test_the_commitments_cover_what_the_register_guarantees() -> None:
    commitments = " ".join(copyright.COMMITMENTS)
    assert "SPDX" in commitments
    assert "Silence is not permission" in commitments
    assert "retained indefinitely" in commitments
