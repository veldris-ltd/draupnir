"""One rule for `docs/acceptance/evidence/`: run records are not committed. RF-31.

`keyboard-pass.json` was committed and rewritten by every a11y run, with a new
timestamp and whatever the seeded database held that day, so every run left a
fifty-line diff. A file that changes on every run is one nobody reads a diff of,
which weakens the evidence as well as cluttering the tree.

The two files in the directory are both records of a run, with a clock and
database state in them, and neither can be reproduced byte for byte without
freezing both. So the rule is the same for both, and for anything written there
later: git ignores the directory, the pipeline writes the records on every
build, and it uploads them with the rest of its evidence. `test-a11y` and
`procedure` also check, after writing, that the tree is still clean.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = "docs/acceptance/evidence"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yaml"

#: What writes there today. Named so the rule is checked against real paths as
#: well as an invented one.
RECORDS = ("keyboard-pass.json", "procedure-m1-m10.json")


def git(*arguments: str) -> subprocess.CompletedProcess[str]:
    binary = shutil.which("git")
    if binary is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout, so there is no index to inspect")
    return subprocess.run(  # noqa: S603
        [binary, *arguments], cwd=ROOT, capture_output=True, text=True, check=False
    )


def test_nothing_under_the_evidence_directory_is_tracked() -> None:
    tracked = git("ls-files", "--", EVIDENCE).stdout.split()

    assert tracked == [], (
        f"{tracked} are committed. A run record in the repository is rewritten by "
        "every run; the pipeline uploads these instead (RF-31)."
    )


@pytest.mark.parametrize("name", [*RECORDS, "a-record-added-later.json"])
def test_every_record_written_there_is_ignored(name: str) -> None:
    ignored = git("check-ignore", "--quiet", f"{EVIDENCE}/{name}")

    assert ignored.returncode == 0, f"{EVIDENCE}/{name} would show up as a change"


def test_the_pipeline_produces_and_uploads_the_records() -> None:
    """Not tracked is only half the rule; the other half is somewhere to find it."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    upload = workflow.split("name: Upload evidence", 1)[1]
    paths = upload.split("path: |", 1)[1].split("retention-days", 1)[0]

    assert f"{EVIDENCE}/" in paths, "the evidence records are not uploaded"
    assert re.search(r"run: python tasks\.py test-a11y\b", workflow), (
        "nothing in the pipeline writes the keyboard record"
    )
    assert re.search(r"run: python tasks\.py procedure\b", workflow), (
        "nothing in the pipeline writes the procedure record, so untracking it lost it"
    )


def test_the_acceptance_readme_says_where_the_records_are() -> None:
    readme = (ROOT / "docs" / "acceptance" / "README.md").read_text(encoding="utf-8")

    assert "acceptance-evidence-" in readme
    for name in RECORDS:
        assert name in readme, f"the README does not say where {name} is"
