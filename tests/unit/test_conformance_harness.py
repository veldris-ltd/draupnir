"""The conformance harness is itself tested, by trying to get impure drivers past it.

A conformance suite nobody has tried to fool is a conformance suite nobody
should trust. Each driver below breaks one thing SAD 8.2 or Decision S5
requires, in the way a real driver would plausibly break it -- a timestamp in
the plan, a registry lookup while planning, a config file written during a dry
run -- and the harness has to catch every one.
"""

from __future__ import annotations

import socket
import time
import uuid
from pathlib import Path

import pytest

from draupnir.interfaces.testing import sample_spec
from draupnir.interfaces.testing.harness import (
    NetworkAccessError,
    check_collect_does_not_mutate,
    check_driver,
    check_job_driver,
    check_parse_progress,
    check_render_has_no_side_effects,
    check_render_is_deterministic,
    check_render_makes_no_network_call,
    check_validate,
    describe,
    no_network,
)
from draupnir.interfaces.types import (
    JobPlan,
    ProgressEvent,
    ProgressKind,
    RunArtefacts,
    RunSpec,
    ValidationError,
)

SPEC = sample_spec()


class Conforming:
    """A driver that does everything the interface asks."""

    name = "hamarr.honest/v1"
    capabilities = frozenset({"lora", "bf16"})

    def validate(self, spec: RunSpec) -> list[ValidationError]:
        del spec
        return []

    def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
        del workdir
        return JobPlan(command=("train", spec.metadata.name), workdir=".")

    def parse_progress(self, line: str) -> ProgressEvent | None:
        if line.startswith("step "):
            return ProgressEvent(kind=ProgressKind.STEP, message=line)
        return None

    def collect(self, workdir: Path) -> RunArtefacts:
        del workdir
        return RunArtefacts()


def test_a_conforming_driver_produces_no_findings(tmp_path: Path) -> None:
    assert check_job_driver(Conforming(), SPEC, tmp_path) == []


# ---------------------------------------------------------------------------
# Decision S5: render must be pure
# ---------------------------------------------------------------------------


def test_a_plan_carrying_a_timestamp_is_caught(tmp_path: Path) -> None:
    class Timestamped(Conforming):
        def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
            del spec, workdir
            return JobPlan(command=("train", f"--run-id={time.time_ns()}"))

    findings = check_render_is_deterministic(Timestamped(), SPEC, tmp_path)
    assert [finding.check for finding in findings] == ["render.deterministic"]
    assert "cannot be reproduced" in findings[0].message
    # Caught by the third render rather than the second: this machine's clock
    # returns the same value to two consecutive calls, which is exactly the
    # case a two-call check misses.
    assert "apart" in findings[0].message


def test_a_plan_carrying_a_random_identifier_is_caught(tmp_path: Path) -> None:
    class Random(Conforming):
        def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
            del spec, workdir
            return JobPlan(command=("train",), environment={"JOB": uuid.uuid4().hex})

    assert check_render_is_deterministic(Random(), SPEC, tmp_path)


def test_a_render_that_reaches_the_network_is_caught(tmp_path: Path) -> None:
    class Resolving(Conforming):
        def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
            del workdir
            # The plausible version of this: looking up the current tag of a
            # base image while planning.
            socket.getaddrinfo("registry.veldris.internal", 443)
            return JobPlan(command=("train", spec.metadata.name))

    findings = check_render_makes_no_network_call(Resolving(), SPEC, tmp_path)
    assert [finding.check for finding in findings] == ["render.no_network"]
    assert "Decision S5" in findings[0].message


def test_a_render_that_opens_a_socket_directly_is_caught(tmp_path: Path) -> None:
    class Connecting(Conforming):
        def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
            del workdir
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            return JobPlan(command=("train", spec.metadata.name))

    assert check_render_makes_no_network_call(Connecting(), SPEC, tmp_path)


def test_a_render_that_writes_a_config_file_is_caught(tmp_path: Path) -> None:
    class Writing(Conforming):
        def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
            (workdir / "train_config.yaml").write_text("method: lora", encoding="utf-8")
            return JobPlan(command=("train", spec.metadata.name))

    findings = check_render_has_no_side_effects(Writing(), SPEC, tmp_path)
    assert [finding.check for finding in findings] == ["render.no_side_effects"]
    assert "train_config.yaml" in findings[0].message


def test_a_render_that_rewrites_an_existing_file_is_caught(tmp_path: Path) -> None:
    (tmp_path / "existing.txt").write_text("original", encoding="utf-8")

    class Rewriting(Conforming):
        def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
            (workdir / "existing.txt").write_text("rewritten and longer", encoding="utf-8")
            return JobPlan(command=("train", spec.metadata.name))

    findings = check_render_has_no_side_effects(Rewriting(), SPEC, tmp_path)
    assert "existing.txt" in findings[0].message


# ---------------------------------------------------------------------------
# The rest of the interface
# ---------------------------------------------------------------------------


def test_a_validate_that_raises_on_the_first_problem_is_caught() -> None:
    class Raising(Conforming):
        def validate(self, spec: RunSpec) -> list[ValidationError]:
            del spec
            msg = "unsupported method"
            raise ValueError(msg)

    findings = check_validate(Raising(), SPEC)
    assert "one round trip and not five" in findings[0].message


def test_a_validate_returning_the_wrong_shape_is_caught() -> None:
    class Stringly(Conforming):
        def validate(self, spec: RunSpec) -> list[ValidationError]:
            del spec
            return ["that method is not supported"]  # type: ignore[list-item]

    assert check_validate(Stringly(), SPEC)


def test_a_parse_progress_that_raises_on_odd_input_is_caught() -> None:
    class Fragile(Conforming):
        def parse_progress(self, line: str) -> ProgressEvent | None:
            return ProgressEvent(kind=ProgressKind.STEP, step=int(line.split()[1].split("/")[0]))

    findings = check_parse_progress(Fragile())
    assert findings
    assert all(finding.check == "parse_progress" for finding in findings)


def test_a_parse_progress_that_is_not_pure_is_caught() -> None:
    class Counting(Conforming):
        def __init__(self) -> None:
            self.seen = 0

        def parse_progress(self, line: str) -> ProgressEvent | None:
            del line
            self.seen += 1
            return ProgressEvent(kind=ProgressKind.STEP, step=self.seen)

    findings = check_parse_progress(Counting())
    assert any("not pure" in finding.message for finding in findings)


def test_a_collect_that_tidies_up_after_itself_is_caught(tmp_path: Path) -> None:
    (tmp_path / "checkpoint.bin").write_bytes(b"weights")

    class Tidying(Conforming):
        def collect(self, workdir: Path) -> RunArtefacts:
            # The plausible version: deleting intermediates while collecting.
            (workdir / "checkpoint.bin").unlink()
            return RunArtefacts()

    findings = check_collect_does_not_mutate(Tidying(), tmp_path)
    assert [finding.check for finding in findings] == ["collect.no_mutation"]


# ---------------------------------------------------------------------------
# What every driver declares
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("hamarr.llamafactory", "not a versioned interface name"),
        ("Hamarr.LlamaFactory/v1", "not a versioned interface name"),
        ("llamafactory/v1", "not a versioned interface name"),
        ("hamarr.llamafactory/v0", "not a versioned interface name"),
    ],
)
def test_an_unusable_driver_name_is_caught(name: str, expected: str) -> None:
    class Named(Conforming):
        pass

    driver = Named()
    driver.name = name
    findings = check_driver(driver)
    assert expected in findings[0].message


def test_capabilities_declared_as_a_mutable_set_are_caught() -> None:
    # A mutable set is the mistake: the core would be able to change what a
    # driver claims it can do.
    class Mutable(Conforming):
        capabilities = {"lora"}  # type: ignore[assignment]  # noqa: RUF012

    findings = check_driver(Mutable())
    assert "frozenset" in findings[0].message


def test_capabilities_that_are_not_strings_are_caught() -> None:
    class Numeric(Conforming):
        capabilities = frozenset({1, 2})  # type: ignore[arg-type]

    assert check_driver(Numeric())


def test_a_driver_without_a_name_is_caught() -> None:
    class Anonymous:
        capabilities: frozenset[str] = frozenset()

    findings = check_driver(Anonymous())
    assert any("`name`" in finding.message for finding in findings)


# ---------------------------------------------------------------------------
# The network guard itself
# ---------------------------------------------------------------------------


def test_the_network_guard_blocks_and_then_restores() -> None:
    original = socket.socket
    with no_network(), pytest.raises(NetworkAccessError):
        socket.getaddrinfo("example.invalid", 80)
    assert socket.socket is original


def test_the_network_guard_restores_even_when_the_body_raises() -> None:
    original = socket.getaddrinfo
    with pytest.raises(ValueError, match="deliberate"), no_network():
        msg = "deliberate"
        raise ValueError(msg)
    assert socket.getaddrinfo is original


def test_findings_render_for_an_assertion_message() -> None:
    assert describe([]) == "conformant"
    findings = check_render_is_deterministic(
        type(
            "R", (Conforming,), {"render": lambda self, s, w: JobPlan(command=(uuid.uuid4().hex,))}
        )(),
        SPEC,
        Path(),
    )
    assert "render.deterministic" in describe(findings)
