"""AC-F8: the weight sweep, and route A/B handling.

AC-F8: "A merge executes with a weight sweep of at least five points, and each
point's gate results are comparable side by side in the console."

The comparison is tested as an object rather than as a rendering, because the
requirement is really about the collection: five runs sharing a naming
convention are not a sweep, and a sixth with a typo in its name is either
silently missing or silently included.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from draupnir.brisingamen import merge, routes, sweep
from draupnir.brisingamen.merge import MergeError, Method
from draupnir.brisingamen.routes import Route, RouteError
from draupnir.brisingamen.sweep import SweepError
from draupnir.core.domain.evidence import Evidence
from draupnir.interfaces.types import GateOutcome

BASE = "b" * 64
ADAPTER = "a" * 64
AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def merged_sha(index: int) -> str:
    """A distinct hash per sweep point."""
    return f"{index:x}" * 64


def evidence_for(index: int, *, score: float, passed: bool = True) -> Evidence:
    """Gate evidence for one merged point."""
    return Evidence(
        artefact_sha256=merged_sha(index),
        artefact_kind="merged",
        outcomes=(
            GateOutcome(
                gate="E2",
                suite_version="2026.01",
                value=score,
                baseline_value=0.74,
                margin=round(score - 0.74, 6),
                passed=passed,
            ),
        ),
        passed=passed,
        suite="general-core",
        suite_version="2026.01",
        evaluated_at=AT,
        measurements={"E2": score},
    )


@pytest.fixture
def five() -> sweep.Sweep:
    """A five point linear sweep, unevaluated."""
    return sweep.linear(method="linear", base_sha256=BASE, adapter_sha256=ADAPTER, points=5)


@pytest.fixture
def evaluated(five: sweep.Sweep) -> sweep.Sweep:
    """The same sweep with every point built and gated."""
    scores = [0.76, 0.81, 0.86, 0.84, 0.79]
    current = five
    for index, (point, score) in enumerate(zip(five.points, scores, strict=True), start=1):
        current = current.with_result(
            point.parameters,
            artefact_sha256=merged_sha(index),
            evidence=evidence_for(index, score=score),
        )
    return current


# -- AC-F8 ------------------------------------------------------------------


def test_a_sweep_has_at_least_five_points(five: sweep.Sweep) -> None:
    """AC-F8's floor, enforced on construction."""
    assert five.size == 5
    assert [point.label for point in five.points] == [
        "weight=0.2",
        "weight=0.4",
        "weight=0.6",
        "weight=0.8",
        "weight=1",
    ]


def test_a_sweep_with_fewer_than_five_points_is_refused() -> None:
    with pytest.raises(SweepError, match="at least 5 points"):
        sweep.build(
            method="linear",
            base_sha256=BASE,
            adapter_sha256=ADAPTER,
            parameters=[{"weight": 0.5}, {"weight": 0.7}],
        )

    with pytest.raises(SweepError, match="at least 5 points"):
        sweep.linear(method="linear", base_sha256=BASE, adapter_sha256=ADAPTER, points=3)


def test_a_repeated_point_is_refused() -> None:
    """A repeated point is a wasted merge and an ambiguous matrix row."""
    with pytest.raises(SweepError, match="repeats the point"):
        sweep.build(
            method="linear",
            base_sha256=BASE,
            adapter_sha256=ADAPTER,
            parameters=[{"weight": w} for w in (0.2, 0.4, 0.4, 0.8, 1.0)],
        )


def test_every_point_is_comparable_side_by_side(evaluated: sweep.Sweep) -> None:
    """AC-F8's comparison, as one object rather than five lookups."""
    matrix = evaluated.matrix()

    assert matrix["size"] == 5
    assert matrix["complete"] is True
    assert matrix["gates"] == ["E2"]
    assert [row["row"]["E2"] for row in matrix["points"]] == [0.76, 0.81, 0.86, 0.84, 0.79]


def test_an_unevaluated_point_appears_in_the_matrix_rather_than_being_dropped(
    five: sweep.Sweep,
) -> None:
    """A comparison that omits the points that failed to build compares survivors."""
    partial = five.with_result(
        {"weight": 0.2}, artefact_sha256=merged_sha(1), evidence=evidence_for(1, score=0.8)
    )

    matrix = partial.matrix()

    assert len(matrix["points"]) == 5
    assert matrix["complete"] is False
    assert matrix["points"][1]["row"]["E2"] is None


def test_the_selected_point_is_recorded_on_the_sweep(evaluated: sweep.Sweep) -> None:
    chosen = evaluated.select({"weight": 0.6}, criterion="E2")

    assert chosen.selected == {"weight": 0.6}
    assert chosen.selection_criterion == "E2"
    assert chosen.selected_point is not None
    assert chosen.selected_point.artefact_sha256 == merged_sha(3)


def test_ranking_orders_passing_points_best_first(evaluated: sweep.Sweep) -> None:
    ranked = evaluated.ranked("E2")

    assert next(point.label for point in ranked) == "weight=0.6"


def test_a_failing_point_cannot_be_selected(five: sweep.Sweep) -> None:
    """BRISINGAMEN runs the sweep; RAUN decides acceptability (SAD 5.2)."""
    current = five.with_result(
        {"weight": 0.2},
        artefact_sha256=merged_sha(1),
        evidence=evidence_for(1, score=0.5, passed=False),
    )

    with pytest.raises(SweepError, match="RAUN decides whether a merge is acceptable"):
        current.select({"weight": 0.2})


def test_an_unevaluated_point_cannot_be_selected(five: sweep.Sweep) -> None:
    with pytest.raises(SweepError, match="has not been evaluated"):
        five.select({"weight": 0.4})


def test_a_failing_point_is_excluded_from_the_ranking(five: sweep.Sweep) -> None:
    """A number beside a model that may not be released reads as a recommendation."""
    current = five.with_result(
        {"weight": 0.2},
        artefact_sha256=merged_sha(1),
        evidence=evidence_for(1, score=0.99, passed=False),
    )
    current = current.with_result(
        {"weight": 0.4},
        artefact_sha256=merged_sha(2),
        evidence=evidence_for(2, score=0.80),
    )

    assert [point.label for point in current.ranked("E2")] == ["weight=0.4"]


def test_evidence_for_the_wrong_bytes_is_refused(five: sweep.Sweep) -> None:
    """Gate results bind to the bytes, inside the sweep as everywhere else."""
    with pytest.raises(SweepError, match="bind to the bytes"):
        five.with_result(
            {"weight": 0.2},
            artefact_sha256=merged_sha(9),
            evidence=evidence_for(1, score=0.8),
        )


def test_the_model_card_view_carries_the_whole_comparison(evaluated: sweep.Sweep) -> None:
    """A card reporting only the winner says a number was picked, not why."""
    card = evaluated.select({"weight": 0.6}, criterion="E2").for_model_card()

    assert card["points"] == 5
    assert card["selected"]["label"] == "weight=0.6"
    assert len(card["comparison"]) == 5


def test_each_point_hashes_its_own_configuration(evaluated: sweep.Sweep) -> None:
    """SAD 6.1 records a merge configuration hash on MERGED to QUANTISED."""
    hashes = {point.config_hash() for point in evaluated.points}

    assert len(hashes) == 5


# -- routes -----------------------------------------------------------------


def test_route_b_publishes_quantised_dense_weights() -> None:
    definition = routes.definition(Route.B)

    assert definition.publishes == "quantised"
    assert definition.requires_base is False
    assert routes.requires_merge_to_dense(Route.B)


def test_route_a_publishes_the_adapter() -> None:
    definition = routes.definition(Route.A)

    assert definition.publishes == "adapter"
    assert definition.requires_base is True
    assert not routes.requires_merge_to_dense(Route.A)


def test_the_sad_example_validates_on_route_b() -> None:
    """SAD 6.2 pairs `route: B` with nvfp4, gguf-q4km and mlx4."""
    assert routes.validate("B", ["nvfp4", "gguf-q4km", "mlx4"]).publishes == "quantised"


def test_a_quantisation_format_on_route_a_is_a_contradiction() -> None:
    """An adapter is not quantised to NVFP4; the model it is merged into is."""
    with pytest.raises(RouteError) as raised:
        routes.validate("A", ["nvfp4"])

    assert "Route B publishes those" in str(raised.value)
    assert "they disagree" in str(raised.value)


def test_a_route_with_no_formats_is_refused() -> None:
    with pytest.raises(RouteError, match="no format was named"):
        routes.validate("B", [])


def test_an_unknown_route_is_refused() -> None:
    with pytest.raises(RouteError, match="not a release route"):
        routes.definition("C")


# -- merge configuration ----------------------------------------------------


def test_adapter_to_dense_export_is_a_linear_merge() -> None:
    """One path to dense weights, so one place a scaling factor can be dropped."""
    config = merge.linear(base_sha256=BASE, adapter_sha256=ADAPTER, jurisdiction="GBR")

    assert config.method is Method.LINEAR
    assert config.adapters[0].weight == 1.0
    assert len(config.config_hash()) == 64


def test_both_routes_merge_and_differ_in_what_they_publish() -> None:
    config_a, publishes_a = merge.plan_for_route(
        Route.A, base_sha256=BASE, adapter_sha256=ADAPTER, jurisdiction="GBR"
    )
    config_b, publishes_b = merge.plan_for_route(
        Route.B, base_sha256=BASE, adapter_sha256=ADAPTER, jurisdiction="GBR"
    )

    assert config_a.config_hash() == config_b.config_hash()
    assert (publishes_a, publishes_b) == ("adapter", "quantised")


def test_a_multi_adapter_method_over_one_adapter_is_refused() -> None:
    """It is the identity, and discovering that costs five allocations."""
    with pytest.raises(MergeError, match="combines several adapters"):
        merge.MergeConfig(
            method=Method.TIES,
            base_sha256=BASE,
            adapters=(merge.AdapterRef(sha256=ADAPTER, jurisdiction="GBR"),),
        )


def test_the_same_adapter_twice_is_refused() -> None:
    with pytest.raises(MergeError, match="counted twice"):
        merge.MergeConfig(
            method=Method.TIES,
            base_sha256=BASE,
            adapters=(
                merge.AdapterRef(sha256=ADAPTER, jurisdiction="GBR"),
                merge.AdapterRef(sha256=ADAPTER, jurisdiction="KEN"),
            ),
        )


def test_a_merge_with_no_adapters_is_refused() -> None:
    with pytest.raises(MergeError, match="that is not a merge"):
        merge.MergeConfig(method=Method.LINEAR, base_sha256=BASE, adapters=())


def test_the_configuration_hash_changes_with_the_weight() -> None:
    config = merge.linear(base_sha256=BASE, adapter_sha256=ADAPTER, jurisdiction="GBR")

    assert config.config_hash() != config.at_weight({"weight": 0.6}).config_hash()
