"""The drift gates, and the bytes they compare. RF-23.

`clients-check` compared `path.read_bytes()` before and after regenerating, and
the generators disagreed with the checkout about line endings — `.gitattributes`
is `* text=auto`, and four of this repository's nine generators wrote with
`Path.write_text` and no `newline`, which translates every newline to
`os.linesep`. On Windows that is CRLF. The gate reported drift in files whose
`git diff --stat` showed no change at all, every time, on every build.

The repository has met this once already — `a18f7c7`, "Let Prettier accept the
line endings Git checks out" — so it is one class of bug in a second place, and
these tests exist so there is not a third.

Two fixes, and they answer different questions. Every generator now writes LF,
so its *output* does not depend on who ran it; the gate normalises its
*comparison*, so it is right about a file however it came to have the endings
it has. Both are asserted below, because either alone leaves a gap: the first
does nothing for a file an editor converted, and the second lets two developers
commit different bytes for the same document.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(REPO_ROOT))

import tasks  # noqa: E402 -- the repository root has to be on the path first

#: Every script that writes a file this repository then compares, diffs or
#: signs. Derived below rather than trusted: the list is what the test reads,
#: and a new generator arrives in it by existing.
GENERATORS = sorted((REPO_ROOT / "scripts").glob("*.py"))


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def test_a_line_ending_difference_is_not_drift(tmp_path: Path) -> None:
    """The failure the gate reported on every Windows build.

    Same content, different endings. `read_bytes` says they differ; a gate that
    names a file whose diff shows nothing teaches people to stop reading it.
    """
    crlf = tmp_path / "crlf.ts"
    lf = tmp_path / "lf.ts"
    crlf.write_bytes(b"export const a = 1;\r\nexport const b = 2;\r\n")
    lf.write_bytes(b"export const a = 1;\nexport const b = 2;\n")

    assert crlf.read_bytes() != lf.read_bytes(), "the fixture is not testing anything"
    assert tasks.content_of(crlf) == tasks.content_of(lf)


def test_a_content_difference_is_still_drift(tmp_path: Path) -> None:
    """The half that has to keep working.

    Normalising line endings must not normalise anything else. A gate that
    tolerated a real hand edit would be worse than the one that cried wolf,
    because the failure it exists to catch would pass.
    """
    original = tmp_path / "original.ts"
    edited = tmp_path / "edited.ts"
    original.write_bytes(b"export const a = 1;\r\n")
    edited.write_bytes(b"export const a = 2;\r\n")

    assert tasks.content_of(original) != tasks.content_of(edited)


def test_a_hand_edit_hidden_among_line_ending_changes_is_still_drift(
    tmp_path: Path,
) -> None:
    """Both at once, which is what a real drift looks like on Windows."""
    committed = tmp_path / "committed.ts"
    regenerated = tmp_path / "regenerated.ts"
    committed.write_bytes(b"a\r\nb\r\nc\r\n")
    regenerated.write_bytes(b"a\nb\nCHANGED\n")

    assert tasks.content_of(committed) != tasks.content_of(regenerated)


def test_an_absent_file_is_not_the_same_as_an_empty_one(tmp_path: Path) -> None:
    """A generated file that has never existed is drift on a fresh clone.

    `None` rather than `b""`, so the gate can tell "the generator has not run"
    from "the generator produced nothing".
    """
    empty = tmp_path / "empty.ts"
    empty.write_bytes(b"")

    assert tasks.content_of(tmp_path / "absent.ts") is None
    assert tasks.content_of(empty) == b""


# ---------------------------------------------------------------------------
# The output
# ---------------------------------------------------------------------------


def _writes_without_newline(source: str) -> list[str]:
    """Calls to `write_text` that do not pass `newline`, by the line they are on."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "write_text":
            continue
        if not any(keyword.arg == "newline" for keyword in node.keywords):
            found.append(f"line {node.lineno}")
    return found


@pytest.mark.parametrize("script", GENERATORS, ids=lambda path: path.name)
def test_every_generator_writes_lf_on_every_platform(script: Path) -> None:
    r"""`write_text` with no `newline` is `os.linesep`, which is CRLF on Windows.

    Five of these scripts passed `newline="\\n"` and four did not, so the same
    document had different bytes depending on who generated it — and one of the
    four writes a manifest that is then *signed*, where a byte difference is a
    verification failure with no explanation anywhere.
    """
    offenders = _writes_without_newline(script.read_text(encoding="utf-8"))

    assert not offenders, (
        f"{script.name} writes text without newline='\\n' at {offenders}. On Windows "
        "that emits CRLF, so what this generator produces depends on who ran it."
    )


def test_the_platform_actually_translates_so_this_matters(tmp_path: Path) -> None:
    r"""The premise, asserted rather than assumed.

    On Linux `os.linesep` is `\\n` and the whole class of bug is invisible, which
    is exactly why it survived: the pipeline runs on Linux and never saw it.
    This states the mechanism so a reader on Linux knows what the test above is
    protecting against.
    """
    target = tmp_path / "probe.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")

    expected = b"one\r\ntwo\r\n" if os.linesep == "\r\n" else b"one\ntwo\n"
    assert target.read_bytes() == expected


# ---------------------------------------------------------------------------
# The export, which could not run at all
# ---------------------------------------------------------------------------


def test_the_document_can_be_exported_with_nothing_configured() -> None:
    """`create_app` refuses without a way to authenticate a caller (RF-01).

    A good refusal, and it made `python tasks.py openapi` impossible to run
    anywhere — including CI stage 2.5 and the `clients-check` gate that calls
    it. Exporting the document serves no request, so there is no caller to
    authenticate.

    Run as a subprocess with the variable removed, because this test process
    may well have it set.
    """
    environment = {key: value for key, value in os.environ.items() if key != "DRAUPNIR_DEV"}
    environment["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        [sys.executable, "scripts/openapi_export.py", "--check"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_the_exported_document_does_not_depend_on_the_auth_posture(tmp_path: Path) -> None:
    """Which is what makes exporting in the development posture safe.

    If a future change makes the document depend on how a caller is
    authenticated, this fails — rather than the export quietly publishing one
    of two contracts and the diff gate comparing against whichever the last
    person happened to generate.
    """
    development = tmp_path / "development.json"
    federated = tmp_path / "federated.json"

    def export(target: Path, **settings: str) -> None:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("DRAUPNIR_DEV", "DRAUPNIR_OIDC"))
        }
        environment.update(settings)
        environment["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(  # noqa: S603
            [sys.executable, "scripts/openapi_export.py", "--output", str(target)],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    export(development, DRAUPNIR_DEV="1")
    export(
        federated,
        DRAUPNIR_OIDC_ISSUER="https://megingjord.example.invalid",
        DRAUPNIR_OIDC_AUDIENCE="draupnir-control-plane",
    )

    assert development.read_bytes() == federated.read_bytes()
