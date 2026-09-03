"""AC-B7: an import from the edge into the domain fails the import linter.

"An import from the edge layer into the domain layer fails the import linter in
continuous integration."

Checked by planting the violation and watching the linter catch it. A test that
asserted the contract exists in `.importlinter` would assert that a file has a
line in it; a linter nobody has watched fail is a linter nobody knows works,
and the failure mode being guarded against -- a contract that is present,
parsed, and enforcing nothing -- looks exactly like a passing build.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[2]

#: Planted inside the domain layer, which SAD 11B says imports no framework and
#: nothing above it. `_planted` is a name no real module would take.
VIOLATION = ROOT / "draupnir" / "core" / "domain" / "_planted_violation.py"

SOURCE = '''"""A deliberate layering violation, planted by a test and removed by it.

If this file is on disk after a test run, the run was interrupted. Delete it:
nothing imports it, and the import linter will fail while it exists.
"""

from draupnir.api.app import create_app

__all__ = ["create_app"]
'''


@pytest.fixture
def planted() -> Iterator[Path]:
    """Write the violating module, and remove it however the test ends."""
    VIOLATION.write_text(SOURCE, encoding="utf-8", newline="\n")
    try:
        yield VIOLATION
    finally:
        VIOLATION.unlink(missing_ok=True)


def _lint() -> subprocess.CompletedProcess[str]:
    """Run the real linter against the real configuration."""
    return subprocess.run(  # noqa: S603 -- fixed argument vector, no shell
        [str(_lint_imports()), "--config", str(ROOT / ".importlinter")],
        capture_output=True,
        text=True,
        # The linter's spinner is an emoji. Decoding its output as the Windows
        # ANSI codepage raises, which looks like a broken linter rather than a
        # broken decode -- the same failure that made a green stage exit 1
        # during the console build.
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        check=False,
        # The spinner encodes an emoji, and a cp1252 stdout kills the process
        # while reporting success. Found in the console build; fixed there for
        # the task runner and needed again here.
        env={"PYTHONIOENCODING": "utf-8", **_environ()},
    )


def _lint_imports() -> Path:
    """The `lint-imports` console script in this environment."""
    candidate = Path(sys.executable).parent / ("lint-imports.exe" if _windows() else "lint-imports")
    if not candidate.exists():
        pytest.skip("import-linter is not installed in this environment")
    return candidate


def _windows() -> bool:
    return sys.platform.startswith("win")


def _environ() -> dict[str, str]:
    import os

    return dict(os.environ)


def test_the_contracts_hold_without_the_planted_violation() -> None:
    """The control. A linter that always failed would pass the test below."""
    result = _lint()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 broken" in result.stdout


def test_an_edge_import_into_the_domain_breaks_a_contract(planted: Path) -> None:
    """AC-B7, with the violation on disk while the linter runs."""
    assert planted.is_file()

    result = _lint()

    assert result.returncode != 0, result.stdout
    assert "BROKEN" in result.stdout
    # And it names what it caught, which is what makes the failure actionable.
    assert "domain" in result.stdout
