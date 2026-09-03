"""AC-Q8: each of the six skills produces a conforming artefact in a demonstration.

Every test here runs a skill's scaffold and then puts the output through the
*real* gate -- the published conformance harness, a mounted FastAPI
application, the migration checks, the token linter's own rules, the suite
registry, the release path. Nothing is edited in between, and nothing is
re-implemented: a demonstration that checked the output against a second copy
of the rules would pass while the rules moved.

That is what makes these skills unable to rot quietly. If a convention changes
and a skill does not, the skill's demonstration fails here rather than the next
time somebody uses it.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from draupnir.api.app import DEFAULT_RESPONSES, bind_context
from draupnir.api.guards import enforce_declarations
from draupnir.api.problems import EXCEPTION_HANDLERS
from draupnir.interfaces.testing import check_job_driver, describe, sample_spec

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"

#: SAD 11G's table. A skill missing from here is a skill nobody discovers.
EXPECTED = (
    "draupnir-driver",
    "draupnir-endpoint",
    "draupnir-migration",
    "jarngreipr-component",
    "raun-suite",
    "cim-release",
)


def load(path: Path, name: str) -> ModuleType:
    """Import a skill's script by path.

    The scripts are commands rather than a package -- they are invoked with
    `python skills/.../new_thing.py` -- so there is nothing to import normally.
    Registering in `sys.modules` before executing is not optional: a dataclass
    defined in a module that is not registered raises while `@dataclass`
    resolves its annotations.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def importable(tmp_path: Path) -> Iterator[Path]:
    """A directory on `sys.path` for the duration of one test."""
    directory = tmp_path / "importable"
    directory.mkdir()
    sys.path.insert(0, str(directory))
    try:
        yield directory
    finally:
        sys.path.remove(str(directory))


# ---------------------------------------------------------------------------
# Every skill
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", EXPECTED)
def test_the_skill_exists_and_is_shaped_like_a_skill(name: str) -> None:
    """A folder with SKILL.md, references and its scripts (SAD 11G)."""
    skill = SKILLS / name
    document = skill / "SKILL.md"

    assert document.is_file(), f"{name} has no SKILL.md"
    text = document.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{name}'s SKILL.md has no frontmatter"
    assert f"name: {name}\n" in text, f"{name}'s frontmatter does not name it"
    assert "description:" in text, f"{name} has no description, so nothing triggers it"
    assert list((skill / "references").glob("*.md")), f"{name} has no references"
    assert list((skill / "scripts").glob("*.py")), f"{name} ships no script"


def test_the_index_lists_every_skill() -> None:
    """Decision S14: skills are a deliverable, and a deliverable is findable."""
    index = (SKILLS / "README.md").read_text(encoding="utf-8")

    for name in EXPECTED:
        assert f"[{name}]" in index or f"`{name}`" in index, f"{name} is not in skills/README.md"


# ---------------------------------------------------------------------------
# draupnir-driver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("group", "name", "distribution", "capabilities", "kind"),
    [
        ("draupnir.export", "skidbladnir.zip/v1", "veldris-draupnir-zip-x", ("zip",), "quantised"),
        (
            "draupnir.train",
            "hamarr.axolotl/v1",
            "veldris-draupnir-axolotl-x",
            ("lora", "bf16"),
            "adapter",
        ),
        (
            "draupnir.merge",
            "brisingamen.slerp/v1",
            "veldris-draupnir-slerp-x",
            ("slerp",),
            "merged",
        ),
    ],
)
def test_a_scaffolded_driver_passes_the_conformance_harness(
    tmp_path: Path,
    importable: Path,
    group: str,
    name: str,
    distribution: str,
    capabilities: tuple[str, ...],
    kind: str,
) -> None:
    """The claim this skill makes, tested directly.

    "draupnir-driver output must pass the conformance harness with no manual
    edit. That is the test of whether the skill is real."
    """
    scaffold = load(SKILLS / "draupnir-driver" / "scripts" / "new_driver.py", "skill_new_driver")
    scaffold.scaffold(
        group=group,
        name=name,
        distribution=distribution,
        capabilities=capabilities,
        out=importable,
        tests_out=tmp_path / "tests",
        # The repository's own pyproject and import contracts are not touched
        # by a demonstration. They are exercised for real every time somebody
        # adds a driver.
        pyproject=None,
        artefact_kind=kind,
    )

    module = importlib.import_module(scaffold.module_of(distribution))
    findings = check_job_driver(module.driver, _spec_for(group, capabilities), tmp_path)

    assert findings == [], describe(findings)


def _spec_for(group: str, capabilities: tuple[str, ...]) -> Any:
    """The SAD 6.2 sample narrowed to what a freshly scaffolded driver declares."""
    if group == "draupnir.export":
        return sample_spec(
            release={"route": "B", "approval": "required", "formats": [*capabilities]}
        )
    if group == "draupnir.train":
        return sample_spec(
            train={
                "driver": "hamarr.llamafactory/v1",
                "method": capabilities[0],
                "precision": capabilities[1],
                "params": {},
            }
        )
    return sample_spec()


def test_the_scaffold_refuses_a_group_whose_shape_it_would_be_guessing(tmp_path: Path) -> None:
    """A ScheduleDriver has no `render` and no `collect`; the harness differs."""
    scaffold = load(SKILLS / "draupnir-driver" / "scripts" / "new_driver.py", "skill_new_driver")

    with pytest.raises(scaffold.ScaffoldError, match="plans no job"):
        scaffold.scaffold(
            group="draupnir.schedule",
            name="motsognir.k8s/v1",
            distribution="veldris-draupnir-k8s",
            capabilities=("array",),
            out=tmp_path,
            tests_out=tmp_path,
            pyproject=None,
        )


def test_the_scaffold_refuses_a_train_driver_that_declares_no_precision(tmp_path: Path) -> None:
    """`capabilities_for` demands the precision, so one that declares none never runs."""
    scaffold = load(SKILLS / "draupnir-driver" / "scripts" / "new_driver.py", "skill_new_driver")

    with pytest.raises(scaffold.ScaffoldError, match="method and its precision"):
        scaffold.scaffold(
            group="draupnir.train",
            name="hamarr.axolotl/v1",
            distribution="veldris-draupnir-axolotl-y",
            capabilities=("lora",),
            out=tmp_path,
            tests_out=tmp_path,
            pyproject=None,
        )


# ---------------------------------------------------------------------------
# draupnir-endpoint
# ---------------------------------------------------------------------------


def _mount(module: ModuleType) -> FastAPI:
    """The scaffolded router on a real application, assembled as `create_app` does."""
    app = FastAPI(exception_handlers=EXCEPTION_HANDLERS, responses=DEFAULT_RESPONSES)
    app.middleware("http")(bind_context)
    versioned = APIRouter(prefix="/v1")
    versioned.include_router(module.router)
    app.include_router(versioned)
    # AC-B6: this raises rather than returning, for a route with no declaration.
    enforce_declarations(app)
    return app


@pytest.mark.parametrize(
    ("shape", "path", "operation_id", "permission", "verb", "concrete"),
    [
        ("collection", "/leases", "listLeases", "READ", "get", "/v1/leases"),
        ("item", "/leases/{lease_id}", "getLease", "READ", "get", "/v1/leases/sample"),
        (
            "mutation",
            "/leases/{lease_id}/release",
            "releaseLease",
            "CURATE",
            "post",
            "/v1/leases/sample/release",
        ),
    ],
)
def test_a_scaffolded_endpoint_registers_and_behaves(
    tmp_path: Path,
    shape: str,
    path: str,
    operation_id: str,
    permission: str,
    verb: str,
    concrete: str,
) -> None:
    """The generated operation starts, guards, and describes itself."""
    scaffold = load(
        SKILLS / "draupnir-endpoint" / "scripts" / "new_endpoint.py", "skill_new_endpoint"
    )
    scaffold.scaffold(
        router="lease",
        shape=shape,
        path=path,
        operation_id=operation_id,
        summary="One lease on a scarce partition",
        permission=permission,
        out=tmp_path,
        tests_out=tmp_path / "tests",
        app_py=None,
    )

    module = load(tmp_path / "lease.py", f"scaffolded_router_{shape}")
    app = _mount(module)

    # AC-S4: the guard runs before the handler, and refuses with a problem
    # document rather than a bare 401 body.
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.request(verb, concrete)
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")

    # AC-N10: an operation id and a typed error path, or the clients cannot be
    # generated from the document.
    operation = app.openapi()["paths"][f"/v1{path}"][verb]
    assert operation["operationId"] == operation_id
    assert "default" in operation["responses"]
    assert "Requires:" in operation["description"]


def test_a_scaffolded_mutation_refuses_a_request_without_an_idempotency_key(
    tmp_path: Path,
) -> None:
    """AC-B1. The retry that actually duplicated something omitted the header."""
    scaffold = load(
        SKILLS / "draupnir-endpoint" / "scripts" / "new_endpoint.py", "skill_new_endpoint"
    )
    scaffold.scaffold(
        router="quarantine",
        shape="mutation",
        path="/corpora/{iso3}/quarantine",
        operation_id="quarantineCorpus",
        summary="Quarantine a corpus",
        permission="CURATE",
        out=tmp_path,
        tests_out=tmp_path / "tests",
        app_py=None,
    )

    module = load(tmp_path / "quarantine.py", "scaffolded_router_quarantine")
    app = _mount(module)

    claims = {
        "sub": "curator-1",
        "iss": "https://megingjord.veldris.internal",
        "roles": ["curator"],
        "amr": ["pwd", "hwk"],
    }

    @app.middleware("http")
    async def inject(request: Any, call_next: Any) -> Any:
        request.state.claims = claims
        return await call_next(request)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/v1/corpora/GBR/quarantine", json={"reason": "spill"})

    assert response.status_code == 428
    assert response.json()["code"] == "idempotency-key-required"


def test_the_endpoint_scaffold_refuses_a_path_carrying_the_version(tmp_path: Path) -> None:
    """`create_app` mounts every router under /v1; a second prefix answers /v1/v1."""
    scaffold = load(
        SKILLS / "draupnir-endpoint" / "scripts" / "new_endpoint.py", "skill_new_endpoint"
    )

    with pytest.raises(scaffold.ScaffoldError, match="carries the version"):
        scaffold.scaffold(
            router="lease",
            shape="collection",
            path="/v1/leases",
            operation_id="listLeases",
            summary="Leases",
            permission="READ",
            out=tmp_path,
            tests_out=tmp_path,
            app_py=None,
        )


# ---------------------------------------------------------------------------
# draupnir-migration
# ---------------------------------------------------------------------------


def test_scaffolded_migrations_pass_the_checks_alongside_the_real_ones(tmp_path: Path) -> None:
    """Three shapes, generated onto the real chain and checked with it."""
    scaffold = load(
        SKILLS / "draupnir-migration" / "scripts" / "new_migration.py", "skill_new_migration"
    )

    versions = tmp_path / "versions"
    versions.mkdir()
    for existing in sorted((ROOT / "migrations" / "versions").glob("[0-9]*.py")):
        shutil.copy(existing, versions / existing.name)

    scaffold.scaffold(
        slug="lease_table",
        shape="table",
        table="lease",
        message="Leases on scarce partitions.",
        site_scoped=True,
        versions=versions,
    )
    scaffold.scaffold(
        slug="run_notes",
        shape="column",
        table="run",
        column="notes",
        message="An operator note on a run.",
        versions=versions,
    )
    scaffold.scaffold(
        slug="run_state_index",
        shape="index",
        table="run",
        column="state",
        message="Index the run board's filter.",
        versions=versions,
    )

    generated = sorted(versions.glob("[0-9]*.py"))
    assert len(generated) == 5

    problems = [
        problem
        for script in generated
        for problem in scaffold.checks(script.read_text(encoding="utf-8"), path=script)
    ]
    assert problems == [], "\n".join(problems)

    # Numbered forward from the real head, and singly headed. Two people
    # branching from one revision is the failure that is easiest to create.
    assert [script.name for script in generated][-3:] == [
        "0003_lease_table.py",
        "0004_run_notes.py",
        "0005_run_state_index.py",
    ]
    assert scaffold.head(versions) == "0005"


def test_the_real_migrations_pass_the_same_checks() -> None:
    """The other half. A check only the scaffold satisfies checks the scaffold."""
    scaffold = load(
        SKILLS / "draupnir-migration" / "scripts" / "new_migration.py", "skill_new_migration"
    )

    scripts = sorted((ROOT / "migrations" / "versions").glob("[0-9]*.py"))
    assert scripts, "there are no migrations to check"

    problems = [
        problem
        for script in scripts
        for problem in scaffold.checks(script.read_text(encoding="utf-8"), path=script)
    ]
    assert problems == [], "\n".join(problems)


def test_the_migration_checks_catch_what_they_claim_to(tmp_path: Path) -> None:
    """A check nobody has watched fail is a check nobody knows works."""
    scaffold = load(
        SKILLS / "draupnir-migration" / "scripts" / "new_migration.py", "skill_new_migration"
    )

    versions = tmp_path / "versions"
    written = scaffold.scaffold(
        slug="lease_table",
        shape="table",
        table="lease",
        message="Leases.",
        site_scoped=True,
        versions=versions,
        down_revision="0002",
    )
    good = written.read_text(encoding="utf-8")

    # A working downgrade.
    reversible = good.replace(scaffold.FORWARD_ONLY, "op.drop_table('lease')")
    assert any("forward only" in problem for problem in scaffold.checks(reversible))

    # RLS enabled but not forced: the owning role stays exempt.
    unforced = good.replace('    op.execute("ALTER TABLE lease FORCE ROW LEVEL SECURITY")\n', "")
    assert any("FORCE" in problem for problem in scaffold.checks(unforced))

    # A site-scoped table with no policy at all.
    unscoped = good
    for statement in (
        '    op.execute("ALTER TABLE lease ENABLE ROW LEVEL SECURITY")\n',
        '    op.execute("ALTER TABLE lease FORCE ROW LEVEL SECURITY")\n',
    ):
        unscoped = unscoped.replace(statement, "")
    assert any("row level security" in problem for problem in scaffold.checks(unscoped))

    # Destructive DDL in `upgrade`.
    destructive = good.replace(
        '    op.execute("ALTER TABLE lease ENABLE ROW LEVEL SECURITY")',
        '    op.drop_column("run", "notes")\n'
        '    op.execute("ALTER TABLE lease ENABLE ROW LEVEL SECURITY")',
    )
    assert any("drop_column" in problem for problem in scaffold.checks(destructive))


# ---------------------------------------------------------------------------
# jarngreipr-component
# ---------------------------------------------------------------------------

EXAMPLE = ROOT / "web" / "packages" / "jarngreipr" / "src" / "example"
EXAMPLE_FILES = ("PoolStatus.tsx", "PoolStatus.css", "PoolStatus.stories.tsx")


def test_the_worked_component_is_exactly_what_the_scaffold_produces(tmp_path: Path) -> None:
    """The committed example is scaffold output, unedited.

    Regenerated and compared byte for byte, so a convention that moves without
    the skill moving fails here. The example is inside the package, so the
    token linter, `tsc`, Storybook and Vitest all cover it as well.
    """
    scaffold = load(
        SKILLS / "jarngreipr-component" / "scripts" / "new_component.py", "skill_new_component"
    )
    scaffold.scaffold(
        name="PoolStatus",
        layer="example",
        summary="Allocation pools and how much of each is in use.",
        label="Allocation pools",
        src=tmp_path,
    )

    for filename in EXAMPLE_FILES:
        regenerated = (tmp_path / "example" / filename).read_text(encoding="utf-8")
        committed = (EXAMPLE / filename).read_text(encoding="utf-8")
        assert regenerated == committed, (
            f"{filename} differs from what the scaffold produces today. Either the "
            "conventions moved and the skill did not, or the example was edited by "
            "hand -- and an edited example is not evidence that the scaffold works."
        )


def test_the_worked_component_ships_all_seven_states() -> None:
    """AC-U2. A component with only a happy path is not done."""
    stories = (EXAMPLE / "PoolStatus.stories.tsx").read_text(encoding="utf-8")

    for export in ("Ready", "Loading", "Empty", "ErrorState", "Denied", "ReadOnly", "Partitioned"):
        assert f"export const {export} = stories.{export};" in stories
    assert "stateStories(" in stories
    assert "...COMPONENT_META" in stories


def test_the_worked_component_states_no_visual_value_of_its_own() -> None:
    """AC-U3, applied to the generated stylesheet.

    The token linter walks the whole workspace and covers this file too. This
    asserts the narrower thing the skill claims: that the scaffold's own output
    contains no literal to copy.
    """
    styles = (EXAMPLE / "PoolStatus.css").read_text(encoding="utf-8")

    declarations = [
        line.split(":", 1)[1]
        for line in styles.splitlines()
        if ":" in line and line.strip().endswith(";")
    ]
    literals = [
        value
        for value in declarations
        if "var(--jg-" not in value and any(char.isdigit() for char in value)
    ]
    assert literals == [], f"a hard-coded value would fail the token linter: {literals}"


def test_a_scaffolded_composite_is_registered_where_the_gates_look(tmp_path: Path) -> None:
    """`stories.test.ts` reads index.ts to decide which components need stories.

    A component missing from that export block ships whatever stories it
    happens to have and nothing fails, so the registration is the half of this
    scaffold that matters most.
    """
    scaffold = load(
        SKILLS / "jarngreipr-component" / "scripts" / "new_component.py", "skill_new_component"
    )
    src = tmp_path / "src"
    shutil.copytree(ROOT / "web" / "packages" / "jarngreipr" / "src", src)

    scaffold.scaffold(
        name="PartitionNotice",
        layer="composites",
        summary="Whether this site can release, and why not.",
        label="Partition state",
        src=src,
    )

    assert (src / "composites" / "PartitionNotice.tsx").is_file()
    assert "export * from './PartitionNotice';" in (src / "composites" / "index.tsx").read_text(
        encoding="utf-8"
    )
    package = (src / "index.ts").read_text(encoding="utf-8")
    assert "  PartitionNotice,\n" in package
    assert "  PartitionNoticeProps,\n" in package

    # Running it twice must not double the exports. A scaffold that is not
    # idempotent is one nobody dares re-run after fixing an argument.
    scaffold.scaffold(
        name="PartitionNotice",
        layer="composites",
        summary="Whether this site can release, and why not.",
        label="Partition state",
        src=src,
    )
    assert (src / "index.ts").read_text(encoding="utf-8").count("  PartitionNotice,\n") == 1


def test_the_component_scaffold_refuses_a_name_storybook_cannot_index(tmp_path: Path) -> None:
    """The name is the exported symbol, the file stem and the story title."""
    scaffold = load(
        SKILLS / "jarngreipr-component" / "scripts" / "new_component.py", "skill_new_component"
    )

    with pytest.raises(scaffold.ComponentError, match="PascalCase"):
        scaffold.scaffold(name="pool status", layer="example", summary="A pool.", src=tmp_path)


# ---------------------------------------------------------------------------
# raun-suite
# ---------------------------------------------------------------------------


def test_a_scaffolded_suite_resolves_alongside_the_general_one(tmp_path: Path) -> None:
    """SAD 6.2 names `general-core` and the jurisdiction suite together.

    Run against a copy of the real module and then imported, so what is checked
    is the registry the scaffold's output actually builds.
    """
    scaffold = load(SKILLS / "raun-suite" / "scripts" / "new_suite.py", "skill_new_suite")

    copied = tmp_path / "suites.py"
    shutil.copy(ROOT / "draupnir" / "raun" / "suites.py", copied)
    scaffold.scaffold(
        name="cim-gbr",
        version="2026.01",
        jurisdiction="GBR",
        applies_to=("adapter", "merged", "quantised"),
        gates=("E1", "E2", "E3", "E4", "E5", "E6"),
        tasks=("uk-legislation", "hansard-qa"),
        rationale="The GBR jurisdiction suite of SAD 6.2.",
        suites=copied,
        tests=None,
    )

    module = load(copied, "scaffolded_suites")
    registry = module.default_registry()

    assert [suite.name for suite in registry.resolve("adapter", "GBR")] == [
        "general-core",
        "cim-gbr",
    ]
    # Specific, or it is not a jurisdiction suite.
    assert [suite.name for suite in registry.resolve("adapter", "IRL")] == ["general-core"]
    assert module.CIM_GBR.key == "cim-gbr/2026.01"

    # A suite is immutable once results exist against it.
    amended = module.Suite(
        name="cim-gbr",
        version="2026.01",
        applies_to=module.CIM_GBR.applies_to,
        gates=("E1",),
        jurisdiction="GBR",
    )
    with pytest.raises(module.SuiteError, match="already registered"):
        registry.register(amended)


def test_the_suite_scaffold_refuses_a_kind_that_cannot_be_evaluated(tmp_path: Path) -> None:
    """The TRAINED to EVALUATING guard, refused before it can fail a run."""
    scaffold = load(SKILLS / "raun-suite" / "scripts" / "new_suite.py", "skill_new_suite")
    copied = tmp_path / "suites.py"
    shutil.copy(ROOT / "draupnir" / "raun" / "suites.py", copied)

    with pytest.raises(scaffold.SuiteScaffoldError, match="cannot evaluate corpus_raw"):
        scaffold.scaffold(
            name="cim-irl",
            version="2026.01",
            jurisdiction="IRL",
            applies_to=("corpus_raw",),
            gates=("E1",),
            tasks=(),
            rationale="Nonsense.",
            suites=copied,
            tests=None,
        )


# ---------------------------------------------------------------------------
# cim-release
# ---------------------------------------------------------------------------


def test_a_complete_release_is_refused_for_nothing(tmp_path: Path) -> None:
    """The worked example, built from the real modules and checked by the real path."""
    preflight = load(SKILLS / "cim-release" / "scripts" / "preflight.py", "skill_preflight")
    release = preflight.build_example(tmp_path / "release")

    assert preflight.refusals(release, released_at=preflight.AT) == []

    manifest = release.package.manifest(released_at=preflight.AT)
    # AC-F10: every artefact and its digest, in one manifest.
    assert set(manifest["artefacts"]) == {
        "modelCard",
        "sbom",
        "lineageAttestation",
        "trainingContentSummary",
    }
    assert manifest["artefactSha256"] == release.package.artefact_sha256


@pytest.mark.parametrize(
    ("how", "expected"),
    [
        ("partitioned", "PARTITIONED"),
        ("sole-approver", "both submitted and approved"),
        ("unapproved", "no usable approval"),
        ("unevaluated-format", "never evaluated: mlx4"),
        ("tampered", "not the one the gates passed"),
    ],
)
def test_each_refusal_stands_and_names_its_cause(tmp_path: Path, how: str, expected: str) -> None:
    """Five ways a release is stopped, and each says which one it was.

    Rebuilt per case: `tampered` rewrites the artefact on disk, and a
    demonstration whose cases depend on their order proves whatever it was run
    in.
    """
    preflight = load(SKILLS / "cim-release" / "scripts" / "preflight.py", "skill_preflight")
    release = preflight.broken(preflight.build_example(tmp_path / how), how)

    problems = preflight.refusals(release, released_at=preflight.AT)

    assert problems, f"{how} was not refused"
    assert any(expected in problem for problem in problems), problems


def test_the_release_demonstration_runs_as_a_command(tmp_path: Path) -> None:
    """The command in SKILL.md, run the way the document says to run it."""
    result = subprocess.run(  # noqa: S603 -- fixed argument vector, no shell
        [
            sys.executable,
            str(SKILLS / "cim-release" / "scripts" / "preflight.py"),
            "--demo",
            "--workdir",
            str(tmp_path / "demo"),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "complete release: 0 refusal(s)" in result.stdout
    assert "UNREFUSED" not in result.stdout
