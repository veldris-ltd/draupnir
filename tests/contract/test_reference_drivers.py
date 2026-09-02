"""Every first party driver, against the published conformance suite.

SAD 11E.3 puts this at the contract level: "Every driver against the
conformance harness, and the harness published for third parties." The drivers
are loaded the way the control plane loads them -- by entry point, from the
installed environment -- rather than imported, so this also proves the
distributions are registered correctly.

Adding a driver to `plugins/` and installing it puts it in front of this suite
automatically. There is no list here to keep up to date, which is the point:
a conformance suite each driver opts into is a conformance suite drivers
forget to opt into.
"""

from __future__ import annotations

from typing import Any

import pytest

from draupnir.core.plugins import DEV_VARIABLE, LoadedPlugin, PluginRegistry
from draupnir.interfaces.testing import (
    JobDriverConformance,
    ScheduleDriverConformance,
    sample_spec,
)
from draupnir.interfaces.types import RunSpec

pytestmark = pytest.mark.contract

#: The reference drivers are unsigned: the Veldris PKI verifier arrives in
#: Prompt 6. Development mode is the one environment variable wide concession
#: that lets them load, and the suite uses it explicitly rather than relying on
#: whatever the developer's shell happens to have set.
DEV_ENVIRONMENT = {DEV_VARIABLE: "1"}


@pytest.fixture(scope="session")
def registry() -> PluginRegistry:
    """Every plug-in installed in this environment."""
    return PluginRegistry.discover(environ=DEV_ENVIRONMENT)


def installed(registry: PluginRegistry, group: str, name: str) -> LoadedPlugin:
    """Resolve a driver, skipping if that distribution is not installed."""
    if name not in registry.names(group):
        pytest.skip(f"{name} is not installed; `uv sync --all-groups` installs it")
    return registry.resolve(group, name)


def test_every_installed_plugin_loaded(registry: PluginRegistry) -> None:
    """No first party driver is refused by the loader."""
    assert registry.failures == (), "\n".join(
        f"{failure.name}: {failure.reason}" for failure in registry.failures
    )


def test_the_reference_drivers_are_discoverable(registry: PluginRegistry) -> None:
    assert "motsognir.local_subprocess/v1" in registry.names("draupnir.schedule")
    assert "skidbladnir.targz/v1" in registry.names("draupnir.export")


class TestLocalSubprocessConformance(ScheduleDriverConformance):
    """The reference ScheduleDriver of SAD 8.2, against the published suite."""

    @pytest.fixture
    def driver(self, registry: PluginRegistry) -> Any:
        return installed(registry, "draupnir.schedule", "motsognir.local_subprocess/v1").driver


class TestTarGzExportConformance(JobDriverConformance):
    """The reference ExportDriver, against the published suite."""

    @pytest.fixture
    def driver(self, registry: PluginRegistry) -> Any:
        return installed(registry, "draupnir.export", "skidbladnir.targz/v1").driver

    @pytest.fixture
    def spec(self) -> RunSpec:
        """A specification asking for the one format this driver produces."""
        return sample_spec(release={"route": "B", "formats": ["targz"], "approval": "required"})


class TestLlamaFactoryConformance(JobDriverConformance):
    """The LLaMA-Factory TrainDriver, against the published suite.

    The specification below is one HAMARR has already prepared: it carries the
    checkpoint interval the driver refuses to invent for itself. That refusal
    is what keeps the thirty minute budget in the control plane rather than in
    a third party package.
    """

    @pytest.fixture
    def driver(self, registry: PluginRegistry) -> Any:
        return installed(registry, "draupnir.train", "hamarr.llamafactory/v1").driver

    @pytest.fixture
    def spec(self) -> RunSpec:
        return sample_spec(
            train={
                "driver": "hamarr.llamafactory/v1",
                "method": "lora",
                "params": {"save_steps": 150, "lora_rank": 64},
                "precision": "bf16",
            }
        )


def test_the_slurm_and_llamafactory_drivers_are_discoverable(
    registry: PluginRegistry,
) -> None:
    """Both arrive by entry point, the way the control plane loads them."""
    assert "motsognir.slurm/v1" in registry.names("draupnir.schedule")
    assert "hamarr.llamafactory/v1" in registry.names("draupnir.train")


def test_the_slurm_driver_conforms_to_what_every_plugin_must_declare(
    registry: PluginRegistry,
) -> None:
    """The submit/poll/cancel suite needs a live Slurm; the rest does not.

    The behaviour that suite would exercise is covered against Slurm's real
    output formats in `tests/unit/test_slurm_driver.py`, with the command
    layer replaced and everything above it running for real.
    """
    from draupnir.interfaces.testing.harness import check_driver

    driver = installed(registry, "draupnir.schedule", "motsognir.slurm/v1").driver

    assert check_driver(driver) == []


class TestMergekitConformance(JobDriverConformance):
    """The mergekit MergeDriver, against the published suite."""

    @pytest.fixture
    def driver(self, registry: PluginRegistry) -> Any:
        return installed(registry, "draupnir.merge", "brisingamen.mergekit/v1").driver

    @pytest.fixture
    def spec(self) -> RunSpec:
        """A two model TIES merge, which is what this driver is for."""
        return sample_spec(
            train={
                "driver": "brisingamen.mergekit/v1",
                "method": "ties",
                "params": {
                    "merge_method": "ties",
                    "base_model": "hodd://sindri/models/core/base",
                    "models": [
                        {"model": "adapter-gbr", "weight": 0.6, "tokenizer_sha256": "a" * 64},
                        {"model": "adapter-can", "weight": 0.4, "tokenizer_sha256": "a" * 64},
                    ],
                },
                "precision": "bf16",
            }
        )


class TestLmEvalConformance(JobDriverConformance):
    """The lm-evaluation-harness EvalDriver, against the published suite."""

    @pytest.fixture
    def driver(self, registry: PluginRegistry) -> Any:
        return installed(registry, "draupnir.eval", "raun.lmeval/v1").driver


class TestQuantiseConformance(JobDriverConformance):
    """The NVFP4/GGUF/MLX ExportDriver, against the published suite."""

    @pytest.fixture
    def driver(self, registry: PluginRegistry) -> Any:
        return installed(registry, "draupnir.export", "skidbladnir.quantise/v1").driver

    @pytest.fixture
    def spec(self) -> RunSpec:
        """The formats of SAD 6.2, which is what this driver builds."""
        return sample_spec(
            release={
                "route": "B",
                "formats": ["nvfp4", "gguf-q4km", "mlx4"],
                "approval": "required",
            }
        )


def test_every_prompt_five_driver_is_discoverable(registry: PluginRegistry) -> None:
    """All three arrive by entry point, the way the control plane loads them."""
    assert "brisingamen.mergekit/v1" in registry.names("draupnir.merge")
    assert "raun.lmeval/v1" in registry.names("draupnir.eval")
    assert "skidbladnir.quantise/v1" in registry.names("draupnir.export")


def test_the_eval_driver_never_decides_whether_a_gate_passed(
    registry: PluginRegistry, tmp_path: Any
) -> None:
    """A driver that filled `passed` in could pass its own evaluation.

    The value is reported; the verdict needs the threshold, the baseline and the
    comparison, all of which are GLEIPNIR's (Decision S4).
    """
    import json

    driver = installed(registry, "draupnir.eval", "raun.lmeval/v1").driver
    (tmp_path / "results.json").write_text(
        json.dumps(
            {"results": {"mmlu": {"acc_norm": 0.81}, "ifeval": {"prompt_level_strict_acc": 0.77}}}
        ),
        encoding="utf-8",
    )

    outcomes = driver.collect_gates(tmp_path, "2026.01")

    assert {item.gate for item in outcomes} == {"E1", "E3"}
    assert all(item.passed is False for item in outcomes)
    assert all(item.baseline_value is None for item in outcomes)
