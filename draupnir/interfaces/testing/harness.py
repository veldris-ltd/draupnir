"""The conformance checks themselves, with no test framework involved.

A driver author should be able to run these from a script, from their own test
suite, or from the pytest classes in `suite.py`. So the checks live here as
functions that return findings, and the pytest layer is a thin wrapper that
turns a finding into a failure.

The purity checks are the reason this module exists. SAD Decision S5 requires
`render` to be pure, and "pure" is not something a code review establishes.
Three properties are checked instead, each of which a real driver has broken
in some project somewhere: rendering twice gives byte-identical plans,
rendering opens no socket, and rendering leaves the working directory as it
found it.
"""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from draupnir.interfaces.naming import InterfaceName, InterfaceNameError
from draupnir.interfaces.types import (
    JobPlan,
    JobState,
    ProgressEvent,
    RunSpec,
    ValidationError,
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One way in which a driver does not conform."""

    check: str
    message: str

    def __str__(self) -> str:
        """Render for a report or an assertion message."""
        return f"{self.check}: {self.message}"


class NetworkAccessError(RuntimeError):
    """Raised when code under `no_network()` attempts to reach the network."""


@contextmanager
def no_network() -> Iterator[None]:
    """Make every outbound network call raise for the duration of the block.

    Patched at the `socket` module, which is what every higher level client
    eventually reaches: `requests`, `httpx`, `urllib` and a raw socket all end
    up here. A driver that resolves a dependency while planning a job is
    caught, whatever library it used to do it.
    """
    # Kept as separate names rather than a dict: a dict of callables widens to
    # `object`, and restoring from it then needs a cast that hides a real
    # mistake as readily as a false one.
    original_socket = socket.socket
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo

    def refuse(*args: object, **kwargs: object) -> Any:
        del args, kwargs
        msg = (
            "render attempted a network call. It must be pure: no side effects, "
            "no network, deterministic given the specification (SAD Decision S5)."
        )
        raise NetworkAccessError(msg)

    # Assigning over `socket.socket` is the point: every higher level client
    # reaches it, so replacing it is what makes the check total.
    socket.socket = refuse  # type: ignore[misc, assignment]
    socket.create_connection = refuse
    socket.getaddrinfo = refuse
    try:
        yield
    finally:
        socket.socket = original_socket  # type: ignore[misc]
        socket.create_connection = original_create_connection
        socket.getaddrinfo = original_getaddrinfo


def _tree(root: Path) -> dict[str, int]:
    """A snapshot of a directory: every file path and its size."""
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.stat().st_size
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# Every driver
# ---------------------------------------------------------------------------


def check_driver(driver: object) -> list[Finding]:
    """Check what SAD 8.2 requires of every plug-in, whichever interface it is."""
    findings: list[Finding] = []

    name = getattr(driver, "name", None)
    if not isinstance(name, str):
        findings.append(Finding("name", "a driver must declare `name` as a string"))
    else:
        try:
            InterfaceName.parse(name)
        except InterfaceNameError as error:
            findings.append(Finding("name", str(error)))

    capabilities = getattr(driver, "capabilities", None)
    if not isinstance(capabilities, frozenset):
        findings.append(
            Finding(
                "capabilities",
                "a driver must declare `capabilities` as a frozenset, so that the core "
                "can check a specification against it without being able to change it "
                "(SAD 10.3 rule 4)",
            )
        )
    elif not all(isinstance(item, str) for item in capabilities):
        findings.append(Finding("capabilities", "every declared capability must be a string"))

    return findings


# ---------------------------------------------------------------------------
# TrainDriver, MergeDriver, EvalDriver, ExportDriver
# ---------------------------------------------------------------------------


def check_validate(driver: Any, spec: RunSpec) -> list[Finding]:
    """`validate` returns every problem it found, and does not raise."""
    findings: list[Finding] = []
    try:
        result = driver.validate(spec)
    except Exception as error:
        return [
            Finding(
                "validate",
                f"raised {type(error).__name__}: {error}. It must return the problems "
                "it found rather than raising on the first one, so that an operator "
                "needs one round trip and not five.",
            )
        ]

    if not isinstance(result, list):
        findings.append(Finding("validate", f"returned {type(result).__name__}, expected a list"))
        return findings
    for item in result:
        if not isinstance(item, ValidationError):
            findings.append(
                Finding("validate", f"returned a {type(item).__name__}, expected ValidationError")
            )
    return findings


#: How long to wait before the third render. The system clock advances in
#: steps -- about 15.6 ms on Windows -- and two renders taken back to back can
#: read the same value from it, so a driver stamping the wall clock into its
#: plan passes a two-call check. Waiting longer than one tick closes that, at
#: a cost of one interval per driver.
SETTLE_SECONDS = 0.05


def check_render_is_deterministic(
    driver: Any, spec: RunSpec, workdir: Path, *, settle: float = SETTLE_SECONDS
) -> list[Finding]:
    """Renders of the same specification produce identical plans.

    Three renders, not two. The first pair catches a counter, a random
    identifier, or anything else that changes on every call. The third is
    taken after a deliberate pause and catches what a back-to-back pair
    cannot: a wall clock read into the plan, which on a coarse clock returns
    the same value twice in a row and a different one a tick later.
    """
    first = driver.render(spec, workdir)
    second = driver.render(spec, workdir)
    time.sleep(settle)
    third = driver.render(spec, workdir)

    if not isinstance(first, JobPlan):
        return [Finding("render", f"returned {type(first).__name__}, expected JobPlan")]

    if first.canonical() != second.canonical():
        return [_differs("two consecutive renders", first, second)]

    if first.canonical() != third.canonical():
        return [
            _differs(
                f"two renders {settle * 1000:.0f} ms apart",
                first,
                third,
                hint=(
                    "They agreed back to back and disagreed after a pause, which is "
                    "what reading the wall clock looks like."
                ),
            )
        ]
    return []


def _differs(what: str, left: JobPlan, right: JobPlan, hint: str = "") -> Finding:
    """Render a determinism failure, showing both plans."""
    return Finding(
        "render.deterministic",
        f"{what} of the same specification produced different plans. "
        "A plan that is not a function of the specification cannot be reproduced "
        f"from recorded inputs (SAD Decision S5, driver D-1). {hint}\n"
        f"  first: {left.canonical().decode()}\n"
        f"  then:  {right.canonical().decode()}",
    )


def check_render_makes_no_network_call(driver: Any, spec: RunSpec, workdir: Path) -> list[Finding]:
    """`render` resolves nothing over the network."""
    try:
        with no_network():
            driver.render(spec, workdir)
    except NetworkAccessError as error:
        return [Finding("render.no_network", str(error))]
    return []


def check_render_has_no_side_effects(driver: Any, spec: RunSpec, workdir: Path) -> list[Finding]:
    """`render` leaves the working directory as it found it.

    A driver that writes its configuration during planning makes a dry run
    unsafe, which matters when an allocation on this estate is expensive
    (SAD 11D, AC-F14).
    """
    workdir.mkdir(parents=True, exist_ok=True)
    before = _tree(workdir)
    driver.render(spec, workdir)
    after = _tree(workdir)

    if before != after:
        added = sorted(set(after) - set(before))
        changed = sorted(path for path in set(after) & set(before) if after[path] != before[path])
        removed = sorted(set(before) - set(after))
        return [
            Finding(
                "render.no_side_effects",
                "render changed the working directory. It must be pure, so that a dry "
                "run is trivially safe (SAD Decision S5). "
                f"added={added} changed={changed} removed={removed}",
            )
        ]
    return []


def check_parse_progress(driver: Any) -> list[Finding]:
    """`parse_progress` is total and pure: any line, twice, same answer."""
    findings: list[Finding] = []
    lines = [
        "",
        "step 10/100 loss=1.234",
        "not a progress line at all",
        "\x00\x01 binary rubbish",
        "a" * 4096,
    ]
    for line in lines:
        try:
            first = driver.parse_progress(line)
            second = driver.parse_progress(line)
        except Exception as error:
            findings.append(
                Finding(
                    "parse_progress",
                    f"raised {type(error).__name__} on {line[:40]!r}. It must return None "
                    "for a line that carries no event, so that replaying a captured log "
                    "yields the events it yielded live.",
                )
            )
            continue

        if first is not None and not isinstance(first, ProgressEvent):
            findings.append(
                Finding(
                    "parse_progress",
                    f"returned {type(first).__name__}, expected ProgressEvent or None",
                )
            )
        if first != second:
            findings.append(
                Finding("parse_progress", f"is not pure: two calls on {line[:40]!r} differed")
            )
    return findings


def check_collect_does_not_mutate(driver: Any, workdir: Path) -> list[Finding]:
    """`collect` reads the working directory and leaves it alone."""
    workdir.mkdir(parents=True, exist_ok=True)
    before = _tree(workdir)
    try:
        driver.collect(workdir)
    except Exception as error:
        return [Finding("collect", f"raised {type(error).__name__}: {error}")]
    after = _tree(workdir)

    if before != after:
        return [
            Finding(
                "collect.no_mutation",
                "collect changed the working directory. It returns produced artefacts "
                "with their hashes; it must not mutate them (SAD 8.2).",
            )
        ]
    return []


def check_job_driver(driver: Any, spec: RunSpec, workdir: Path) -> list[Finding]:
    """Run every check that applies to a driver which plans and collects a job."""
    return [
        *check_driver(driver),
        *check_validate(driver, spec),
        *check_render_is_deterministic(driver, spec, workdir),
        *check_render_makes_no_network_call(driver, spec, workdir),
        *check_render_has_no_side_effects(driver, spec, workdir),
        *check_parse_progress(driver),
        *check_collect_does_not_mutate(driver, workdir),
    ]


# ---------------------------------------------------------------------------
# ScheduleDriver
# ---------------------------------------------------------------------------


def check_schedule_driver(driver: Any, plan: JobPlan) -> list[Finding]:
    """Submit, poll, cancel and read logs, checking each behaves as specified."""
    findings: list[Finding] = []

    handle = driver.submit(plan)
    if not hasattr(handle, "job_id"):
        return [Finding("submit", f"returned {type(handle).__name__}, expected a JobHandle")]
    if handle.driver != getattr(driver, "name", None):
        findings.append(
            Finding(
                "submit",
                f"returned a handle naming driver {handle.driver!r}, but this driver is "
                f"{getattr(driver, 'name', None)!r}. A handle has to identify what can "
                "poll it.",
            )
        )

    first = driver.poll(handle)
    second = driver.poll(handle)
    if first.state != second.state and first.state in {JobState.COMPLETED, JobState.FAILED}:
        findings.append(
            Finding("poll", "a terminal status changed between two polls; poll must be stable")
        )

    settled = _wait(driver, handle)
    if settled.state not in set(JobState):
        findings.append(Finding("poll", f"returned an unknown state {settled.state!r}"))

    # AC-F13: cancelling leaves a defined state, and cancelling a job that has
    # already finished is not an error.
    try:
        after_cancel = driver.cancel(handle)
    except Exception as error:
        findings.append(
            Finding(
                "cancel",
                f"raised {type(error).__name__} on an already finished job. Cancelling "
                "one is not an error (SAD 8.2, AC-F13).",
            )
        )
    else:
        if after_cancel.state not in set(JobState):
            findings.append(Finding("cancel", "returned an undefined state"))

    try:
        logs = driver.logs(handle)
    except Exception as error:
        findings.append(Finding("logs", f"raised {type(error).__name__}: {error}"))
    else:
        if not isinstance(logs, str):
            findings.append(Finding("logs", f"returned {type(logs).__name__}, expected str"))

    return [*check_driver(driver), *findings]


def _wait(driver: Any, handle: object, attempts: int = 200) -> Any:
    """Poll until the job settles, or give up and return the last status."""
    status = driver.poll(handle)
    for _ in range(attempts):
        if status.state in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}:
            return status
        status = driver.poll(handle)
    return status


def describe(findings: list[Finding]) -> str:
    """Render findings for an assertion message."""
    if not findings:
        return "conformant"
    return "\n".join(f"  - {finding}" for finding in findings)


def environment_summary() -> dict[str, str]:
    """What the suite ran against, for a conformance report."""
    return {"cwd": os.getcwd(), "dev_mode": os.environ.get("DRAUPNIR_DEV", "0")}
