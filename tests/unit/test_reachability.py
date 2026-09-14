"""The reachability analysis, and the reconciliation's marks against it. RF-30.

IMPLEMENTED is satisfied by a unit test, so it could not say that the
platform's security, federation and publication controls sat on no path a
deployment takes. REACHABLE can, and only if it is derived: a hand-kept list of
reachable modules is a list that is right on the day it is written.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts import reachability

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
RECONCILIATION = ROOT / "docs" / "acceptance" / "imhotep-reconciliation.md"

ROW = re.compile(r"^\| `(draupnir\.[\w.]+)` \| \*\*(REACHABLE|NOT REACHABLE)\*\* \|", re.MULTILINE)


def marks() -> dict[str, str]:
    """Every module the reconciliation marks, with its mark."""
    return dict(ROW.findall(RECONCILIATION.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def reached() -> dict[str, str]:
    return reachability.reachable()


# ---------------------------------------------------------------------------
# The analysis
# ---------------------------------------------------------------------------


def test_an_import_only_a_type_checker_sees_is_not_counted(tmp_path: Path) -> None:
    """`if TYPE_CHECKING:` never runs, so it reaches nothing."""
    source = tmp_path / "module.py"
    source.write_text(
        "from typing import TYPE_CHECKING\n"
        "import draupnir.a\n"
        "if TYPE_CHECKING:\n"
        "    import draupnir.hidden\n"
        "else:\n"
        "    import draupnir.otherwise\n",
        encoding="utf-8",
    )

    names = reachability.imported_names(source, "draupnir.pkg.module")

    assert "draupnir.a" in names
    assert "draupnir.otherwise" in names
    assert "draupnir.hidden" not in names


def test_a_relative_import_resolves_against_its_package(tmp_path: Path) -> None:
    module = tmp_path / "module.py"
    module.write_text("from . import sibling\nfrom ..other import thing\n", encoding="utf-8")
    package = tmp_path / "__init__.py"
    package.write_text("from .child import value\n", encoding="utf-8")

    from_module = reachability.imported_names(module, "draupnir.pkg.module")
    from_package = reachability.imported_names(package, "draupnir.pkg")

    assert {"draupnir.pkg.sibling", "draupnir.other.thing"} <= from_module
    assert "draupnir.pkg.child" in from_package


def test_an_import_inside_a_function_counts() -> None:
    """The one over-approximation, asserted so the docstring's claim is true.

    `tls_configured` imports the settings inside the function body.
    """
    path = ROOT / "draupnir" / "svalinn" / "inventory.py"

    assert "draupnir.core.infrastructure.config" in reachability.imported_names(
        path, "draupnir.svalinn.inventory"
    )


def test_the_deployment_entry_points_are_reachable(reached: dict[str, str]) -> None:
    for entry in reachability.ENTRY_POINTS:
        assert entry in reached
    assert "draupnir.api.routers.runs" in reached, "the API's own routers are not reached"


def test_every_installed_plugin_is_a_root(reached: dict[str, str]) -> None:
    """`draupnir.core.plugins` loads drivers by entry point, which no import shows."""
    packages = reachability._plugin_packages()

    assert packages, "no plug-in declares an entry point"
    for package in packages:
        assert reached.get(package) == reachability.PLUGIN


def test_a_test_does_not_make_a_module_reachable() -> None:
    assert not any(name.startswith("tests") for name in reachability.modules())


# ---------------------------------------------------------------------------
# The reconciliation against it
# ---------------------------------------------------------------------------


def test_every_reachability_mark_in_the_reconciliation_is_the_derived_one(
    reached: dict[str, str],
) -> None:
    stated = marks()
    assert stated, "the reconciliation has no reachability table"

    wrong = [
        f"{module} is marked {mark}, and it is "
        f"{'REACHABLE' if module in reached else 'NOT REACHABLE'}"
        for module, mark in stated.items()
        if (mark == "REACHABLE") != (module in reached)
    ]

    assert wrong == [], "\n".join(wrong)


def test_every_unreachable_platform_module_is_named_in_the_reconciliation() -> None:
    """A new orphan fails the build until somebody says why it is one."""
    stated = marks()
    missing = [name for name in reachability.unreachable() if name not in stated]

    assert missing == [], (
        "platform modules nothing a deployment runs reaches, and the reconciliation "
        f"does not name: {missing}"
    )
