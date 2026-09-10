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

    # Singular as well as plural. The count is meant to fall to one and then to
    # none, and a check that only parsed "N items are" would fail on the
    # sentence a document with one item left has to write.
    stated = re.search(r"(\w+) items? (?:are|is) \*\*NOT BUILT\*\*", text)
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


# ---------------------------------------------------------------------------
# The join between the two documents. RF-E19.
# ---------------------------------------------------------------------------

DEPLOYMENT = ROOT / "docs" / "DEPLOYMENT.md"


def test_the_deployment_guide_says_which_document_is_authoritative() -> None:
    """RF-E19's central ambiguity.

    Two documents describe how work gets done at Sindri and neither said which
    one wins. The consequence is not academic: an operator following the manual
    builds an estate with no control plane, and an operator following this
    guide installs one onto a host the manual never prepared.
    """
    text = DEPLOYMENT.read_text(encoding="utf-8")

    assert "Which document is in charge" in text
    assert "by hand at commissioning" in text.lower(), (
        "the guide does not state the division of authority in a form somebody skimming will find"
    )
    assert "VLD-INF-SINDRI-001" in text


def test_the_deployment_guide_cites_the_procedure_that_prepares_the_host() -> None:
    """S13 is the seam. A guide that did not name it would be an orphan.

    The manual's Procedure S13 covers what the estate owes the control plane
    and then defers here for the installation. Neither document repeats the
    other, which only works if each says so.
    """
    text = DEPLOYMENT.read_text(encoding="utf-8")

    assert "Procedure S13" in text
    assert "does not repeat" in text, (
        "the guide does not say that it and S13 are complementary rather than "
        "duplicates, which is the property that stops them drifting"
    )


def test_the_deployment_guide_names_the_values_the_scripts_produce() -> None:
    """AC-D3, and RF-E19's acceptance criterion.

    Each value is read from the place that actually decides it rather than
    from a second list here, so this fails when the guide drifts from the
    scripts *or* when the scripts change under a guide nobody updated.
    """
    import subprocess

    from draupnir.core.infrastructure.config import Settings

    text = DEPLOYMENT.read_text(encoding="utf-8")
    settings = Settings()
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")

    # The database host, from lib.sh's own derivation rather than a literal.
    host = subprocess.run(
        ["bash", "-c", "source deploy/lib.sh; DRAUPNIR_SITE_ID=sindri draupnir_host_for andvari"],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    ).stdout.strip()

    expected = {
        "the database host": host or "andvari.sindri.veldris.internal",
        "the database name": settings.database_url.rsplit("/", 1)[-1],
        "the bucket": settings.object_store_bucket,
        "the vault path": _default_of(installer, "DRAUPNIR_VAULT_ROOT"),
        "the scheduler port": "6820",
    }

    missing = [f"{label} ({value})" for label, value in expected.items() if value not in text]

    assert not missing, (
        f"docs/DEPLOYMENT.md does not name {', '.join(missing)}. An operator "
        "following it would configure something the scripts do not expect, and the "
        "mismatch surfaces as a dependency check that cannot be made to pass."
    )


def _default_of(installer: str, name: str) -> str:
    """The default `install.sh` gives one setting, read from the script."""
    match = re.search(rf"\$\{{{re.escape(name)}-([^}}]*)\}}", installer)
    assert match, f"install.sh no longer defaults {name}"
    return match.group(1)


def test_the_guide_only_shows_check_lines_the_preflight_can_produce() -> None:
    """The guide quotes the script's output, so it can quote it wrongly.

    That is not hypothetical: this test was written after a `scheduler:` line
    was added to the guide in the wrong section and with invented wording. A
    reader comparing their terminal against the page would have found a
    mismatch and, following the guide's own instruction, stopped.

    Checked structurally rather than by string. Every `<dependency>: <verdict>`
    the failure table names must be a pair `preflight.py` can actually emit.
    """
    import inspect

    from scripts import preflight

    text = DEPLOYMENT.read_text(encoding="utf-8")
    source = inspect.getsource(preflight)

    dependencies = {result.dependency for result in preflight.check({})}
    verdicts = set(preflight.Verdict.__args__)  # type: ignore[attr-defined]

    quoted = set(re.findall(r"`([a-z-]+): ([a-z-]+)`", text))
    named = {(name, word) for name, word in quoted if name in dependencies}

    assert named, "the guide's failure table names no dependency at all any more"

    wrong = {
        f"{name}: {word}" for name, word in named if word not in verdicts and word not in source
    }
    assert not wrong, (
        f"docs/DEPLOYMENT.md tells an operator to look for {sorted(wrong)}, and "
        "scripts/preflight.py never emits that. A guide that describes output the "
        "software does not produce sends a reader to Part 8 over nothing."
    )


def test_the_guide_covers_every_dependency_the_check_reports() -> None:
    """A dependency the check reports and the guide never mentions is a dead end.

    `scheduler` was exactly that: `install.sh --check` has reported on it since
    RF-E05, and the guide named neither the dependency, the host it reaches,
    nor what to do when it fails.
    """
    from scripts import preflight

    text = DEPLOYMENT.read_text(encoding="utf-8")
    missing = [
        result.dependency for result in preflight.check({}) if f"{result.dependency}:" not in text
    ]

    assert not missing, (
        f"`install.sh --check` reports on {missing} and docs/DEPLOYMENT.md never "
        "mentions it. An operator who sees that line has nowhere to look."
    )
