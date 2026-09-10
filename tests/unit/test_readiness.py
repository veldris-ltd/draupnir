"""What `/readyz` probes, and how it behaves when a dependency does not. RF-17.

Readiness reported one dependency and built a connection pool to ask about it.
These cover the properties that make the rework worth having: only configured
dependencies are reported, a failing one degrades rather than raising, one slow
one does not decide how long the probe takes, and each name sends an operator to
a section of the runbook that exists.

No database and no socket. The engine and the two link clients are injected for
exactly that reason -- a readiness module that built its own would be
untestable without the things it reports on, which is the shape the old one had.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

import pytest

from draupnir.api import readiness
from draupnir.hodd.reconcile import VAULT_MARKER

REPO_ROOT = Path(__file__).resolve().parents[2]


class Answering:
    """A client that answers with one status, without opening anything."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.asked: list[str] = []

    def get(self, url: str, **_named: Any) -> Any:
        self.asked.append(url)
        return type("Response", (), {"status_code": self.status})()


class Refusing:
    """A client that raises, the way the broker does for an undeclared call."""

    def get(self, url: str, **_named: Any) -> Any:
        msg = f"egress to {url!r} was refused: it is not in the allow list."
        raise RuntimeError(msg)


#: Longer than any timeout these tests set, and no longer. A client that slept
#: for thirty seconds would hold a thread-pool thread long after the check gave
#: up on it, and the event loop joins that pool when it closes -- so the file
#: would take thirty seconds per slow check to report results it already had.
SLOWER_THAN_ANY_TIMEOUT = 1.5


class Slow:
    """A client that does not answer within the timeout it is given."""

    def get(self, _url: str, **_named: Any) -> Any:
        time.sleep(SLOWER_THAN_ANY_TIMEOUT)
        raise AssertionError("the probe waited for a client that never answers")


class Engine:
    """The narrowest thing that looks like an async engine to the check."""

    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.connections = 0

    def connect(self) -> Any:
        engine = self

        class Connection:
            async def __aenter__(self) -> Connection:
                engine.connections += 1
                if engine.fails:
                    msg = "connection refused"
                    raise OSError(msg)
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def execute(self, _statement: Any) -> None:
                return None

        return Connection()


# ---------------------------------------------------------------------------
# What is reported
# ---------------------------------------------------------------------------


async def test_a_dependency_that_is_not_configured_is_not_reported() -> None:
    """Absent from the report, not `false`. RF-17.

    A forge with no scheduler is not degraded for want of one, and a probe that
    says otherwise is a probe operators learn to ignore -- which costs the probe
    its only purpose.
    """
    checks = await readiness.Dependencies(engine=Engine()).checks()

    assert checks == {"database": True}


async def test_every_configured_dependency_is_reported_by_name() -> None:
    probe = readiness.Dependencies(
        engine=Engine(),
        vault_root="",
        object_store=lambda: True,
        scheduler_url="http://regin.invalid:6820",
        registry_url="https://megingjord.invalid",
        scheduler_client=Answering(200),
        federation_client=Answering(200),
    )

    checks = await probe.checks()

    assert checks == {
        "database": True,
        "object_store": True,
        "scheduler": True,
        "federation": True,
    }


async def test_the_object_store_is_not_probed_where_a_vault_is_configured(
    tmp_path: Path,
) -> None:
    """`hodd.stores.store_for` uses the vault and never opens a bucket.

    So a bucket check would report on a dependency that cannot affect service,
    and a forge whose unused MinIO is down would sit permanently degraded with
    nothing wrong.
    """
    marker = tmp_path / VAULT_MARKER
    marker.write_text("{}", encoding="utf-8")
    opened = False

    def bucket() -> bool:
        nonlocal opened
        opened = True
        return True

    checks = await readiness.Dependencies(vault_root=str(tmp_path), object_store=bucket).checks()

    assert "object_store" not in checks
    assert not opened, "the bucket was opened for a deployment that does not use it"


# ---------------------------------------------------------------------------
# What happens when a dependency does not answer
# ---------------------------------------------------------------------------


async def test_a_failing_check_is_false_and_takes_nothing_else_with_it() -> None:
    """SAD 11.2: degraded modes are visible rather than fatal.

    `gather` propagates the first exception and discards the other results, so
    a check that raised would cost the operator the four answers that arrived
    in order to be told about the one that did not.
    """
    probe = readiness.Dependencies(
        engine=Engine(fails=True),
        scheduler_url="http://regin.invalid:6820",
        scheduler_client=Answering(200),
    )

    checks = await probe.checks()

    assert checks == {"database": False, "scheduler": True}


async def test_a_refused_egress_is_a_failed_check_rather_than_an_exception() -> None:
    """A refusal is a finding about configuration, not a reason to 500.

    The broker raises for a destination nobody declared. That says the allow
    list and the settings disagree, which an operator needs to see as a failed
    check rather than as a probe that died.
    """
    checks = await readiness.Dependencies(
        registry_url="https://somewhere.invalid", federation_client=Refusing()
    ).checks()

    assert checks == {"federation": False}


@pytest.mark.parametrize(
    ("status", "reachable"),
    [(200, True), (401, True), (404, True), (500, False), (502, False)],
)
async def test_a_link_check_asks_whether_something_answered(status: int, reachable: bool) -> None:
    """Reachability, not capability.

    `slurmrestd` answering "unauthorised" is `slurmrestd` answering, and the
    runbook rows these map to are about the link rather than about the API
    version behind it. A 5xx is the far end failing rather than answering.
    """
    checks = await readiness.Dependencies(
        scheduler_url="http://regin.invalid:6820", scheduler_client=Answering(status)
    ).checks()

    assert checks == {"scheduler": reachable}


async def test_an_unmounted_vault_is_false(tmp_path: Path) -> None:
    absent = tmp_path / "not-mounted"

    checks = await readiness.Dependencies(vault_root=str(absent)).checks()

    assert checks == {"vault": False}


async def test_a_directory_on_the_mount_point_is_false_as_well(tmp_path: Path) -> None:
    """The failure a bare `is_dir()` misses, which is the dangerous one.

    A directory somebody created where the mount should be looks exactly like a
    mounted vault and is empty. `require_vault` is what distinguishes them, and
    it is why the check goes through it rather than calling `is_dir()` here.
    """
    checks = await readiness.Dependencies(vault_root=str(tmp_path)).checks()

    assert checks == {"vault": False}


async def test_a_mounted_vault_is_true(tmp_path: Path) -> None:
    (tmp_path / VAULT_MARKER).write_text("{}", encoding="utf-8")

    checks = await readiness.Dependencies(vault_root=str(tmp_path)).checks()

    assert checks == {"vault": True}


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


async def test_one_slow_dependency_does_not_delay_the_probe_beyond_its_timeout() -> None:
    """An orchestrator that times the probe out reports the whole process dead.

    Which is the opposite of what a readiness probe is for: one degraded
    dependency would take the process out of service, and SAD 11.2's whole
    point is that this system keeps running through them.
    """
    probe = readiness.Dependencies(
        scheduler_url="http://regin.invalid:6820", scheduler_client=Slow(), timeout=0.2
    )

    started = time.monotonic()
    checks = await probe.checks()
    took = time.monotonic() - started

    assert checks == {"scheduler": False}
    assert took < SLOWER_THAN_ANY_TIMEOUT, f"the probe waited {took:.1f}s for a check budgeted 0.2s"


async def test_the_checks_run_concurrently_rather_than_one_after_another() -> None:
    """Otherwise the probe costs the sum of the timeouts rather than the worst.

    Four dependencies at two seconds each is eight seconds, which is longer
    than the interval an orchestrator probes on -- so the probes overlap, and
    the pile-up looks like the API being slow.
    """
    probe = readiness.Dependencies(
        scheduler_url="http://regin.invalid:6820",
        registry_url="https://megingjord.invalid",
        scheduler_client=Slow(),
        federation_client=Slow(),
        timeout=0.3,
    )

    started = time.monotonic()
    checks = await probe.checks()
    took = time.monotonic() - started

    assert checks == {"scheduler": False, "federation": False}
    assert took < 0.3 * 2, (
        f"two checks budgeted 0.3s each took {took:.1f}s, so they ran in sequence"
    )


async def test_a_slow_check_does_not_hold_up_one_that_answered() -> None:
    probe = readiness.Dependencies(
        engine=Engine(),
        scheduler_url="http://regin.invalid:6820",
        scheduler_client=Slow(),
        timeout=0.3,
    )

    checks = await asyncio.wait_for(probe.checks(), timeout=5)

    assert checks == {"database": True, "scheduler": False}


# ---------------------------------------------------------------------------
# The names are the operator's index into the runbook
# ---------------------------------------------------------------------------


def test_every_check_name_names_a_runbook_section_that_exists() -> None:
    """An operator reading `"vault": false` has to be able to find the row.

    AC-D3, from the probe's end. The register asked for the check names to be
    cross-referenced against the nine rows of SAD 11.2, and this is that
    cross-reference -- asserted rather than written down twice, because a
    mapping kept in prose drifts the first time somebody renumbers a section.
    """
    runbook = (REPO_ROOT / "docs/runbook.md").read_text(encoding="utf-8")
    sections = {int(number) for number in re.findall(r"^## (\d+)\. ", runbook, re.M)}

    assert sections, "no numbered sections were found, so this proves nothing"
    for name, section in readiness.RUNBOOK_SECTIONS.items():
        assert section in sections, f"{name!r} points at runbook section {section}, which is absent"


def test_every_check_the_probe_can_report_has_a_runbook_section() -> None:
    """So a new check cannot ship without somewhere for an operator to go."""
    import inspect

    source = inspect.getsource(readiness.Dependencies.checks)
    reported = set(re.findall(r'\(\s*"(\w+)",\s*self\._\w+\s*\)', source))

    assert reported, "the check names could not be read, so this proves nothing"
    nowhere = sorted(reported - set(readiness.RUNBOOK_SECTIONS))

    assert not nowhere, f"these checks send an operator nowhere: {nowhere}"
