"""AC-N9: a new ExportDriver, added and working, in under 200 lines, no core change.

The criterion has three clauses and each is measured here rather than asserted:

  "added"          the driver is discovered by entry point and resolved through
                   the registry, so nothing in DRAUPNIR was edited to admit it
  "working"        the plan it renders is submitted through the reference
                   ScheduleDriver, runs, and produces the artefact it promised
  "under 200 lines" the distribution's source is counted

The "no core file modified" clause is held by an import contract rather than
by this test: `.importlinter` forbids a driver from importing `draupnir.core`
at all, so a driver that needed a core change could not be written in the
first place.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from draupnir.core.plugins import DEV_VARIABLE, MissingCapabilityError, PluginRegistry
from draupnir.interfaces.testing import sample_spec
from draupnir.interfaces.types import JobState

pytestmark = pytest.mark.contract

REPO_ROOT = Path(__file__).resolve().parents[2]
DISTRIBUTION = REPO_ROOT / "plugins" / "targz_export"
LINE_BUDGET = 200

EXPORT = "skidbladnir.targz/v1"
SCHEDULER = "motsognir.local_subprocess/v1"


@pytest.fixture(scope="module")
def registry() -> PluginRegistry:
    return PluginRegistry.discover(environ={DEV_VARIABLE: "1"})


def source_lines() -> int:
    """Count the driver's own source: no blanks, no comments, no docstrings."""
    import ast

    total = 0
    for path in sorted(DISTRIBUTION.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Strip docstrings, then count the lines that remain non-blank.
        for node in ast.walk(tree):
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef) and (
                docstring := ast.get_docstring(node, clean=False)
            ):
                del docstring
        rendered = ast.unparse(tree)
        total += sum(
            1 for line in rendered.splitlines() if line.strip() and not line.strip().startswith("#")
        )
    return total


def test_the_driver_is_under_the_line_budget() -> None:
    """AC-N9: "under 200 lines"."""
    lines = source_lines()
    print(f"\n  AC-N9: {DISTRIBUTION.name} is {lines} lines, budget {LINE_BUDGET}")  # noqa: T201
    assert lines < LINE_BUDGET, f"{lines} lines exceeds the {LINE_BUDGET} line budget of AC-N9"


def test_the_driver_is_added_by_installation_alone(registry: PluginRegistry) -> None:
    """AC-N9: "added". Discovered by entry point; nothing in DRAUPNIR names it."""
    assert EXPORT in registry.names("draupnir.export")

    plugin = registry.resolve("draupnir.export", EXPORT)
    assert plugin.capabilities == frozenset({"targz"})
    assert plugin.distribution == "draupnir-targz-export"


def test_the_registry_refuses_a_format_the_driver_does_not_declare(
    registry: PluginRegistry,
) -> None:
    """SAD 10.3 rule 4, before an allocation is consumed."""
    asking_for_nvfp4 = sample_spec(
        release={"route": "B", "formats": ["nvfp4"], "approval": "required"}
    )
    with pytest.raises(MissingCapabilityError) as raised:
        registry.require_capabilities(
            registry.resolve("draupnir.export", EXPORT),
            asking_for_nvfp4.capabilities_for("draupnir.export"),
        )
    assert raised.value.missing == frozenset({"nvfp4"})


def test_the_driver_validates_a_specification_it_cannot_run(registry: PluginRegistry) -> None:
    driver = registry.resolve("draupnir.export", EXPORT).driver
    problems = driver.validate(sample_spec())
    assert [problem.code for problem in problems] == ["unsupported-format"]
    assert "nvfp4" in problems[0].message


def test_the_new_format_works_end_to_end(registry: PluginRegistry, tmp_path: Path) -> None:
    """AC-N9: "working". Render, submit, run, collect -- through the registry.

    Both halves come from installed distributions: the export driver renders
    the plan and the reference scheduler runs it. No core file knows either of
    them exists.
    """
    # Something to package.
    source = tmp_path / "adapter"
    source.mkdir()
    (source / "weights.bin").write_bytes(b"adapter weights" * 100)
    (source / "config.json").write_text('{"rank": 64}', encoding="utf-8")

    spec = sample_spec(
        base={"artefact": "hodd://sindri/adapters/adapter", "expectSha256": "a" * 64},
        release={"route": "B", "formats": ["targz"], "approval": "required"},
    )

    exporter = registry.for_spec(spec, "draupnir.export").driver
    scheduler = registry.resolve("draupnir.schedule", SCHEDULER).driver

    plan = exporter.render(spec, tmp_path)
    assert plan.expected_artefacts == ("export.tar.gz",)

    handle = scheduler.submit(plan)
    status = _wait(scheduler, handle)
    assert status.state is JobState.COMPLETED, (
        f"the export job did not complete: {status}\n{scheduler.logs(handle)}"
    )

    produced = exporter.collect(tmp_path)
    assert len(produced.artefacts) == 1

    artefact = produced.artefacts[0]
    assert artefact.path == "export.tar.gz"
    assert artefact.size > 0
    assert len(artefact.sha256) == 64
    # The archive holds the directory and both files.
    assert produced.metrics["members"] == 3

    # And the hash is the hash of what is actually on disk.
    import hashlib

    on_disk = hashlib.sha256((tmp_path / "export.tar.gz").read_bytes()).hexdigest()
    assert artefact.sha256 == on_disk


def _wait(scheduler: Any, handle: Any, timeout: float = 60.0) -> Any:
    deadline = time.monotonic() + timeout
    status = scheduler.poll(handle)
    while time.monotonic() < deadline:
        if status.state in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}:
            return status
        time.sleep(0.05)
        status = scheduler.poll(handle)
    return status
