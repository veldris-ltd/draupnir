"""DRAUPNIR task runner.

One implementation of every task, so that `make`, `make.ps1` and the pipeline
all run exactly the same commands. Standard library only: it has to work on a
machine that has nothing installed but Python, Node and Docker.

    python tasks.py --list
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from subprocess import Popen

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
WINDOWS = platform.system() == "Windows"
BOOTSTRAP_VENV = ROOT / ".uv-bootstrap"
COMPOSE_FILE = ROOT / "docker" / "compose.dev.yaml"
COMPOSE_PROJECT = "draupnir-dev"
OPENAPI = ROOT / "docs" / "api" / "openapi.json"

#: Where each measured stage writes its report. One each, for the reason the
#: data files are separate: two stages writing one file contend for it, and on
#: Windows the loser reports a corrupt database rather than a coverage
#: failure. `coverage.xml` keeps the name the pipeline already collects.
UNIT_REPORT = ROOT / "coverage.xml"
CONTRACT_REPORT = ROOT / "coverage-contract.xml"
INTEGRATION_REPORT = ROOT / "coverage-integration.xml"
API_URL = "http://127.0.0.1:8000"

TASKS: dict[str, Callable[..., int]] = {}
HELP: dict[str, str] = {}
#: Tasks that forward unrecognised arguments to the tool they wrap. The runbook
#: documents `verify-chain --site sindri` and the like, and until this existed
#: those spellings were rejected -- an operator following the runbook during an
#: incident met an argparse usage message.
PASSTHROUGH: set[str] = set()


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


class Failure(Exception):
    """A task step failed."""


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------
#
# Dotted module names, never paths. RF-19: `--cov=draupnir/api/idempotency.py`
# is treated as a module name, finds no module of that name, warns
# `module-not-imported` and measures nothing -- while the percentage still
# prints, computed over whatever else was named. Eleven targets were in that
# state, so both floors were lower than they read.
#
# Written out here rather than inline at each call because the verification
# below checks the same list the run was given. A target list that could drift
# from the flags would reintroduce the defect one rename later.

#: The floors each stage must clear, each set to the figure that stage reaches.
#:
#: Two of them read lower than they did before RF-20, and that is a wider
#: measurement rather than a regression. RF-20 brought HODD, GLEIPNIR, the
#: driver interfaces, the worker, the read model and nine more edge modules
#: under a floor for the first time, so the denominators changed and the
#: percentages are not comparable across that commit. The absolute figures are,
#: and they went up in every stage:
#:
#:     unit         4,788 -> 6,661 statements covered   (91.16% -> 90.28%)
#:     contract     1,305 -> 1,457                      (87.16% -> 87.56%)
#:     integration    788 -> 2,313                      (81.25% -> 77.96%)
#:
#: What stops a floor being lowered to fit a result is not the number: it is
#: `COVERAGE_EXCLUSIONS` and the meta-test that derives the measured set from
#: the tree. The set cannot shrink without a decision recorded here.
UNIT_FLOOR = 90
CONTRACT_FLOOR = 87
INTEGRATION_FLOOR = 77

#: The unit stage: pure domain and module logic, plus the edge's pure
#: mechanisms. Routers are exercised by a request and are measured at the
#: contract level, where a request exists.
UNIT_COVERAGE: tuple[str, ...] = (
    "draupnir.core.domain",
    "draupnir.motsognir",
    "draupnir.hamarr",
    "draupnir.brisingamen",
    "draupnir.raun",
    "draupnir.skidbladnir",
    "draupnir.svalinn",
    "draupnir.gullinbursti",
    "draupnir.megingjord",
    # RF-20 added these three. They hold the licence register, the retention
    # rule, the policy gate, the release sign-off and every driver protocol,
    # and they were under no floor at all -- so coverage could fall to zero in
    # them without a stage noticing.
    "draupnir.hodd",
    "draupnir.gleipnir",
    "draupnir.interfaces",
    "draupnir.api.concurrency",
    "draupnir.api.context",
    "draupnir.api.events",
    "draupnir.api.guards",
    "draupnir.api.idempotency",
    "draupnir.api.pagination",
    "draupnir.api.telemetry",
    "draupnir.api.metrics",
    "draupnir.api.readiness",
    "draupnir.api.tracing",
    # Found by the tree check rather than by anybody's memory, which is the
    # argument for having it. `assurance` is the seam where GLEIPNIR's gate
    # definitions meet RAUN's execution -- four lines of adaptation that an
    # architecture review is meant to be able to find -- and `plugins` is the
    # entry point loader that decides which drivers this forge will run and
    # verifies their signatures (SAD 8.2). Five hundred lines of it, under no
    # floor.
    "draupnir.api.assurance",
    "draupnir.core.plugins",
)

#: The contract stage: the edge, where a convention is shown to be attached to
#: a route rather than merely implemented.
CONTRACT_COVERAGE: tuple[str, ...] = (
    "draupnir.api.app",
    "draupnir.api.deps",
    "draupnir.api.problems",
    "draupnir.api.routers",
    "draupnir.api.schemas",
    # Also the edge, and also measured by a request rather than by a unit test
    # (RF-20). `authentication` verifies a bearer token on the way in,
    # `reading` and `writing` are the two sides of the read model, and
    # `development` decides whether the unconfigured principal is allowed.
    "draupnir.api.authentication",
    "draupnir.api.development",
    # S17's documents (RF-27). Generated per request from the read model, so
    # the request is what exercises them, against a stub that holds a release.
    "draupnir.api.release_documents",
    # The API image's command (RF-37). What exercises it is a server started
    # over mTLS and a client refused by it, which is the contract stage's
    # `test_transport.py`.
    "draupnir.api.serve",
)

#: The integration stage: what needs a database or an object store to exercise.
INTEGRATION_COVERAGE: tuple[str, ...] = (
    "draupnir.core.infrastructure",
    "draupnir.core.application",
    "draupnir.procedures",
    # The worker is a process, and what it does needs a database to observe
    # (RF-20). Measuring it at the unit level would report the tick loop as
    # uncovered while `tests/integration/test_worker_loop.py` drives a run from
    # QUEUED to AWAITING_APPROVAL through every line of it.
    "draupnir.worker",
    # Both of these are edge modules whose whole point is that they are not
    # process-local: the idempotency store is a table and the ledger listener
    # is a PostgreSQL LISTEN. Neither has anything to measure without one.
    "draupnir.api.idempotency_store",
    "draupnir.api.ledger_events",
    # The read model and the writer. Both were measured at the contract level,
    # where `EmptyReadModel` stands in for one and nothing writes -- so the
    # largest module in the edge reported 29 per cent while every request a
    # forge serves went through it. What exercises them needs a database.
    "draupnir.api.reading",
    "draupnir.api.writing",
)

#: Modules deliberately under no floor, with the reason. Read by the meta-test
#: that derives the measured set from the tree, so an exclusion is a decision
#: recorded here rather than a module quietly missing from every list.
COVERAGE_EXCLUSIONS: dict[str, str] = {
    "draupnir/__init__.py": (
        "the distribution's version string and nothing else. Every import in the "
        "process executes it, so it cannot be uncovered; naming it as a target "
        "would measure a constant."
    ),
    "draupnir/api/__init__.py": (
        "a docstring. It states what the edge layer owns and must not do, which "
        "is worth reading and holds no statement to execute."
    ),
    "draupnir/core/__init__.py": (
        "a docstring, as above: the layering of SAD 11B and what the core may "
        "not know. No statements."
    ),
}


def coverage_flags(targets: Sequence[str], *, report: Path, floor: int) -> list[str]:
    """The `--cov` arguments for these targets, plus the reports and the floor."""
    return [
        *(f"--cov={target}" for target in targets),
        f"--cov-fail-under={floor}",
        "--cov-report=term-missing",
        f"--cov-report=xml:{report}",
    ]


def measured(report: Path) -> set[str]:
    """Every module the report actually measured, as dotted names."""
    import xml.etree.ElementTree as ElementTree

    if not report.is_file():
        raise Failure(f"{report} was not written, so what was measured cannot be checked")

    found: set[str] = set()
    # The file is one `coverage` wrote a moment ago into this repository, not
    # untrusted input; adding `defusedxml` to parse our own build output would
    # be a dependency to justify in the SBOM for no threat.
    for element in ElementTree.parse(report).iter("class"):  # noqa: S314
        filename = element.get("filename", "")
        if filename.endswith(".py"):
            found.add(filename.replace("\\", "/").replace("/", ".").removesuffix(".py"))
    return found


def verify_coverage(report: Path, targets: Sequence[str]) -> None:
    """Refuse a run in which a named target measured nothing. RF-19.

    A coverage target that measures nothing is worse than an absent one,
    because the percentage still prints -- over a smaller denominator, so the
    figure goes *up*. Eleven targets were in that state and both floors read
    higher than they were.

    Checked against what the report holds rather than against the warning
    coverage emits, because a warning on standard error is a thing a pipeline
    scrolls past.
    """
    seen = measured(report)
    missing = [
        target
        for target in targets
        if not any(name == target or name.startswith(f"{target}.") for name in seen)
    ]
    if missing:
        named = ", ".join(missing)
        raise Failure(
            f"these coverage targets measured nothing: {named}. A target coverage "
            "cannot resolve is silently dropped and the percentage is computed "
            "without it, so the floor is lower than it reads. Name modules "
            "(draupnir.api.idempotency), never paths."
        )


def task(
    name: str, description: str, *, passthrough: bool = False
) -> Callable[[Callable[..., int]], Callable[..., int]]:
    """Register a task under `name`.

    `passthrough` lets the task take the arguments the command line did not
    recognise and hand them to the tool it wraps. It is opt in: a task that
    does not declare it rejects a stray argument rather than ignoring it, so a
    mistyped flag is reported instead of silently doing nothing.
    """

    def register(function: Callable[..., int]) -> Callable[..., int]:
        TASKS[name] = function
        HELP[name] = description
        if passthrough:
            PASSTHROUGH.add(name)
        return function

    return register


def say(message: str) -> None:
    """Print a step banner."""
    print(f"\n\033[1m==> {message}\033[0m", flush=True)


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> int:
    """Run one command, echoing it first."""
    printable = " ".join(command)
    print(f"    $ {printable}", flush=True)
    # UTF-8 for every child, always. Several tools in this pipeline write
    # non-ASCII to the console -- `import-linter` renders its progress spinner
    # with an emoji -- and on Windows a redirected stream falls back to the
    # active code page, which is cp1252 here. The result is a UnicodeEncodeError
    # raised while *tearing down a spinner*, after the work has succeeded: the
    # contracts report "7 kept, 0 broken" and the stage still exits 1. That is
    # the worst shape a failure can have, because the output says it passed.
    merged = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})}
    completed = subprocess.run(command, cwd=cwd or ROOT, env=merged, check=False)  # noqa: S603
    if check and completed.returncode != 0:
        raise Failure(f"failed ({completed.returncode}): {printable}")
    return completed.returncode


def which(name: str) -> str | None:
    """Locate an executable, tolerating the Windows extension dance."""
    return shutil.which(name)


def uv() -> str:
    """Return a usable `uv`, bootstrapping a project-local one if required.

    The official installer is the documented route (see docs/CONTRIBUTING.md).
    This fallback exists so that `make dev` on a clean machine works with
    nothing but a system Python, and it installs into `.uv-bootstrap/` rather
    than touching anything global.
    """
    found = which("uv")
    if found:
        return found

    binary = BOOTSTRAP_VENV / ("Scripts" if WINDOWS else "bin") / ("uv.exe" if WINDOWS else "uv")
    if binary.exists():
        return str(binary)

    say("uv is not on PATH; bootstrapping a project-local copy into .uv-bootstrap/")
    run([sys.executable, "-m", "venv", str(BOOTSTRAP_VENV)])
    # `python -m pip`, not the pip shim: on Windows pip refuses to replace its
    # own running executable, so the shim cannot upgrade itself.
    interpreter = (
        BOOTSTRAP_VENV / ("Scripts" if WINDOWS else "bin") / ("python.exe" if WINDOWS else "python")
    )
    run([str(interpreter), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "uv"])
    if not binary.exists():
        raise Failure("could not bootstrap uv; install it from https://astral.sh/uv")
    return str(binary)


def uv_run(*args: str, env: dict[str, str] | None = None, check: bool = True) -> int:
    """Run a command inside the project environment."""
    return run([uv(), "run", "--frozen", *args], env=env, check=check)


def pinned_pnpm() -> str:
    """Return the pnpm version `web/package.json` pins, e.g. `pnpm@9.12.0`."""
    manifest = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
    pinned = manifest.get("packageManager", "")
    if not isinstance(pinned, str) or not pinned.startswith("pnpm@"):
        raise Failure("web/package.json does not pin pnpm under `packageManager`")
    return pinned


def pnpm_command() -> list[str]:
    """Return the command prefix that runs pnpm, at the pinned version.

    Three routes, in order of directness: pnpm on PATH, then corepack, then
    npm. All three end at the version pinned in `web/package.json` under
    `packageManager`, which is the single source of truth for it.

    The fallbacks are not theoretical. A globally installed pnpm lives in the
    npm global prefix, and a Python installed from the Microsoft Store cannot
    see that directory at all: its file APIs are redirected by the app
    container, so `shutil.which` reports nothing while `pnpm` works perfectly
    in the same terminal. Corepack covers that, but Node 25 removed corepack
    from the distribution, so npm is the floor: it is the one thing a machine
    with Node is guaranteed to have.
    """
    for name in ("pnpm", "pnpm.cmd"):
        found = which(name)
        if found:
            return [found]

    corepack = which("corepack") or which("corepack.cmd")
    if corepack is not None:
        return [corepack, pinned_pnpm()]

    npm = which("npm") or which("npm.cmd")
    if npm is not None:
        return [npm, "exec", "--yes", "--package", pinned_pnpm(), "--", "pnpm"]

    raise Failure(
        "pnpm could not be run: no pnpm, corepack or npm was found. "
        "Install Node 20 or later. See docs/CONTRIBUTING.md."
    )


def pnpm(*args: str, check: bool = True, env: dict[str, str] | None = None) -> int:
    """Run pnpm in the web workspace."""
    return run(
        [*pnpm_command(), *args],
        cwd=WEB,
        env={"COREPACK_ENABLE_DOWNLOAD_PROMPT": "0", **(env or {})},
        check=check,
    )


def api_command() -> str:
    """How Playwright should start the API.

    Passed in rather than hard-coded in `playwright.config.ts`, because a bare
    `python` there resolves to whatever is first on PATH -- on Windows that is
    the Microsoft Store shim, which has no uvicorn and produces a webServer
    failure that looks nothing like its cause. This names the interpreter the
    rest of the pipeline uses.
    """
    return (
        f'"{uv()}" run --frozen python -m uvicorn draupnir.api.app:app --host 127.0.0.1 --port 8000'
    )


def docker(*args: str, check: bool = True) -> int:
    """Run docker."""
    binary = which("docker")
    if binary is None:
        raise Failure("docker is not installed. See docs/CONTRIBUTING.md.")
    return run([binary, *args], check=check)


def compose(*args: str, check: bool = True) -> int:
    """Run docker compose against the development stack."""
    return docker("compose", "-p", COMPOSE_PROJECT, "-f", str(COMPOSE_FILE), *args, check=check)


def wait_for(url: str, *, timeout: int = 120) -> None:
    """Block until `url` answers, or give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310
                if response.status < 500:
                    return
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(1)
    raise Failure(f"timed out waiting for {url}")


def env_file() -> None:
    """Create `.env` from the example if it is missing."""
    target = ROOT / ".env"
    if not target.exists():
        target.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        print("    wrote .env from .env.example")


def git_is_clean(paths: Sequence[str], *, ignore_untracked: bool = False) -> bool:
    """Return whether `paths` have no uncommitted change.

    `ignore_untracked` skips `??` entries, for the case where a file being
    absent from the index is not itself the problem being looked for.
    """
    binary = which("git")
    if binary is None:
        return True
    result = subprocess.run(  # noqa: S603
        [binary, "status", "--porcelain", "--", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [
        line
        for line in result.stdout.splitlines()
        if line.strip() and not (ignore_untracked and line.startswith("??"))
    ]
    if lines:
        print("\n".join(lines))
        return False
    return True


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@task("bootstrap", "Install every toolchain and dependency this repository needs")
def bootstrap() -> int:
    say("Python environment")
    run([uv(), "sync", "--all-groups"])
    say("Frontend workspace")
    pnpm(
        "install",
        "--frozen-lockfile" if (WEB / "pnpm-lock.yaml").exists() else "--no-frozen-lockfile",
    )
    say("Playwright browsers")
    pnpm("exec", "playwright", "install", "--with-deps", "chromium", check=False)
    install_gitleaks()
    env_file()
    return 0


def install_gitleaks() -> None:
    """Acquire gitleaks, if it is not here and a package manager can get it.

    So the container is a fallback rather than the path most developers take
    (RF-25). The container needs the drive shared with Docker Desktop's virtual
    machine, which is a setting on somebody else's laptop, and when it is not
    set the failure is `exit 125` and a sentence about `/run/desktop/mnt/host`.

    Never fatal. A machine with no package manager this knows about still has
    the container, and a bootstrap that refused to finish over a tool with a
    working fallback would be worse than the problem. It says what it did.
    """
    say("gitleaks")
    if which("gitleaks"):
        print("    already installed")
        return

    for manager, command in (
        (
            "winget",
            [
                "winget",
                "install",
                "--id",
                "gitleaks.gitleaks",
                "--silent",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
        ),
        ("scoop", ["scoop", "install", "gitleaks"]),
        ("brew", ["brew", "install", "gitleaks"]),
    ):
        if which(manager) is None:
            continue
        print(f"    installing with {manager}")
        if run(command, check=False) == 0 and which("gitleaks"):
            return
        print(f"    {manager} did not install it")
        break

    print(
        "    not installed. `python tasks.py secrets` will use the pinned container,\n"
        "    which needs this drive shared with Docker Desktop. See docs/CONTRIBUTING.md."
    )


@task("hooks", "Install the pre-commit hooks, including the gitleaks scan (AC-Q3)")
def hooks() -> int:
    run([uv(), "tool", "run", "pre-commit", "install"])
    return 0


# ---------------------------------------------------------------------------
# Pipeline stage 1: static
# ---------------------------------------------------------------------------


@task("format", "Apply ruff and prettier formatting")
def format_code() -> int:
    uv_run("ruff", "format", ".")
    uv_run("ruff", "check", "--fix", ".")
    pnpm("run", "format")
    return 0


@task("lint", "ruff format --check and ruff check")
def lint() -> int:
    say("ruff format --check")
    uv_run("ruff", "format", "--check", ".")
    say("ruff check")
    uv_run("ruff", "check", ".")
    return 0


@task("typecheck", "mypy --strict")
def typecheck() -> int:
    uv_run("mypy")
    return 0


@task("lint-web", "prettier, eslint, the token linter and tsc --noEmit")
def lint_web() -> int:
    # JARNGREIPR resolves through `dist-types/`, and `dist-types/` is build
    # output, so it is gitignored and a clean checkout does not have it. Skip
    # this and type-aware eslint cannot resolve `@draupnir/jarngreipr` at all:
    # every import from it becomes an `error` type and the `no-unsafe-*` rules
    # report the cascade, ~106 of them, in files that are perfectly fine. It
    # passes on a developer machine only because a previous build left the
    # directory behind, which is exactly the gap that let this reach CI.
    say("build workspace packages")
    pnpm("--recursive", "--filter", "./packages/**", "run", "build")
    # The counterpart of `ruff format --check` in stage 1.1: formatting is
    # checked, never applied, so the build states the drift rather than
    # quietly rewriting the tree under a developer. Cheap, so it runs first
    # and a misformatted file is reported before a slower gate hides it.
    say("prettier --check")
    pnpm("run", "format:check")
    say("eslint")
    pnpm("run", "lint")
    # AC-U3. Tokens are the only source of visual values, and a design system
    # whose only defence is a contribution guideline drifts within a release.
    say("token-lint")
    pnpm("run", "lint:tokens")
    # AC-V6. Every token pair the components actually make, at 4.5:1 for text
    # and 3:1 for a boundary, in both themes. A dark theme nobody measured is
    # the usual way a design system claims AA and delivers it in one theme.
    say("contrast-lint")
    pnpm("run", "lint:contrast")
    say("tsc --noEmit")
    pnpm("run", "typecheck")
    return 0


@task("imports", "import-linter: the inward-only dependency rule of SAD 11B")
def imports() -> int:
    uv_run("lint-imports", "--config", ".importlinter")
    return 0


#: The image the container fallback runs, pinned. A secret scan that floated to
#: `latest` would change what it detects without anybody deciding to.
GITLEAKS_IMAGE = "zricethezav/gitleaks:v8.21.2"

#: The arguments, shared by both paths. Written once so the local binary and
#: the container cannot come to scan different things -- which is the failure
#: mode a fallback introduces, and the one nobody notices, because the fallback
#: only runs on the machines nobody is watching.
GITLEAKS_ARGUMENTS = ("detect", "--config", ".gitleaks.toml", "--redact", "--verbose")

#: What Docker Desktop says when the drive holding the repository is not shared
#: with the Linux virtual machine. Matched on substrings rather than on the
#: whole message because the path in it is the developer's own.
MOUNT_FAILURES = ("error while creating mount source path", "mkdir /run/desktop/mnt/host")


def mount_remedy(output: str) -> str | None:
    """The advice for a Docker mount failure, or `None` if this is not one.

    A `docker run` that cannot mount the working tree exits 125 and says so in
    a sentence about `/run/desktop/mnt/host` that means nothing to anybody who
    has not met it before (RF-25). It is not a fault in this repository -- the
    drive is not shared with Docker Desktop's virtual machine -- but it stops
    `make static` on a platform the README documents, and a task that reports
    only `failed (125)` leaves a developer with nothing to act on.

    Two remedies, and both are given because which one suits depends on the
    machine: sharing a drive is a setting somebody may not be able to change on
    a managed laptop, and installing a binary may be equally awkward.
    """
    if not any(marker in output for marker in MOUNT_FAILURES):
        return None
    return (
        "gitleaks ran in a container and Docker could not mount this working tree.\n"
        "\n"
        "That is a Docker Desktop drive-sharing setting rather than anything in this\n"
        "repository: the drive holding the checkout is not shared with the Linux\n"
        "virtual machine the daemon runs in.\n"
        "\n"
        "Two ways forward:\n"
        "\n"
        "  1. Docker Desktop -> Settings -> Resources -> File sharing, add the drive\n"
        "     this checkout is on, and apply. Docker restarts.\n"
        "\n"
        "  2. Install gitleaks itself and this task will prefer it, with no container\n"
        "     and no mount:\n"
        "         winget install gitleaks          (or: scoop install gitleaks)\n"
        "         brew install gitleaks            (macOS)\n"
        "         go install github.com/gitleaks/gitleaks/v8@latest\n"
        "\n"
        "`python tasks.py bootstrap` will attempt the second for you.\n"
        "\n"
        "What this must not do is pass. AC-Q3 is a secret scan over the working tree\n"
        "and the whole history, and a scan that could not run has found nothing in\n"
        "the way that an empty room has found nothing."
    )


@task("secrets", "gitleaks over the working tree and the full history (AC-Q3)")
def secrets() -> int:
    """Scan with the local binary if there is one, otherwise the pinned image.

    Never falls through to a pass. An unrunnable secret scan is a failure, and
    the one thing worse than a red stage here is a green one.
    """
    binary = which("gitleaks")
    if binary:
        run([binary, *GITLEAKS_ARGUMENTS])
        return 0

    say("gitleaks is not installed locally; running the pinned container image")
    if which("docker") is None:
        raise Failure(
            "gitleaks is not installed and neither is docker, so the secret scan\n"
            "cannot run at all. Install gitleaks -- `winget install gitleaks`, or see\n"
            "docs/CONTRIBUTING.md -- or install Docker Desktop.\n"
            "\n"
            "This is a failure rather than a skip: AC-Q3 asks for a scan, and one\n"
            "that did not run has not found anything."
        )

    # Captured rather than streamed, because the failure worth explaining is in
    # the output and has to be read before it can be recognised. Echoed
    # afterwards either way, so a real finding still reaches the log.
    command = [
        str(which("docker")),
        "run",
        "--rm",
        "-v",
        f"{ROOT.as_posix()}:/repo",
        "-w",
        "/repo",
        GITLEAKS_IMAGE,
        *GITLEAKS_ARGUMENTS,
    ]
    print(f"    $ {' '.join(command)}", flush=True)
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        check=False,
    )
    output = f"{completed.stdout}{completed.stderr}"
    print(output, end="", flush=True)

    if completed.returncode == 0:
        return 0

    remedy = mount_remedy(output)
    if remedy is not None:
        raise Failure(remedy)
    raise Failure(f"failed ({completed.returncode}): gitleaks found something, or could not run")


#: The lowest severity that fails the build. Moderate rather than high (RF-26):
#: at `--audit-level high` two moderate advisories sat in every report, below
#: the line, and nobody had decided anything about them. An advisory below a
#: threshold is invisible; one on `AUDIT_EXCEPTIONS` is a decision.
AUDIT_LEVEL = "moderate"

#: pnpm's severities, least severe first.
SEVERITIES = ("info", "low", "moderate", "high", "critical")

#: The furthest from today an exception may expire. Long enough to wait for a
#: parent package to release the fix; short enough that an exception is
#: revisited by somebody rather than inherited by everybody.
AUDIT_EXCEPTION_HORIZON = timedelta(days=90)


@dataclass(frozen=True)
class AuditException:
    """One frontend advisory the build accepts, for a stated reason, until a date.

    Matched on the advisory *and* the package, so a mistyped identifier excuses
    nothing rather than something else.
    """

    advisory: str
    package: str
    reason: str
    expires: date


#: Advisories accepted rather than fixed. Empty: the three open when RF-26 was
#: worked were all fixable through `pnpm.overrides`. An entry needs the GHSA
#: identifier, the package, why the advisory does not reach anything DRAUPNIR
#: ships, and an expiry no further away than `AUDIT_EXCEPTION_HORIZON`.
AUDIT_EXCEPTIONS: tuple[AuditException, ...] = ()


def _describe(advisory: Mapping[str, object]) -> str:
    """One line naming an advisory well enough to act on it."""
    findings = advisory.get("findings")
    versions = sorted(
        {
            str(finding.get("version"))
            for finding in (findings if isinstance(findings, list) else [])
            if isinstance(finding, Mapping)
        }
    )
    return (
        f"{advisory.get('github_advisory_id')} {advisory.get('module_name')}"
        f"@{','.join(versions) or '?'} ({advisory.get('severity')}): {advisory.get('title')}"
        f" -- fixed in {advisory.get('patched_versions')}"
    )


def audit_verdict(
    report: Mapping[str, object],
    *,
    exceptions: Sequence[AuditException],
    today: date,
    level: str = AUDIT_LEVEL,
) -> tuple[list[str], list[str]]:
    """Judge a `pnpm audit --json` report into what fails the build and what is noted.

    Every advisory lands in one list or the other. None goes unmentioned
    because it fell below the line, which is how RF-26's two came to sit in the
    report without anybody deciding anything about them.
    """
    found = report.get("advisories")
    if not isinstance(found, Mapping):
        return (["pnpm audit produced no advisories section, so nothing was audited"], [])

    blocking: list[str] = []
    noted: list[str] = []

    for exception in exceptions:
        named = f"the exception for {exception.advisory} ({exception.package})"
        if exception.expires < today:
            blocking.append(
                f"{named} expired on {exception.expires.isoformat()}. Fix the advisory, "
                "or renew the exception with a reason that is still true."
            )
        elif exception.expires - today > AUDIT_EXCEPTION_HORIZON:
            blocking.append(
                f"{named} runs to {exception.expires.isoformat()}, further than "
                f"{AUDIT_EXCEPTION_HORIZON.days} days away. An exception that never "
                "comes up again is a threshold by another name."
            )
        if not exception.reason.strip():
            blocking.append(f"{named} gives no reason")

    excused: set[AuditException] = set()
    for advisory in found.values():
        if not isinstance(advisory, Mapping):
            blocking.append(f"pnpm audit reported an advisory this task cannot read: {advisory!r}")
            continue
        described = _describe(advisory)
        severity = str(advisory.get("severity"))
        accepted = next(
            (
                candidate
                for candidate in exceptions
                if candidate.advisory == advisory.get("github_advisory_id")
                and candidate.package == advisory.get("module_name")
            ),
            None,
        )
        if accepted is not None:
            excused.add(accepted)
            noted.append(
                f"accepted until {accepted.expires.isoformat()}: {described}\n"
                f"      because {accepted.reason}"
            )
        elif severity not in SEVERITIES:
            blocking.append(f"unrecognised severity, so treated as failing: {described}")
        elif SEVERITIES.index(severity) >= SEVERITIES.index(level):
            blocking.append(described)
        else:
            noted.append(f"below the {level} threshold: {described}")

    for exception in exceptions:
        if exception not in excused:
            blocking.append(
                f"the exception for {exception.advisory} ({exception.package}) excuses "
                "an advisory the audit no longer reports. Remove it."
            )

    return blocking, noted


def pnpm_audit_report() -> dict[str, object]:
    """Run `pnpm audit --json` and return the report it prints.

    Read as JSON rather than judged by exit code: the exit code knows only the
    `--audit-level` line, and the exceptions need each advisory by name. A run
    that produces no report -- the registry unreachable, say -- is a failure,
    because an audit that did not happen has not found anything.
    """
    command = [*pnpm_command(), "audit", "--json"]
    print(f"    $ {' '.join(command)}", flush=True)
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=WEB,
        env={**os.environ, "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        report = None
    if not isinstance(report, dict):
        print(f"{completed.stdout}{completed.stderr}", end="", flush=True)
        raise Failure(
            f"pnpm audit did not produce a report (exit {completed.returncode}), "
            "so nothing was audited"
        )
    return report


@task("audit", "Dependency audit for both toolchains")
def audit() -> int:
    say("pip-audit")
    # Audit the lockfile, not the environment: the environment also holds
    # DRAUPNIR itself, which is not on PyPI and cannot be looked up.
    requirements = ROOT / "sbom" / "requirements.lock.txt"
    requirements.parent.mkdir(exist_ok=True)
    run(
        [
            uv(),
            "export",
            "--frozen",
            "--all-groups",
            # Exclude every workspace distribution, not merely the root. The
            # plug-in members depend on `draupnir`, and a public package of
            # that name exists on PyPI -- so exporting them makes the audit
            # resolve a stranger's package instead of ours.
            "--no-emit-workspace",
            "--no-hashes",
            "--format",
            "requirements-txt",
            "-o",
            str(requirements),
        ]
    )
    # `--disable-pip` is the flag that matters: pip-audit otherwise builds a
    # throwaway virtualenv and pip-installs the requirements to resolve them,
    # and the uv-managed standalone CPython that CI runs on has no working
    # `ensurepip`, so venv creation dies with exit 127 before auditing
    # anything. `--no-deps` alone does not avoid that -- it only tells
    # pip-audit the set is complete, and pip-audit still builds the venv.
    # `--disable-pip` takes the pre-resolved path instead, and it is accepted
    # only alongside `--no-deps` or a hashed file, so the two travel together.
    #
    # Resolving would also be the wrong gate here: the file is a fully pinned
    # `uv export`, so the lockfile is the artefact under audit, and
    # re-resolving could audit a set the project never installs.
    uv_run(
        "pip-audit",
        "--strict",
        "--no-deps",
        "--disable-pip",
        "--progress-spinner",
        "off",
        "-r",
        str(requirements),
    )
    say(f"pnpm audit (fails at {AUDIT_LEVEL} and above)")
    blocking, noted = audit_verdict(
        pnpm_audit_report(), exceptions=AUDIT_EXCEPTIONS, today=datetime.now(UTC).date()
    )
    for line in noted:
        print(f"    {line}", flush=True)
    if blocking:
        raise Failure("the frontend dependency audit failed:\n  " + "\n  ".join(blocking))
    if not noted:
        print("    no advisories", flush=True)
    return 0


@task("sbom", "CycloneDX SBOM for both toolchains")
def sbom() -> int:
    (ROOT / "sbom").mkdir(exist_ok=True)
    say("CycloneDX, Python")
    uv_run(
        "cyclonedx-py",
        "environment",
        "--output-format",
        "JSON",
        "--output-reproducible",
        "--output-file",
        str(ROOT / "sbom" / "draupnir-python.cdx.json"),
    )
    say("CycloneDX, Node")
    # cdxgen rather than cyclonedx-npm: the latter shells out to `npm ls`,
    # which cannot read a pnpm workspace. It runs as a pinned one-off tool
    # rather than a devDependency, so its own large dependency tree stays out
    # of the workspace and out of the audit of what DRAUPNIR ships.
    pnpm("run", "sbom")
    return 0


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


@task("crypto-inventory", "Cryptographic inventory, a build artefact (AC-S16)")
def crypto_inventory() -> int:
    # AC-S16: "The cryptographic inventory lists every algorithm, key length and
    # module in use, and each entry maps to NCSC guidance or an ISO/IEC
    # standard." Generated from the constants the system actually uses, because
    # an inventory maintained by hand describes what somebody believed the
    # system did when they last looked.
    say("cryptographic inventory")
    target = ROOT / "sbom"
    target.mkdir(parents=True, exist_ok=True)
    uv_run(
        "python",
        "-c",
        "import datetime, pathlib;"
        "from draupnir.svalinn import inventory;"
        "i = inventory.build(datetime.datetime.now(datetime.UTC));"
        "root = pathlib.Path('sbom');"
        "root.joinpath('crypto-inventory.json').write_text(i.to_json(), encoding='utf-8');"
        "root.joinpath('crypto-inventory.md').write_text(i.to_markdown(), encoding='utf-8');"
        "print(f'{len(i.rows)} entries, {len(i.in_use)} in use')",
    )
    return 0


@task("egress-policy", "Reconcile the two egress allow lists (RF-E17)")
def egress_policy() -> int:
    # Two allow lists govern the same traffic: `svalinn.egress.ALLOW_LIST` and
    # the site router's, at VLD-INF-SINDRI-001 section 10.5. A destination in
    # one and not the other fails, and the direction determines how it looks --
    # the broker refuses legibly, the router drops silently and it reads as a
    # timeout. Generated from the broker's own list, for the reason the crypto
    # inventory is.
    say("egress reconciliation")
    uv_run("python", "scripts/egress_policy.py")
    return 0


@task("openapi", "Export the OpenAPI document from the application")
def openapi() -> int:
    uv_run("python", "scripts/openapi_export.py")
    return 0


@task("clients", "Regenerate the CLI command table and the TypeScript client")
def clients() -> int:
    say("CLI command table")
    uv_run("python", "scripts/generate_cli.py")
    uv_run("ruff", "format", "draupnirctl/_generated.py")
    say("TypeScript client")
    pnpm("run", "generate:client")
    say("TypeScript operation table")
    uv_run("python", "scripts/generate_ts_operations.py")
    say("TypeScript tier table")
    uv_run("python", "scripts/generate_ts_tiers.py")
    return 0


#: The three files the build writes from the application's own OpenAPI
#: document. Nothing else may write them.
def content_of(path: Path) -> bytes | None:
    """A file's content with line endings normalised, or `None` if absent.

    The drift gates compare what a generator produces against what is on disk,
    and both halves of that comparison have been platform-dependent (RF-23).
    A generator writing with `Path.write_text` and no `newline` emits CRLF on
    Windows; `.gitattributes` is `* text=auto`, so a checkout may hand you
    either; and an editor that saved a file once may have converted it. The
    gate then reported drift in a file whose `git diff --stat` showed no change
    at all -- and reported it every time, on every Windows build, which is how a
    gate gets run with one eye closed.

    **Both fixes are applied, because they answer different questions.** Every
    generator now writes LF explicitly, so the *output* is deterministic and
    the repository holds one set of bytes; this normalises the *comparison*, so
    the gate is right about a file however it came to have the endings it has.
    The register offered these as alternatives and said to pick one and say
    which. Picking only the first would leave the gate correct on a clean
    checkout and wrong for anybody whose editor had touched a generated file;
    picking only the second would leave two developers' generators disagreeing
    about the bytes to commit.

    Read as bytes rather than as text: a generated file is compared, not
    interpreted, and decoding it would make an encoding change look like drift
    on a line the diff cannot show.
    """
    if not path.exists():
        return None
    return path.read_bytes().replace(b"\r\n", b"\n")


GENERATED = (
    ROOT / "docs" / "api" / "openapi.json",
    ROOT / "draupnirctl" / "_generated.py",
    ROOT / "web" / "packages" / "api-client" / "src" / "generated" / "schema.d.ts",
    ROOT / "web" / "packages" / "api-client" / "src" / "generated" / "operations.ts",
    ROOT / "web" / "packages" / "api-client" / "src" / "generated" / "tiers.ts",
)


@task("clients-check", "Fail if the CLI or TypeScript client has drifted (AC-Q2)")
def clients_check() -> int:
    # The check is on content, not on git state. Regenerating and comparing
    # catches a hand edit whether or not the file was ever committed, which a
    # `git status` check alone does not: an untracked generated file looks like
    # drift on a fresh clone and like nothing at all once someone adds it to
    # .gitignore.
    #
    # `content_of` rather than `read_bytes` because line endings are not
    # content (RF-23). This gate failed on every Windows build, naming files
    # whose `git diff --stat` reported no change.
    before = {path: content_of(path) for path in GENERATED}

    openapi()
    clients()

    drifted = [path for path in GENERATED if content_of(path) != before[path]]
    if drifted:
        listing = "\n".join(f"  {path.relative_to(ROOT).as_posix()}" for path in drifted)
        raise Failure(
            "These files do not match what the generator produces from the\n"
            f"OpenAPI document:\n{listing}\n\n"
            "They have been regenerated in place; commit them. A hand edited\n"
            "client is the most common way a generated interface quietly stops\n"
            "being generated (SAD 11H)."
        )

    # A tracked file that is modified is drift as well, even if regeneration
    # happens to reproduce it: it means someone committed something else.
    tracked = [path.relative_to(ROOT).as_posix() for path in GENERATED]
    if not git_is_clean(tracked, ignore_untracked=True):
        raise Failure("A generated file is modified relative to the commit. Commit or revert it.")

    print("    clients are current")
    return 0


@task("openapi-diff", "Fail the build on a breaking API change (SAD 11E.2)")
def openapi_diff() -> int:
    baseline = ROOT / "docs" / "api" / "openapi.released.json"
    uv_run("python", "scripts/openapi_diff.py", str(baseline), str(OPENAPI))
    return 0


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@task("migrate-dry", "Render the pending migrations as SQL without applying them (AC-Q6)")
def migrate_dry() -> int:
    uv_run("alembic", "upgrade", "head", "--sql")
    return 0


@task("migrate", "Apply migrations, forward only")
def migrate() -> int:
    uv_run("alembic", "upgrade", "head")
    return 0


@task("reset-db", "Drop and recreate the schema, then migrate")
def reset_db() -> int:
    say("Dropping the schema")
    uv_run("python", "-c", RESET_SQL)
    migrate()
    return 0


RESET_SQL = (
    "from sqlalchemy import create_engine, text;"
    "from draupnir.core.infrastructure.config import get_settings;"
    "e=create_engine(get_settings().database_url_sync);"
    "c=e.connect();"
    "c.execute(text('DROP SCHEMA public CASCADE'));"
    "c.execute(text('CREATE SCHEMA public'));"
    "c.commit();"
    "print('    schema reset')"
)


@task("seed", "Seed the development dataset (2 sites, 6 sources, 13 runs, 1 release, 400 entries)")
def seed() -> int:
    uv_run("python", "scripts/seed.py")
    return 0


#: Where runs write their records. Ignored by git and uploaded by the pipeline.
EVIDENCE_RECORDS = "docs/acceptance/evidence"


def records_left_the_tree_clean() -> None:
    """Fail if writing a run record changed what git sees. RF-31.

    The keyboard walk's record was committed, and every a11y run rewrote it
    with that run's clock and database, so the tree was dirty after every
    routine test run. The directory is ignored now; this is what notices if a
    record is ever committed again or the ignore rule stops covering it.
    """
    if not git_is_clean([EVIDENCE_RECORDS]):
        raise Failure(
            f"Writing a run record changed the working tree under {EVIDENCE_RECORDS}.\n"
            "Run records are not committed: each carries its own clock and database\n"
            "state, so a committed copy is rewritten by every run (RF-31). Untrack\n"
            "the file above, or restore the ignore rule that covers it."
        )


@task("procedure", "AC-F12: run Procedures M1 to M10 end to end, in one command")
def procedure() -> int:
    # The database has to be there and migrated; the procedure writes its own
    # site row. Nothing else is asked of the operator, which is the point.
    seeded_stack()
    # The reference drivers are unsigned until the Veldris PKI verifier has a
    # key to verify against (SAD 9.3), so a demonstration that did not set this
    # would record every driver as refused and render no plan. The flag names
    # itself in the log on every load it permits, which is the point of it.
    uv_run("python", "scripts/procedure.py", env={"DRAUPNIR_DEV": "1"})
    records_left_the_tree_clean()
    return 0


@task("worker", "Run the worker: move every queued run without being asked (SAD 5.1)")
def worker() -> int:
    # Ticks until interrupted. `make worker-once` is the one-shot form, which
    # is what a runbook step and a smoke test want.
    seeded_stack()
    uv_run("python", "-m", "draupnir.worker", env={"DRAUPNIR_DEV": "1"})
    return 0


@task("worker-once", "One worker tick, reported as JSON")
def worker_once() -> int:
    seeded_stack()
    uv_run("python", "-m", "draupnir.worker", "--once", env={"DRAUPNIR_DEV": "1"})
    return 0


@task("vault-status", "Is the HODD vault mounted, is it the vault, and how full?")
def vault_status() -> int:
    uv_run("python", "scripts/vault_admin.py", "status")
    return 0


@task("reconcile-vault", "Reconcile the vault after an outage (SAD 11.2, row 4)")
def reconcile_vault() -> int:
    # A dry run. Staging is `python scripts/vault_admin.py reconcile --apply`,
    # spelled out rather than given a target: the moment after an outage is the
    # moment to read what happened before changing it.
    uv_run("python", "scripts/vault_admin.py", "reconcile")
    return 0


@task("verify-chain", "Verify a site's ledger chain (SAD 11.2, row 6)", passthrough=True)
def verify_chain(*args: str) -> int:
    uv_run("python", "scripts/ledger_admin.py", "verify", *args)
    return 0


@task("rebuild-projection", "Replay a site's chain into the run registry", passthrough=True)
def rebuild_projection(*args: str) -> int:
    uv_run("python", "scripts/ledger_admin.py", "rebuild", *args)
    return 0


@task("module-readmes", "AC-D1: regenerate every module README from its docstring")
def module_readmes() -> int:
    uv_run("python", "scripts/module_readmes.py")
    return 0


@task("acceptance", "Assemble the acceptance evidence pack (SAD 12)")
def acceptance() -> int:
    # Regenerating and checking, in that order. The pack is generated from the
    # SAD and from the citations in the repository, so a criterion that lost
    # its last citation fails here rather than reading as covered.
    uv_run("python", "scripts/module_readmes.py")
    uv_run("python", "scripts/acceptance.py")
    uv_run("python", "scripts/acceptance.py", "--check")
    return 0


# ---------------------------------------------------------------------------
# Pipeline stage 2: test
# ---------------------------------------------------------------------------


@task("test-unit", "Unit tests, 90 per cent statement coverage on core/")
def test_unit() -> int:
    # SAD 11E.3 scopes the unit level to "pure domain logic" and sets the
    # target at 90 per cent; AC-N8 names the state machine and the ledger
    # specifically. Both are `draupnir/core/domain`. The infrastructure half of
    # the core is measured by the integration stage, which is the only level
    # that can honestly exercise a repository.
    #
    # The feature modules are measured here too. They are pure domain logic by
    # the same test -- no I/O, no framework, no clock -- and they decide things
    # that must not go unmeasured because of where they sit in the tree: where
    # a ring run may be placed, how much work may be unwritten, which merge
    # point was chosen, whether an artefact may be published, who may call a
    # route, whether a forge may release. The drivers are plug-ins and are
    # measured by the contract level, and so is the API edge: a router is
    # exercised by a request, and measuring it here would report the routers
    # as uncovered while the contract level exercises every one of them.
    # The edge's pure mechanisms -- idempotency, cursors, entity tags, event
    # deltas, redaction -- are in `UNIT_COVERAGE` too. They are unit testable
    # and are tested here; the routers that use them are exercised by the
    # contract level, which is where a request exists.
    uv_run(
        "pytest",
        "tests/unit",
        *coverage_flags(UNIT_COVERAGE, report=UNIT_REPORT, floor=UNIT_FLOOR),
        # Its own data file. Two pipeline stages measuring coverage into the
        # same one contend for it, and on Windows the loser reports a corrupt
        # database rather than a coverage failure.
        env={"COVERAGE_FILE": str(ROOT / ".coverage.unit")},
    )
    verify_coverage(UNIT_REPORT, UNIT_COVERAGE)
    return 0


@task("test-property", "Hypothesis property tests")
def test_property() -> int:
    uv_run("pytest", "tests/property")
    return 0


@task("test-contract", "Driver conformance harness and the API surface")
def test_contract() -> int:
    # The API edge is measured here rather than at the unit level. A router is
    # exercised by a request: the conventions of SAD 11E.2 have unit tests over
    # their mechanisms, and this level is where those mechanisms are shown to be
    # attached to a route, which is the half that actually breaks.
    uv_run(
        "pytest",
        "tests/contract",
        *coverage_flags(CONTRACT_COVERAGE, report=CONTRACT_REPORT, floor=CONTRACT_FLOOR),
        env={"COVERAGE_FILE": str(ROOT / ".coverage.contract")},
    )
    verify_coverage(CONTRACT_REPORT, CONTRACT_COVERAGE)
    return 0


@task("test-skills", "AC-Q8: each skill produces a conforming artefact")
def test_skills() -> int:
    # Runs as part of test-contract as well. It is a target of its own because
    # a skill is used at a keyboard rather than in CI, and somebody editing one
    # wants the ten-second answer rather than the whole contract level.
    uv_run("pytest", "tests/contract/test_skills.py", "-p", "no:cacheprovider")
    return 0


@task("test-degraded", "Every degraded mode of SAD 11.2, with the fault injected")
def test_degraded() -> int:
    # A target of its own because it is the one an operator runs before a shift
    # to see what the system does, rather than one CI runs to see that it still
    # does it. It is part of the integration level either way.
    uv_run(
        "pytest",
        "tests/integration/test_degraded_modes.py",
        "-p",
        "no:cacheprovider",
        env={"DRAUPNIR_DEV": "1"},
    )
    return 0


@task("test-integration", "Integration tests against ephemeral PostgreSQL and MinIO")
def test_integration() -> int:
    uv_run(
        "pytest",
        "tests/integration",
        *coverage_flags(INTEGRATION_COVERAGE, report=INTEGRATION_REPORT, floor=INTEGRATION_FLOOR),
        # The M1-M10 procedure and the degraded-mode injections both place work
        # through the reference drivers, which are unsigned until the Veldris
        # PKI verifier has a key (SAD 9.3). Without this the procedure would run
        # with no driver loaded and record every rendered plan as unavailable.
        env={
            "COVERAGE_FILE": str(ROOT / ".coverage.integration"),
            "DRAUPNIR_DEV": "1",
        },
    )
    verify_coverage(INTEGRATION_REPORT, INTEGRATION_COVERAGE)
    return 0


@task("test-frontend", "vitest and Testing Library")
def test_frontend() -> int:
    pnpm("run", "test")
    return 0


@task("test-e2e", "Playwright, the four journeys of SAD 11F.2")
def test_e2e() -> int:
    # AC-U1 requires the journeys to complete "against a seeded stack", so the
    # stack is brought up rather than assumed. Playwright starts the API and
    # the console itself; what it cannot start is the database, and a journey
    # run against an empty one would pass its navigation and prove nothing.
    seeded_stack()
    pnpm("run", "test:e2e", env={"DRAUPNIR_API_COMMAND": api_command()})
    return 0


def seeded_stack() -> None:
    """Bring up the database, migrate it and seed it if it is empty."""
    up()
    say("Schema")
    migrate()
    say("Seed data")
    seed_if_empty()


@task("test-a11y", "axe over every route and every Storybook story")
def test_a11y() -> int:
    # The console half of this scans real routes, so it needs the same stack
    # the journeys do.
    seeded_stack()
    pnpm("run", "test:a11y", env={"DRAUPNIR_API_COMMAND": api_command()})
    # The keyboard walk writes its record as it finishes (RF-31).
    records_left_the_tree_clean()
    return 0


@task("test-visual", "Storybook visual regression snapshots")
def test_visual() -> int:
    # The visual project does not read the API, but Playwright starts every
    # configured webServer whichever project runs, so it still has to be told
    # which interpreter to start it with.
    pnpm("run", "test:visual", env={"DRAUPNIR_API_COMMAND": api_command()})
    return 0


@task("test", "Every Python test level")
def test() -> int:
    test_unit()
    test_property()
    test_contract()
    test_integration()
    return 0


# ---------------------------------------------------------------------------
# Pipeline stage 3: build
# ---------------------------------------------------------------------------


@task("con-a", "Build the CON-A local view, the wheel DVALIN installs (S30)")
def con_a() -> int:
    # A wheel rather than a container. CON-A's whole value is that it depends
    # on nothing beyond the appliance it is attached to (Decision U2), and a
    # container runtime is a dependency -- one that would also have to survive
    # the total network failure the view is bought for.
    #
    # It was built, tested and shipped nowhere: not a workspace member, not in
    # the pipeline, not in a Dockerfile, not in deploy/. There was no artefact
    # to install, which is why VLD-INF-SINDRI-001 Procedure S12 step 10 puts an
    # `xterm` running `watch nvidia-smi` on the panel instead.
    run([uv(), "build", "tools/stedi-view", "--out-dir", "dist"])
    return 0


@task("images", "Build the aarch64 distroless images, rootless (AC-Q7)")
def images() -> int:
    for name, dockerfile in (
        ("draupnir-api", "docker/api.Dockerfile"),
        ("draupnir-web", "docker/web.Dockerfile"),
    ):
        say(f"{name} (linux/arm64)")
        docker(
            "buildx",
            "build",
            "--platform",
            "linux/arm64",
            "-f",
            dockerfile,
            "-t",
            f"{name}:{os.environ.get('DRAUPNIR_TAG', 'dev')}",
            "--load" if os.environ.get("DRAUPNIR_LOAD") else "--output=type=cacheonly",
            ".",
        )
    return 0


@task("build-web", "Build the console and the design system")
def build_web() -> int:
    pnpm("run", "build")
    return 0


# ---------------------------------------------------------------------------
# Stack
# ---------------------------------------------------------------------------


@task("up", "Start PostgreSQL and MinIO")
def up() -> int:
    env_file()
    # --wait only on the long-running services: it treats a one-shot container
    # that has exited as a failed service, even when it exited zero.
    compose("up", "-d", "--wait", "postgres", "minio")
    say("Artefact bucket")
    compose("run", "--rm", "minio-init")
    return 0


@task("down", "Stop the development stack and remove its volumes")
def down() -> int:
    compose("down", "--volumes")
    return 0


@task("logs", "Follow the development stack logs")
def logs() -> int:
    compose("logs", "-f")
    return 0


@task("api", "Run the API with reload")
def api() -> int:
    uv_run(
        "uvicorn",
        "draupnir.api.app:app",
        "--reload",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    )
    return 0


@task("web", "Run the console dev server")
def web() -> int:
    pnpm("run", "dev")
    return 0


@task("dev", "Clean machine to a running stack with seeded data. One command (AC-Q9)")
def dev() -> int:
    bootstrap()
    up()
    say("Schema")
    migrate()
    say("Seed data")
    seed_if_empty()
    say("Starting the API and the console")
    return serve()


def seed_if_empty() -> None:
    """Seed, tolerating an already seeded database."""
    if uv_run("python", "scripts/seed.py", check=False) != 0:
        print("    database already seeded; skipping")


def spawn(command: Sequence[str], *, cwd: Path, env: dict[str, str] | None = None) -> Popen:
    """Start a long-running process in its own group, so it can be killed whole."""
    extra: dict[str, object] = {}
    if WINDOWS:
        extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        extra["start_new_session"] = True
    return subprocess.Popen(  # noqa: S603
        command,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        **extra,  # type: ignore[arg-type]
    )


def terminate_tree(process: Popen) -> None:
    """Stop a process and everything it spawned.

    Terminating the process alone is not enough. `uvicorn --reload` runs the
    application in a worker it spawns itself, and vite does the same; killing
    only the parent orphans a worker that goes on holding port 8000. The next
    `make dev` then answers from the stale server, which is the sort of thing
    that costs an afternoon before anyone suspects it.
    """
    if process.poll() is not None:
        return

    if WINDOWS:
        # taskkill walks the tree; Windows has no process group to signal.
        subprocess.run(  # noqa: S603
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            process.terminate()

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def serve() -> int:
    """Run uvicorn and vite together until interrupted."""
    processes: list[Popen] = []
    try:
        processes.append(
            spawn(
                [
                    uv(),
                    "run",
                    "--frozen",
                    "uvicorn",
                    "draupnir.api.app:app",
                    "--reload",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                ],
                cwd=ROOT,
            )
        )
        wait_for(f"{API_URL}/healthz")
        print(f"\n    API      {API_URL}")
        print(f"    Docs     {API_URL}/docs")

        processes.append(
            spawn(
                [*pnpm_command(), "run", "dev"],
                cwd=WEB,
                env={"COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"},
            )
        )
        print("    Console  http://127.0.0.1:5173\n")
        print("    Ctrl-C to stop.")
        processes[0].wait()
    except KeyboardInterrupt:
        print("\n    stopping")
    finally:
        for process in processes:
            terminate_tree(process)
    return 0


@task(
    "smoke", "healthz, readyz and a ledger chain verification (SAD 11H stage 4)", passthrough=True
)
def smoke(*args: str) -> int:
    uv_run("python", "scripts/smoke.py", *args)
    return 0


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


@task("static", "Pipeline stage 1 in full")
def static() -> int:
    lint()
    typecheck()
    lint_web()
    imports()
    secrets()
    audit()
    sbom()
    crypto_inventory()
    return 0


#: Every stage the pipeline runs, by task name, in the workflow's order.
#:
#: One list, read by `ci()` below and by the test that compares it against
#: `.github/workflows/ci.yaml` (RF-24). The README's claim about this build is
#: that "the pipeline runs the same commands a developer does, so a stage that
#: passes locally and fails in CI is a bug in the task rather than a difference
#: between two scripts" -- and `ci()` ran twelve of the workflow's twenty-one
#: task invocations, so `acceptance`, `egress-policy`, `openapi` and `con-a`
#: could be red in CI after a green `make ci`. A claim about the build is worth
#: exactly as much as the thing that enforces it.
PIPELINE: tuple[str, ...] = (
    # 1 STATIC
    "lint",
    "typecheck",
    "lint-web",
    "imports",
    "secrets",
    "audit",
    "sbom",
    "crypto-inventory",
    # 2 TEST
    "test-unit",
    "test-property",
    "test-contract",
    "test-integration",
    "acceptance",
    "egress-policy",
    "openapi",
    "openapi-diff",
    "test-frontend",
    "test-e2e",
    "test-a11y",
    "procedure",
    "test-visual",
    # 3 BUILD
    "build-web",
    "images",
    "con-a",
    "clients-check",
    "sign",
)

#: Stages `ci()` runs that the workflow does *not* invoke as a task, and why.
#:
#: `sign` is deliberately not here: the workflow invokes it, and the task
#: itself reports skipped without a key. A stage that behaves differently in
#: two places is a difference; a stage that says so is a task.
#:
#: Each of these is a real difference rather than an oversight, and naming it
#: here is what keeps it from becoming one. The test reads this map: a task in
#: `PIPELINE` is either invoked by the workflow or explained here, so the two
#: cannot drift apart without somebody writing down which it is.
LOCAL_ONLY: dict[str, str] = {
    "images": (
        "the workflow's stage 3.1 builds the same two images and then tags them "
        "for the registry, pushes them on main, and reads and writes a GitHub "
        "Actions cache. None of that belongs in a task a developer runs: a "
        "developer does not push to the site registry. The *build* is the same "
        "and is what `make ci` checks."
    ),
    "build-web": (
        "`docker/web.Dockerfile` compiles the console inside the image, so the "
        "workflow already builds it at stage 3.1 and doing it again beforehand "
        "would be the same work twice on every run. A developer has no image "
        "build, so `make ci` does it directly."
    ),
}


@task("sign", "Sign the SBOM and the plug-in distributions (Decision S9)")
def sign() -> int:
    """Sign what this build produced, or say why it did not.

    Skipped rather than failed without a key, and *reported* rather than
    silent, which is the shape stage 3.4 already uses. The distinction matters:
    a stage that prints nothing when it does nothing is indistinguishable from
    a stage that ran, and Decision S9's whole subject is being able to tell a
    signed artefact from one that merely looks like it.
    """
    if not os.environ.get("DRAUPNIR_SIGNING_KEY"):
        print(
            "    skipped: no DRAUPNIR_SIGNING_KEY. Artefact signing is a required\n"
            "    stage on main (SAD 11H stage 3, Decision S9); locally there is no\n"
            "    key and nothing here is released."
        )
        return 0

    say("plug-in distributions")
    uv_run(
        "python",
        "-m",
        "scripts.sign_artefacts",
        "--distributions",
        "--out",
        str(ROOT / "sbom" / "plugin-signatures.json"),
    )
    say("artefacts")
    uv_run("python", "-m", "scripts.sign_artefacts")
    return 0


@task("ci", "Every stage the pipeline runs, in pipeline order")
def ci() -> int:
    """Run `PIPELINE`, in order, stopping at the first failure.

    Dispatched from the list rather than written out as calls, so that the list
    is the single description of the pipeline and the test can read it. Written
    out, the two would agree until somebody added a stage to one of them.
    """
    for name in PIPELINE:
        TASKS[name]()
    return 0


@task("clean", "Remove build output and caches")
def clean() -> int:
    for path in (
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".hypothesis",
        "htmlcov",
        "sbom",
        "web/node_modules/.vite",
    ):
        target = ROOT / path
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            print(f"    removed {path}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Dispatch one task."""
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument("task", nargs="?", help="Task to run")
    parser.add_argument("--list", action="store_true", help="List every task")
    args, forwarded = parser.parse_known_args(argv)

    if args.list or not args.task:
        width = max(len(name) for name in TASKS)
        print("DRAUPNIR tasks\n")
        for name in sorted(TASKS):
            print(f"  {name:<{width}}  {HELP[name]}")
        return 0

    if args.task not in TASKS:
        print(f"unknown task: {args.task}", file=sys.stderr)
        return 2

    if forwarded and args.task not in PASSTHROUGH:
        print(
            f"{args.task} takes no arguments; got {' '.join(forwarded)}",
            file=sys.stderr,
        )
        return 2

    try:
        return TASKS[args.task](*forwarded) if args.task in PASSTHROUGH else TASKS[args.task]()
    except Failure as failure:
        print(f"\n\033[31m{failure}\033[0m", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
