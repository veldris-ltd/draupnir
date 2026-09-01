"""Conformance suites a driver package can inherit.

A driver author writes this and gets the whole suite:

    from draupnir.interfaces.testing import ScheduleDriverConformance
    from my_driver import MyDriver

    class TestMyDriver(ScheduleDriverConformance):
        @pytest.fixture
        def driver(self):
            return MyDriver()

pytest collects the inherited tests, so a new check added here is a check every
driver acquires without touching a driver package. That is the point: a
conformance suite that each driver copies is a conformance suite that drifts.

The checks themselves are in `harness.py` and need no test framework, so the
same suite can be run from a script by a driver author who does not use pytest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from draupnir.interfaces.testing.fixtures import sample_spec
from draupnir.interfaces.testing.harness import (
    check_collect_does_not_mutate,
    check_driver,
    check_parse_progress,
    check_render_has_no_side_effects,
    check_render_is_deterministic,
    check_render_makes_no_network_call,
    check_schedule_driver,
    check_validate,
    describe,
)
from draupnir.interfaces.types import JobPlan, ResourceRequest, RunSpec


def _assert(findings: list[Any]) -> None:
    assert not findings, "\n" + describe(findings)


class DriverConformance:
    """What SAD 8.2 requires of every plug-in. Subclass and provide `driver`."""

    @pytest.fixture
    def driver(self) -> Any:  # pragma: no cover -- subclasses override
        raise NotImplementedError("provide a `driver` fixture")

    @pytest.fixture
    def spec(self) -> RunSpec:
        """The specification the suite renders. Override to use your own."""
        return sample_spec()

    def test_declares_a_versioned_name_and_capabilities(self, driver: Any) -> None:
        _assert(check_driver(driver))


class JobDriverConformance(DriverConformance):
    """For `TrainDriver`, `MergeDriver`, `EvalDriver` and `ExportDriver`."""

    def test_validate_returns_every_problem_rather_than_raising(
        self, driver: Any, spec: RunSpec
    ) -> None:
        _assert(check_validate(driver, spec))

    def test_render_is_deterministic(self, driver: Any, spec: RunSpec, tmp_path: Path) -> None:
        """SAD Decision S5: the plan is a function of the specification."""
        _assert(check_render_is_deterministic(driver, spec, tmp_path))

    def test_render_makes_no_network_call(self, driver: Any, spec: RunSpec, tmp_path: Path) -> None:
        """SAD Decision S5: no driver resolves a dependency while planning."""
        _assert(check_render_makes_no_network_call(driver, spec, tmp_path))

    def test_render_has_no_side_effects(self, driver: Any, spec: RunSpec, tmp_path: Path) -> None:
        """SAD Decision S5, and what makes a dry run safe (AC-F14)."""
        _assert(check_render_has_no_side_effects(driver, spec, tmp_path))

    def test_parse_progress_is_total_and_pure(self, driver: Any) -> None:
        _assert(check_parse_progress(driver))

    def test_collect_does_not_mutate_what_it_reads(self, driver: Any, tmp_path: Path) -> None:
        _assert(check_collect_does_not_mutate(driver, tmp_path))


class ScheduleDriverConformance(DriverConformance):
    """For `ScheduleDriver`. Provide a `plan` fixture, or accept the default."""

    @pytest.fixture
    def plan(self, tmp_path: Path) -> JobPlan:
        """A job that exits zero quickly, on any platform."""
        return JobPlan(
            command=("python", "-c", "print('conformance')"),
            workdir=str(tmp_path),
            resources=ResourceRequest(partition="default", nodes=1),
        )

    def test_submits_polls_cancels_and_reads_logs(self, driver: Any, plan: JobPlan) -> None:
        """SAD 8.2 and AC-F13: cancelling leaves a defined state."""
        _assert(check_schedule_driver(driver, plan))
