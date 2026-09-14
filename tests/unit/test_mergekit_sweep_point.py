"""A sweep point reaches the merge it names. RF-27.

BRISINGAMEN built a five point sweep and the merge driver rendered the same
configuration whatever point it was given -- there was no way to give it one.
Five merges would have been one merge five times, and the comparison S15 shows
would have compared a model with itself.
"""

from __future__ import annotations

from typing import Any

import pytest
from draupnir_brisingamen_mergekit import MergekitDriver

from draupnir.interfaces.testing import sample_spec
from draupnir.interfaces.types import RunSpec

pytestmark = pytest.mark.unit

DRIVER = MergekitDriver()


def merge(method: str, point: Any = None) -> RunSpec:
    params: dict[str, Any] = {
        "merge_method": method,
        "base_model": "hodd://sindri/models/core/base",
        "models": [
            {"model": "adapter-gbr", "tokenizer_sha256": "a" * 64},
            {"model": "adapter-can", "tokenizer_sha256": "a" * 64},
        ],
    }
    if point is not None:
        params["sweep_point"] = point
    return sample_spec(
        train={
            "driver": "brisingamen.mergekit/v1",
            "method": method,
            "params": params,
            "precision": "bf16",
        }
    )


def test_a_slerp_point_renders_its_own_interpolation() -> None:
    assert DRIVER.configuration(merge("slerp", {"weight": 0.4}))["parameters"] == {"t": 0.4}


def test_five_points_are_five_configurations() -> None:
    """The finding: five points used to hash to one configuration."""
    hashes = {
        DRIVER.config_hash(merge("slerp", {"weight": weight}))
        for weight in (0.2, 0.4, 0.6, 0.8, 1.0)
    }

    assert len(hashes) == 5


def test_a_ties_point_keeps_its_density_and_adds_the_weight() -> None:
    parameters = DRIVER.configuration(merge("ties", {"weight": 0.6}))["parameters"]

    assert parameters == {"density": 0.5, "normalize": True, "weight": 0.6}


def test_a_merge_with_no_point_renders_as_it_did() -> None:
    assert "parameters" not in DRIVER.configuration(merge("slerp"))
    assert DRIVER.validate(merge("slerp")) == []


@pytest.mark.parametrize(
    "point",
    [{"weight": 1.5}, {"weight": 0.2, "t": 0.3}, {"weight": "0.4"}, {"weight": True}, [0.4]],
)
def test_a_malformed_point_is_refused_rather_than_rendered(point: Any) -> None:
    codes = {problem.code for problem in DRIVER.validate(merge("slerp", point))}

    assert "invalid_sweep_point" in codes
    assert "parameters" not in DRIVER.configuration(merge("slerp", point))
