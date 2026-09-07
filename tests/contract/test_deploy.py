"""The deployment scripts agree with each other and with the pipeline.

Three scripts commission, roll out and roll back the same three units, and a
fourth thing -- the pipeline -- decides which images exist. Nothing made them
agree, and they did not: `rollout.sh` and `rollback.sh` both resolved
`draupnir-worker` to an image of that name while stage 3.1 builds `api` and
`web` only, so a rollout would have failed on the pull, on the estate, during
a release.

These are file-level assertions rather than a deployment: ALVISS is not a test
runner and Podman is not installed here. What they check is the thing that was
actually wrong -- four copies of a fact, one of them false.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
UNITS_DIR = DEPLOY / "units"
CI = ROOT / ".github" / "workflows" / "ci.yaml"

#: The scripts that must not keep their own copy of the unit list.
SCRIPTS = ("install.sh", "rollout.sh", "rollback.sh")


def _lib() -> str:
    return (DEPLOY / "lib.sh").read_text(encoding="utf-8")


BASH = shutil.which("bash")

#: Reading shell with a regular expression tests the reading, not the shell.
#: These call `lib.sh` and use what it answers.
requires_bash = pytest.mark.skipif(BASH is None, reason="bash is not on PATH")


def ask_lib(snippet: str) -> str:
    """Source lib.sh and run one line of shell against it."""
    assert BASH is not None
    result = subprocess.run(  # noqa: S603
        [BASH, "-c", f'set -euo pipefail; source "{(DEPLOY / "lib.sh").as_posix()}"; {snippet}'],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"lib.sh failed: {result.stderr.strip()}"
    return result.stdout.strip()


def units() -> tuple[str, ...]:
    """The units, read from the one place that declares them."""
    match = re.search(r"DRAUPNIR_UNITS=\(([^)]*)\)", _lib())
    assert match, "lib.sh no longer declares DRAUPNIR_UNITS"
    return tuple(re.findall(r'"([^"]+)"', match.group(1)))


def images_the_pipeline_builds() -> set[str]:
    """The image names stage 3.1 actually builds, read from the workflow."""
    workflow = CI.read_text(encoding="utf-8")
    match = re.search(r"for target in ([a-z0-9 _-]+); do", workflow)
    assert match, "ci.yaml no longer builds images in a `for target in ...` loop"
    return {f"draupnir-{target}" for target in match.group(1).split()}


def test_every_unit_has_a_template() -> None:
    for unit in units():
        template = UNITS_DIR / f"{unit}.service.in"
        assert template.is_file(), f"{unit} has no unit template at {template}"


@requires_bash
def test_every_unit_runs_an_image_the_pipeline_builds() -> None:
    """The failure that prompted this file.

    Every image `lib.sh` can resolve has to be one the pipeline publishes, or
    the rollout pulls something that was never built and the release stops
    with the estate half deployed.
    """
    built = images_the_pipeline_builds()

    for unit in units():
        reference = ask_lib(f"draupnir_image_for {unit} testrev registry.invalid")
        assert reference.startswith("registry.invalid/"), reference
        component = reference.removeprefix("registry.invalid/").rsplit(":", 1)[0]
        assert component in built, (
            f"{unit} resolves to image {component!r}, which stage 3.1 does not build. "
            f"It builds {sorted(built)}."
        )


@requires_bash
def test_an_unknown_unit_is_refused_rather_than_guessed() -> None:
    """A typo must not resolve to a plausible image reference."""
    assert BASH is not None
    result = subprocess.run(  # noqa: S603
        [
            BASH,
            "-c",
            f'source "{(DEPLOY / "lib.sh").as_posix()}"; draupnir_image_for draupnir-nope rev',
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0, "an unknown unit resolved to an image"
    assert "unknown unit" in result.stderr


def test_no_script_keeps_its_own_copy_of_the_unit_list() -> None:
    """Four copies of a fact is how one of them becomes false."""
    for name in SCRIPTS:
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "lib.sh" in text, f"{name} does not source lib.sh"
        assert not re.search(r'UNITS=\(\s*"draupnir-', text), (
            f"{name} declares its own unit list instead of using DRAUPNIR_UNITS"
        )


@requires_bash
def test_the_image_variable_is_spelled_the_same_everywhere() -> None:
    """rollout.sh sets it and draupnir-run.sh reads it. One spelling, or neither works.

    Derived both ways and compared, rather than matched as a string: the two
    derivations agreeing is the property, and a string check passes happily
    while they disagree.
    """
    wrapper = (UNITS_DIR / "draupnir-run.sh").read_text(encoding="utf-8")
    assert 'image_var="DRAUPNIR_IMAGE_${UNIT//-/_}"' in wrapper, (
        "the wrapper no longer derives the variable name the way lib.sh writes it"
    )
    for unit in units():
        from_lib = ask_lib(f"draupnir_image_var {unit}")
        from_wrapper = f"DRAUPNIR_IMAGE_{unit.replace('-', '_')}"
        assert from_lib == from_wrapper, (
            f"{unit}: lib.sh sets {from_lib}, the wrapper reads {from_wrapper}"
        )


@pytest.mark.parametrize("unit", units())
def test_units_are_rootless_and_start_through_the_wrapper(unit: str) -> None:
    """AC-Q7: rootless. A unit that names a user or a root path is not."""
    template = (UNITS_DIR / f"{unit}.service.in").read_text(encoding="utf-8")
    assert f"ExecStart=@LIBEXEC@/draupnir-run.sh {unit}\n" in template
    assert "User=" not in template, f"{unit} sets User=, which a user unit may not"
    assert "WantedBy=default.target" in template, (
        f"{unit} is not wanted by default.target, so it will not start with the user manager"
    )


@pytest.mark.parametrize("unit", units())
def test_units_carry_no_credential(unit: str) -> None:
    """SAD 11.1 step 5: secrets are brokered, never in configuration."""
    template = (UNITS_DIR / f"{unit}.service.in").read_text(encoding="utf-8")
    for smell in ("PASSWORD", "SECRET", "ACCESS_KEY", "TOKEN"):
        assert smell not in template.upper(), f"{unit} names {smell} in a unit file"


def test_the_installer_writes_no_credential() -> None:
    """The generated configuration is the non-secret half, by construction."""
    installer = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    # The heredoc it writes to draupnir.env.
    match = re.search(r"rendered=\"\$\(cat <<EOF\n(.*?)\nEOF", installer, re.DOTALL)
    assert match, "install.sh no longer renders draupnir.env from a heredoc"
    rendered = match.group(1)
    for smell in ("PASSWORD", "SECRET_KEY", "ACCESS_KEY"):
        assert smell not in rendered.upper(), (
            f"install.sh writes {smell} into draupnir.env; it belongs in secrets.env"
        )
