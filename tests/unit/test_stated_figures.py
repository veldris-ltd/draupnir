"""Figures written in prose, checked against the things they count. RF-30.

A number written in three places is a number that is wrong in two of them. The
README said 168 stories, the reconciliation 175, the keyboard pass 175, and
Storybook held 220; three documents had three components counts and none was
right. Nothing failed, because nothing read them.

So every design-system figure in the documents that state one is read here and
compared with the stories themselves, and the reconciliation's operation count
with the OpenAPI document. Adding a component or a story fails this until the
documents say so.

The stories are counted from their source rather than from
`web/storybook-static/index.json`. The index is a build output, ignored by git
and absent when this runs in stage 2.1, and a gate that only checks when
somebody happened to build Storybook first is not a gate. Where the index is
present, the source count is checked against it, so the parser here cannot drift
from Storybook's own indexer unnoticed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ROOT / "web" / "packages"
STORYBOOK_INDEX = ROOT / "web" / "storybook-static" / "index.json"
RECONCILIATION = ROOT / "docs" / "acceptance" / "imhotep-reconciliation.md"

#: Every document that states a design-system figure.
DOCUMENTS = (
    ROOT / "README.md",
    PACKAGES / "jarngreipr" / "README.md",
    RECONCILIATION,
    ROOT / "docs" / "acceptance" / "keyboard-pass.md",
)

#: A figure and what it counts. Whitespace rather than a space, because prose
#: wraps: "30" ends one line of the README and "components" starts the next.
FIGURE = re.compile(
    r"\b(\d+|[A-Za-z]+(?:-[a-z]+)?)\s+(primitives|composites|components|Storybook\s+stories|stories)\b"
)
OPERATIONS = re.compile(r"\b(\d+|[A-Za-z]+(?:-[a-z]+)?)\s+operations\b")

UNITS = {
    word: value
    for value, word in enumerate(
        (
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        )
    )
}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60}


def as_number(word: str) -> int | None:
    """`220`, `eleven` or `thirty-four` as a number; anything else as None.

    None rather than an error, because most words before "stories" are not
    numbers -- "the stories", "whose stories" -- and an ordinal such as
    "twentieth" is not a count.
    """
    word = word.lower()
    if word.isdigit():
        return int(word)
    if word in UNITS:
        return UNITS[word]
    tens, _, unit = word.partition("-")
    if tens in TENS and (not unit or (unit in UNITS and UNITS[unit] < 10)):
        return TENS[tens] + (UNITS[unit] if unit else 0)
    return None


def stories_by_title() -> dict[str, int]:
    """Every Storybook title in the packages, with how many stories it holds.

    Read the way Storybook's static indexer reads a story file: a literal
    `title`, and one story per named export.
    """
    found: dict[str, int] = {}
    for path in sorted(PACKAGES.glob("*/src/**/*.stories.ts*")):
        text = path.read_text(encoding="utf-8")
        title = re.search(r"^\s*title:\s*'([^']+)'", text, re.MULTILINE)
        assert title is not None, f"{path.relative_to(ROOT)} has no literal title"
        found[title.group(1)] = len(re.findall(r"^export const \w+", text, re.MULTILINE))
    return found


def figures() -> dict[str, int]:
    """What the design system actually has."""
    titles = stories_by_title()
    primitives = sum(1 for title in titles if title.startswith("Primitives/"))
    composites = sum(1 for title in titles if title.startswith("Composites/"))
    return {
        "primitives": primitives,
        "composites": composites,
        "components": primitives + composites,
        "stories": sum(titles.values()),
    }


def test_the_stories_are_counted_the_way_storybook_counts_them() -> None:
    """The source count against the built index, where there is a current one.

    Skipped when the index is older than a story file, because a build from
    before the last edit describes stories that no longer exist, and failing on
    it would fail this for whoever last built Storybook rather than for a
    miscount.
    """
    if not STORYBOOK_INDEX.exists():
        pytest.skip("Storybook has not been built here; the source count stands alone")
    built_at = STORYBOOK_INDEX.stat().st_mtime
    if any(path.stat().st_mtime > built_at for path in PACKAGES.glob("*/src/**/*.stories.ts*")):
        pytest.skip("the Storybook build predates a story file; rebuild it to compare")
    index = json.loads(STORYBOOK_INDEX.read_text(encoding="utf-8"))
    built: dict[str, int] = {}
    for entry in index["entries"].values():
        if entry.get("type") == "story":
            built[entry["title"]] = built.get(entry["title"], 0) + 1

    assert stories_by_title() == built


def test_every_design_system_figure_in_the_documents_is_the_real_one() -> None:
    actual = figures()
    stated: list[str] = []
    wrong: list[str] = []
    for document in DOCUMENTS:
        text = document.read_text(encoding="utf-8")
        for match in FIGURE.finditer(text):
            number = as_number(match.group(1))
            if number is None:
                continue
            kind = "stories" if match.group(2).endswith("stories") else match.group(2)
            where = f"{document.relative_to(ROOT)}: '{' '.join(match.group(0).split())}'"
            stated.append(where)
            if number != actual[kind]:
                wrong.append(f"{where}, and there are {actual[kind]} {kind}")

    assert wrong == [], "\n".join(wrong)
    # A check that stopped finding figures would pass for ever, so it has to
    # keep finding the ones the documents are known to state.
    assert len(stated) >= 8, f"expected the documents to state their figures; found {stated}"


def test_no_story_is_filed_as_an_example() -> None:
    """RF-30. The sidebar is read as the inventory, and a sample padded it."""
    assert not [title for title in stories_by_title() if title.startswith("Example/")]


def test_the_reconciliation_states_the_real_operation_count() -> None:
    document = json.loads((ROOT / "docs" / "api" / "openapi.json").read_text(encoding="utf-8"))
    methods = {"get", "put", "post", "patch", "delete"}
    count = sum(1 for item in document["paths"].values() for method in item if method in methods)

    stated = [
        (match.group(0), as_number(match.group(1)))
        for match in OPERATIONS.finditer(RECONCILIATION.read_text(encoding="utf-8"))
    ]
    numbers = [(phrase, number) for phrase, number in stated if number is not None]

    assert numbers, "the reconciliation no longer states how many operations there are"
    assert all(number == count for _, number in numbers), (
        f"the OpenAPI document has {count} operations; the reconciliation says {numbers}"
    )
