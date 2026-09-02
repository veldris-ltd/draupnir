"""Run configuration: what HAMARR settles before a driver renders anything.

SAD 5.2 gives HAMARR "run configuration rendering" and forbids it from
choosing which model to train or judging the result. So this module settles the
two things that are policy rather than framework detail, and then hands the
specification to a driver:

* the base model, which follows from the jurisdiction's tier and from nothing
  else, so that two jurisdictions in one tier cannot silently diverge; and
* the checkpoint interval, which follows from observed step time so that no
  more than thirty minutes of work is ever unwritten.

Both are decided here rather than in a plug-in on purpose. A driver that chose
its own checkpoint interval would put the thirty minute budget in a
third party package, where changing it would be a dependency upgrade rather
than a decision.

`prepare` returns a new specification. The original is untouched, because SAD
6.2 makes the specification the unit of reproduction and a specification that
is edited in flight is not one.

**One discrepancy in the source documents, resolved here and worth confirming.**
SAD 6.2's worked example is `cim-gbr-v0.1`, jurisdiction GBR, `tier: A`, with
`base.artefact: hodd://models/core/MIDGARD-CORE-QWEN36-35B-A3B-v1.0`. SAD 13.5
and SAD Q2 both say Tier A trains on the 27B dense base and Tier B on the
35B-A3B MoE base, so the example contradicts the rule. The rule is taken as
authoritative -- it is stated twice, and revision 1.1 is the one that fixed the
Tier A list -- and the example as predating it. The consequence is visible:
`prepare` refuses SAD 6.2's example verbatim.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from draupnir.hamarr import checkpoints, tiers
from draupnir.hamarr.progress import Progress
from draupnir.interfaces.types import RunSpec


class ConfigurationError(Exception):
    """Raised when a specification cannot be configured for execution."""


def prepare(
    spec: RunSpec,
    *,
    site: str = "sindri",
    assumed_step: timedelta = checkpoints.ASSUMED_STEP,
) -> tuple[RunSpec, checkpoints.Policy]:
    """Settle base model and checkpoint interval. Called at submission.

    Validates the jurisdiction enumeration first (AC-F16): a tier table that
    has drifted would assign the wrong base, and the failure would be silent.
    """
    tiers.validate()

    jurisdiction = spec.metadata.jurisdiction
    tier = tiers.assert_declared_tier(jurisdiction, spec.metadata.tier)
    expected_base = tiers.base_artefact(jurisdiction, site=site)

    if spec.base.artefact != expected_base:
        msg = (
            f"{jurisdiction} is Tier {tier} and trains against "
            f"{tiers.base_for(jurisdiction)}, but the specification names "
            f"{spec.base.artefact}. Base selection follows from the tier; a "
            "specification does not get to choose one."
        )
        raise ConfigurationError(msg)

    authored = spec.train.params.get("save_steps")
    if authored is not None:
        checkpoints.check(int(authored), assumed_step)
        policy = checkpoints.derive(assumed_step)
        policy = replace_save_steps(policy, int(authored))
    else:
        policy = checkpoints.initial(assumed_step)

    return with_policy(spec, policy), policy


def with_policy(spec: RunSpec, policy: checkpoints.Policy) -> RunSpec:
    """Return the specification carrying a checkpoint interval."""
    return replace(
        spec,
        train=replace(
            spec.train,
            params={**dict(spec.train.params), "save_steps": policy.save_steps},
        ),
    )


def replace_save_steps(policy: checkpoints.Policy, save_steps: int) -> checkpoints.Policy:
    """Keep an authored interval, with the reasoning that it was authored."""
    return replace(
        policy,
        save_steps=save_steps,
        exposure=checkpoints.exposure_of(save_steps, policy.step_time),
        provisional=False,
        reason="authored in the specification and checked against the budget",
    )


def revise(
    spec: RunSpec, current: checkpoints.Policy, progress: Progress
) -> tuple[RunSpec, checkpoints.Policy] | None:
    """Recompute the checkpoint interval from what the run has actually done.

    Returns `None` while the measurement is too short to trust, and when the
    revision would not change the interval. The caller rewrites the driver's
    configuration and records the change; a revision on every poll would
    produce a ledger of noise.
    """
    step_time = progress.step_time
    if step_time is None:
        return None

    # `Observation` divides elapsed by steps, and `Progress.step_time` already
    # accounts for the interval-versus-count difference. Rebuilding the
    # elapsed time from the corrected step time keeps one definition of it.
    observation = checkpoints.Observation(
        steps=progress.observed_steps, elapsed=step_time * progress.observed_steps
    )
    revised = checkpoints.recompute(observation, current)
    if revised is None:
        return None
    return with_policy(spec, revised), revised
