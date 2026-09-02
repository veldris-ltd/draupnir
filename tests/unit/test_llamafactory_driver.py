"""The LLaMA-Factory driver, and the refusal it exists to make.

The exit condition for this prompt is explicit: "a test proves that an unknown
base model raises rather than defaulting". It is the first test below, and the
reason it matters is that the alternative failure is silent. A default chat
template trains a model against a conversation format it will never be sent;
the loss curve looks ordinary; the damage surfaces at evaluation, days later,
looking like a data problem.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from draupnir_hamarr_llamafactory import (
    CONFIG_VARIABLE,
    LlamaFactoryDriver,
    base_model_of,
    templates,
)
from draupnir_hamarr_llamafactory.templates import UnknownBaseModelError

from draupnir.interfaces.testing import sample_spec
from draupnir.interfaces.types import RunSpec

pytestmark = pytest.mark.unit


@pytest.fixture
def driver() -> LlamaFactoryDriver:
    return LlamaFactoryDriver()


@pytest.fixture
def spec() -> RunSpec:
    """A specification HAMARR has already prepared: it carries an interval."""
    return sample_spec(
        train={
            "driver": "hamarr.llamafactory/v1",
            "method": "lora",
            "params": {"save_steps": 150, "lora_rank": 64, "lora_alpha": 128, "seed": 7},
            "precision": "bf16",
        }
    )


# -- the refusal ------------------------------------------------------------


def test_an_unknown_base_model_raises_rather_than_defaulting() -> None:
    """The exit condition, stated once and directly."""
    with pytest.raises(UnknownBaseModelError) as raised:
        templates.resolve("meta-llama/Llama-3.3-70B-Instruct")

    assert "no chat template is registered" in str(raised.value)
    assert "deliberately no default" in str(raised.value)
    assert raised.value.base == "meta-llama/Llama-3.3-70B-Instruct"
    assert "MIDGARD-CORE-GEMMA3-27B-v1.0" in raised.value.known


def test_render_raises_on_an_unknown_base_rather_than_producing_a_job(
    driver: LlamaFactoryDriver, tmp_path: Path
) -> None:
    """The refusal reaches the point where an allocation would be consumed."""
    unknown = sample_spec(
        base={"artefact": "hodd://sindri/models/core/SOMETHING-ELSE-v1", "expectSha256": "a" * 64},
        train={
            "driver": "hamarr.llamafactory/v1",
            "method": "lora",
            "params": {"save_steps": 150},
            "precision": "bf16",
        },
    )

    with pytest.raises(UnknownBaseModelError):
        driver.render(unknown, tmp_path)


def test_validate_reports_an_unknown_base_as_a_problem_not_an_exception(
    driver: LlamaFactoryDriver,
) -> None:
    """`validate` collects problems; `render` refuses. Both, for one mistake."""
    unknown = sample_spec(
        base={"artefact": "hodd://sindri/models/core/SOMETHING-ELSE-v1", "expectSha256": "a" * 64},
        train={
            "driver": "hamarr.llamafactory/v1",
            "method": "lora",
            "params": {"save_steps": 150},
            "precision": "bf16",
        },
    )

    codes = {problem.code for problem in driver.validate(unknown)}

    assert "unknown_base_model" in codes


def test_there_is_no_fallback_template_anywhere_in_the_map() -> None:
    """A `default` key would reintroduce exactly what this design removes."""
    for version in (templates.CURRENT, templates.PREVIOUS):
        assert "default" not in version.templates
        assert "" not in version.templates


# -- versioned resolution ---------------------------------------------------


def test_the_template_map_is_versioned_so_a_replay_resolves_what_it_used() -> None:
    """SAD 10.1: a run reproduced later resolves the template it actually used."""
    base = "MIDGARD-CORE-QWEN36-35B-A3B-v1.0"

    assert templates.resolve(base) == "qwen3_nothink"
    assert templates.resolve(base, version=templates.PREVIOUS_VERSION) == "qwen3"


def test_a_map_version_this_build_does_not_carry_is_refused() -> None:
    with pytest.raises(ValueError, match="is not available"):
        templates.by_version("hamarr-templates/1999.01")


# -- validate ---------------------------------------------------------------


def test_validate_returns_every_problem_rather_than_the_first(
    driver: LlamaFactoryDriver,
) -> None:
    broken = sample_spec(
        train={
            "driver": "hamarr.llamafactory/v1",
            "method": "dpo",
            "params": {},
            "precision": "int8",
        }
    )

    codes = {problem.code for problem in driver.validate(broken)}

    assert codes == {
        "unsupported_method",
        "unsupported_precision",
        "missing_checkpoint_interval",
    }


def test_a_prepared_specification_validates_clean(
    driver: LlamaFactoryDriver, spec: RunSpec
) -> None:
    assert driver.validate(spec) == []


def test_a_missing_checkpoint_interval_is_a_refusal_not_a_default(
    driver: LlamaFactoryDriver,
) -> None:
    """The thirty minute budget is HAMARR's, and must not live in a plug-in."""
    problems = driver.validate(sample_spec())
    messages = {problem.code: problem.message for problem in problems}

    assert "missing_checkpoint_interval" in messages
    assert "thirty minutes" in messages["missing_checkpoint_interval"]


# -- render -----------------------------------------------------------------


def test_render_produces_the_configuration_in_the_environment(
    driver: LlamaFactoryDriver, spec: RunSpec, tmp_path: Path
) -> None:
    """A pure render cannot write a file, so the job writes it."""
    plan = driver.render(spec, tmp_path)

    assert plan.command[0] == "sh"
    assert CONFIG_VARIABLE in plan.environment
    configuration = json.loads(plan.environment[CONFIG_VARIABLE])
    assert configuration["template"] == "qwen3_nothink"
    assert configuration["save_steps"] == 150
    assert configuration["bf16"] is True
    assert configuration["lora_rank"] == 64


def test_render_writes_nothing_to_the_working_directory(
    driver: LlamaFactoryDriver, spec: RunSpec, tmp_path: Path
) -> None:
    driver.render(spec, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_the_base_model_is_the_last_segment_of_the_hodd_uri() -> None:
    """SAD 7.4 addresses artefacts by URI; the map is keyed by model name."""
    assert base_model_of(sample_spec()) == "MIDGARD-CORE-QWEN36-35B-A3B-v1.0"


# -- parse_progress ---------------------------------------------------------


def test_progress_parsing_produces_structured_events(
    driver: LlamaFactoryDriver,
) -> None:
    """The regex lives here, and nothing downstream ever sees the line."""
    loss = driver.parse_progress(
        "{'loss': 1.2345, 'grad_norm': 0.5, 'learning_rate': 1e-4, 'epoch': 0.12} 60/1000 ["
    )
    assert loss is not None
    assert loss.value == pytest.approx(1.2345)
    assert loss.step == 60
    assert loss.total == 1000

    checkpoint = driver.parse_progress("Saving model checkpoint to output/cim-gbr/checkpoint-150")
    assert checkpoint is not None
    assert checkpoint.message == "output/cim-gbr/checkpoint-150"

    metric = driver.parse_progress("{'eval_loss': 0.98, 'epoch': 1.0}")
    assert metric is not None
    assert metric.message == "eval_loss"
    assert metric.value == pytest.approx(0.98)


def test_parse_progress_returns_none_for_a_line_that_says_nothing(
    driver: LlamaFactoryDriver,
) -> None:
    assert driver.parse_progress("") is None
    assert driver.parse_progress("Loading checkpoint shards") is None


def test_parse_progress_is_pure(driver: LlamaFactoryDriver) -> None:
    line = "{'loss': 1.5, 'epoch': 0.1} 10/100 ["

    assert driver.parse_progress(line) == driver.parse_progress(line)


# -- collect ----------------------------------------------------------------


def test_collect_hashes_what_it_finds_and_changes_nothing(
    driver: LlamaFactoryDriver, tmp_path: Path
) -> None:
    (tmp_path / "adapter_model.safetensors").write_bytes(b"weights")
    (tmp_path / "trainer_state.json").write_text(
        json.dumps({"log_history": [{"loss": 1.0}, {"loss": 0.5, "epoch": 3.0}]}),
        encoding="utf-8",
    )
    before = {path.name: path.stat().st_mtime_ns for path in tmp_path.iterdir()}

    produced = driver.collect(tmp_path)

    assert {item.path for item in produced.artefacts} == {
        "adapter_model.safetensors",
        "trainer_state.json",
    }
    assert {item.kind for item in produced.artefacts} == {"adapter", "metadata"}
    assert produced.metrics["loss"] == 0.5
    assert {path.name: path.stat().st_mtime_ns for path in tmp_path.iterdir()} == before


def test_collect_on_an_empty_directory_returns_nothing(
    driver: LlamaFactoryDriver, tmp_path: Path
) -> None:
    assert driver.collect(tmp_path / "absent").artefacts == ()
