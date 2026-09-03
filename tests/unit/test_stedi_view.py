"""CON-A, the local status view. AC-S15 and the prompt's own statement of it.

"CON-A view is read-only, reads the local appliance only, and works when the
API is unreachable. That is its entire purpose."

So the test is the outage. Every reading is taken with nothing available -- no
`nvidia-smi`, no sysfs, no sockets, no scheduler, no API -- and the view has to
render eight lines and exit zero. A test of the happy path would tell us
nothing about the case the panel was bought for.

The second half is structural. "It depends on nothing beyond the appliance" is
a property that decays the first time somebody adds a helper, so the source is
read and the absence is asserted rather than assumed.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "tools" / "stedi-view"
SOURCE = PACKAGE / "stedi_view"

pytestmark = pytest.mark.unit


def run(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the view in a subprocess with a hostile environment.

    A subprocess rather than an import, because "exits zero" is part of what is
    being asserted and a systemd timer or a cron will judge it on exactly that.
    """
    return subprocess.run(  # noqa: S603 -- this interpreter, a fixed module, literal flags
        [sys.executable, "-m", "stedi_view", *arguments],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=PACKAGE,
        env={
            "PATH": "",  # no nvidia-smi, no anything
            "SYSTEMROOT": "C:\\Windows",  # Windows needs this to start a process
            "PYTHONPATH": str(PACKAGE),
            "DRAUPNIR_API_HOST": "127.0.0.1:9",  # discard port: never answers
            "DRAUPNIR_RING_NEIGHBOURS": "",
        },
    )


# ---------------------------------------------------------------------------
# It works when nothing else does
# ---------------------------------------------------------------------------


def test_it_renders_with_every_source_unavailable() -> None:
    result = run()
    assert result.returncode == 0, result.stderr


def test_it_renders_all_eight_lines_with_every_source_unavailable() -> None:
    """Eight lines, not the ones that happened to work.

    A panel that dropped the readings it could not take would show a different
    number of lines during an outage than in normal operation, and the person
    reading it during the outage has no way to know what is missing.
    """
    result = run()
    labels = ["GPU", "Throttle", "Fabric", "Ring", "Run", "Vault", "Scheduler", "API"]
    for label in labels:
        assert f"{label}:" in result.stdout, f"{label} is missing from the panel"


def test_it_says_the_api_is_unreachable_rather_than_failing() -> None:
    result = run()
    assert "API:" in result.stdout
    assert "unreachable" in result.stdout


def test_it_distinguishes_unreachable_from_unknown() -> None:
    """An outage and a misconfiguration are different things to be told.

    `unreachable` means it was asked and did not answer. `unknown` means it
    could not be asked, which on this appliance means a missing tool. An
    operator does something different about each.
    """
    result = run("--json")
    assert '"status": "unreachable"' in result.stdout
    assert '"status": "unknown"' in result.stdout


def test_the_footer_names_what_is_unreachable() -> None:
    """So that "the last two lines are red" reads as expected, not as a second fault."""
    result = run()
    assert "Local readings only" in result.stdout
    assert "Unreachable:" in result.stdout


def test_json_mode_is_machine_readable_under_the_same_outage() -> None:
    import json

    result = run("--json")
    payload = json.loads(result.stdout)
    assert len(payload) == 8
    assert {entry["label"] for entry in payload} == {
        "GPU",
        "Throttle",
        "Fabric",
        "Ring",
        "Run",
        "Vault",
        "Scheduler",
        "API",
    }


# ---------------------------------------------------------------------------
# It depends on nothing
# ---------------------------------------------------------------------------


def imported_modules() -> set[str]:
    """Every module name the package imports, read from the source."""
    found: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize(
    "forbidden",
    ["httpx", "requests", "urllib3", "aiohttp", "draupnir", "draupnirctl"],
)
def test_it_imports_no_http_client_and_nothing_from_draupnir(forbidden: str) -> None:
    """The dependency that would quietly undo the whole thing.

    A view that reached the API would show nothing during the outage it exists
    for. This is checked against the source rather than left to a code review,
    because the import that breaks it will be added by someone solving an
    unrelated problem.
    """
    assert forbidden not in imported_modules()


def test_it_shares_no_code_path_with_the_console() -> None:
    """There is nothing to share, so nothing can be accidentally shared.

    The console lives in `web/`. If this package ever imported from a common
    Python module that the console's tooling also used, the two would have a
    shared path down which an API dependency could arrive.
    """
    assert imported_modules() <= {
        "__future__",
        "argparse",
        "ast",
        "dataclasses",
        "datetime",
        "enum",
        "json",
        "os",
        "pathlib",
        "shutil",
        "socket",
        "stedi_view",
        "subprocess",
        "sys",
        "time",
        "typing",
    }


def test_it_declares_no_dependencies() -> None:
    """The manifest says so too, so a `pip install` cannot add one silently."""
    manifest = (PACKAGE / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in manifest


def test_it_offers_no_control() -> None:
    """Read only: there is no action on this view, so no action can fail.

    Checked by the absence of any argument that would change something. The
    view's flags are `--watch`, `--json`, `--interval` and `--version`.
    """
    result = run("--help")
    assert result.returncode == 0
    for verb in ("--submit", "--cancel", "--retry", "--approve", "--delete"):
        assert verb not in result.stdout
