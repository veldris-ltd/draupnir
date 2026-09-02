"""What HAMARR settles at submission, and AC-N2.

AC-N2: "Control plane adds no measurable overhead to training step time", with
the threshold "step time within 1 per cent of the same job run by hand".

The structural half of that is the more important half and is asserted first:
the rendered command is the trainer and nothing else. There is no supervisor
in the training loop, no wrapper that polls, no interposed process. Observation
happens out of band, by reading a log the trainer would have written anyway. A
control plane that cannot touch the step cannot slow it down.

The numeric half then bounds what the out of band work costs, in case the
structural argument is ever quietly broken.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from draupnir_hamarr_llamafactory import LlamaFactoryDriver

from draupnir.hamarr import checkpoints, config, tiers
from draupnir.hamarr import progress as progress_module
from draupnir.hamarr.config import ConfigurationError
from draupnir.hamarr.tiers import TierError
from draupnir.interfaces.testing import sample_spec
from draupnir.interfaces.testing.fixtures import SAMPLE_SPEC_MAPPING
from draupnir.interfaces.types import ProgressEvent, ProgressKind, RunSpec

pytestmark = pytest.mark.unit

START = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def tier_a_spec(**overrides: Any) -> RunSpec:
    """A GBR specification with the base its tier actually requires.

    SAD 6.2's worked example names GBR as Tier A but points at the 35B-A3B MoE
    base, which section 13.5 assigns to Tier B. The rule is taken as
    authoritative and the example as stale; see the note in
    `draupnir/hamarr/config.py`.
    """
    return sample_spec(
        base={"artefact": tiers.base_artefact("GBR"), "expectSha256": "a" * 64},
        **overrides,
    )


# -- what submission settles ------------------------------------------------


def test_preparation_validates_the_enumeration_before_anything_else() -> None:
    """AC-F16 is checked at submission, not at import."""
    prepared, policy = config.prepare(tier_a_spec())

    assert prepared.train.params["save_steps"] == policy.save_steps
    assert policy.provisional is True


def test_the_base_follows_from_the_tier_and_not_from_the_specification() -> None:
    """Two jurisdictions in one tier cannot silently diverge."""
    with pytest.raises(ConfigurationError, match="Base selection follows from the tier"):
        config.prepare(sample_spec())  # SAD 6.2's example: Tier A on the MoE base


def test_a_specification_declaring_the_wrong_tier_is_refused() -> None:
    """GBR is Tier A; a specification saying otherwise would train a wrong base."""
    mapping = {
        **SAMPLE_SPEC_MAPPING,
        "metadata": {**SAMPLE_SPEC_MAPPING["metadata"], "tier": "B"},
        "spec": {
            **SAMPLE_SPEC_MAPPING["spec"],
            "base": {"artefact": tiers.base_artefact("GBR"), "expectSha256": "a" * 64},
        },
    }

    with pytest.raises(TierError, match="GBR is Tier A"):
        config.prepare(RunSpec.from_mapping(mapping))


def test_an_authored_interval_is_kept_but_checked() -> None:
    prepared, policy = config.prepare(
        tier_a_spec(
            train={
                "driver": "hamarr.llamafactory/v1",
                "method": "lora",
                "params": {"save_steps": 120},
                "precision": "bf16",
            }
        )
    )

    assert prepared.train.params["save_steps"] == 120
    assert policy.provisional is False
    assert "authored" in policy.reason


def test_an_authored_interval_that_loses_too_much_work_is_refused() -> None:
    with pytest.raises(checkpoints.CheckpointError, match="unwritten"):
        config.prepare(
            tier_a_spec(
                train={
                    "driver": "hamarr.llamafactory/v1",
                    "method": "lora",
                    "params": {"save_steps": 5000},
                    "precision": "bf16",
                }
            )
        )


def test_the_interval_is_revised_once_the_run_has_been_measured() -> None:
    """The guess is replaced by the measurement, and the spec is rewritten."""
    prepared, policy = config.prepare(tier_a_spec())
    observed = progress_module.fold(
        [
            (
                ProgressEvent(kind=ProgressKind.STEP, step=index + 1, total=5000),
                START + timedelta(seconds=index * 3),
            )
            for index in range(51)
        ]
    )

    revision = config.revise(prepared, policy, observed)

    assert revision is not None
    revised_spec, revised_policy = revision
    assert revised_policy.provisional is False
    assert revised_policy.step_time == timedelta(seconds=3)
    assert revised_policy.save_steps == 600
    assert revised_spec.train.params["save_steps"] == 600
    # And the original is untouched: SAD 6.2 makes the specification the unit
    # of reproduction, and one edited in flight is not one.
    assert prepared.train.params["save_steps"] == policy.save_steps


def test_no_revision_before_the_run_has_done_fifty_steps() -> None:
    prepared, policy = config.prepare(tier_a_spec())
    early = progress_module.fold(
        [
            (
                ProgressEvent(kind=ProgressKind.STEP, step=index + 1, total=5000),
                START + timedelta(seconds=index * 3),
            )
            for index in range(10)
        ]
    )

    assert config.revise(prepared, policy, early) is None


# -- AC-N2 ------------------------------------------------------------------


def test_the_control_plane_is_not_in_the_training_loop(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """AC-N2, structurally: the rendered command is the trainer, alone.

    No supervisor, no polling wrapper, no interposed process. The only thing
    the command does before the trainer is write the configuration file that a
    pure `render` could not write.
    """
    prepared, _ = config.prepare(
        tier_a_spec(
            train={
                "driver": "hamarr.llamafactory/v1",
                "method": "lora",
                "params": {"save_steps": 150},
                "precision": "bf16",
            }
        )
    )
    # The Tier A base has to be one the driver knows; it is.
    plan = LlamaFactoryDriver().render(prepared, tmp_path)

    script = plan.command[-1]
    assert plan.command[:2] == ("sh", "-c")

    # Two commands: write the configuration, then exec the trainer. Nothing
    # of the control plane runs alongside the training loop.
    stages = [part.strip() for part in script.split("&&")]
    assert len(stages) == 2
    assert stages[0].startswith("printf")
    assert stages[1] == "llamafactory-cli train llamafactory.json"


def test_observation_costs_a_negligible_fraction_of_a_training_step() -> None:
    """AC-N2, numerically, with a wide margin.

    A step on this estate takes seconds. Parsing a line and folding the event
    must cost so much less than one per cent of that as to be unmeasurable
    beside it.
    """
    driver = LlamaFactoryDriver()
    line = "{'loss': 1.2345, 'grad_norm': 0.5, 'epoch': 0.12} 60/1000 ["
    rounds = 2000

    started = time.perf_counter()
    stream = progress_module.Stream()
    for index in range(rounds):
        event = driver.parse_progress(line)
        if event is not None:
            stream = stream.consume(event, at=START + timedelta(seconds=index))
    elapsed = time.perf_counter() - started

    per_step = elapsed / rounds
    # One per cent of the fastest step time worth calling a training step.
    budget = 1.0 * 0.01

    assert per_step < budget, f"{per_step * 1000:.3f} ms per step exceeds {budget * 1000:.1f} ms"
