"""Driver discovery at the contract level.

The suites themselves are in `test_reference_drivers.py`, which runs the
published conformance harness against every installed first party driver. What
is checked here is the shape of the directory those drivers live in: that
`plugins/` holds installable distributions and that each one declares an entry
point, because a driver that is not registered is a driver the conformance
suite never sees.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from draupnir.interfaces.naming import InterfaceName
from draupnir.interfaces.types import GROUPS

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS = REPO_ROOT / "plugins"

pytestmark = pytest.mark.contract


def distributions() -> list[Path]:
    """Every first party driver distribution under `plugins/`."""
    return sorted(path.parent for path in PLUGINS.glob("*/pyproject.toml"))


def test_the_plugins_directory_holds_distributions() -> None:
    assert PLUGINS.is_dir(), "plugins/ is part of the repository layout (SAD 11E.1)"
    assert distributions(), "no first party driver is installed"


@pytest.mark.parametrize("distribution", distributions(), ids=lambda path: path.name)
def test_the_distribution_registers_a_versioned_entry_point(distribution: Path) -> None:
    """Each driver declares a group of SAD 8.2 and a versioned name of SAD 10.3."""
    manifest = tomllib.loads((distribution / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["project"]["name"], f"{distribution.name} declares no name"

    points = manifest["project"].get("entry-points", {})
    assert points, f"{distribution.name} registers no entry point, so nothing will load it"

    for group, registered in points.items():
        assert group in GROUPS, f"{group} is not an entry point group of SAD 8.2"
        for name in registered:
            # Raises if the name is not `namespace.implementation/vMAJOR`.
            assert InterfaceName.parse(name).major >= 1


@pytest.mark.parametrize("distribution", distributions(), ids=lambda path: path.name)
def test_the_distribution_depends_on_draupnir(distribution: Path) -> None:
    manifest = tomllib.loads((distribution / "pyproject.toml").read_text(encoding="utf-8"))
    assert "draupnir" in manifest["project"]["dependencies"]
