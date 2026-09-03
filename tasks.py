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
from collections.abc import Callable, Sequence
from pathlib import Path
from subprocess import Popen

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
WINDOWS = platform.system() == "Windows"
BOOTSTRAP_VENV = ROOT / ".uv-bootstrap"
COMPOSE_FILE = ROOT / "docker" / "compose.dev.yaml"
COMPOSE_PROJECT = "draupnir-dev"
OPENAPI = ROOT / "docs" / "api" / "openapi.json"
API_URL = "http://127.0.0.1:8000"

TASKS: dict[str, Callable[[], int]] = {}
HELP: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


class Failure(Exception):
    """A task step failed."""


def task(name: str, description: str) -> Callable[[Callable[[], int]], Callable[[], int]]:
    """Register a task under `name`."""

    def register(function: Callable[[], int]) -> Callable[[], int]:
        TASKS[name] = function
        HELP[name] = description
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
    env_file()
    return 0


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


@task("lint-web", "eslint, the token linter and tsc --noEmit")
def lint_web() -> int:
    say("eslint")
    pnpm("run", "lint")
    # AC-U3. Tokens are the only source of visual values, and a design system
    # whose only defence is a contribution guideline drifts within a release.
    say("token-lint")
    pnpm("run", "lint:tokens")
    say("tsc --noEmit")
    pnpm("run", "typecheck")
    return 0


@task("imports", "import-linter: the inward-only dependency rule of SAD 11B")
def imports() -> int:
    uv_run("lint-imports", "--config", ".importlinter")
    return 0


@task("secrets", "gitleaks over the working tree and the full history (AC-Q3)")
def secrets() -> int:
    binary = which("gitleaks")
    if binary:
        run([binary, "detect", "--config", ".gitleaks.toml", "--redact", "--verbose"])
        return 0
    say("gitleaks is not installed locally; running the pinned container image")
    docker(
        "run",
        "--rm",
        "-v",
        f"{ROOT.as_posix()}:/repo",
        "-w",
        "/repo",
        "zricethezav/gitleaks:v8.21.2",
        "detect",
        "--config",
        ".gitleaks.toml",
        "--redact",
        "--verbose",
    )
    return 0


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
    uv_run("pip-audit", "--strict", "--progress-spinner", "off", "-r", str(requirements))
    say("pnpm audit")
    pnpm("audit", "--audit-level", "high")
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
    return 0


#: The three files the build writes from the application's own OpenAPI
#: document. Nothing else may write them.
GENERATED = (
    ROOT / "docs" / "api" / "openapi.json",
    ROOT / "draupnirctl" / "_generated.py",
    ROOT / "web" / "packages" / "api-client" / "src" / "generated" / "schema.d.ts",
    ROOT / "web" / "packages" / "api-client" / "src" / "generated" / "operations.ts",
)


@task("clients-check", "Fail if the CLI or TypeScript client has drifted (AC-Q2)")
def clients_check() -> int:
    # The check is on content, not on git state. Regenerating and comparing
    # bytes catches a hand edit whether or not the file was ever committed,
    # which a `git status` check alone does not: an untracked generated file
    # looks like drift on a fresh clone and like nothing at all once someone
    # adds it to .gitignore.
    before = {path: path.read_bytes() if path.exists() else None for path in GENERATED}

    openapi()
    clients()

    drifted = [path for path in GENERATED if path.read_bytes() != before[path]]
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


@task("seed", "Seed the development dataset (2 sites, 6 sources, 12 runs, 3 releases, 400 entries)")
def seed() -> int:
    uv_run("python", "scripts/seed.py")
    return 0


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
    return 0


@task("verify-chain", "Verify a site's ledger chain (SAD 11.2, row 6)")
def verify_chain() -> int:
    uv_run("python", "scripts/ledger_admin.py", "verify")
    return 0


@task("rebuild-projection", "Replay a site's chain into the run registry")
def rebuild_projection() -> int:
    uv_run("python", "scripts/ledger_admin.py", "rebuild")
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
    uv_run(
        "pytest",
        "tests/unit",
        "--cov=draupnir/core/domain",
        "--cov=draupnir/motsognir",
        "--cov=draupnir/hamarr",
        "--cov=draupnir/brisingamen",
        "--cov=draupnir/raun",
        "--cov=draupnir/skidbladnir",
        "--cov=draupnir/svalinn",
        "--cov=draupnir/gullinbursti",
        "--cov=draupnir/megingjord",
        # The edge's pure mechanisms -- idempotency, cursors, entity tags,
        # event deltas, redaction -- are unit testable and are tested here.
        # The routers that use them are exercised by the contract level, which
        # is where a request exists.
        "--cov=draupnir/api/concurrency.py",
        "--cov=draupnir/api/context.py",
        "--cov=draupnir/api/events.py",
        "--cov=draupnir/api/guards.py",
        "--cov=draupnir/api/idempotency.py",
        "--cov=draupnir/api/pagination.py",
        "--cov=draupnir/api/telemetry.py",
        "--cov-fail-under=90",
        "--cov-report=term-missing",
        "--cov-report=xml",
        # Its own data file. Two pipeline stages measuring coverage into the
        # same one contend for it, and on Windows the loser reports a corrupt
        # database rather than a coverage failure.
        env={"COVERAGE_FILE": str(ROOT / ".coverage.unit")},
    )
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
        "--cov=draupnir/api/app.py",
        "--cov=draupnir/api/deps.py",
        "--cov=draupnir/api/problems.py",
        "--cov=draupnir/api/routers",
        "--cov=draupnir/api/schemas.py",
        "--cov-fail-under=85",
        "--cov-report=term-missing",
        env={"COVERAGE_FILE": str(ROOT / ".coverage.contract")},
    )
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
        "--cov=draupnir/core/infrastructure",
        "--cov=draupnir/core/application",
        "--cov=draupnir/procedures",
        "--cov-fail-under=80",
        "--cov-report=term-missing",
        # The M1-M10 procedure and the degraded-mode injections both place work
        # through the reference drivers, which are unsigned until the Veldris
        # PKI verifier has a key (SAD 9.3). Without this the procedure would run
        # with no driver loaded and record every rendered plan as unavailable.
        env={
            "COVERAGE_FILE": str(ROOT / ".coverage.integration"),
            "DRAUPNIR_DEV": "1",
        },
    )
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


@task("smoke", "healthz, readyz and a ledger chain verification (SAD 11H stage 4)")
def smoke() -> int:
    uv_run("python", "scripts/smoke.py")
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


@task("ci", "Every stage the pipeline runs, in pipeline order")
def ci() -> int:
    static()
    test_unit()
    test_property()
    test_contract()
    test_integration()
    clients_check()
    openapi_diff()
    test_frontend()
    test_e2e()
    test_a11y()
    test_visual()
    build_web()
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
    args = parser.parse_args(argv)

    if args.list or not args.task:
        width = max(len(name) for name in TASKS)
        print("DRAUPNIR tasks\n")
        for name in sorted(TASKS):
            print(f"  {name:<{width}}  {HELP[name]}")
        return 0

    if args.task not in TASKS:
        print(f"unknown task: {args.task}", file=sys.stderr)
        return 2

    try:
        return TASKS[args.task]()
    except Failure as failure:
        print(f"\n\033[31m{failure}\033[0m", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
