"""Every coverage target names something coverage can find. RF-19.

`tasks.py` passed `--cov=draupnir/api/idempotency.py` and ten more like it.
Coverage treats a `--cov` argument as a module or package name, not a file
path, so each of the eleven found no module of that name, warned
`module-not-imported`, and measured nothing -- while the percentage still
printed, computed over whatever else was named. Both floors therefore read
higher than they were.

Two checks, and the second is the one that matters. Asserting the arguments are
importable catches the mistake as it is written. Asserting they are *the same
list the run is verified against* catches the version of the mistake that
arrives later: a target list and a set of flags that can drift apart is a
target list that will.

The run itself is verified in `tasks.verify_coverage`, against the report
rather than against the warning -- a warning on standard error is a thing a
pipeline scrolls past.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(REPO_ROOT))

import tasks  # noqa: E402 -- the repository root has to be on the path first

#: Every declared target, with the stage it belongs to, for a readable failure.
DECLARED = (
    [("unit", target) for target in tasks.UNIT_COVERAGE]
    + [("contract", target) for target in tasks.CONTRACT_COVERAGE]
    + [("integration", target) for target in tasks.INTEGRATION_COVERAGE]
)


@pytest.mark.parametrize(("stage", "target"), DECLARED)
def test_every_coverage_target_is_an_importable_module(stage: str, target: str) -> None:
    """The defect, stated directly.

    A path is not a module name. `importlib.util.find_spec` answers the same
    question coverage asks and answers it here, where the failure names the
    target instead of scrolling past as a warning.
    """
    assert "/" not in target and not target.endswith(".py"), (
        f"the {stage} stage names {target!r}, which is a path. Coverage reads a "
        "--cov argument as a module name, finds nothing, and measures nothing -- "
        "silently, because the percentage still prints."
    )
    assert importlib.util.find_spec(target) is not None, (
        f"the {stage} stage names {target!r}, which does not import"
    )


@pytest.mark.parametrize(("stage", "target"), DECLARED)
def test_every_coverage_target_holds_code(stage: str, target: str) -> None:
    """A package with no modules under it measures nothing either.

    An empty package imports perfectly well, so the check above would pass on
    one -- and the percentage would again be computed over a smaller
    denominator than it names.
    """
    module = importlib.import_module(target)
    source = Path(str(module.__file__))
    files = list(source.parent.rglob("*.py")) if source.name == "__init__.py" else [source]
    substantial = [item for item in files if item.stat().st_size > 0]

    assert substantial, f"the {stage} stage names {target!r}, which holds no code"


def test_the_flags_a_stage_runs_are_the_targets_it_is_verified_against() -> None:
    """The drift this is really guarding.

    `coverage_flags` builds the `--cov` arguments and `verify_coverage` checks
    the report; both take the same tuple, and this asserts that arrangement is
    still in place. A stage that built its flags inline could name a target the
    verification never looks for, which is exactly the state RF-19 found -- the
    flags said eleven modules and nothing checked that eleven were measured.
    """
    for targets in (tasks.UNIT_COVERAGE, tasks.CONTRACT_COVERAGE, tasks.INTEGRATION_COVERAGE):
        flags = tasks.coverage_flags(targets, report=Path("unused.xml"), floor=1)
        named = [flag.removeprefix("--cov=") for flag in flags if flag.startswith("--cov=")]

        assert named == list(targets)


def test_no_stage_passes_a_coverage_path_anywhere_in_the_file() -> None:
    """Including in a stage nobody has added to the lists above.

    The lists are the intended mechanism, but a `--cov=` written inline in a
    new task would bypass them entirely and bring the defect back. This reads
    the file.
    """
    source = (REPO_ROOT / "tasks.py").read_text(encoding="utf-8")
    inline = re.findall(r'"--cov=([^"]+)"', source)

    paths = [target for target in inline if "/" in target or target.endswith(".py")]

    assert not paths, (
        f"these coverage arguments are paths rather than module names: {paths}. "
        "Coverage measures nothing for each of them and prints a percentage anyway."
    )


def test_the_floors_are_the_figures_the_stages_actually_reach() -> None:
    """Set to the achieved level rather than fitted to it. RF-19, then RF-20.

    Two of these read lower than RF-19 left them, and that is a wider
    measurement rather than a regression: RF-20 brought HODD, GLEIPNIR, the
    driver interfaces, the worker, the read model and nine more edge modules
    under a floor for the first time, so the denominators changed. The absolute
    figures went up in every stage -- the unit one from 4,788 statements
    covered to 6,661.

    Asserted as a lower bound rather than an equality: raising a floor after
    genuinely improving coverage should not need this test edited, but lowering
    one should be a decision somebody makes here. What stops a floor being
    lowered to fit a result is the check below, not this one.
    """
    assert tasks.UNIT_FLOOR >= 90
    assert tasks.CONTRACT_FLOOR >= 87
    assert tasks.INTEGRATION_FLOOR >= 77


# ---------------------------------------------------------------------------
# Every shipped module is measured somewhere. RF-20.
# ---------------------------------------------------------------------------


def shipped() -> list[str]:
    """Every module under `draupnir/`, as a repository-relative path.

    Derived from the tree rather than listed, which is the requirement: a new
    module has to arrive measured, and a list somebody maintains is a list that
    lags the code by exactly as long as nobody notices.
    """
    package = REPO_ROOT / "draupnir"
    return sorted(
        item.relative_to(REPO_ROOT).as_posix()
        for item in package.rglob("*.py")
        if "__pycache__" not in item.parts
    )


def covered_by(path: str) -> list[str]:
    """Which stages' targets measure this module, if any."""
    dotted = path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
    stages = {
        "unit": tasks.UNIT_COVERAGE,
        "contract": tasks.CONTRACT_COVERAGE,
        "integration": tasks.INTEGRATION_COVERAGE,
    }
    return [
        stage
        for stage, targets in stages.items()
        if any(dotted == target or dotted.startswith(f"{target}.") for target in targets)
    ]


@pytest.mark.parametrize("path", shipped())
def test_every_shipped_module_is_under_a_coverage_floor(path: str) -> None:
    """RF-20's whole point.

    `draupnir/hodd/` and `draupnir/gleipnir/` were named by no stage. They hold
    the licence register, the retention rule, the policy gate and the release
    sign-off, they have unit tests, and coverage in them could have fallen to
    zero without a stage noticing -- because a floor only constrains what it
    measures.

    An exclusion is allowed and is a decision: it goes in
    `tasks.COVERAGE_EXCLUSIONS` with the reason, where somebody reviewing this
    can disagree with it.
    """
    if path in tasks.COVERAGE_EXCLUSIONS:
        assert tasks.COVERAGE_EXCLUSIONS[path].strip(), (
            f"{path} is excluded from coverage with no reason given"
        )
        return

    assert covered_by(path), (
        f"{path} is measured by no stage. Add it to the list of the stage that "
        "exercises it, or to COVERAGE_EXCLUSIONS with the reason it cannot be."
    )


def test_nothing_is_excluded_that_is_also_measured() -> None:
    """An exclusion that is measured anyway is a stale decision.

    It reads as "this cannot be covered" while the coverage is being collected,
    which is the sort of comment that outlives its reason by years.
    """
    contradictory = [path for path in tasks.COVERAGE_EXCLUSIONS if covered_by(path)]

    assert not contradictory, f"these are excluded and measured at the same time: {contradictory}"


def test_every_exclusion_names_a_file_that_exists() -> None:
    """So a deleted module does not leave a permanent excuse behind it."""
    absent = [path for path in tasks.COVERAGE_EXCLUSIONS if not (REPO_ROOT / path).is_file()]

    assert not absent, f"these exclusions name nothing: {absent}"


def test_no_module_is_measured_by_two_stages() -> None:
    """Not an error, but worth knowing about.

    Two stages measuring one module means two floors constrain it and the
    weaker one is doing nothing. It also doubles the module's weight across the
    pipeline's reports, which makes a change to it look larger than it is.
    """
    twice = {path: covered_by(path) for path in shipped() if len(covered_by(path)) > 1}

    assert not twice, f"measured by more than one stage: {twice}"


def test_a_target_that_measured_nothing_is_refused(tmp_path: Path) -> None:
    """`verify_coverage` is the half that runs in the pipeline.

    Written against a report that names one of two targets, which is what a
    coverage run produces when an argument resolves to nothing: a valid report,
    a printed percentage, and a module missing from it.
    """
    report = tmp_path / "coverage.xml"
    report.write_text(
        '<?xml version="1.0" ?><coverage><packages><package name="draupnir.api">'
        '<classes><class filename="draupnir/api/pagination.py" name="pagination.py"/>'
        "</classes></package></packages></coverage>",
        encoding="utf-8",
    )

    tasks.verify_coverage(report, ("draupnir.api.pagination",))

    with pytest.raises(tasks.Failure, match="measured nothing"):
        tasks.verify_coverage(report, ("draupnir.api.pagination", "draupnir.api.idempotency"))


def test_a_missing_report_is_refused_rather_than_treated_as_empty(tmp_path: Path) -> None:
    """Otherwise a run that wrote no report would verify vacuously."""
    with pytest.raises(tasks.Failure, match="was not written"):
        tasks.verify_coverage(tmp_path / "absent.xml", ("draupnir.api.pagination",))


def test_a_package_target_is_satisfied_by_a_module_beneath_it(tmp_path: Path) -> None:
    """`--cov=draupnir.motsognir` is measured by the files under it.

    A verification that demanded the package's own `__init__` appear would
    fail on every package whose `__init__` is empty -- which is most of them,
    and would make the guard useless exactly where it is most needed.
    """
    report = tmp_path / "coverage.xml"
    report.write_text(
        '<?xml version="1.0" ?><coverage><packages><package name="draupnir.motsognir">'
        '<classes><class filename="draupnir/motsognir/placement.py" name="placement.py"/>'
        "</classes></package></packages></coverage>",
        encoding="utf-8",
    )

    tasks.verify_coverage(report, ("draupnir.motsognir",))
