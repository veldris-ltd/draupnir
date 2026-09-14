"""Every screen's primary action has something behind it. RF-27.

VLD-UX-DRAUPNIR-001 section 8 gives each of thirty one screens a primary
action, and nine of them named an action the API could not perform: S05, S06,
S12, S15, S17 and S22 to S25. The console did not offer those controls either,
so the screens agreed with the API and disagreed with the specification --
and nothing noticed, because nothing compared the two.

This compares them. `docs/api/screen-actions.json` says, for every row of
section 8, what performs its action: an operation in `docs/api/openapi.json`,
the read a navigation action opens, or a stated reason the screen is read-only
in this release. The test fails when a screen, its action text, or an operation
drifts, so the two documents cannot come apart again without a build noticing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
UX = REPO_ROOT / "docs" / "build" / "draupnir-ux.md"
REGISTER = REPO_ROOT / "docs" / "api" / "screen-actions.json"
OPENAPI = REPO_ROOT / "docs" / "api" / "openapi.json"

#: A row of the section 8 table: `| S01 | Overview | purpose | action | role |`.
ROW = re.compile(
    r"^\|\s*(S\d{2})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$"
)

#: A reason short enough to say nothing is not a reason.
MINIMUM_REASON_WORDS = 15


def inventory() -> dict[str, str]:
    """Screen reference to primary action, read from section 8 of the UX specification."""
    text = UX.read_text(encoding="utf-8")
    start = text.index("## 8  Screen inventory")
    end = text.index("\n## 9", start)
    found = {}
    for line in text[start:end].splitlines():
        matched = ROW.match(line.strip())
        if matched:
            found[matched.group(1)] = matched.group(4)
    return found


def register() -> dict[str, dict[str, Any]]:
    screens: dict[str, dict[str, Any]] = json.loads(REGISTER.read_text(encoding="utf-8"))["screens"]
    return screens


def operation_ids() -> set[str]:
    document = json.loads(OPENAPI.read_text(encoding="utf-8"))
    return {
        operation["operationId"]
        for operations in document["paths"].values()
        for operation in operations.values()
        if isinstance(operation, dict) and "operationId" in operation
    }


def test_the_inventory_is_read_rather_than_assumed() -> None:
    """A parser that found nothing would pass every assertion below."""
    assert len(inventory()) == 31


def test_every_screen_in_the_specification_is_in_the_register() -> None:
    assert set(register()) == set(inventory())


def test_every_action_is_quoted_as_the_specification_states_it() -> None:
    """So an amended row in section 8 fails here until the register follows it."""
    drifted = {
        screen: (entry["action"], inventory()[screen])
        for screen, entry in register().items()
        if entry["action"] != inventory()[screen]
    }

    assert drifted == {}


def test_every_operation_named_exists() -> None:
    known = operation_ids()
    named = {
        screen: operation
        for screen, entry in register().items()
        for operation in (*entry.get("operations", ()), *entry.get("reads", ()))
        if operation not in known
    }

    assert named == {}, "the register names operations the API does not have"


def test_an_action_is_performed_by_an_operation_or_says_why_not() -> None:
    for screen, entry in register().items():
        kind = entry["kind"]
        assert kind in {"operation", "navigation", "readOnly"}, screen
        if kind == "operation":
            assert entry.get("operations"), f"{screen} names no operation"
            assert "reason" not in entry, f"{screen} is performed and still gives a reason"
            continue
        words = len(str(entry.get("reason", "")).split())
        assert words >= MINIMUM_REASON_WORDS, (
            f"{screen}: {words} words is not a reason for having no operation"
        )
        if kind == "navigation":
            assert entry.get("reads"), f"{screen} navigates to a view that reads nothing"


def test_a_read_only_screen_whose_row_names_an_action_cites_its_amendment() -> None:
    """The specification still names an action; the register says there is none.

    That is a disagreement between two documents, and it is allowed only while
    a proposal to amend section 8 exists and says so. Without the citation the
    test would be the drift it exists to catch.
    """
    for screen, entry in register().items():
        if entry["kind"] != "readOnly" or entry["action"].startswith("None."):
            continue
        amendment = entry.get("amendment")
        assert amendment, f"{screen} is read-only against a row that names an action"
        proposal = REPO_ROOT / amendment
        assert proposal.is_file(), f"{screen} cites {amendment}, which does not exist"
        assert screen in proposal.read_text(encoding="utf-8"), (
            f"{amendment} does not mention {screen}"
        )


def test_the_nine_screens_rf27_found_are_each_answered() -> None:
    """Built, or read-only with a reason: never an action nothing can perform."""
    entries = register()
    built = {"S06", "S12", "S15", "S17"}
    declined = {"S05", "S22", "S23", "S24", "S25"}

    assert {screen for screen in built if entries[screen]["kind"] == "operation"} == built
    assert {screen for screen in declined if entries[screen]["kind"] == "readOnly"} == declined
