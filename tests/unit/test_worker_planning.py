"""What the worker dispatches, and what it judges it on. RF-10.

Every dispatch, for every run, built `execution.stand_in_plan` -- a Python
one-liner over the corpus -- while `POST /v1/runs/dry-run` rendered the real
driver's command and returned it to the operator. AC-F14's stated property,
"the plan shown here is the plan that would actually be run rather than an
approximation of it", was therefore false of the running system: the console
showed a LLaMA-Factory invocation and the worker ran `sys.executable -c`.

Evaluation was worse than a stub. `_measurements` derived a score per gate from
the artefact's SHA-256 and `_baselines` returned `measurement * 0.95`, so every
run was judged against ninety-five per cent of its own score and every run
passed. Six gates, four of them relative, decided nothing.

The docstrings were candid about both. The consequence was not.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
import structlog

from draupnir.core.application.orchestrator import RunFacts
from draupnir.core.domain.states import RunState
from draupnir.hamarr import tiers
from draupnir.interfaces.testing import sample_spec
from draupnir.interfaces.testing.fixtures import SAMPLE_SPEC_MAPPING
from draupnir.interfaces.types import JobHandle, JobState, JobStatus
from draupnir.raun.baselines import Baseline, registry_of
from draupnir.worker import stages
from draupnir.worker.loop import WorkerSettings

pytestmark = pytest.mark.unit

SITE = "sindri"
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _spec() -> Any:
    """A specification the installed training driver accepts.

    The SAD 6.2 worked example on its own does not: `save_steps` is derived by
    `draupnir.hamarr.config.prepare` from the run's time budget, and the driver
    refuses to invent a checkpoint interval -- correctly, since a wrong one
    costs a run its resumability. Nothing on the submission path calls
    `prepare`, which is RF-11's finding and not this one's, so the interval is
    supplied here.
    """
    return sample_spec(
        base={"artefact": tiers.base_artefact("GBR"), "expectSha256": "a" * 64},
        train={
            **SAMPLE_SPEC_MAPPING["spec"]["train"],
            "params": {
                **SAMPLE_SPEC_MAPPING["spec"]["train"].get("params", {}),
                "save_steps": 500,
            },
        },
    )


def _facts(
    run_id: UUID, state: RunState = RunState.QUEUED, *, specification: Any = None
) -> RunFacts:
    return RunFacts(
        run_id=run_id,
        name="cim-gbr-v1.0",
        state=state,
        submitter="operator@veldris.internal",
        spec_hash="d" * 64,
        specification=specification,
    )


class _Recording:
    """A scheduler that keeps the plan and says the job is running."""

    def __init__(self) -> None:
        self.plans: list[Any] = []

    def submit(self, plan: Any, name: str = "") -> JobHandle:
        del name
        self.plans.append(plan)
        return JobHandle(driver="local", job_id="1")

    def poll(self, handle: JobHandle) -> JobStatus:
        del handle
        return JobStatus(state=JobState.RUNNING)

    def logs(self, handle: JobHandle, lines: int = 50) -> str:
        del handle, lines
        return ""


class _Orchestrator:
    """Records transitions and answers with an empty history."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def transition(self, run_id: Any, target: Any, **kwargs: Any) -> Any:
        del run_id
        self.payloads.append(dict(kwargs["payload"]))
        return SimpleNamespace(state=target)

    def history(self, run_id: Any) -> Any:
        del run_id
        return iter(())


def _registry() -> Any:
    """The production registry, the same one `dryRunSpecification` builds."""
    from draupnir.svalinn.pki import registry

    return registry()


# ---------------------------------------------------------------------------
# The plan the operator was shown is the plan that is submitted
# ---------------------------------------------------------------------------


def test_the_worker_dispatches_what_the_dry_run_rendered(tmp_path: Path) -> None:
    """AC-F14, asserted rather than assumed.

    `render` is pure by Decision S5 and the conformance harness enforces it, so
    the two calls *should* agree -- but "should" is what the finding was made
    of. This renders the plan the way the dry-run handler does, dispatches the
    run through the worker, and compares.

    The comparison is over what the driver produced. What the estate adds
    afterwards is its own and is not the driver's to know: the accelerator this
    forge offers (a specification is portable across the Forge Matrix and must
    not name one forge's hardware), the confinement profile, and the lease
    references a job redeems at start.
    """
    spec = _spec()
    scheduler = _Recording()
    orchestrator = _Orchestrator()
    run_id = uuid4()

    context = stages.Context(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        scheduler=scheduler,
        scratch=tmp_path,
        site_id=SITE,
        registry=_registry(),
    )
    workdir = context.workdir(run_id)

    # What the dry run shows: the registry, `validate`, then `render`.
    plugin = context.registry.for_spec(spec, "draupnir.train")
    assert list(plugin.driver.validate(spec)) == []
    shown = plugin.driver.render(spec, workdir)

    outcome = stages.dispatch(context, _facts(run_id, specification=spec.as_mapping()))

    assert outcome.result is stages.Result.PLACED, outcome.detail
    dispatched = scheduler.plans[-1]
    assert dispatched.command == shown.command, (
        "the worker submitted a different command from the one the operator was shown"
    )
    driver_rendered = {
        key: value
        for key, value in dispatched.environment.items()
        if not key.startswith("DRAUPNIR_LEASE_")
    }
    assert driver_rendered == dict(shown.environment)


def test_the_dispatched_plan_carries_the_estates_own_additions(tmp_path: Path) -> None:
    """And they are the estate's, not the driver's.

    A driver that named this forge's accelerator would be a driver whose
    specification is not portable across the Forge Matrix (SAD 6.2), and a plan
    with no confinement is the one RF-09 put there.
    """
    spec = _spec()
    scheduler = _Recording()
    run_id = uuid4()
    context = stages.Context(
        orchestrator=_Orchestrator(),  # type: ignore[arg-type]
        scheduler=scheduler,
        scratch=tmp_path,
        site_id=SITE,
        registry=_registry(),
    )

    stages.dispatch(context, _facts(run_id, specification=spec.as_mapping()))

    dispatched = scheduler.plans[-1]
    assert dispatched.sandbox["network"] == "none"
    assert dispatched.sandbox["uid"] != 0


def test_the_chain_records_which_driver_rendered_the_job(tmp_path: Path) -> None:
    """So a reader can tell a run the estate trained from one it simulated.

    This was recorded at TRAINED as the literal string "stand-in" whatever had
    run -- true then, and the kind of truth that quietly stops being one.
    """
    scheduler = _Recording()
    orchestrator = _Orchestrator()
    run_id = uuid4()
    context = stages.Context(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        scheduler=scheduler,
        scratch=tmp_path,
        site_id=SITE,
        registry=_registry(),
    )

    stages.dispatch(context, _facts(run_id, specification=_spec().as_mapping()))

    recorded = orchestrator.payloads[-1]
    assert recorded["job_driver"], "the chain does not record which driver rendered the job"
    assert recorded["job_driver"] != stages.STAND_IN_EXECUTOR
    assert recorded["expected_artefacts"], (
        "the chain does not record what the job said it would leave behind, so the "
        "tick that observes it would look for the stand-in's filename"
    )


# ---------------------------------------------------------------------------
# A run nobody specified is not a run the worker invents a job for
# ---------------------------------------------------------------------------


def test_a_run_with_no_recorded_specification_is_deferred(tmp_path: Path) -> None:
    """Rather than falling back to the stand-in.

    A stand-in substituted silently is the finding. The refusal names what is
    missing and what SAD 6.2 says about it, because "the run did not start"
    sends somebody to the scheduler.
    """
    context = stages.Context(
        orchestrator=_Orchestrator(),  # type: ignore[arg-type]
        scheduler=_Recording(),
        scratch=tmp_path,
        site_id=SITE,
        registry=_registry(),
    )

    outcome = stages.dispatch(context, _facts(uuid4()))

    assert outcome.result is stages.Result.DEFERRED
    assert "no specification" in outcome.detail
    assert "unit of reproduction" in outcome.detail


def test_a_worker_with_no_registry_defers_and_names_the_flag(tmp_path: Path) -> None:
    """The refusal tells an operator both ways out, and neither is silent."""
    context = stages.Context(
        orchestrator=_Orchestrator(),  # type: ignore[arg-type]
        scheduler=_Recording(),
        scratch=tmp_path,
        site_id=SITE,
    )

    outcome = stages.dispatch(context, _facts(uuid4(), specification=_spec().as_mapping()))

    assert outcome.result is stages.Result.DEFERRED
    assert "DRAUPNIR_WORKER_STAND_IN" in outcome.detail


# ---------------------------------------------------------------------------
# The stand-in announces itself
# ---------------------------------------------------------------------------


def test_the_stand_in_is_off_by_default() -> None:
    """A simulation nobody asked for is the problem this finding is about."""
    assert WorkerSettings.from_environment({}).stand_in is False
    assert WorkerSettings.from_environment({"DRAUPNIR_WORKER_STAND_IN": "1"}).stand_in is True


def test_the_stand_in_names_itself_in_the_log(tmp_path: Path) -> None:
    """In the shape `plugin.unverified` uses.

    A simulation that announces itself is a development tool. One that does not
    is a control plane reporting a model it did not train.
    """
    context = stages.Context(
        orchestrator=_Orchestrator(),  # type: ignore[arg-type]
        scheduler=_Recording(),
        scratch=tmp_path,
        site_id=SITE,
        stand_in=True,
    )

    with structlog.testing.capture_logs() as captured:
        stages.dispatch(context, _facts(uuid4()))

    events = [item for item in captured if item.get("event") == "executor.stand-in"]
    assert events, "the development executor ran and said nothing"
    assert events[0]["log_level"] == "warning"
    assert "not a model" in events[0]["reason"]


def test_a_simulated_run_says_so_in_the_chain(tmp_path: Path) -> None:
    """For as long as the chain does, which is longer than any log."""
    orchestrator = _Orchestrator()
    context = stages.Context(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        scheduler=_Recording(),
        scratch=tmp_path,
        site_id=SITE,
        stand_in=True,
    )

    stages.dispatch(context, _facts(uuid4()))

    assert orchestrator.payloads[-1]["job_driver"] == stages.STAND_IN_EXECUTOR


# ---------------------------------------------------------------------------
# A run is not judged against itself
# ---------------------------------------------------------------------------


def _judging_context(tmp_path: Path, **overrides: Any) -> stages.Context:
    return stages.Context(
        orchestrator=_Orchestrator(),  # type: ignore[arg-type]
        scheduler=_Recording(),
        scratch=tmp_path,
        site_id=SITE,
        registry=_registry(),
        **overrides,
    )


def test_a_run_with_no_baseline_is_deferred_rather_than_passed(tmp_path: Path) -> None:
    """The acceptance criterion, and the reason the old code always passed.

    `_baselines` returned `measurement * 0.95`, so every relative gate compared
    a run against ninety-five per cent of its own score. That is not a lenient
    baseline; it is no baseline, expressed in a way that always passes.
    """
    suite = SimpleNamespace(key="general", version="2026.01", gates=["E1", "E2"])
    context = _judging_context(tmp_path)

    with pytest.raises(stages.NoJudgementError) as refusal:
        stages._baseline_for(
            context,
            suite=suite,
            artefact_kind="adapter",
            facts=_facts(uuid4(), specification=_spec().as_mapping()),
        )

    assert "nothing to compare against" in str(refusal.value)
    assert "passes by construction" in str(refusal.value)


def test_a_recorded_baseline_is_what_a_relative_gate_reads(tmp_path: Path) -> None:
    """Out of the chain, and resolved by suite, artefact kind and jurisdiction."""
    suite = SimpleNamespace(key="general", version="2026.01", gates=["E1", "E2"])
    baseline = Baseline(
        artefact_sha256="a" * 64,
        artefact_kind="adapter",
        suite="general",
        suite_version="2026.01",
        measurements={"E1": 0.80, "E2": 0.70},
        captured_at=NOW,
        jurisdiction="GBR",
    )
    context = _judging_context(tmp_path, baselines=registry_of([baseline]))

    values = stages._baseline_for(
        context,
        suite=suite,
        artefact_kind="adapter",
        facts=_facts(uuid4(), specification=_spec().as_mapping()),
    )

    assert values == {"E1": 0.80, "E2": 0.70}


def test_the_judgement_is_deferred_when_a_gate_was_not_measured(tmp_path: Path) -> None:
    """A gate with no measurement is not a gate that passed.

    Judging the rest would report a pass for a suite that did not run, which is
    the shape of every finding in this register.
    """
    suite = SimpleNamespace(key="general", version="2026.01", gates=["E1", "E2"])

    class _Silent:
        name = "lm-eval"
        driver = SimpleNamespace(collect_gates=lambda workdir, version: ())

    context = _judging_context(tmp_path)
    context.registry = SimpleNamespace(for_spec=lambda spec, group: _Silent())

    with pytest.raises(stages.NoJudgementError) as refusal:
        stages._measured(
            context,
            _facts(uuid4(), specification=_spec().as_mapping()),
            workdir=tmp_path,
            suite=suite,
        )

    assert "no measurement for E1, E2" in str(refusal.value)
