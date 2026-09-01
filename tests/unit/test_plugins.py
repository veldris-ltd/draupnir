"""The plug-in loader, and the four rules of SAD 10.3 it enforces.

Entry points are presented directly rather than by installing distributions,
so that a version the core must refuse can be tested without publishing one.
The reference drivers are loaded for real in `tests/contract/`.
"""

from __future__ import annotations

import sys
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any

import pytest

from draupnir.core.plugins import (
    DEV_VARIABLE,
    DriverNotFoundError,
    MissingCapabilityError,
    PluginRegistry,
    developer_mode,
    report,
)
from draupnir.interfaces.signing import SignatureStatus
from draupnir.interfaces.testing import sample_spec
from draupnir.interfaces.types import (
    JobHandle,
    JobPlan,
    JobState,
    JobStatus,
    ProgressEvent,
    RunArtefacts,
    RunSpec,
    ValidationError,
)

DEV = {DEV_VARIABLE: "1"}


# ---------------------------------------------------------------------------
# Drivers to present to the loader
# ---------------------------------------------------------------------------


class Scheduler:
    """A conforming ScheduleDriver."""

    def __init__(
        self, name: str = "motsognir.fake/v1", capabilities: frozenset[str] | None = None
    ) -> None:
        self.name = name
        self.capabilities: frozenset[str] = (
            capabilities if capabilities is not None else frozenset({"local"})
        )

    def submit(self, plan: JobPlan) -> JobHandle:
        del plan
        return JobHandle(driver=self.name, job_id="1")

    def poll(self, handle: JobHandle) -> JobStatus:
        del handle
        return JobStatus(state=JobState.COMPLETED, exit_code=0)

    def cancel(self, handle: JobHandle) -> JobStatus:
        del handle
        return JobStatus(state=JobState.CANCELLED)

    def logs(self, handle: JobHandle) -> str:
        del handle
        return ""


class Trainer:
    """A conforming TrainDriver."""

    def __init__(self, capabilities: frozenset[str] | None = None) -> None:
        self.name = "hamarr.fake/v1"
        self.capabilities = (
            capabilities if capabilities is not None else frozenset({"lora", "bf16"})
        )

    def validate(self, spec: RunSpec) -> list[ValidationError]:
        del spec
        return []

    def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
        del workdir
        return JobPlan(command=("train", spec.metadata.name))

    def parse_progress(self, line: str) -> ProgressEvent | None:
        del line
        return None

    def collect(self, workdir: Path) -> RunArtefacts:
        del workdir
        return RunArtefacts()


class NotADriver:
    """Registered under a group whose Protocol it does not satisfy."""

    name = "motsognir.broken/v1"
    capabilities: frozenset[str] = frozenset()


class Verifier:
    """A verifier that reports whatever the test asks it to."""

    def __init__(self, verified: bool = True) -> None:
        self.verified = verified

    def verify(self, distribution: str, version: str) -> SignatureStatus:
        del version
        return SignatureStatus(
            verified=self.verified,
            signer="veldris-pki" if self.verified else None,
            reason=None if self.verified else f"{distribution} carries no signature",
        )


class Exploding:
    """A plug-in that fails on import. A plug-in may raise anything."""

    def __init__(self) -> None:
        msg = "this plug-in explodes on import"
        raise RuntimeError(msg)


def point(name: str, group: str, target: Any) -> EntryPoint:
    """A real `EntryPoint` resolving to `target`.

    Real rather than a stand-in: `EntryPoint.load` imports a module and walks
    attributes, and a test that replaced that would not be testing the thing
    the installed environment actually does. The target is published as a
    module attribute so the real machinery can find it.
    """
    attribute = f"_target_{abs(hash((name, group, id(target)))):x}"
    setattr(sys.modules[__name__], attribute, target)
    return EntryPoint(name=name, value=f"{__name__}:{attribute}", group=group)


def registry(
    *points: EntryPoint, verifier: Any = None, environ: dict[str, str] | None = None
) -> PluginRegistry:
    return PluginRegistry.discover(
        points=points,
        verifier=verifier if verifier is not None else Verifier(verified=True),
        environ=environ if environ is not None else {},
    )


def schedule_point(driver: Any, name: str | None = None) -> EntryPoint:
    return point(name or driver.name, "draupnir.schedule", driver)


# ---------------------------------------------------------------------------
# Rule 1 and 2: versioned names, current and previous major only
# ---------------------------------------------------------------------------


def test_a_signed_conforming_plugin_loads() -> None:
    found = registry(schedule_point(Scheduler()))
    assert found.names("draupnir.schedule") == ("motsognir.fake/v1",)
    assert found.failures == ()

    plugin = found.resolve("draupnir.schedule", "motsognir.fake/v1")
    assert plugin.name.namespace == "motsognir"
    assert plugin.name.major == 1
    assert plugin.capabilities == frozenset({"local"})
    assert plugin.signature.verified


def test_an_unversioned_name_is_refused() -> None:
    found = registry(schedule_point(Scheduler(), name="motsognir.fake"))
    assert found.names("draupnir.schedule") == ()
    assert "not a versioned interface name" in found.failures[0].reason


def test_a_future_major_version_is_refused_naming_what_was_expected() -> None:
    found = registry(schedule_point(Scheduler("motsognir.fake/v3"), name="motsognir.fake/v3"))
    reason = found.failures[0].reason
    assert "major version 3" in reason
    assert "v1" in reason

    with pytest.raises(DriverNotFoundError):
        found.resolve("draupnir.schedule", "motsognir.fake/v3")


def test_the_previous_major_version_would_be_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    # SAD 10.3 rule 2. With the current major at 2, both v2 and v1 load and v3
    # does not; this is the only test that can show the rule rather than the
    # special case of being at v1 with no predecessor.
    import draupnir.interfaces.protocols as protocols

    monkeypatch.setitem(protocols.CURRENT_MAJOR, "draupnir.schedule", 2)

    found = registry(
        schedule_point(Scheduler("motsognir.a/v2")),
        schedule_point(Scheduler("motsognir.b/v1")),
        schedule_point(Scheduler("motsognir.c/v3")),
    )
    assert found.names("draupnir.schedule") == ("motsognir.a/v2", "motsognir.b/v1")
    assert [failure.name for failure in found.failures] == ["motsognir.c/v3"]


def test_a_driver_whose_declared_name_differs_from_its_registration_is_refused() -> None:
    # Otherwise a specification naming one driver resolves another.
    found = registry(schedule_point(Scheduler("motsognir.honest/v1"), name="motsognir.other/v1"))
    assert found.names("draupnir.schedule") == ()
    assert "declares name=" in found.failures[0].reason


def test_an_object_that_does_not_satisfy_the_protocol_is_refused() -> None:
    found = registry(schedule_point(NotADriver()))
    reason = found.failures[0].reason
    assert "ScheduleDriver" in reason
    for member in ("submit", "poll", "cancel", "logs"):
        assert member in reason


def test_a_class_is_instantiated_and_an_instance_is_taken_as_it_is() -> None:
    class Registered(Scheduler):
        def __init__(self) -> None:
            super().__init__("motsognir.klass/v1")

    from_class = registry(point("motsognir.klass/v1", "draupnir.schedule", Registered))
    from_instance = registry(schedule_point(Scheduler("motsognir.instance/v1")))

    assert from_class.names("draupnir.schedule") == ("motsognir.klass/v1",)
    assert from_instance.names("draupnir.schedule") == ("motsognir.instance/v1",)


# ---------------------------------------------------------------------------
# Signature verification, and the one environment variable wide concession
# ---------------------------------------------------------------------------


def test_an_unverified_plugin_is_refused_by_default() -> None:
    found = registry(schedule_point(Scheduler()), verifier=Verifier(verified=False))
    assert found.names("draupnir.schedule") == ()
    assert "not signature verified" in found.failures[0].reason
    assert DEV_VARIABLE in found.failures[0].reason


def test_an_unverified_plugin_loads_in_development_mode() -> None:
    found = registry(schedule_point(Scheduler()), verifier=Verifier(verified=False), environ=DEV)
    plugin = found.resolve("draupnir.schedule", "motsognir.fake/v1")
    assert not plugin.signature.verified
    # It loaded, and the fact that it is unverified is not lost.
    assert report(found).unverified == ("motsognir.fake/v1",)
    assert not report(found).healthy


def test_development_mode_is_exactly_one_value() -> None:
    assert developer_mode({DEV_VARIABLE: "1"})
    for value in ("0", "true", "yes", "", "TRUE"):
        assert not developer_mode({DEV_VARIABLE: value})
    assert not developer_mode({})


# ---------------------------------------------------------------------------
# Rule 3: resolve what was asked for, or fail loudly
# ---------------------------------------------------------------------------


def test_resolving_an_absent_driver_names_what_is_available() -> None:
    found = registry(schedule_point(Scheduler()))
    with pytest.raises(DriverNotFoundError) as raised:
        found.resolve("draupnir.schedule", "motsognir.slurm/v1")
    assert "motsognir.fake/v1" in str(raised.value)
    assert raised.value.available == ("motsognir.fake/v1",)


def test_resolving_a_refused_driver_says_why_it_was_refused() -> None:
    found = registry(schedule_point(Scheduler()), verifier=Verifier(verified=False))
    with pytest.raises(DriverNotFoundError) as raised:
        found.resolve("draupnir.schedule", "motsognir.fake/v1")
    assert "not signature verified" in str(raised.value)


def test_a_newer_version_is_never_substituted() -> None:
    # SAD 10.3 rule 3: replaying a historical run resolves the version it
    # recorded, or fails. There is no nearest match.
    found = registry(schedule_point(Scheduler("motsognir.fake/v1")))
    with pytest.raises(DriverNotFoundError):
        found.resolve("draupnir.schedule", "motsognir.fake/v2")


# ---------------------------------------------------------------------------
# Rule 4: capabilities are checked before an allocation is consumed
# ---------------------------------------------------------------------------


def test_a_specification_resolves_the_driver_it_names() -> None:
    spec = sample_spec(train={"driver": "hamarr.fake/v1", "method": "lora", "precision": "bf16"})
    found = registry(point("hamarr.fake/v1", "draupnir.train", Trainer()))
    assert str(found.for_spec(spec, "draupnir.train").name) == "hamarr.fake/v1"


def test_an_undeclared_capability_is_refused() -> None:
    spec = sample_spec(train={"driver": "hamarr.fake/v1", "method": "qlora", "precision": "bf16"})
    found = registry(
        point("hamarr.fake/v1", "draupnir.train", Trainer(frozenset({"lora", "bf16"})))
    )

    with pytest.raises(MissingCapabilityError) as raised:
        found.for_spec(spec, "draupnir.train")
    assert raised.value.missing == frozenset({"qlora"})
    assert "qlora" in str(raised.value)
    assert "rule 4" in str(raised.value)


def test_a_multinode_placement_demands_the_multinode_capability() -> None:
    spec = sample_spec(
        train={"driver": "hamarr.fake/v1", "method": "lora", "precision": "bf16"},
        placement={"driver": "motsognir.fake/v1", "partition": "ring", "nodes": 3},
    )
    found = registry(
        point("hamarr.fake/v1", "draupnir.train", Trainer(frozenset({"lora", "bf16"})))
    )
    with pytest.raises(MissingCapabilityError) as raised:
        found.for_spec(spec, "draupnir.train")
    assert "multinode" in raised.value.missing


def test_a_group_with_nothing_installed_says_so() -> None:
    # A specification names no store driver, so the core selects by capability
    # and finds nothing at all. The message has to say that, not print an
    # empty list of demands.
    from draupnir.core.plugins import NoProviderError

    found = registry(schedule_point(Scheduler()))
    with pytest.raises(NoProviderError) as raised:
        found.for_spec(sample_spec(), "draupnir.store")
    assert "no draupnir.store driver is installed" in str(raised.value)


# ---------------------------------------------------------------------------
# Discovery survives a bad plug-in; use of it does not
# ---------------------------------------------------------------------------


def test_one_broken_plugin_does_not_stop_the_others_loading() -> None:
    found = registry(
        point("motsognir.bomb/v1", "draupnir.schedule", Exploding),
        schedule_point(Scheduler("motsognir.good/v1")),
    )
    assert found.names("draupnir.schedule") == ("motsognir.good/v1",)
    assert "explodes on import" in found.failures[0].reason


def test_the_report_summarises_what_loaded_and_what_did_not() -> None:
    found = registry(
        schedule_point(Scheduler("motsognir.good/v1")),
        schedule_point(Scheduler("motsognir.bad/v9"), name="motsognir.bad/v9"),
    )
    summary = report(found)
    assert summary.loaded == ("motsognir.good/v1",)
    assert summary.refused == ("motsognir.bad/v9 (draupnir.schedule)",)
    assert summary.unverified == ()
    assert not summary.healthy


def test_an_empty_registry_is_healthy_and_says_nothing_is_loaded() -> None:
    empty = PluginRegistry()
    assert report(empty).healthy
    assert empty.names("draupnir.train") == ()
    assert empty.all("draupnir.train") == ()


# ---------------------------------------------------------------------------
# Selection by capability, which is what makes AC-N9 possible
# ---------------------------------------------------------------------------


class Exporter:
    """A conforming ExportDriver declaring the formats it produces."""

    def __init__(self, name: str, formats: frozenset[str]) -> None:
        self.name = name
        self.capabilities = formats

    def validate(self, spec: RunSpec) -> list[ValidationError]:
        del spec
        return []

    def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
        del spec, workdir
        return JobPlan(command=("export",))

    def parse_progress(self, line: str) -> ProgressEvent | None:
        del line
        return None

    def collect(self, workdir: Path) -> RunArtefacts:
        del workdir
        return RunArtefacts()


def export_point(driver: Any) -> EntryPoint:
    return point(driver.name, "draupnir.export", driver)


def test_a_driver_is_selected_by_what_it_declares() -> None:
    # No table in the core says which driver produces nvfp4. That is the whole
    # of AC-N9: a new format is a new distribution.
    found = registry(
        export_point(Exporter("skidbladnir.trtmo/v1", frozenset({"nvfp4"}))),
        export_point(Exporter("skidbladnir.llamacpp/v1", frozenset({"gguf-q4km"}))),
    )
    assert str(found.provider("draupnir.export", {"nvfp4"}).name) == "skidbladnir.trtmo/v1"
    assert str(found.provider("draupnir.export", {"gguf-q4km"}).name) == "skidbladnir.llamacpp/v1"


def test_a_specification_naming_no_driver_selects_by_capability() -> None:
    # SAD 6.2's `release` block asks for formats and never for an exporter.
    spec = sample_spec(release={"route": "B", "formats": ["mlx4"], "approval": "required"})
    found = registry(export_point(Exporter("skidbladnir.mlx/v1", frozenset({"mlx4"}))))
    assert str(found.for_spec(spec, "draupnir.export").name) == "skidbladnir.mlx/v1"


def test_a_format_nothing_declares_is_refused_naming_what_is_offered() -> None:
    from draupnir.core.plugins import NoProviderError

    found = registry(export_point(Exporter("skidbladnir.mlx/v1", frozenset({"mlx4"}))))
    with pytest.raises(NoProviderError) as raised:
        found.provider("draupnir.export", {"nvfp4"})
    assert "nvfp4" in str(raised.value)
    assert "skidbladnir.mlx/v1 offers mlx4" in str(raised.value)
    assert "AC-N9" in str(raised.value)


def test_two_drivers_offering_the_same_format_are_not_chosen_between() -> None:
    from draupnir.core.plugins import AmbiguousDriverError

    found = registry(
        export_point(Exporter("skidbladnir.llamacpp/v1", frozenset({"gguf-q4km"}))),
        export_point(Exporter("skidbladnir.other/v1", frozenset({"gguf-q4km"}))),
    )
    with pytest.raises(AmbiguousDriverError) as raised:
        found.provider("draupnir.export", {"gguf-q4km"})
    assert raised.value.candidates == ("skidbladnir.llamacpp/v1", "skidbladnir.other/v1")
    # A run records the driver version it used, so the core must not pick.
    assert "rule 3" in str(raised.value)


def test_providers_returns_every_match_in_name_order() -> None:
    found = registry(
        export_point(Exporter("skidbladnir.b/v1", frozenset({"gguf-q4km", "mlx4"}))),
        export_point(Exporter("skidbladnir.a/v1", frozenset({"gguf-q4km"}))),
    )
    assert [str(p.name) for p in found.providers("draupnir.export", {"gguf-q4km"})] == [
        "skidbladnir.a/v1",
        "skidbladnir.b/v1",
    ]
    assert [str(p.name) for p in found.providers("draupnir.export", {"mlx4"})] == [
        "skidbladnir.b/v1"
    ]
    assert found.providers("draupnir.export", {"nvfp4"}) == ()
