"""Which executor model is in force, and the difference from the other one.

RF-E16. The finding was a document saying a control was in force that nothing
applied: `docs/DEPLOYMENT.md` described the appliances as running "one
container per job", `sandbox.py` generated the profile for exactly that, and
VLD-INF-SINDRI-001 Rev 3.3 runs `python train.py` inside `/forge/venv` under
`slurmd`.

A gap of that shape does not announce itself. The design is good, the module is
complete, the tests pass, and an auditor reading the threat model finds T7
closed. What is missing is the estate, and nothing in the repository was in a
position to say so. These tests are what says so.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest

from draupnir.svalinn import containment, sandbox

pytestmark = pytest.mark.unit

#: Everything that decides how a job runs and dispatches it. If the sandbox
#: profile were a control in force, it would be applied from one of these.
DISPATCH_PATH = (
    "draupnir/worker",
    "draupnir/motsognir",
    "draupnir/procedures",
    "plugins/motsognir_slurm",
    "plugins/motsognir_slurmrest",
    "plugins/local_subprocess",
)


def sources(*roots: str) -> list[Path]:
    """Every Python file under `roots` that exists in this checkout."""
    found: list[Path] = []
    for root in roots:
        base = Path(root)
        if base.exists():
            found.extend(sorted(base.rglob("*.py")))
    return found


def imported_names(path: Path) -> set[str]:
    """Every module name `path` imports, dotted and whole."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


# ---------------------------------------------------------------------------
# The profile is a specification, and nothing treats it as a control
# ---------------------------------------------------------------------------


def rendered_profiles(path: Path) -> set[str]:
    """Every `.render(...)` call in `path`, by the name it is called on.

    `SandboxProfile.render` turns a profile into a runtime's arguments --
    `--network=none`, `--cap-drop=ALL`, `--read-only`. It is the only method on
    the profile that could confine anything, and calling it on the dispatch
    path is what "the estate applies the profile" would look like in code.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.func.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "render"
        and isinstance(node.func.value, ast.Name)
    }


def test_no_dispatched_plan_applies_the_sandbox_profile() -> None:
    """The acceptance criterion, structurally, and narrowed to what it means.

    This asserted that nothing on the dispatch path *imports* `sandbox`, which
    was right until RF-09 and is not right now: the worker composes the profile
    into every plan it builds, because the plan carrying the profile is the
    control plane's job even where applying it is not.

    Carrying and applying are different things and the distinction is the whole
    of RF-E16. A plan that names a profile the appliance ignores is an honest
    specification travelling with the work; a control plane that *rendered* the
    profile into runtime arguments would be claiming a confinement Sindri
    cannot honour, because VLD-INF-SINDRI-001 Rev 3.3 runs `python train.py`
    inside `/forge/venv` under `slurmd` and there is no container to put the
    arguments on.

    So the check moved from the import to `render`, which is the only method
    that could confine anything. It must still fail the day a container
    executor is built -- at which point `containment.SLURM_SHARED_VENV`,
    docs/DEPLOYMENT.md and AC-S11 all say the opposite and this is the thing
    that asks whether they were updated with it.
    """
    offenders = [
        path.as_posix() for path in sources(*DISPATCH_PATH) if "profile" in rendered_profiles(path)
    ]

    assert not offenders, (
        f"{', '.join(offenders)} renders the sandbox profile into runtime arguments. "
        "Either a container executor now exists -- in which case "
        "`containment.SLURM_SHARED_VENV`, docs/DEPLOYMENT.md and AC-S11 are all stale "
        "and say the opposite -- or something is applying a profile the estate cannot "
        "honour."
    )


def test_every_plan_the_worker_dispatches_carries_a_profile() -> None:
    """RF-09's other half, and the reason the test above had to narrow.

    `sandbox.py` commented that the plan carries lease references while
    `execution.stand_in_plan` wrote `{"PYTHONHASHSEED": "0"}` and no plan ever
    went near a profile. A security module nobody calls is worse than an absent
    one, because it reads as coverage.
    """
    from draupnir.motsognir import execution

    plan = execution.stand_in_plan(
        Path("out.bin"),
        [Path("in.bin")],
        workdir=Path("work"),
        sandbox=sandbox.for_job(
            workdir="work", artefacts=[("in.bin", "/inputs/in.bin")]
        ).as_payload(),
    )

    assert plan.sandbox["network"] == "none"
    assert plan.sandbox["uid"] != 0
    assert plan.sandbox["capabilities"] == []
    assert all(mount["readOnly"] for mount in plan.sandbox["mounts"]), (
        "an artefact mount is writable, which is threat T8 reached from inside"
    )
    # And the profile has no way to turn any of it on: `network`, `uid` and
    # `capabilities` are properties computed from constants, not fields.
    assert not hasattr(sandbox.SandboxProfile, "allow_network")
    assert "network" not in {field.name for field in fields(sandbox.SandboxProfile)}


def test_the_sandbox_module_says_it_is_not_in_force() -> None:
    """A module that is a specification has to be readable as one.

    The test asserts the sentence exists because the sentence is the control
    here. Someone reading `sandbox.py` to answer "is T7 mitigated" must not
    have to check the dispatch path to find out.
    """
    doc = sandbox.__doc__ or ""

    assert "not a control in force" in doc
    assert "containment" in doc, "the docstring does not point at what is in force instead"


def test_the_package_docstring_does_not_claim_the_sandbox_is_enforced() -> None:
    """SVALINN's own summary is where an auditor starts."""
    import draupnir.svalinn as package

    doc = package.__doc__ or ""

    assert "specification" in doc
    assert "not a control in force" in doc


# ---------------------------------------------------------------------------
# What is in force
# ---------------------------------------------------------------------------


def test_the_estate_runs_jobs_as_the_administrator() -> None:
    """No dedicated account exists, and this is where that is written down.

    VLD-INF-SINDRI-001 section 25 creates `nvidia` on all three appliances,
    puts it in `sudo`, and section 26 gives it `/forge`. Nothing introduces a
    second account, so a job runs as the administrator.
    """
    assert containment.SLURM_SHARED_VENV.account == "nvidia"
    assert containment.SLURM_SHARED_VENV.dedicated_account is False


def test_cgroup_containment_does_not_constrain_memory_or_devices() -> None:
    """Two occurrences of the word `cgroup` are not two constraints.

    `slurm.conf` sets `ProctrackType=proctrack/cgroup` and
    `TaskPlugin=task/cgroup`, and there is no `cgroup.conf` in the manual at
    all. `proctrack/cgroup` is a cleanup guarantee -- a job's processes are
    killed with it -- and `task/cgroup` places tasks in cgroups so that a
    `cgroup.conf` can constrain them. With no such file every constraint takes
    its default, and `ConstrainRAMSpace`, `ConstrainDevices` and
    `ConstrainCores` all default to `no`.
    """
    in_force = containment.SLURM_SHARED_VENV

    assert "proctrack/cgroup" in in_force.process_tracking
    assert in_force.memory_constrained is False
    assert in_force.devices_constrained is False


def test_the_job_has_a_network_because_the_work_requires_one() -> None:
    """This shortfall is not an oversight and must not be recorded as one.

    The job reports to MLflow on ANDVARI and reads the corpus over NFS from
    the same host. A sandbox with no network namespace would not run it.
    """
    assert containment.SLURM_SHARED_VENV.outbound_network != "none"
    assert "MLflow" in containment.SLURM_SHARED_VENV.outbound_network


def test_the_checkpoint_path_is_writable() -> None:
    """The other property the profile forbids and the work requires."""
    assert any(
        "models/adapters" in path for path in containment.SLURM_SHARED_VENV.writable_paths
    ), "the job writes checkpoints under /forge/vault/models/adapters"


# ---------------------------------------------------------------------------
# The gap, as a list somebody can work through
# ---------------------------------------------------------------------------


def test_every_shortfall_names_its_consequence() -> None:
    """A gap with no consequence attached is one nobody prioritises."""
    found = containment.shortfall()

    assert found, "the two models differ; a shortfall list that is empty is wrong"
    for item in found:
        assert item.consequence, f"{item.field_name} has no stated consequence"
        assert len(item.consequence) > 40, f"{item.field_name}'s consequence says too little"


def test_four_of_the_seven_shortfalls_are_configuration_not_architecture() -> None:
    """The reason to write the gap as a list rather than a paragraph.

    A dedicated account, `ConstrainRAMSpace`, `ConstrainDevices` and
    `--export=NONE` need no container runtime, no image for the training stack
    and no change to the batch scripts in Part 5. That is only visible once the
    difference is enumerated.
    """
    found = containment.shortfall()
    closeable = {item.field_name for item in found if item.closeable_by_configuration}

    assert closeable == {
        "dedicated_account",
        "memory_constrained",
        "devices_constrained",
        "environment_isolated",
    }


def test_the_network_shortfall_is_not_closeable_by_configuration() -> None:
    """It is the one that genuinely needs the other execution model.

    Closing it means a container with an egress exception for MLflow, which is
    a Procedure S5 change and a change to every batch script -- not a setting.
    """
    found = {item.field_name: item for item in containment.shortfall()}

    assert found["outbound_network"].closeable_by_configuration is False


def test_a_site_that_adopted_containers_would_report_no_shortfall() -> None:
    """The comparison is a function of the two models, not a fixed list."""
    assert (
        containment.shortfall(
            in_force=containment.CONTAINER_PER_JOB, intended=containment.CONTAINER_PER_JOB
        )
        == ()
    )


def test_the_containment_record_is_serialisable_for_the_ledger() -> None:
    """SAD 6.2 makes the record the unit of reproduction, so it must be true.

    The point of `as_payload` here rather than on the sandbox profile: an
    entry claiming a job ran as uid 65532 with no network would be false at
    Sindri, and would stay false for as long as the chain does.
    """
    payload = containment.for_site("sindri").as_payload()

    assert payload["model"] == "slurm-shared-venv"
    assert payload["account"] == "nvidia"
    assert payload["dedicatedAccount"] is False
    assert payload["outboundNetwork"] != "none"


def test_the_two_models_are_described_by_the_same_type() -> None:
    """Otherwise the comparison is prose and drifts."""
    assert isinstance(containment.SLURM_SHARED_VENV, containment.Containment)
    assert isinstance(containment.CONTAINER_PER_JOB, containment.Containment)
    assert containment.SLURM_SHARED_VENV.model != containment.CONTAINER_PER_JOB.model


def test_the_deployment_document_does_not_claim_containers() -> None:
    """The document the finding named. RF-E16.

    A reader following the deployment guide is told what runs where, and being
    told "one container per job" is how the gap propagated into the threat
    model in the first place.
    """
    text = Path("docs/DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "one container per job" not in text, (
        "docs/DEPLOYMENT.md still describes the appliances as running one container "
        "per job. They run Slurm jobs in a shared virtual environment."
    )
