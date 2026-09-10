"""The secret scan, and what it does when it cannot run. RF-25.

`python tasks.py secrets` — and so `static` and `ci` — died on a documented
development platform with:

    docker: Error response from daemon: error while creating mount source path
    '/run/desktop/mnt/host/d/repos/veldris/draupnir': mkdir /run/desktop/mnt/host/d: file exists

which is a Docker Desktop drive-sharing setting rather than anything in this
repository. It reached the developer as `failed (125)` and a sentence about a
path inside a virtual machine, with nothing to act on.

The scan itself is the thing AC-Q3 asks for, and the one behaviour that must
never appear here is a pass. A secret scan that could not run has found nothing
in the way an empty room has found nothing, and a green stage saying so is
worse than a red one, because it is the shape of an assurance.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:  # pragma: no cover - `tasks` lives at the root
    sys.path.insert(0, str(REPO_ROOT))

import tasks  # noqa: E402 -- the repository root has to be on the path first

pytestmark = pytest.mark.unit

#: The failure exactly as Docker Desktop reported it, from the register.
MOUNT_FAILURE = (
    "docker: Error response from daemon: error while creating mount source path\n"
    "'/run/desktop/mnt/host/d/repos/veldris/draupnir': "
    "mkdir /run/desktop/mnt/host/d: file exists\n"
)


# ---------------------------------------------------------------------------
# The local binary is preferred
# ---------------------------------------------------------------------------


def test_a_local_binary_is_used_in_preference_to_the_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No container means no mount, which is the whole of the problem.

    Preferring the binary is also faster and is what the pipeline's own runner
    would do if one were installed there.
    """
    invoked: list[list[str]] = []

    def record(command: Sequence[str], **_named: object) -> int:
        invoked.append(list(command))
        return 0

    monkeypatch.setattr(
        tasks, "which", lambda name: "/usr/bin/gitleaks" if name == "gitleaks" else None
    )
    monkeypatch.setattr(tasks, "run", record)

    assert tasks.secrets() == 0
    assert invoked and invoked[0][0] == "/usr/bin/gitleaks"
    assert "detect" in invoked[0]


def test_both_paths_scan_for_the_same_things() -> None:
    """A fallback that scanned differently would be the failure nobody sees.

    It only runs on the machines without the binary, so a divergence would show
    up as one developer's clean scan and another's finding, on the same commit.
    """
    assert "--config" in tasks.GITLEAKS_ARGUMENTS
    assert ".gitleaks.toml" in tasks.GITLEAKS_ARGUMENTS
    assert "--redact" in tasks.GITLEAKS_ARGUMENTS, (
        "a scan that printed what it found would put the secret in the build log"
    )


def test_the_container_image_is_pinned() -> None:
    """A secret scan floating to `latest` changes what it detects, unasked."""
    assert ":" in tasks.GITLEAKS_IMAGE
    assert not tasks.GITLEAKS_IMAGE.endswith(":latest")


# ---------------------------------------------------------------------------
# What a mount failure produces
# ---------------------------------------------------------------------------


def test_a_mount_failure_is_recognised() -> None:
    assert tasks.mount_remedy(MOUNT_FAILURE) is not None


def test_the_advice_names_both_remedies() -> None:
    """Both, because which one is available depends on the machine.

    Sharing a drive is a setting somebody may not be able to change on a
    managed laptop; installing a binary may be equally awkward. Naming one
    would leave half the people who hit this with nothing.
    """
    advice = tasks.mount_remedy(MOUNT_FAILURE) or ""

    assert "File sharing" in advice, "the Docker Desktop setting is not named"
    assert "install gitleaks" in advice, "installing the binary is not offered"
    assert "bootstrap" in advice, "the task that does it for you is not named"


def test_the_advice_says_this_is_not_a_finding() -> None:
    """The reader's first thought is that the scan found a secret.

    It did not; it could not start. Saying so first is the difference between
    a developer checking a setting and a developer searching their own diff.
    """
    advice = tasks.mount_remedy(MOUNT_FAILURE) or ""

    assert "could not mount" in advice
    assert "rather than anything in this" in advice


def test_a_real_finding_is_not_mistaken_for_a_mount_problem() -> None:
    """The rule that keeps the diagnosis honest.

    Advice about drive sharing shown for a leaked key would be the worst
    possible outcome here: the one failure this stage exists to surface,
    explained away as a configuration problem.
    """
    assert tasks.mount_remedy("Finding: aws-access-key in deploy/install.sh") is None
    assert tasks.mount_remedy("") is None


# ---------------------------------------------------------------------------
# And never a pass
# ---------------------------------------------------------------------------


def test_no_scanner_at_all_is_a_failure_rather_than_a_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither the binary nor docker. AC-Q3 asks for a scan.

    A skip here would be a stage that reports success having done nothing, on
    exactly the machines least likely to be watched.
    """
    monkeypatch.setattr(tasks, "which", lambda _name: None)

    with pytest.raises(tasks.Failure) as refused:
        tasks.secrets()

    assert "has not found anything" in str(refused.value)


def test_the_advice_says_a_failed_scan_is_not_a_clean_one() -> None:
    advice = tasks.mount_remedy(MOUNT_FAILURE) or ""

    assert "must not do is pass" in advice
