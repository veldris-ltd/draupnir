"""The documentation deliverables of SAD 12.4, checked rather than asserted.

AC-D1, AC-D3 and AC-D4 are documents, and a document is the easiest deliverable
to let go stale: nothing fails when it does. So each is generated from the thing
it describes where that is possible, and this checks that the generation is
current and that the hand-written parts cover what they claim to.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts import acceptance, module_readmes

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def test_every_module_readme_matches_its_docstring() -> None:
    """AC-D1. A README edited by hand is a second statement of one thing."""
    stale = [
        package.name
        for package in module_readmes.packages()
        if (package / "README.md").read_text(encoding="utf-8") != module_readmes.render(package)
    ]

    assert stale == [], f"stale README(s): {', '.join(stale)}. Run `make module-readmes`."


def test_every_module_states_what_it_must_not_do() -> None:
    """AC-D1's second clause, which is the half that is usually missing."""
    for package in module_readmes.packages():
        text = (package / "README.md").read_text(encoding="utf-8")
        assert "**Owns.**" in text, package.name
        assert "**Must not.**" in text, package.name


def test_the_runbook_covers_every_degraded_mode() -> None:
    """AC-D3, against the table in the SAD rather than against a list here.

    The rows are parsed out of SAD 11.2, so a row added to the specification
    and not to the runbook fails here.
    """
    sad = (ROOT / "docs" / "build" / "draupnir-sad.md").read_text(encoding="utf-8")
    block = sad[sad.index("### 11.2  Degraded modes") : sad.index("### 11.3  Observability")]
    failures = [
        row.split("|")[1].strip()
        for row in block.splitlines()
        if row.startswith("|") and "---" not in row
    ][1:]
    assert len(failures) == 9, failures

    runbook = (ROOT / "docs" / "runbook.md").read_text(encoding="utf-8").lower()

    # Matched on the distinguishing noun of each row rather than on the whole
    # sentence: the runbook is written for an operator and phrases each as a
    # symptom, which is the right thing for it to do and the wrong thing to
    # compare literally.
    for noun in (
        "control plane restart",
        "slurm",
        "appliance",
        "vault",
        "postgresql",
        "chain verification",
        "megingjord",
        "divergence",
        "mains",
    ):
        assert noun in runbook, f"the runbook does not cover {noun}"


def test_the_acceptance_pack_is_current_and_every_must_has_evidence() -> None:
    """AC-D4, and the prompt's exit condition.

    `problems()` is the same check `make acceptance --check` runs: every
    criterion in SAD 12 has a status, every deviation has a reason, every
    implemented criterion is cited somewhere in the repository, and every page
    is what the register and the citations currently produce.
    """
    issues = acceptance.problems()
    assert issues == [], "\n".join(issues)

    musts = [
        criterion
        for criterion in acceptance.criteria()
        if criterion.priority == "Must"
        and acceptance.REGISTER[criterion.ref].status == acceptance.NOT_BUILT
    ]
    assert musts == [], f"Must criteria with no evidence: {[item.ref for item in musts]}"


def test_the_reconciliation_marks_every_item_in_the_vocabulary_ac_d4_asks_for() -> None:
    """AC-D4. IMPLEMENTED, DEVIATED with reasons, or NOT BUILT."""
    text = (ROOT / "docs" / "acceptance" / "imhotep-reconciliation.md").read_text(encoding="utf-8")

    for mark in ("IMPLEMENTED", "DEVIATED", "NOT BUILT"):
        assert mark in text, mark

    # Every section of the SAD that states a requirement is reconciled. The
    # numbers are matched rather than the titles: a section renamed is still
    # the same section, and a section removed is a change to the SAD.
    for section in ("5.1", "5.2", "6.1", "6.2", "8.1", "8.2", "9A", "11A", "11G", "11H"):
        assert re.search(rf"\b{re.escape(section)}\b", text), f"section {section} is unreconciled"

    # Every NOT BUILT says what is missing rather than only that something is,
    # and the summary agrees with how many there are. The count is read from the
    # document rather than pinned here: pinning it makes closing one of them a
    # test failure, which is the wrong incentive.
    headings = re.findall(r"^### NOT BUILT (\d+) — (.+)$", text, re.MULTILINE)
    assert headings, "the reconciliation names nothing as NOT BUILT and nothing as complete"
    assert [number for number, _ in headings] == [
        str(index) for index in range(1, len(headings) + 1)
    ], "the NOT BUILT items are misnumbered"
    # Each has a body. A heading alone names something and explains nothing,
    # and "what is missing is stated" is what the vocabulary promises.
    sections = re.split(r"^### NOT BUILT \d+ — .+$", text, flags=re.MULTILINE)[1:]
    for (number, title), body in zip(headings, sections, strict=True):
        assert len(body.split()) > 30, f"NOT BUILT {number} ({title}) says too little"

    stated = re.search(r"(\w+) items are \*\*NOT BUILT\*\*", text)
    assert stated is not None, "the summary does not say how many items are not built"
    words = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5, "Six": 6}
    assert words[stated.group(1)] == len(headings), (
        f"the summary says {stated.group(1)} and there are {len(headings)}"
    )


def test_the_keyboard_pass_records_its_method_and_its_limits() -> None:
    """AC-U5. A pass that claimed more than it did would be worse than none."""
    text = (ROOT / "docs" / "acceptance" / "keyboard-pass.md").read_text(encoding="utf-8")

    assert "How it was performed" in text
    assert "What is not covered" in text
    # The distinction that matters: this is a keyboard traversal, not a screen
    # reader pass, and saying so is the difference between evidence and a claim.
    assert "not a screen-reader pass" in text
