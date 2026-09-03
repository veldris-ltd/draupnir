"""The eight readings, each taken from the appliance and nothing else.

Every reader here answers in one of three ways: a value, `unreachable`, or
`unknown`. None of them raises, and none of them can block for long, because
the one condition this view is bought for is the condition where the things it
reads are broken. A reader that raised would take the panel down at exactly
the moment the panel is the only thing left.

`unreachable` and `unknown` are different and the difference is load bearing.
`unreachable` means the thing was asked and did not answer -- the scheduler is
down, the API is not there. `unknown` means it could not be asked at all, which
on this appliance means the tool is missing or the interface has been renamed.
An operator acts differently on the two: one is an outage, the other is a
misconfiguration.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

#: Nothing here waits long. The panel redraws every two seconds and a reader
#: that took longer would make the whole view lag behind the appliance.
TIMEOUT: Final = 1.5


class Status(StrEnum):
    """How a reading turned out."""

    OK = "ok"
    WARN = "warn"
    #: Asked, no answer. An outage.
    UNREACHABLE = "unreachable"
    #: Could not be asked. A misconfiguration.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Reading:
    """One line of the panel."""

    label: str
    value: str
    status: Status

    def render(self, width: int = 46) -> str:
        """One fixed-width line, for a framebuffer console."""
        mark = {
            Status.OK: "+",
            Status.WARN: "!",
            Status.UNREACHABLE: "x",
            Status.UNKNOWN: "?",
        }[self.status]
        label = f"{self.label}:".ljust(12)
        return f"[{mark}] {label}{self.value}"[:width]


def _run(command: list[str]) -> str | None:
    """Run a local command, returning `None` if it is missing or fails."""
    if shutil.which(command[0]) is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell, local tools only
            command,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def gpu(root: Path | None = None) -> Reading:
    """Utilisation and memory. `nvidia-smi`, or nothing."""
    del root
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    if out is None:
        return Reading("GPU", "nvidia-smi unavailable", Status.UNKNOWN)
    first = out.splitlines()[0] if out.splitlines() else ""
    parts = [part.strip() for part in first.split(",")]
    if len(parts) < 3:
        return Reading("GPU", "unreadable", Status.UNKNOWN)
    used, total = parts[1], parts[2]
    return Reading("GPU", f"{parts[0]}%, {used}/{total} MiB", Status.OK)


def throttle(root: Path | None = None) -> Reading:
    """Thermal or power capping, which is what makes a run slow for no reason."""
    del root
    out = _run(
        ["nvidia-smi", "--query-gpu=clocks_throttle_reasons.active", "--format=csv,noheader"]
    )
    if out is None:
        return Reading("Throttle", "nvidia-smi unavailable", Status.UNKNOWN)
    active = out.splitlines()[0].strip() if out.splitlines() else "0x0000000000000000"
    capped = active not in {"0x0000000000000000", "Not Active", ""}
    return Reading(
        "Throttle",
        "capped" if capped else "none",
        Status.WARN if capped else Status.OK,
    )


def fabric(root: Path | None = None) -> Reading:
    """The ConnectX link, read from sysfs rather than from a tool."""
    base = (root or Path("/")) / "sys/class/net"
    if not base.is_dir():
        return Reading("Fabric", "no sysfs", Status.UNKNOWN)
    for name in sorted(entry.name for entry in base.iterdir()):
        if not name.startswith(("ib", "en")):
            continue
        state = _read(base / name / "operstate")
        if state is None:
            continue
        return Reading(
            "Fabric",
            f"{name} {state}",
            Status.OK if state == "up" else Status.UNREACHABLE,
        )
    return Reading("Fabric", "no interface found", Status.UNKNOWN)


def ring(neighbours: tuple[str, ...] = (), root: Path | None = None) -> Reading:
    """The two ring neighbours, by opening a socket on the local segment.

    A TCP connect rather than ICMP: this runs unprivileged, and a raw socket
    would need capabilities that a read-only status panel has no business
    holding.
    """
    del root
    hosts = neighbours or _neighbours_from_environment()
    if not hosts:
        return Reading("Ring", "no neighbours configured", Status.UNKNOWN)
    reachable = [host for host in hosts if _connects(host)]
    if len(reachable) == len(hosts):
        return Reading("Ring", f"{len(hosts)} of {len(hosts)} up", Status.OK)
    return Reading(
        "Ring",
        f"{len(reachable)} of {len(hosts)} up",
        Status.WARN if reachable else Status.UNREACHABLE,
    )


def current_run(root: Path | None = None) -> Reading:
    """What this appliance is training, from the local scheduler spool.

    Read from a file the executor writes, not from the API. The run this
    appliance is doing is a local fact and stays knowable when nothing else is.
    """
    spool = (root or Path("/")) / "var/lib/draupnir/current-run.json"
    text = _read(spool)
    if text is None:
        return Reading("Run", "idle", Status.OK)
    try:
        payload = json.loads(text)
    except ValueError:
        return Reading("Run", "spool unreadable", Status.UNKNOWN)
    name = str(payload.get("name", "unnamed"))
    step = payload.get("step")
    total = payload.get("totalSteps")
    if isinstance(step, int) and isinstance(total, int) and total > 0:
        return Reading("Run", f"{name} step {step}/{total}", Status.OK)
    return Reading("Run", name, Status.OK)


def vault(root: Path | None = None) -> Reading:
    """The local secret agent, over its unix socket."""
    path = (root or Path("/")) / "run/draupnir/vault.sock"
    if not path.exists():
        return Reading("Vault", "agent socket absent", Status.UNREACHABLE)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(TIMEOUT)
            client.connect(str(path))
    except (OSError, AttributeError):
        # AttributeError: AF_UNIX does not exist on every platform, and a
        # panel that crashed on a developer's laptop would not get looked at.
        return Reading("Vault", "agent not answering", Status.UNREACHABLE)
    return Reading("Vault", "answering", Status.OK)


def scheduler(root: Path | None = None) -> Reading:
    """The local `slurmd`, by its pidfile and its port."""
    pidfile = (root or Path("/")) / "run/slurmd.pid"
    if _read(pidfile) is None:
        return Reading("Scheduler", "slurmd not running", Status.UNREACHABLE)
    return Reading(
        "Scheduler",
        "running" if _connects("127.0.0.1", 6818) else "not answering",
        Status.OK if _connects("127.0.0.1", 6818) else Status.UNREACHABLE,
    )


def api(url: str | None = None) -> Reading:
    """Whether the control plane answers.

    This is the only line that mentions the API, and it is a *status* line: it
    reports whether the API answered and nothing on this panel depends on the
    answer. It is expected to read unreachable during an outage, and the view
    continues to function, which is its entire purpose.

    A TCP connect rather than an HTTP request, so there is no HTTP client in
    this package at all -- see `test_stedi_view.py`, which reads the source and
    asserts exactly that.
    """
    target = url or os.environ.get("DRAUPNIR_API_HOST", "127.0.0.1:8000")
    host, _, port = target.partition(":")
    try:
        number = int(port) if port else 8000
    except ValueError:
        return Reading("API", "address unreadable", Status.UNKNOWN)
    return (
        Reading("API", f"answering at {host}", Status.OK)
        if _connects(host, number)
        else Reading("API", f"unreachable at {host}", Status.UNREACHABLE)
    )


def all_readings(root: Path | None = None) -> list[Reading]:
    """The eight lines, in the order someone at the rack asks them."""
    return [
        gpu(root),
        throttle(root),
        fabric(root),
        ring(root=root),
        current_run(root),
        vault(root),
        scheduler(root),
        api(),
    ]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _connects(host: str, port: int = 22) -> bool:
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT):
            return True
    except OSError:
        return False


def _neighbours_from_environment() -> tuple[str, ...]:
    raw = os.environ.get("DRAUPNIR_RING_NEIGHBOURS", "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())
