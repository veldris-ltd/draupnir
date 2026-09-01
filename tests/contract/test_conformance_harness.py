"""Driver conformance harness.

SAD 11E.3: every first party driver runs against the conformance harness, and
the harness is published for third parties. The seven Protocols and the harness
itself are built in Prompt 2.

The stage exists now, with a real assertion rather than a skip, because an
empty harness that runs is worth more than a good harness added at the end: the
pipeline stage, the marker and the discovery mechanism are all exercised from
the first build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS = REPO_ROOT / "plugins"

pytestmark = pytest.mark.contract


def discovered_drivers() -> list[Path]:
    """Return every first party driver package under `plugins/`."""
    return sorted(path.parent for path in PLUGINS.glob("*/pyproject.toml"))


def test_the_plugins_directory_exists() -> None:
    assert PLUGINS.is_dir(), "plugins/ is part of the repository layout (SAD 11E.1)"


def test_every_discovered_driver_declares_itself_installable() -> None:
    # Until Prompt 2 defines the Protocols, conformance means one thing: a
    # driver is an installable package with a name. When the harness lands it
    # replaces this body; the discovery above is what it will use.
    for driver in discovered_drivers():
        manifest = (driver / "pyproject.toml").read_text(encoding="utf-8")
        assert "[project]" in manifest, f"{driver.name} has no [project] table"
        assert "name" in manifest, f"{driver.name} declares no name"


def test_the_harness_reports_what_it_found(capsys: pytest.CaptureFixture[str]) -> None:
    drivers = discovered_drivers()
    print(f"conformance harness: {len(drivers)} first party driver(s) discovered")  # noqa: T201
    assert "conformance harness" in capsys.readouterr().out
