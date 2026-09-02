"""RAUN: suites, baselines, regression, and the facts the guards read.

AC-F7: "Gates E1 to E6 execute against an adapter, results are recorded with
baseline and margin, and a failure requeues the run automatically within its
retry budget."

AC-F9's half of the bargain is here too: the facts that tell the QUANTISED to
AWAITING_APPROVAL guard whether every built format was actually evaluated.

The judge used throughout is GLEIPNIR's, reached through
`draupnir.api.assurance` -- the composition layer. RAUN and GLEIPNIR are
independent siblings that cannot import each other, and the tests wire them
together the same way the running system does.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from draupnir.api.assurance import gleipnir_judge
from draupnir.core.domain.evidence import Evidence, EvidenceLog
from draupnir.raun import baselines as baseline_module
from draupnir.raun import regression, suites, transitions
from draupnir.raun.baselines import BaselineError, BaselineRegistry, NoBaselineError
from draupnir.raun.suites import GENERAL, NoSuiteError, Suite, SuiteError

ADAPTER = "a" * 64
SUBSTRATE = "5" * 64
MERGED = "e" * 64
AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

#: Comfortably clear of every gate: E4 above its 0.60 floor, E6 below its
#: 0.001 ceiling, the rest above a 0.75 baseline.
GOOD = {"E1": 0.80, "E2": 0.86, "E3": 0.79, "E4": 0.71, "E5": 0.77, "E6": 0.0002}
BASE = {"E1": 0.78, "E2": 0.74, "E3": 0.78, "E4": 0.62, "E5": 0.76, "E6": 0.0}


@pytest.fixture
def registry() -> BaselineRegistry:
    """A registry holding the substrate baseline."""
    holder = BaselineRegistry()
    holder.capture(
        baseline_module.capture(
            artefact_sha256=SUBSTRATE,
            artefact_kind="substrate",
            suite="general-core",
            suite_version="2026.01",
            measurements=BASE,
            captured_at=AT,
            label="MIDGARD-CORE",
        )
    )
    return holder


def judge_with(
    measurements: dict[str, float], registry: BaselineRegistry, **kw: object
) -> Evidence:
    """Evaluate one artefact through the real judge."""
    return suites.evaluate(
        suite=GENERAL,
        judge=gleipnir_judge,
        artefact_sha256=str(kw.get("sha", ADAPTER)),
        artefact_kind=str(kw.get("kind", "adapter")),
        measurements=measurements,
        baselines=registry,
        evaluated_at=AT,
        export_format=kw.get("fmt"),  # type: ignore[arg-type]
    )


# -- AC-F7: gates execute, results carry baseline and margin ----------------


def test_gates_e1_to_e6_execute_against_an_adapter(registry: BaselineRegistry) -> None:
    """AC-F7, first clause."""
    result = judge_with(GOOD, registry)

    assert result.passed
    assert {item.gate for item in result.outcomes} == {"E1", "E2", "E3", "E4", "E5", "E6"}


def test_results_are_recorded_with_baseline_and_margin(registry: BaselineRegistry) -> None:
    """AC-F7, second clause, and SAD 7.1's `gate_result` fields."""
    result = judge_with(GOOD, registry)
    e1 = next(item for item in result.outcomes if item.gate == "E1")

    assert e1.baseline_value == pytest.approx(0.78)
    assert e1.margin == pytest.approx(0.02)
    assert result.baseline_sha256 == SUBSTRATE


def test_a_failing_gate_requeues_within_budget(registry: BaselineRegistry) -> None:
    """AC-F7, third clause: the guard's facts drive the requeue."""
    failing = {**GOOD, "E4": 0.40}  # Below E4's absolute floor of 0.60.
    result = judge_with(failing, registry)

    facts = transitions.evaluation_facts(result, retry_budget_remaining=2)

    assert not result.passed
    assert facts["failing_gates"] == ["E4"]
    assert facts["retry_budget_remaining"] == 2
    assert "E4" in str(facts["requeue_reason"])


def test_an_exhausted_budget_leaves_the_guard_to_refuse(registry: BaselineRegistry) -> None:
    """RAUN reports; the core's guard does the budget arithmetic."""
    result = judge_with({**GOOD, "E4": 0.40}, registry)

    facts = transitions.evaluation_facts(result, retry_budget_remaining=0)

    assert facts["failing_gates"] == ["E4"]
    assert facts["retry_budget_remaining"] == 0


def test_a_gate_nobody_measured_is_a_gate_nobody_passed(registry: BaselineRegistry) -> None:
    """An omitted measurement fails rather than being skipped."""
    result = judge_with({key: value for key, value in GOOD.items() if key != "E3"}, registry)

    assert not result.passed
    assert "E3" in result.failing


# -- suites -----------------------------------------------------------------


def test_a_suite_resolves_for_the_artefact_type() -> None:
    """The TRAINED to EVALUATING guard of SAD 6.1."""
    resolved = suites.default_registry().resolve("adapter")

    assert [item.name for item in resolved] == ["general-core"]


def test_a_jurisdiction_suite_is_returned_alongside_the_general_one() -> None:
    """SAD 6.2's example names `general-core` and `cim-gbr` together."""
    holder = suites.default_registry()
    holder.register(
        Suite(
            name="cim-gbr",
            version="2026.01",
            applies_to=frozenset({"adapter", "merged"}),
            gates=("E2", "E4"),
            jurisdiction="GBR",
        )
    )

    resolved = holder.resolve("adapter", "GBR")

    assert [item.name for item in resolved] == ["general-core", "cim-gbr"]
    assert [item.name for item in holder.resolve("adapter", "KEN")] == ["general-core"]


def test_an_artefact_kind_with_no_suite_stops_the_run() -> None:
    """Not a fallback: the wrong suite produces numbers that mean something else."""
    holder = suites.SuiteRegistry()
    holder.register(
        Suite(
            name="merged-only",
            version="1",
            applies_to=frozenset({"merged"}),
            gates=("E1",),
        )
    )

    with pytest.raises(NoSuiteError, match="no evaluation suite is registered"):
        holder.resolve("adapter")


def test_a_suite_version_is_immutable_once_registered() -> None:
    """Changing tasks under a fixed version rewrites every historical result."""
    holder = suites.default_registry()

    with pytest.raises(SuiteError, match="immutable once results exist"):
        holder.register(
            Suite(
                name="general-core",
                version="2026.01",
                applies_to=frozenset({"adapter"}),
                gates=("E1",),
            )
        )


def test_a_suite_feeding_no_gates_is_refused() -> None:
    with pytest.raises(SuiteError, match="decides nothing"):
        Suite(name="empty", version="1", applies_to=frozenset({"adapter"}), gates=())


def test_evaluating_with_the_wrong_suite_is_refused(registry: BaselineRegistry) -> None:
    narrow = Suite(
        name="general-core",
        version="2026.02",
        applies_to=frozenset({"merged"}),
        gates=("E1",),
    )

    with pytest.raises(SuiteError, match="does not evaluate a adapter"):
        suites.evaluate(
            suite=narrow,
            judge=gleipnir_judge,
            artefact_sha256=ADAPTER,
            artefact_kind="adapter",
            measurements=GOOD,
            baselines=registry,
            evaluated_at=AT,
        )


# -- baselines --------------------------------------------------------------


def test_a_baseline_is_identified_by_the_bytes_it_was_measured_on() -> None:
    holder = BaselineRegistry()
    holder.capture(
        baseline_module.capture(
            artefact_sha256=SUBSTRATE,
            artefact_kind="substrate",
            suite="general-core",
            suite_version="2026.01",
            measurements=BASE,
            captured_at=AT,
        )
    )

    assert holder.resolve("general-core", "substrate").artefact_sha256 == SUBSTRATE


def test_a_missing_baseline_is_not_a_pass() -> None:
    with pytest.raises(NoBaselineError, match="not a pass, it is an unknown"):
        BaselineRegistry().resolve("general-core", "substrate")


def test_a_baseline_is_not_silently_overwritten(registry: BaselineRegistry) -> None:
    """Re-baselining against the model that regressed hides the regression."""
    replacement = baseline_module.capture(
        artefact_sha256=MERGED,
        artefact_kind="substrate",
        suite="general-core",
        suite_version="2026.01",
        measurements={"E1": 0.5},
        captured_at=AT,
    )

    with pytest.raises(BaselineError, match="how a regression vanishes"):
        registry.capture(replacement)

    registry.capture(replacement, replace_existing=True)
    assert registry.resolve("general-core", "substrate").artefact_sha256 == MERGED


def test_recapturing_the_same_baseline_is_idempotent(registry: BaselineRegistry) -> None:
    same = baseline_module.capture(
        artefact_sha256=SUBSTRATE,
        artefact_kind="substrate",
        suite="general-core",
        suite_version="2026.01",
        measurements=BASE,
        captured_at=AT,
    )

    assert registry.capture(same).artefact_sha256 == SUBSTRATE


def test_a_baseline_with_no_measurements_is_refused() -> None:
    with pytest.raises(BaselineError, match="not a baseline"):
        baseline_module.capture(
            artefact_sha256=SUBSTRATE,
            artefact_kind="substrate",
            suite="general-core",
            suite_version="2026.01",
            measurements={},
            captured_at=AT,
        )


# -- regression -------------------------------------------------------------


def test_a_first_release_has_nothing_to_regress_against(registry: BaselineRegistry) -> None:
    current = judge_with(GOOD, registry)

    comparison = regression.compare(current, None, compared_at=AT)

    assert comparison.first_release
    assert not comparison.regressed
    assert "nothing to compare against" in comparison.describe()


def test_a_fall_beyond_tolerance_is_a_regression(registry: BaselineRegistry) -> None:
    previous = judge_with(GOOD, registry)
    current = judge_with({**GOOD, "E1": 0.74}, registry, sha=MERGED, kind="merged")

    comparison = regression.compare(current, previous, compared_at=AT)

    assert comparison.regressed
    assert [item.gate for item in comparison.regressions] == ["E1"]
    assert "regression against" in comparison.describe()


def test_a_move_within_tolerance_is_not_a_regression(registry: BaselineRegistry) -> None:
    previous = judge_with(GOOD, registry)
    current = judge_with({**GOOD, "E1": 0.798}, registry, sha=MERGED, kind="merged")

    comparison = regression.compare(current, previous, compared_at=AT)

    assert not comparison.regressed


def test_a_gate_the_previous_release_never_measured_is_not_a_fall(
    registry: BaselineRegistry,
) -> None:
    """Reporting it as a fall from zero would be a lie about a model nobody ran."""
    previous = judge_with({key: value for key, value in GOOD.items() if key != "E5"}, registry)
    current = judge_with(GOOD, registry, sha=MERGED, kind="merged")

    comparison = regression.compare(current, previous, compared_at=AT)

    assert "E5" not in {item.gate for item in comparison.movements}
    assert not comparison.regressed


def test_the_trend_is_ordered_oldest_first(registry: BaselineRegistry) -> None:
    """SAD 8.3's per jurisdiction trend."""
    import dataclasses

    first = judge_with(GOOD, registry)
    second = dataclasses.replace(
        first, evaluated_at=AT + timedelta(days=30), measurements={**GOOD, "E1": 0.85}
    )

    series = regression.trend([second, first], "E1")

    assert [value for _, value in series] == [0.80, 0.85]


def test_the_latest_released_evidence_is_the_most_recent(registry: BaselineRegistry) -> None:
    import dataclasses

    first = judge_with(GOOD, registry)
    second = dataclasses.replace(first, evaluated_at=AT + timedelta(days=1))

    assert regression.latest_released([first, second]) is second
    assert regression.latest_released([]) is None


# -- AC-F9: every built format is evaluated ---------------------------------


def quantised(fmt: str, sha: str, *, passed: bool = True) -> Evidence:
    """Evidence for one quantised build."""
    return Evidence(
        artefact_sha256=sha,
        artefact_kind="quantised",
        outcomes=(),
        passed=passed,
        suite="general-core",
        suite_version="2026.01",
        evaluated_at=AT,
        format=fmt,
    )


def test_every_built_format_with_passing_evidence_clears_the_guard() -> None:
    log = (
        EvidenceLog()
        .with_evidence(quantised("nvfp4", "1" * 64))
        .with_evidence(quantised("gguf-q4km", "2" * 64))
        .with_evidence(quantised("mlx4", "3" * 64))
    )

    facts = transitions.quantisation_facts(log, built_formats=["nvfp4", "gguf-q4km", "mlx4"])

    assert facts["formats_failing"] == []
    assert facts["formats_unevaluated"] == []


def test_a_format_built_but_never_evaluated_is_reported_as_failing() -> None:
    """AC-F9: an unevaluated build must not be invisible to the guard."""
    log = EvidenceLog().with_evidence(quantised("nvfp4", "1" * 64))

    facts = transitions.quantisation_facts(log, built_formats=["nvfp4", "mlx4"])

    assert facts["formats_failing"] == ["mlx4"]
    assert facts["formats_unevaluated"] == ["mlx4"]


def test_a_format_that_failed_its_regate_is_reported_as_failing() -> None:
    log = EvidenceLog().with_evidence(quantised("mlx4", "3" * 64, passed=False))

    facts = transitions.quantisation_facts(log, built_formats=["mlx4"])

    assert facts["formats_failing"] == ["mlx4"]
    assert facts["formats_unevaluated"] == []


def test_naming_no_built_formats_is_refused_rather_than_passing_vacuously() -> None:
    """An empty set satisfies "every format was evaluated" and means nothing."""
    with pytest.raises(transitions.TransitionFactError, match="pass vacuously"):
        transitions.quantisation_facts(EvidenceLog(), built_formats=[])
