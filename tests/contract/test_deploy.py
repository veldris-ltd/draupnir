"""The deployment scripts agree with each other and with the pipeline.

Three scripts commission, roll out and roll back the same three units, and a
fourth thing -- the pipeline -- decides which images exist. Nothing made them
agree, and they did not: `rollout.sh` and `rollback.sh` both resolved
`draupnir-worker` to an image of that name while stage 3.1 builds `api` and
`web` only, so a rollout would have failed on the pull, on the estate, during
a release.

ALVISS is a Mac mini M4 Pro (VLD-INF-SINDRI-001 Rev 3.3 section 6), so there
are two service managers and therefore two units per unit: a systemd user unit
and a launchd user agent. Everything that has to agree between them is checked
here, because nothing else can: a launchd agent cannot be started on a Linux
runner and a systemd unit cannot be started on a Mac.

These are file-level assertions rather than a deployment: ALVISS is not a test
runner and Podman is not installed here. What they check is the thing that was
actually wrong -- four copies of a fact, one of them false.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

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


def ask_lib(snippet: str, *, platform: str | None = None) -> str:
    """Source lib.sh and run one line of shell against it.

    `platform` sets DRAUPNIR_PLATFORM, which is how the manager this runner is
    not running is exercised at all. Without it the estate's two halves would
    only ever be checked one at a time, on whichever machine ran the build.
    """
    assert BASH is not None
    environment = {"DRAUPNIR_PLATFORM": platform} if platform else {}
    result = subprocess.run(  # noqa: S603
        [BASH, "-c", f'set -euo pipefail; source "{(DEPLOY / "lib.sh").as_posix()}"; {snippet}'],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, **environment},
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


@pytest.mark.parametrize("suffix", ["service", "plist"])
def test_every_unit_has_a_template_for_both_managers(suffix: str) -> None:
    """A unit with one template is a unit that deploys on one platform.

    ALVISS runs macOS and the pipeline runs Linux, so a missing plist is not a
    gap in an unused code path: it is the host in the specification.
    """
    for unit in units():
        template = UNITS_DIR / f"{unit}.{suffix}.in"
        assert template.is_file(), f"{unit} has no {suffix} template at {template}"


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


# ---------------------------------------------------------------------------
# The launchd half. RF-E01: ALVISS is a Mac mini M4 Pro and has no systemd.
# ---------------------------------------------------------------------------

#: The substitutions install.sh makes when it renders a template. Values that
#: are recognisable, so an assertion failure names which one did not land.
PLACEHOLDERS = {
    "@LIBEXEC@": "/home/svc/.local/libexec/draupnir",
    "@PODMAN@": "/opt/homebrew/bin/podman",
    "@SITE_ID@": "sindri",
    "@LOGDIR@": "/home/svc/Library/Logs/draupnir",
}


def render_plist(unit: str) -> dict[str, Any]:
    """Render one launchd template the way install.sh does, and parse it.

    Parsed rather than pattern matched: a plist that is well formed XML and not
    a valid property list loads on nobody's Mac, and a regular expression over
    it would report that as a pass.
    """
    text = (UNITS_DIR / f"{unit}.plist.in").read_text(encoding="utf-8")
    for placeholder, value in PLACEHOLDERS.items():
        text = text.replace(placeholder, value)
    text = text.replace("@LABEL@", ask_lib(f"draupnir_launchd_label {unit}"))

    left = re.findall(r"@[A-Z]+@", text)
    assert not left, f"{unit}.plist.in still holds unsubstituted placeholders: {left}"

    loaded = plistlib.loads(text.encode("utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@requires_bash
@pytest.mark.parametrize("unit", units())
def test_the_launchd_agent_is_a_valid_property_list(unit: str) -> None:
    plist = render_plist(unit)
    assert plist["Label"] == f"com.veldris.{unit.replace('-', '.')}"


@requires_bash
@pytest.mark.parametrize("unit", units())
def test_both_managers_start_the_same_wrapper_with_the_same_argument(unit: str) -> None:
    """The one thing that must not differ between the two units.

    Everything AC-Q7 asserts -- rootless, read only root, dropped capabilities,
    no new privileges -- is a property of what `draupnir-run.sh` invokes. Two
    units that started different things would mean the Linux build proved
    nothing about the Mac the control plane actually runs on.
    """
    service = (UNITS_DIR / f"{unit}.service.in").read_text(encoding="utf-8")
    plist = render_plist(unit)

    assert f"ExecStart=@LIBEXEC@/draupnir-run.sh {unit}\n" in service
    assert plist["ProgramArguments"] == [
        f"{PLACEHOLDERS['@LIBEXEC@']}/draupnir-run.sh",
        unit,
    ], f"{unit}: the launchd agent does not start what the systemd unit starts"


@requires_bash
@pytest.mark.parametrize("unit", units())
def test_the_launchd_agent_reproduces_the_systemd_restart_policy(unit: str) -> None:
    """`Restart=on-failure`, `RestartSec=5s` and `TimeoutStopSec=30s`, in launchd.

    Read out of the systemd unit rather than written down twice, so changing
    one and forgetting the other fails here.
    """
    service = (UNITS_DIR / f"{unit}.service.in").read_text(encoding="utf-8")
    plist = render_plist(unit)

    assert "Restart=on-failure" in service
    assert plist["KeepAlive"] == {"SuccessfulExit": False}, (
        f"{unit}: KeepAlive must restart on failure only; a clean exit is a stop somebody asked for"
    )

    restart = re.search(r"RestartSec=(\d+)s", service)
    assert restart is not None
    assert plist["ThrottleInterval"] == int(restart.group(1))

    stop = re.search(r"TimeoutStopSec=(\d+)s", service)
    assert stop is not None
    assert plist["ExitTimeOut"] == int(stop.group(1))

    assert "WantedBy=default.target" in service
    assert plist["RunAtLoad"] is True, f"{unit}: the agent does not start with the user session"


@requires_bash
@pytest.mark.parametrize("unit", units())
def test_the_launchd_agent_is_given_podman_by_absolute_path(unit: str) -> None:
    """Launchd's PATH does not include Homebrew, which is where podman is.

    An agent that could not find podman would fail exactly like one whose image
    was missing, and the two need very different remedies.
    """
    plist = render_plist(unit)
    podman = plist["EnvironmentVariables"]["DRAUPNIR_PODMAN"]
    assert podman.startswith("/"), f"{unit}: DRAUPNIR_PODMAN is not an absolute path"
    assert podman == PLACEHOLDERS["@PODMAN@"]


@requires_bash
@pytest.mark.parametrize("unit", units())
def test_the_launchd_agent_writes_somewhere_an_operator_is_told_about(unit: str) -> None:
    """Launchd has no journal, so the log path is part of the unit.

    Checked against what `draupnir_service_logs_hint` tells an operator, so the
    runbook cannot send somebody to a file nothing writes.
    """
    plist = render_plist(unit)
    expected = f"{PLACEHOLDERS['@LOGDIR@']}/{unit}.log"
    assert plist["StandardOutPath"] == expected
    assert plist["StandardErrorPath"] == expected

    hint = ask_lib(f"HOME=/home/svc draupnir_service_logs_hint {unit}", platform="darwin")
    assert expected in hint, f"{unit}: the logs hint points somewhere the agent does not write"


@requires_bash
@pytest.mark.parametrize("unit", units())
def test_launchd_agents_carry_no_credential(unit: str) -> None:
    """SAD 11.1 step 5, for the manager the systemd assertion does not cover."""
    template = (UNITS_DIR / f"{unit}.plist.in").read_text(encoding="utf-8")
    for smell in ("PASSWORD", "SECRET", "ACCESS_KEY", "TOKEN"):
        assert smell not in template.upper(), f"{unit} names {smell} in a launchd agent"


@requires_bash
@pytest.mark.parametrize("unit", units())
def test_the_installed_file_name_matches_the_manager(unit: str) -> None:
    """install.sh writes it, uninstall removes it, launchctl bootstraps it.

    Three callers, one derivation, or the uninstall leaves an agent behind that
    keeps restarting a container nobody expects.
    """
    assert ask_lib(f"draupnir_unit_filename {unit}", platform="linux") == f"{unit}.service"

    label = ask_lib(f"draupnir_launchd_label {unit}", platform="darwin")
    assert ask_lib(f"draupnir_unit_filename {unit}", platform="darwin") == f"{label}.plist"


@requires_bash
def test_an_unsupported_platform_is_refused_rather_than_assumed_linux() -> None:
    """A third platform taking the systemd path would install units nothing reads."""
    assert BASH is not None
    result = subprocess.run(  # noqa: S603
        [BASH, "-c", f'source "{(DEPLOY / "lib.sh").as_posix()}"; draupnir_platform'],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, "DRAUPNIR_PLATFORM": "", "PATH": "/nonexistent"},
    )
    assert result.returncode != 0, "an unknown platform resolved to a service manager"
    assert "unsupported platform" in result.stderr


def test_no_script_names_a_service_manager_except_lib_and_the_preflight() -> None:
    """The point of the five verbs.

    rollout.sh and rollback.sh run unchanged on both platforms. install.sh may
    name a manager only where it checks that one is there, because the two
    preflights genuinely differ; everywhere else it goes through lib.sh.
    """
    for name in ("rollout.sh", "rollback.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        for manager in ("systemctl", "launchctl"):
            assert manager not in text, (
                f"{name} names {manager} directly; use the draupnir_service_* verbs "
                "so the script runs on both platforms"
            )

    installer = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    for manager, function in (
        ("systemctl", "preflight_systemd"),
        ("launchctl", "preflight_launchd"),
    ):
        body = installer.split(function + "() {", 1)[1].split("\n}\n", 1)[0]
        assert manager not in installer.replace(body, ""), (
            f"install.sh names {manager} outside {function}; only the preflight may, "
            "because only the preflight is asking whether the manager exists"
        )


def test_the_wrapper_clears_a_stale_container_name() -> None:
    """It moved out of ExecStartPre, so something has to assert it landed.

    launchd has no pre start hook. If this were dropped rather than moved, a
    unit killed hard would fail every subsequent start on a name clash, and the
    error would name the clash rather than the kill.
    """
    wrapper = (UNITS_DIR / "draupnir-run.sh").read_text(encoding="utf-8")
    assert '"${PODMAN}" rm --ignore --force "${UNIT}"' in wrapper

    for unit in units():
        service = (UNITS_DIR / f"{unit}.service.in").read_text(encoding="utf-8")
        directives = [line for line in service.splitlines() if line.startswith("ExecStartPre=")]
        assert not directives, (
            f"{unit}.service.in still has an ExecStartPre directive; the clean is in the "
            "wrapper now, and two copies is how one of them becomes false"
        )


def test_the_wrapper_starts_the_podman_machine_before_giving_up() -> None:
    """A Mac back from a power cut has no machine running.

    Without this the agent fails, launchd throttles it, and the control plane
    stays down until somebody logs in and looks, which is the failure the
    image reference fallback beside it was written to avoid.
    """
    wrapper = (UNITS_DIR / "draupnir-run.sh").read_text(encoding="utf-8")
    assert '"$(draupnir_platform)" == "darwin"' in wrapper, (
        "the wrapper has no Darwin branch, or it tests the platform itself rather "
        "than asking lib.sh, which is the copy that drifts"
    )
    assert "machine start" in wrapper
    assert "machine init" in wrapper, (
        "the wrapper does not say what to do when there is no machine at all"
    )


def test_the_installer_preflights_the_podman_machine_and_the_gui_domain() -> None:
    """Two macOS failures that read as a broken install and are not.

    No machine means every podman command fails obscurely. No gui domain means
    `launchctl bootstrap` refuses, which is what happens over an ssh session
    with no console, the way an engineer would most naturally connect.
    """
    installer = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    body = installer.split("preflight_launchd() {", 1)[1].split("\n}\n", 1)[0]
    assert "machine start" in body, "the preflight does not check for a running podman machine"
    assert 'launchctl print "gui/$(id -u)"' in body, "the preflight does not check the gui domain"
    assert "fdesetup" in body, (
        "the preflight does not mention FileVault, which decides whether this host "
        "returns unattended after a power cut"
    )


# ---------------------------------------------------------------------------
# Names. RF-E02: every default was one label short of the zone REGIN serves.
# ---------------------------------------------------------------------------

#: Files that resolve a hostname at run time. `README.md` is prose and may name
#: Sindri; these may not name anything.
NAME_BEARING = ("install.sh", "lib.sh", "rollout.sh", "rollback.sh", "units/draupnir-run.sh")


def code_of(path: str) -> str:
    """One deploy file with its comments and its usage text removed.

    A comment explaining why `andvari.veldris.internal` was wrong has to be
    allowed to say `andvari.veldris.internal`. What must not survive is a line
    that resolves one.
    """
    text = (DEPLOY / path).read_text(encoding="utf-8")
    text = re.sub(r"<<'USAGE'.*?^USAGE$", "", text, flags=re.DOTALL | re.MULTILINE)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


@pytest.mark.parametrize("path", NAME_BEARING)
def test_no_script_spells_a_hostname(path: str) -> None:
    """Five files had a default hostname and all five were wrong the same way.

    VLD-INF-SINDRI-001 Rev 3.3 section 10.4: dnsmasq on REGIN is authoritative
    for `sindri.veldris.internal` and nothing wider, so `andvari.veldris
    .internal` reached the site router and was denied by the egress policy. The
    installer's own default could not resolve on the host it installs on.
    """
    code = code_of(path)
    hosts = re.findall(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.veldris\.internal", code)

    # MEGINGJORD is the exception, and it is the same exception `egress.py`
    # makes for the same reason: SAD 11A makes it one registry for the whole
    # Forge Matrix, and VLD-INF-SINDRI-001 puts it on Veldris_NXT rather than
    # at a forge. `megingjord.sindri.veldris.internal` would name a thing that
    # should not exist, so deriving it through `draupnir_host_for` would be
    # wrong rather than merely unnecessary.
    hosts = [item for item in hosts if not item.startswith("megingjord.")]

    if path == "lib.sh":
        # The one derivation, and it is a suffix rather than a hostname.
        assert code.count("veldris.internal") == 1, (
            "lib.sh should hold exactly one veldris.internal, in draupnir_site_domain"
        )
        assert "${site}.veldris.internal" in code
        return

    assert not hosts, (
        f"{path} spells the hostname(s) {sorted(set(hosts))}. Derive them with "
        "draupnir_host_for so a second forge needs no edit and the first one resolves."
    )


@requires_bash
def test_every_default_name_sits_in_the_site_zone() -> None:
    """The names the installer would actually use, asked of lib.sh."""
    for role in ("andvari", "registry"):
        assert ask_lib(f"draupnir_host_for {role}") == f"{role}.sindri.veldris.internal", (
            f"{role} does not resolve into the zone REGIN serves"
        )


@requires_bash
def test_a_second_forge_needs_one_variable_and_no_edit() -> None:
    """The Forge Matrix has more than one site and they are not the same site."""
    assert (
        ask_lib("DRAUPNIR_SITE_ID=bergelmir draupnir_host_for andvari")
        == "andvari.bergelmir.veldris.internal"
    )
    assert (
        ask_lib("DRAUPNIR_SITE_DOMAIN=forge.example.test draupnir_host_for andvari")
        == "andvari.forge.example.test"
    ), "DRAUPNIR_SITE_DOMAIN does not override the derivation"


@requires_bash
def test_the_image_reference_is_site_scoped() -> None:
    """A partitioned forge still has to be able to roll out (Decision S8).

    It cannot do that against a registry on the other side of the partition, so
    the registry is a name at the forge rather than one for the estate.
    """
    reference = ask_lib("draupnir_image_for draupnir-api abc123")
    assert reference == "registry.sindri.veldris.internal/draupnir-api:abc123"


@requires_bash
def test_the_installer_derives_its_names_after_reading_the_arguments() -> None:
    """`--site` changes the zone, so anything derived before it is wrong.

    Run rather than read: the ordering bug this guards against is invisible in
    the text, because both orderings look identical on the page.
    """
    assert BASH is not None
    result = subprocess.run(  # noqa: S603
        [BASH, str(DEPLOY / "install.sh"), "--site", "bergelmir", "--check"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "DRAUPNIR_PLATFORM": "linux", "DRAUPNIR_PODMAN": "no-such-podman"},
    )
    assert "site: bergelmir (bergelmir.veldris.internal)" in result.stdout, (
        "--site did not reach the derived names:\n" + result.stdout + result.stderr
    )


# ---------------------------------------------------------------------------
# "Does not resolve" and "does not answer" are different failures.
# ---------------------------------------------------------------------------


def resolver_stub(directory: Path, *, knows: str) -> Path:
    """A fake `getent` that resolves one name and nothing else."""
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / "getent"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'[[ "$2" == "{knows}" ]] && {{ echo "10.20.0.21 $2"; exit 0; }}\n'
        "exit 2\n",
        encoding="utf-8",
        newline="\n",
    )
    stub.chmod(0o755)
    return directory


@requires_bash
def test_resolves_tells_the_three_answers_apart(tmp_path: Path) -> None:
    """0 resolves, 1 does not, 2 could not tell. Called, not read.

    `install.sh` runs `main` only when executed, so a test can source it and
    ask one function a question. Before that this could only be pattern
    matched, which tests the pattern.
    """
    assert BASH is not None

    def ask(name: str, *, path: str) -> int:
        result = subprocess.run(  # noqa: S603
            [
                BASH,
                "-c",
                f'set -uo pipefail; source "{(DEPLOY / "install.sh").as_posix()}"; '
                f'resolves "{name}"',
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={**os.environ, "DRAUPNIR_PLATFORM": "linux", "PATH": path},
        )
        return result.returncode

    with_stub = f"{resolver_stub(tmp_path / 'bin', knows='andvari.sindri.veldris.internal')}"
    bash_dir = str(Path(BASH).parent)
    path = f"{with_stub}{os.pathsep}{bash_dir}"

    assert ask("andvari.sindri.veldris.internal", path=path) == 0, (
        "a name that resolves reported otherwise"
    )
    assert ask("andvari.veldris.internal", path=path) == 1, (
        "a name outside the site zone was not reported as unresolvable"
    )
    assert ask("10.20.0.21", path=path) == 0, (
        "a literal address needs no resolver; asking one about it is how a host "
        "configured by address gets reported as broken"
    )
    assert ask("andvari.sindri.veldris.internal", path=bash_dir) == 2, (
        "with no resolver on PATH this must say it cannot tell, not guess"
    )


def test_the_dependency_check_reports_the_two_failures_differently() -> None:
    """Same symptom, different phone call.

    A name that does not resolve is nearly always the wrong zone, which the
    operator fixes in a second. A name that resolves and does not answer is a
    machine that is down, which is somebody else's job. Reporting both as
    "no answer" sent an operator to the infrastructure team for a typo.
    """
    installer = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    body = installer.split("check_dependencies() {", 1)[1].split("\n}\n", 1)[0]

    assert "does not resolve" in body
    assert "resolves, no answer" in body
    assert "${SITE_DOMAIN}" in body, (
        "the unresolvable message does not name the site domain, which is the fix"
    )
    assert "--skip-dependency-check" in body


# ---------------------------------------------------------------------------
# The vault. RF-E04: mounted on the appliances, not on the host that measures it.
# ---------------------------------------------------------------------------


def vault_at(directory: Path, *, site: str = "sindri") -> Path:
    """A directory that is a vault, marker and all."""
    from draupnir.hodd import reconcile
    from draupnir.hodd.stores import PosixStoreDriver

    directory.mkdir(parents=True, exist_ok=True)
    reconcile.initialise(PosixStoreDriver(root=directory, local_site=site))
    return directory


def test_a_vault_that_is_there_is_ok(tmp_path: Path) -> None:
    from scripts.preflight import check_vault

    result = check_vault(str(vault_at(tmp_path / "vault")))
    assert result.verdict == "ok", result
    assert "free" in result.detail, "the capacity figure SAD 11.3 alarms on is not reported"


def test_no_vault_configured_is_unverified_rather_than_a_failure(tmp_path: Path) -> None:
    """A forge without a vault is a legitimate configuration.

    `config.py` treats an unset root as "this installation has none" and skips
    the checks, which is right. What was wrong is that it skipped them
    silently, so a forge that *has* a vault and has not mounted it looked
    identical to one that has none.
    """
    from scripts.preflight import check_vault

    del tmp_path
    result = check_vault("")
    assert result.verdict == "unverified", result
    assert not result.failed
    assert "11.3" in result.detail, "the reason does not say what signal is lost"


def test_nothing_mounted_is_unreachable(tmp_path: Path) -> None:
    from scripts.preflight import check_vault

    result = check_vault(str(tmp_path / "never-mounted"))
    assert result.verdict == "unreachable", result
    assert result.failed


def test_a_directory_where_the_vault_should_be_is_missing_not_unreachable(tmp_path: Path) -> None:
    """The dangerous middle state, and the reason the marker exists.

    A bare directory on the mount point is worse than no directory: the store
    creates an artefact's parent directories on write, so a run would stage its
    weights onto the control plane's own disk and the real mount returning
    later would hide it. `reconcile.mounted()` answers False for both, which is
    why this asks `require_vault`, which raises a different type for each.
    """
    from scripts.preflight import check_vault

    bare = tmp_path / "mount-point"
    bare.mkdir()
    result = check_vault(str(bare))
    assert result.verdict == "missing", result
    assert ".hodd-vault" in result.detail
    assert "do not initialise" in result.remedy, (
        "the remedy does not warn against initialising over a dropped mount"
    )


def test_the_installer_configures_a_vault_root() -> None:
    """The worker measures the vault; it needs to be told where one is.

    VLD-INF-SINDRI-001 Procedure S9 mounts it on the three appliances only, so
    without this the SAD 11.3 capacity alarm has no source on the host that
    raises it.
    """
    installer = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert 'VAULT_ROOT="${DRAUPNIR_VAULT_ROOT-/forge/vault}"' in installer, (
        "install.sh does not default the vault root to the path the manual mounts"
    )
    body = installer.split('rendered="$(cat <<EOF', 1)[1].split("\nEOF", 1)[0]
    assert "DRAUPNIR_VAULT_ROOT=${VAULT_ROOT}" in body, (
        "the vault root is not written into draupnir.env, so the container never sees it"
    )


def test_the_vault_root_can_be_emptied_for_a_forge_without_one() -> None:
    """`:-` would treat an explicit empty string as unset. `-` does not.

    A forge with no vault sets DRAUPNIR_VAULT_ROOT='' and must get an empty
    value through, not the default.
    """
    installer = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "${DRAUPNIR_VAULT_ROOT-/forge/vault}" in installer
    assert "${DRAUPNIR_VAULT_ROOT:-/forge/vault}" not in installer, (
        "`:-` makes an explicitly empty vault root fall back to the default, so a forge "
        "with no vault cannot say so"
    )


@requires_bash
@pytest.mark.parametrize("unit", units())
def test_only_the_worker_is_given_the_vault(unit: str, tmp_path: Path) -> None:
    """The API and the console have no business holding a handle on the export.

    Driven rather than read: the wrapper resolves the root from draupnir.env,
    and whether that resolution reaches the podman arguments is the property.
    """
    assert BASH is not None
    config = tmp_path / "config"
    config.mkdir()
    vault = vault_at(tmp_path / "vault")
    (config / "draupnir.env").write_text(
        f"DRAUPNIR_VAULT_ROOT={vault}\n", encoding="utf-8", newline="\n"
    )

    result = subprocess.run(  # noqa: S603
        [BASH, str(UNITS_DIR / "draupnir-run.sh"), unit],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            **os.environ,
            "DRAUPNIR_PLATFORM": "linux",
            "DRAUPNIR_CONFIG_DIR": str(config),
            "DRAUPNIR_STATE_DIR": str(tmp_path / "state"),
            "DRAUPNIR_PODMAN": "echo",
            f"DRAUPNIR_IMAGE_{unit.replace('-', '_')}": "registry.invalid/image:test",
        },
    )
    mounted = f"--volume {vault}:{vault}:ro" in result.stdout

    if unit == "draupnir-worker":
        assert mounted, f"the worker was not given the vault:\n{result.stdout}"
    else:
        assert not mounted, f"{unit} was given the vault and does not read it"


@requires_bash
def test_the_worker_starts_without_a_vault_and_says_so(tmp_path: Path) -> None:
    """SAD 11.2 row 4 makes a missing vault a degraded mode, not a stop.

    A worker that refused to start would take the run board down with the NFS
    export, which is the opposite of what the degraded mode asks for.
    """
    assert BASH is not None
    config = tmp_path / "config"
    config.mkdir()
    (config / "draupnir.env").write_text(
        f"DRAUPNIR_VAULT_ROOT={tmp_path / 'not-mounted'}\n", encoding="utf-8", newline="\n"
    )

    result = subprocess.run(  # noqa: S603
        [BASH, str(UNITS_DIR / "draupnir-run.sh"), "draupnir-worker"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            **os.environ,
            "DRAUPNIR_PLATFORM": "linux",
            "DRAUPNIR_CONFIG_DIR": str(config),
            "DRAUPNIR_STATE_DIR": str(tmp_path / "state"),
            "DRAUPNIR_PODMAN": "echo",
            "DRAUPNIR_IMAGE_draupnir_worker": "registry.invalid/image:test",
        },
    )
    assert result.returncode == 0, "a missing vault stopped the worker starting"
    assert "is not mounted; starting without it" in result.stderr
    assert "--volume" not in result.stdout


def test_nothing_names_a_vault_path_the_estate_does_not_have() -> None:
    """`/mnt/hodd` was this repository's invention and exists on no host.

    VLD-INF-SINDRI-001 Procedure S9 mounts ANDVARI's export at /forge/vault. An
    operator following the runbook's section 4 was being sent to a path that
    would never be there, during the incident that section is written for.
    """
    for path in (
        ROOT / "docs" / "runbook.md",
        ROOT / "docs" / "DEPLOYMENT.md",
        ROOT / "scripts" / "vault_admin.py",
    ):
        text = path.read_text(encoding="utf-8")
        offenders = [
            line
            for line in text.splitlines()
            if "/mnt/hodd" in line and not line.lstrip().startswith("#")
        ]
        assert not offenders, f"{path.name} still sends an operator to /mnt/hodd: {offenders}"


# ---------------------------------------------------------------------------
# CON-A. RF-E14: the console that was built and shipped nowhere.
# ---------------------------------------------------------------------------


def test_the_local_view_is_built_by_the_pipeline() -> None:
    """S30 was tested thoroughly and delivered by nothing.

    Not a workspace member, not in an image, not in deploy/, not in the
    pipeline. There was no artefact for anyone to install on DVALIN, which is
    why VLD-INF-SINDRI-001 Procedure S12 step 10 puts an `xterm` running
    `watch nvidia-smi` on the panel instead — the alternative could not be
    installed, whatever the repository said about it.
    """
    workflow = CI.read_text(encoding="utf-8")
    assert "python tasks.py con-a" in workflow, (
        "the pipeline does not build CON-A, so there is nothing to install on DVALIN"
    )
    assert "            dist/\n" in workflow, (
        "CON-A is built and not uploaded, so the artefact exists only inside the runner"
    )

    tasks = (ROOT / "tasks.py").read_text(encoding="utf-8")
    assert '@task("con-a"' in tasks


def test_the_local_view_depends_on_nothing() -> None:
    """Decision U2, and the reason CON-A is a wheel rather than a container.

    It is the only console that survives a total network failure, and its
    entire value is depending on nothing beyond the appliance it is attached
    to. A container runtime is a dependency, and one that would have to
    survive the same failure.
    """
    import tomllib

    manifest = tomllib.loads(
        (ROOT / "tools" / "stedi-view" / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert manifest["project"]["dependencies"] == [], (
        "CON-A has acquired a dependency, which is the thing it exists not to have"
    )


def test_the_installer_configures_the_collector() -> None:
    """CON-B's thermal panel needs an address to read. RF-E15.

    Prometheus on REGIN is the estate's store (VLD-INF-SINDRI-001 section 34
    step 7). Without this the control plane has no source for the thermal and
    throttle signals of SAD 11.3 at all, and the panel that says "Appliances"
    reports run state instead -- so an appliance that is idle *because* it is
    thermally throttled reads `idle`, in grey.
    """
    installer = (DEPLOY / "install.sh").read_text(encoding="utf-8")

    assert 'PROMETHEUS_URL="${DRAUPNIR_PROMETHEUS_URL-}"' in installer, (
        "install.sh does not read a collector address from the environment"
    )
    assert '${PROMETHEUS_URL:=http://$(draupnir_host_for regin "${SITE_ID}"):9090}' in installer, (
        "the collector is not derived from the site's own zone, so a second forge "
        "would read the first forge's Prometheus"
    )

    body = installer.split('rendered="$(cat <<EOF', 1)[1].split("\nEOF", 1)[0]
    assert "DRAUPNIR_PROMETHEUS_URL=${PROMETHEUS_URL}" in body, (
        "the collector address is not written into draupnir.env, so the container "
        "never sees it and every reading is unmeasured"
    )


def test_each_service_on_regin_is_its_own_destination_and_approval() -> None:
    """Three services on REGIN, three approvals. RF-E15, and RF-18 for the third.

    The broker checks the approving policy on every call, so one allow-list
    entry covering several would let a console holding the telemetry approval
    cancel a job, or a driver holding the scheduling one post traces. They
    differ only by port, which is why `Destination` has one.

    The third is the OpenTelemetry collector. It is declared before anything is
    deployed on that port, and the entry says so in its `gap`: wiring a
    collector should be a configuration change rather than a change to the
    allow list, because a permission granted in the same commit as the first
    call that needs it is a permission nobody reviewed.
    """
    from draupnir.svalinn.egress import ALLOW_LIST

    regin = [item for item in ALLOW_LIST if item.host == "regin.sindri.veldris.internal"]

    assert len(regin) == 3, "REGIN runs slurmrestd, Prometheus and a collector"
    assert {item.port for item in regin} == {6820, 9090, 4318}
    assert len({item.approving_policy for item in regin}) == 3, (
        "two of these services share an approving policy, so either approval grants both"
    )


def test_the_scheduler_destination_matches_the_url_the_driver_calls() -> None:
    """A declared destination that does not match is a refusal, not a control.

    The driver's default is `http://regin...:6820`. An entry declaring https,
    or no port, either refuses every real submission or approves more than it
    meant to -- and the refusal presents as the scheduler being down.
    """
    from draupnir.svalinn.egress import EgressBroker
    from draupnir_motsognir_slurmrest import SlurmRestDriver

    broker = EgressBroker()
    url = f"{SlurmRestDriver().base_url}/slurm/v0.0.40/ping"

    assert broker.destination_for(url) is not None, (
        f"{url} -- the address the driver actually calls -- matches no allow-list entry"
    )


@requires_bash
@pytest.mark.parametrize("unit", units())
def test_only_the_worker_is_given_the_supply_status(unit: str, tmp_path: Path) -> None:
    """SAD 11.2's last row makes the worker the thing that acts on a transfer.

    Read-only, and here that is the whole relationship rather than a
    precaution: the daemon writes this file and DRAUPNIR only ever reads it. A
    writable mount would let the control plane edit the evidence it acts on.
    RF-E18.
    """
    assert BASH is not None
    config = tmp_path / "config"
    config.mkdir()
    status = tmp_path / "supply.status"
    status.write_text("ups.status: OL\nbattery.charge: 100\n", encoding="utf-8", newline="\n")
    (config / "draupnir.env").write_text(
        f"DRAUPNIR_WORKER_SUPPLY_STATUS={status}\n", encoding="utf-8", newline="\n"
    )

    result = subprocess.run(  # noqa: S603
        [BASH, str(UNITS_DIR / "draupnir-run.sh"), unit],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            **os.environ,
            "DRAUPNIR_PLATFORM": "linux",
            "DRAUPNIR_CONFIG_DIR": str(config),
            "DRAUPNIR_STATE_DIR": str(tmp_path / "state"),
            "DRAUPNIR_PODMAN": "echo",
            f"DRAUPNIR_IMAGE_{unit.replace('-', '_')}": "registry.invalid/image:test",
        },
    )
    mounted = f"--volume {status}:{status}:ro" in result.stdout

    if unit == "draupnir-worker":
        assert mounted, f"the worker was not given the supply status:\n{result.stdout}"
    else:
        assert not mounted, f"{unit} was given the supply status and does not read it"

    assert f"{status}:{status}:rw" not in result.stdout, (
        "the supply status is mounted writable; the daemon owns this file"
    )


@requires_bash
def test_a_site_with_no_supply_starts_unchanged(tmp_path: Path) -> None:
    """Every estate today, gap G1. The UPS is on order.

    Configuring nothing must produce no mount and no warning, so adding the
    setting does not change what a developer machine or an un-fitted estate
    does.
    """
    assert BASH is not None
    config = tmp_path / "config"
    config.mkdir()
    (config / "draupnir.env").write_text(
        "DRAUPNIR_SITE_ID=sindri\n", encoding="utf-8", newline="\n"
    )

    result = subprocess.run(  # noqa: S603
        [BASH, str(UNITS_DIR / "draupnir-run.sh"), "draupnir-worker"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            **os.environ,
            "DRAUPNIR_PLATFORM": "linux",
            "DRAUPNIR_CONFIG_DIR": str(config),
            "DRAUPNIR_STATE_DIR": str(tmp_path / "state"),
            "DRAUPNIR_PODMAN": "echo",
            "DRAUPNIR_IMAGE_draupnir_worker": "registry.invalid/image:test",
        },
    )

    assert "supply" not in result.stderr.lower(), (
        f"an estate with no supply was warned about one:\n{result.stderr}"
    )
    assert result.returncode == 0


@requires_bash
def test_a_configured_supply_path_that_is_absent_says_so_and_starts(tmp_path: Path) -> None:
    """The dangerous middle case: somebody believes there is a signal.

    A configured path with no file means the daemon is not running, so the
    estate has no supply signal at all. Not fatal — SAD 11.2 makes a degraded
    mode visible rather than fatal — but not silent either.
    """
    assert BASH is not None
    config = tmp_path / "config"
    config.mkdir()
    (config / "draupnir.env").write_text(
        f"DRAUPNIR_WORKER_SUPPLY_STATUS={tmp_path / 'absent.status'}\n",
        encoding="utf-8",
        newline="\n",
    )

    result = subprocess.run(  # noqa: S603
        [BASH, str(UNITS_DIR / "draupnir-run.sh"), "draupnir-worker"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            **os.environ,
            "DRAUPNIR_PLATFORM": "linux",
            "DRAUPNIR_CONFIG_DIR": str(config),
            "DRAUPNIR_STATE_DIR": str(tmp_path / "state"),
            "DRAUPNIR_PODMAN": "echo",
            "DRAUPNIR_IMAGE_draupnir_worker": "registry.invalid/image:test",
        },
    )

    assert result.returncode == 0, "a missing status file stopped the worker"
    assert "does not exist" in result.stderr
    assert "will not force a checkpoint" in result.stderr


def test_the_installer_carries_the_supply_status_through() -> None:
    """The worker reads the file from inside a container; something must say where."""
    installer = (DEPLOY / "install.sh").read_text(encoding="utf-8")

    assert 'SUPPLY_STATUS="${DRAUPNIR_WORKER_SUPPLY_STATUS-}"' in installer, (
        "install.sh does not read a supply status path, and `:-` would stop a site "
        "setting it explicitly empty"
    )
    body = installer.split('rendered="$(cat <<EOF', 1)[1].split("\nEOF", 1)[0]
    assert "DRAUPNIR_WORKER_SUPPLY_STATUS=${SUPPLY_STATUS}" in body, (
        "the supply status path is not written into draupnir.env, so the wrapper "
        "cannot resolve it and the worker never sees the file"
    )


def test_the_installer_carries_the_declared_ring_through() -> None:
    """RF-E21. VLD-WIR-SINDRI-001 section 7.4's recovery configuration.

    An operator sets this during a recovery, with one machine already dead. It
    has to reach the worker through `draupnir.env`, and `-` rather than `:-` so
    that clearing it when the ring is restored actually clears it.
    """
    installer = (DEPLOY / "install.sh").read_text(encoding="utf-8")

    assert 'RING_MEMBERS="${DRAUPNIR_RING_MEMBERS-}"' in installer, (
        "install.sh does not read a declared ring, and `:-` would stop a site "
        "clearing it once the ring is restored"
    )
    body = installer.split('rendered="$(cat <<EOF', 1)[1].split("\nEOF", 1)[0]
    assert "DRAUPNIR_RING_MEMBERS=${RING_MEMBERS}" in body, (
        "the declared ring is not written into draupnir.env, so the worker keeps "
        "planning three-node substrate runs against a two-node ring"
    )


def test_the_declared_ring_reaches_the_worker_from_the_environment() -> None:
    """A comma separated list, because an operator writes it by hand.

    `draupnir.env` is a shell-style file edited during a recovery with the
    estate down. A JSON list there is a quoting problem waiting to happen, and
    the failure mode would be a control plane that will not start.
    """
    from draupnir.worker.loop import WorkerSettings

    settings = WorkerSettings.from_environment({"DRAUPNIR_RING_MEMBERS": "durin, dain "})

    assert settings.ring_members == ("durin", "dain")


def test_an_empty_declared_ring_means_the_whole_estate() -> None:
    """Every forge today. Adding the setting must change nothing for them."""
    from draupnir.worker.loop import WorkerSettings

    assert WorkerSettings.from_environment({"DRAUPNIR_RING_MEMBERS": ""}).ring_members == ()
    assert WorkerSettings.from_environment({}).ring_members == ()


def test_the_filevault_warning_names_the_planned_reboot_remedy() -> None:
    """RF-E22's acceptance criterion.

    A warning that says only "this will not come back" leaves an operator with
    nowhere to go. `fdesetup authrestart` is the one thing that helps, it helps
    only for a *planned* restart, and both halves have to be in the warning
    because the second is what stops somebody trying it after a power cut.
    """
    installer = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    block = installer.split("preflight_launchd()", 1)[1].split("\n}", 1)[0]

    assert "fdesetup status" in block, "install.sh does not check FileVault at all"
    assert "sudo fdesetup authrestart" in block, (
        "the FileVault warning does not name the planned-reboot remedy"
    )
    assert "planned reboot" in block, (
        "the warning does not say the remedy is for a planned reboot, so it reads as "
        "though it would help after a power cut"
    )


def test_the_preflight_names_every_wall_between_a_power_cut_and_a_return() -> None:
    """FileVault is one of three, and removing it alone changes nothing.

    The `gui` launchd domain needs a logged-in session and macOS has no
    `enable-linger`; `podman machine` is a per-user virtual machine tied to
    that session. An operator told only about FileVault would reasonably
    conclude that turning it off buys an unattended return, and it does not.
    """
    installer = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    block = installer.split("preflight_launchd()", 1)[1].split("\n}", 1)[0]

    assert "gui" in block and "logged in" in block, (
        "the preflight does not explain that the agents need a logged-in session"
    )
    assert "podman machine" in block
    assert "FileVault" in block


def test_the_wrapper_does_not_claim_an_unattended_return() -> None:
    """The reasoning the finding was about, asserted so it cannot come back.

    The state-directory fallback is right and stays. What was wrong was the
    justification: "a control plane that comes back from a power cut" is a
    thing this host does not do.
    """
    wrapper = (UNITS_DIR / "draupnir-run.sh").read_text(encoding="utf-8")
    header = wrapper.split("set -euo pipefail", 1)[0]

    # Asserted positively, and there is deliberately no "the old sentence is
    # absent" check to go with it: the corrected comment *quotes* the old
    # justification while explaining why it was wrong, so a test for the
    # phrase's absence would fire on the correct file and pass on a file that
    # had simply reworded the claim.
    assert "attended" in header, (
        "the wrapper does not say which kind of restart the fallback is for"
    )
    assert "FileVault" in header and "gui" in header and "podman machine" in header, (
        "the wrapper names fewer than the three things that stop an unattended return"
    )


def test_the_runbook_states_the_recovery_time_consequence() -> None:
    """RF-E22: recorded in words, because it is a floor on every power incident."""
    runbook = " ".join((ROOT / "docs" / "runbook.md").read_text(encoding="utf-8").split())

    assert "somebody has to be at ALVISS" in runbook
    assert "every incident that begins with a power event" in runbook
    assert "sudo fdesetup authrestart" in runbook
    assert "ANDVARI keeps FileVault" in runbook, (
        "the runbook does not say the decision is ALVISS-specific, which is how a "
        "reader talks themselves into turning it off on the host that holds the vault"
    )


# ---------------------------------------------------------------------------
# The production dependency set actually contains what the process imports.
# RF-E23.
# ---------------------------------------------------------------------------

#: SQLAlchemy names a driver in the URL; the distribution is usually the same
#: word. Where it is not, it is here. Kept short on purpose -- a long map is a
#: map somebody maintains instead of reading the URL.
_DBAPI_DISTRIBUTIONS = {"psycopg2": "psycopg2-binary"}


def _uv() -> str | None:
    """A usable `uv`, including the project-local bootstrap.

    `shutil.which` alone is not enough: `tasks.py` installs one under
    `.uv-bootstrap/` on a machine that has no system uv, and a test that
    skipped on those machines would be a test that never ran anywhere the
    defect could be introduced.
    """
    found = shutil.which("uv")
    if found:
        return found
    for candidate in (
        ROOT / ".uv-bootstrap" / "Scripts" / "uv.exe",
        ROOT / ".uv-bootstrap" / "bin" / "uv",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def production_dependencies() -> set[str]:
    """Every distribution the images are built with. `uv sync --no-dev`."""
    binary = _uv()
    assert binary is not None
    exported = subprocess.run(  # noqa: S603
        [binary, "export", "--frozen", "--no-dev", "--no-emit-project"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=180,
        check=True,
    ).stdout
    return {
        line.split("==", 1)[0].strip().lower().replace("_", "-")
        for line in exported.splitlines()
        if "==" in line and not line.startswith(" ")
    }


requires_uv = pytest.mark.skipif(_uv() is None, reason="uv resolves the production set")


def _drivers_in(url: str) -> str:
    """The `+driver` token of a SQLAlchemy URL, or the bare scheme."""
    scheme = url.split("://", 1)[0]
    return scheme.split("+", 1)[1] if "+" in scheme else scheme


@requires_uv
def test_every_configured_dbapi_is_a_runtime_dependency() -> None:
    """RF-E23, as a property rather than as the one instance that bit.

    `psycopg` sat in the `dev` dependency group. It is not a test dependency:
    the API's lifespan builds a synchronous engine for the repositories, and
    every alembic run needs the same URL. SQLAlchemy imports the DBAPI when the
    engine is *created*, so this was not a slow failure on the write path — it
    was the process refusing to start, inside the lifespan, before the first
    request.

    Nothing caught it because every test environment installs the dev group by
    definition. This resolves the set the image is built from instead.
    """
    from draupnir.core.infrastructure.config import Settings

    resolved = production_dependencies()
    settings = Settings()
    wanted = {
        _drivers_in(settings.database_url),
        _drivers_in(settings.database_url_sync),
    }

    missing = [
        driver
        for driver in sorted(wanted)
        if _DBAPI_DISTRIBUTIONS.get(driver, driver).lower().replace("_", "-") not in resolved
    ]

    assert not missing, (
        f"{', '.join(missing)} is named in a configured database URL and is not in the "
        "production dependency set. The images build with `uv sync --frozen --no-dev`, "
        "so the process would fail to start inside its lifespan — and no test would "
        "catch it, because every test environment installs the dev group."
    )


@requires_uv
def test_the_production_set_carries_no_test_only_distribution() -> None:
    """The mirror of the above, and the reason the first one is not enough.

    A dependency in the wrong direction is cheaper but not free: it puts a test
    framework in a distroless image that has no shell, and it widens what a
    supply-chain review has to cover for artefacts that ship.
    """
    resolved = production_dependencies()

    for name in ("pytest", "hypothesis", "ruff", "mypy", "playwright"):
        assert name not in resolved, f"{name} ships in the production image"


def test_the_pipeline_starts_the_image_it_built() -> None:
    """Stage 3.1 built two images and nothing ever started one. RF-E23.

    A dependency that resolves in the checkout and not in the image is
    invisible until deployment, because every test environment installs the dev
    group by definition. Building an artefact and never running it is the gap
    that let `psycopg` sit in the wrong group through a full green build.
    """
    workflow = CI.read_text(encoding="utf-8")
    # Comment lines dropped: RF-04's step explains why `--output
    # type=cacheonly` was replaced, and a check over the raw file would fail on
    # the explanation. This is the third test in this suite to need that, which
    # is itself worth noticing — a prose mention and a directive look identical
    # to a substring search.
    directives = chr(10).join(
        line for line in workflow.splitlines() if not line.lstrip().startswith("#")
    )

    assert "--output type=cacheonly" not in directives, (
        "the images are built to the cache only, so nothing can start one"
    )
    assert "docker run --rm --platform linux/arm64" in workflow, (
        "the pipeline never starts the image it built"
    )
    assert "setup-qemu-action" in workflow, (
        "the images are linux/arm64 and the runner is not; without emulation the "
        "smoke stage cannot run them"
    )


def test_the_image_smoke_creates_the_engines_rather_than_importing_the_module() -> None:
    """Importing the module would not have caught the defect it exists for.

    SQLAlchemy imports a DBAPI when the engine is *created*, and both engines
    are created inside the API's lifespan. So `import draupnir.api.app` passes
    on an image whose synchronous driver is missing, and the failure appears at
    startup in production instead.
    """
    workflow = CI.read_text(encoding="utf-8")
    smoke = workflow.split("3.1a the built image starts", 1)[1].split("- name:", 1)[0]

    assert "create_engine()" in smoke, "the smoke does not build the async engine"
    assert "sync_engine(settings.database_url_sync" in smoke, (
        "the smoke does not build the synchronous engine, which is the one that failed"
    )
    assert "import draupnir.worker.loop" in smoke, (
        "the worker shares this image and is not exercised by the smoke"
    )


def test_the_federation_host_is_deliberately_not_site_scoped() -> None:
    """The one exemption from the hostname rule, asserted as a decision.

    SAD 11A makes MEGINGJORD one registry for the whole Forge Matrix, and
    VLD-INF-SINDRI-001 puts it on Veldris_NXT rather than at a forge — so
    `megingjord.sindri.veldris.internal` would name a thing that should not
    exist. The exemption in `test_no_script_spells_a_hostname` is narrow, and
    this is what stops it becoming a hole: a site-scoped spelling is still a
    mistake, and every *other* hostname is still derived.
    """
    installer = code_of("install.sh")

    assert "megingjord.veldris.internal" in installer, (
        "install.sh no longer defaults the issuer; if that is deliberate, this test "
        "and the exemption above should go together"
    )
    assert "megingjord.sindri" not in installer, (
        "the federation registry is site scoped, which names a host that should not "
        "exist and will not resolve in the zone REGIN serves"
    )

    from draupnir.svalinn.egress import ALLOW_LIST

    (federation,) = [item for item in ALLOW_LIST if item.host.startswith("megingjord.")]
    assert federation.host == "megingjord.veldris.internal", (
        "the allow list and the installer disagree about where the registry is"
    )


# ---------------------------------------------------------------------------
# The ingress. RF-03.
# ---------------------------------------------------------------------------

NGINX = ROOT / "docker" / "nginx.conf"


def test_the_console_image_proxies_the_api() -> None:
    """The console and the API were two origins with nothing joining them.

    `client.ts` calls same-origin with an empty base URL, the units publish on
    8080 and 8000, and `location /` matched `try_files ... /index.html` — so a
    console request to `/v1/runs` was answered with the SPA's own HTML. A 200,
    carrying a document, where JSON was expected.
    """
    config = NGINX.read_text(encoding="utf-8")

    assert "location /v1/" in config, "the console image does not proxy the API"
    assert "proxy_pass" in config
    for probe in ("/healthz", "/readyz", "/openapi.json"):
        assert probe in config, f"{probe} is not reachable through the console origin"


def test_the_event_streams_are_not_buffered() -> None:
    """A buffered SSE response arrives when the connection closes.

    For a stream that is never, so the run board would render empty and report
    no error. The read timeout matters for the same reason: nginx's default
    minute would sever an idle stream, and the console's reconnect would hide
    it as a flicker rather than a fault.
    """
    config = NGINX.read_text(encoding="utf-8")
    block = config.split("runs/[^/]+/events", 1)[1].split("location", 1)[0]

    assert "proxy_buffering off" in block
    assert "proxy_read_timeout" in block
    assert "proxy_http_version 1.1" in block


def test_the_metrics_endpoint_is_not_proxied() -> None:
    """SAD 8.1 leaves `/metrics` unauthenticated *because* it is loopback-bound.

    Proxying it would publish it on whatever the console is reachable from and
    retire that justification silently: the endpoint would go on saying it
    needs no credential, and it would stop being true.
    """
    config = NGINX.read_text(encoding="utf-8")
    block = config.split("location = /metrics", 1)[1].split("location", 1)[0]

    assert "proxy_pass" not in block, "/metrics is proxied; it is loopback-bound by design"
    assert "return 404" in block


def test_the_console_sends_a_content_security_policy_and_hsts() -> None:
    """Both `always`, so they are present on an error response too."""
    config = NGINX.read_text(encoding="utf-8")

    assert "Strict-Transport-Security" in config

    # The directive itself, not the file: the comment above it explains that
    # the console needs neither `unsafe-inline` nor `unsafe-eval`, and a check
    # over the whole file would fail on the explanation.
    (policy,) = [
        line.strip() for line in config.splitlines() if "add_header Content-Security-Policy" in line
    ]

    assert "frame-ancestors 'none'" in policy
    assert "unsafe-inline" not in policy, (
        "the policy permits inline script; the console is a built bundle and needs none"
    )
    assert "unsafe-eval" not in policy
    assert "connect-src 'self'" in policy, (
        "the policy does not restrict where the console may call, which is the "
        "directive the same-origin proxy exists to make possible"
    )


def _check_tls() -> str:
    installer = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "check_tls" in installer, "install.sh does not check for a certificate"
    return installer.split("check_tls() {", 1)[1].split("\n}", 1)[0]


def test_the_installer_refuses_to_commission_without_tls() -> None:
    """SAD 9.5 is TLS 1.3 only, with mTLS between control plane components.

    A refusal rather than a warning, because the failure it prevents is silent:
    the session cookie is marked `Secure`, a browser will not send it over
    plain HTTP, so signing in appears to work and every request after it is
    anonymous. RF-37 added the material the two mutually authenticated hops
    need, and every piece of it is refused when absent.
    """
    block = _check_tls()

    for setting in (
        "DRAUPNIR_TLS_CERTIFICATE",
        "DRAUPNIR_INTERNAL_CA",
        "DRAUPNIR_PROXY_CLIENT_CERTIFICATE",
        "DRAUPNIR_API_TLS_CERTIFICATE",
        "DRAUPNIR_FEDERATION_CLIENT_CERTIFICATE",
    ):
        assert setting in block, f"--check does not require {setting}"
    assert "fail " in block, "a missing certificate warns rather than refusing"
    assert "DRAUPNIR_DEV" in block, (
        "there is no escape for a developer machine, so the check will be deleted"
    )


def test_the_installer_asks_the_proxy_to_load_its_certificates() -> None:
    """RF-37: that the proxy loads them, not that the files exist.

    `-r` passed for a certificate that is not the one for its key, and for a
    file that is not a certificate at all. `nginx -t` loads the configuration
    the image ships with the files mounted where it reads them, which is the
    thing that has to work. The Python ends' material is loaded in the API
    image by `draupnir.svalinn.transport`, as the user the unit runs as: a
    host-side check reads the files as the service account, and under rootless
    podman that is not who reads them.
    """
    block = _check_tls()

    assert '"${PODMAN}" run' in block, "--check does not start the proxy image"
    assert "/usr/sbin/nginx" in block
    assert " -t" in block, "the proxy is started, not asked to test its configuration"
    assert "/etc/draupnir/tls/server.pem" in block
    assert 'check_python_end "the API" server' in block, "the API's material is not loaded"

    installer = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    checked = installer.split("check_python_end() {", 1)[1].split("\n}", 1)[0]
    assert "-m draupnir.svalinn.transport" in checked, (
        "the API's material is not loaded the way the API loads it"
    )
    assert "openssl" not in checked, "the check reads the files as the host user"


@requires_bash
def test_the_console_unit_mounts_its_tls_material_and_publishes_tls(tmp_path: Path) -> None:
    """The proxy reads fixed paths, so the wrapper has to put the files there."""
    assert BASH is not None
    material = {}
    for name in ("server.pem", "server.key", "proxy-client.pem", "proxy-client.key", "ca.pem"):
        path = tmp_path / name
        path.write_text("placeholder\n", encoding="utf-8")
        material[name] = path

    result = subprocess.run(  # noqa: S603
        [BASH, str(UNITS_DIR / "draupnir-run.sh"), "draupnir-web"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            **os.environ,
            "DRAUPNIR_PLATFORM": "linux",
            "DRAUPNIR_CONFIG_DIR": str(tmp_path / "config"),
            "DRAUPNIR_STATE_DIR": str(tmp_path / "state"),
            "DRAUPNIR_PODMAN": "echo",
            "DRAUPNIR_IMAGE_draupnir_web": "registry.invalid/web:test",
            "DRAUPNIR_TLS_CERTIFICATE": str(material["server.pem"]),
            "DRAUPNIR_TLS_PRIVATE_KEY": str(material["server.key"]),
            "DRAUPNIR_PROXY_CLIENT_CERTIFICATE": str(material["proxy-client.pem"]),
            "DRAUPNIR_PROXY_CLIENT_PRIVATE_KEY": str(material["proxy-client.key"]),
            "DRAUPNIR_INTERNAL_CA": str(material["ca.pem"]),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "127.0.0.1:8443:8443" in result.stdout
    assert "8080" not in result.stdout, "the console still publishes a plain HTTP port"
    for source, target in (
        ("server.pem", "server.pem"),
        ("server.key", "server.key"),
        ("proxy-client.pem", "proxy-client.pem"),
        ("proxy-client.key", "proxy-client.key"),
        ("ca.pem", "internal-ca.pem"),
    ):
        assert f"{material[source]}:/etc/draupnir/tls/{target}:ro" in result.stdout


@requires_bash
def test_the_api_unit_is_told_where_its_tls_material_is_mounted(tmp_path: Path) -> None:
    """The API reads the setting, and the setting in draupnir.env is a host path."""
    assert BASH is not None
    certificate = tmp_path / "api.pem"
    certificate.write_text("placeholder\n", encoding="utf-8")

    result = subprocess.run(  # noqa: S603
        [BASH, str(UNITS_DIR / "draupnir-run.sh"), "draupnir-api"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            **os.environ,
            "DRAUPNIR_PLATFORM": "linux",
            "DRAUPNIR_CONFIG_DIR": str(tmp_path / "config"),
            "DRAUPNIR_STATE_DIR": str(tmp_path / "state"),
            "DRAUPNIR_PODMAN": "echo",
            "DRAUPNIR_IMAGE_draupnir_api": "registry.invalid/api:test",
            "DRAUPNIR_API_TLS_CERTIFICATE": str(certificate),
            "DRAUPNIR_API_TLS_PRIVATE_KEY": str(tmp_path / "absent.key"),
        },
    )

    assert result.returncode == 0, result.stderr
    assert f"{certificate}:/etc/draupnir/tls/api.pem:ro" in result.stdout
    assert "DRAUPNIR_API_TLS_CERTIFICATE=/etc/draupnir/tls/api.pem" in result.stdout
    assert "absent.key, which does not exist" in result.stderr


# ---------------------------------------------------------------------------
# Stage 3 produces what stage 4 consumes. RF-04.
# ---------------------------------------------------------------------------


def test_the_pipeline_pushes_the_images_on_main() -> None:
    """Nothing pushed one, and `rollout.sh` pulled.

    Stage 3.1 built both images with `--output type=cacheonly` and tagged them
    locally, so `podman pull registry.<site>.veldris.internal/draupnir-api:<sha>`
    ran against a registry that had never received an image — and
    `draupnir-run.sh` uses `--pull=never`, so the unit could not recover
    either. Stage 4 of SAD 11H was unrunnable.
    """
    workflow = CI.read_text(encoding="utf-8")

    assert "docker push" in workflow, "the pipeline never pushes an image"
    assert "DRAUPNIR_REGISTRY" in workflow, "the push has no registry to push to"
    assert "docker/login-action" in workflow, "nothing authenticates to the registry"


def test_a_fork_still_builds() -> None:
    """The push is conditional; the build is not.

    A pull request from a fork has no registry credentials. Making the build
    conditional too would mean a fork's changes to a Dockerfile were never
    compiled, which is the check most worth keeping for exactly those changes.
    """
    workflow = CI.read_text(encoding="utf-8")
    block = workflow.split("3.1 aarch64 images", 1)[1].split("- name:", 1)[0]

    assert "refs/heads/main" in block, "the push is not restricted to main"
    assert "docker buildx build" in block
    # The build command itself must not be inside an `if`.
    assert "if: " not in block, "the build is conditional, so a fork would not compile"


def test_rollout_and_rollback_resolve_the_same_reference() -> None:
    """They must, or a rollback pulls something the rollout never pushed."""
    rollout = (DEPLOY / "rollout.sh").read_text(encoding="utf-8")
    rollback = (DEPLOY / "rollback.sh").read_text(encoding="utf-8")

    for script, name in ((rollout, "rollout.sh"), (rollback, "rollback.sh")):
        assert "draupnir_image_for" in script, (
            f"{name} builds an image reference some other way, so the two can diverge"
        )


@requires_bash
def test_rollback_refuses_a_revision_that_is_not_a_tag() -> None:
    """The exact string `deploy.yaml` used to hand it. RF-04.

    `draupnirctl version` prints `draupnirctl 0.1.0 (OpenAPI 1.0.0)`, and that
    whole sentence became the image tag. Checked at the top of the script
    rather than discovered at the pull, because a rollback runs when a
    deployment has already gone wrong.
    """
    assert BASH is not None

    result = subprocess.run(  # noqa: S603
        [BASH, str(DEPLOY / "rollback.sh"), "draupnirctl 0.1.0 (OpenAPI 1.0.0)"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "DRAUPNIR_PLATFORM": "linux"},
    )

    assert result.returncode != 0, "a sentence was accepted as a revision"
    assert "is not an image tag" in result.stderr
    assert "draupnirctl" in result.stderr, "the refusal does not name what it received"
    assert "current-revision.sh" in result.stderr, (
        "the refusal does not name the command that would have given a real one"
    )


@requires_bash
def test_a_plain_revision_passes_the_tag_check() -> None:
    """The check must not refuse the thing it exists to let through."""
    assert BASH is not None

    result = subprocess.run(  # noqa: S603
        [BASH, "-c", 'source deploy/lib.sh; draupnir_is_tag "a1b2c3d4" && echo yes'],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
        check=False,
    )

    assert result.stdout.strip() == "yes"


@requires_bash
def test_current_revision_prints_a_bare_tag(tmp_path: Path) -> None:
    """A caller captures this into a variable, so it prints a tag and nothing else."""
    assert BASH is not None
    state = tmp_path / "state"
    state.mkdir()
    (state / "image-draupnir-api").write_text(
        "registry.sindri.veldris.internal/draupnir-api:a1b2c3d4\n",
        encoding="utf-8",
        newline="\n",
    )

    result = subprocess.run(  # noqa: S603
        [BASH, str(DEPLOY / "current-revision.sh"), "draupnir-api"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "DRAUPNIR_STATE_DIR": str(state), "DRAUPNIR_PLATFORM": "linux"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "a1b2c3d4"


@requires_bash
def test_current_revision_handles_a_registry_with_a_port(tmp_path: Path) -> None:
    """`host:5000/draupnir-api:sha` must not yield `5000/draupnir-api:sha`."""
    assert BASH is not None
    state = tmp_path / "state"
    state.mkdir()
    (state / "image-draupnir-api").write_text(
        "registry.example:5000/draupnir-api:a1b2c3d4\n", encoding="utf-8", newline="\n"
    )

    result = subprocess.run(  # noqa: S603
        [BASH, str(DEPLOY / "current-revision.sh")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "DRAUPNIR_STATE_DIR": str(state), "DRAUPNIR_PLATFORM": "linux"},
    )

    assert result.stdout.strip() == "a1b2c3d4"


@requires_bash
def test_current_revision_says_nothing_on_stdout_when_there_is_none(tmp_path: Path) -> None:
    """A message on stdout would be captured as though it were a revision.

    Which is the class of fault this script exists to fix.
    """
    assert BASH is not None

    result = subprocess.run(  # noqa: S603
        [BASH, str(DEPLOY / "current-revision.sh")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            **os.environ,
            "DRAUPNIR_STATE_DIR": str(tmp_path / "absent"),
            "DRAUPNIR_PLATFORM": "linux",
        },
    )

    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "does not exist" in result.stderr


def test_the_deploy_workflow_asks_the_host_not_the_client() -> None:
    """`draupnirctl` is a client on the runner, not the thing running on ALVISS."""
    workflow = (ROOT / ".github" / "workflows" / "deploy.yaml").read_text(encoding="utf-8")
    block = workflow.split("Record the current revision for rollback", 1)[1].split("- name:", 1)[0]

    assert "current-revision.sh" in block
    assert "draupnirctl version" not in block.split("#")[0], (
        "the rollback revision still comes from the client's own version string"
    )


def test_rollout_claims_no_signature_nothing_makes() -> None:
    """It said it "pulls the signed image", and nothing signs one.

    A script that describes a control it does not perform is worse than one
    that performs no control, because the first is read as evidence.
    """
    rollout = " ".join((DEPLOY / "rollout.sh").read_text(encoding="utf-8").split())

    assert "pulls the signed image" not in rollout
    assert "no step signs a container" in rollout, (
        "the script does not record that image signing is outstanding, so the claim "
        "will be quietly restored"
    )
