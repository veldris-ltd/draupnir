# Contributing to DRAUPNIR

DRAUPNIR is the CIM-56 model factory control plane. The architecture it
implements is VLD-SAD-DRAUPNIR-001 (`docs/build/draupnir-sad.md`); the console
and design system are VLD-UX-DRAUPNIR-001 (`docs/build/draupnir-ux.md`). When
this document and the SAD disagree, the SAD is right and this document is a
bug.

---

## 1. One command

```bash
make dev
```

That is the whole of it. On a clean machine it installs the toolchain, starts
PostgreSQL and MinIO, applies the migrations, seeds a realistic dataset, and
leaves the API and the console running.

| | |
|---|---|
| API | <http://127.0.0.1:8000> |
| API docs | <http://127.0.0.1:8000/docs> |
| Console | <http://127.0.0.1:5173> |
| MinIO console | <http://127.0.0.1:9001> |

On Windows, where `make` is not present:

```powershell
.\make.ps1 dev
```

Both are thin wrappers around `tasks.py`, which holds the single
implementation of every task. `make`, `make.ps1` and the pipeline therefore run
identical commands, and a task that works locally works on the runner.

Run `make` with no target, or `python tasks.py --list`, for the full list.

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12 or later | Only to bootstrap. `uv` installs and pins 3.12 itself |
| Node | 20 or later | |
| pnpm | 9.12 | Pinned in `web/package.json` under `packageManager` |
| Docker | any recent | PostgreSQL, MinIO, the integration tests and the image build |
| uv | 0.5 or later | Optional. `make dev` installs a project-local copy into `.uv-bootstrap/` if it is missing |

The recommended way to install `uv` properly is the official installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

pnpm does not have to be on `PATH`. The task runner uses one if it finds one,
falls back to `corepack`, and falls back again to `npm exec` at the pinned
version. All three routes end at the version in `packageManager`, which is the
single source of truth for it.

The fallbacks earn their place. A globally installed pnpm lives in the npm
global prefix, which a Python installed from the Microsoft Store cannot see at
all — its file APIs are redirected by the app container, so the task runner
finds nothing while `pnpm` works perfectly in the same terminal. Corepack
covers that case, but Node 25 removed corepack from the distribution, leaving
npm as the floor.

### The seeded dataset

`make seed` writes a deterministic dataset: 2 sites, 6 sources, 12 runs
covering every run state, 3 releases and 400 hash-chained ledger entries. It is
reproducible, so two developers see identical identifiers and a screenshot in a
bug report means something.

To start over:

```bash
make reset-db && make seed
```

`reset-db` drops and recreates the schema. It has to: the `ledger_entry` table
refuses `DELETE` and `TRUNCATE` by design, so there is no gentler way to clear
it, and that is the correct behaviour rather than an inconvenience.

---

## 2. The rules the build enforces

Three things are enforced mechanically rather than by review. Each has a reason
recorded in the SAD.

### Dependencies point inward (SAD 11B)

```
draupnir/api                edge          knows HTTP, no domain logic
draupnir/<module>           modules       HODD, GLEIPNIR, MOTSOGNIR, ...
draupnir/core
    infrastructure          knows the technology, substitutable
    application             knows the workflow
    domain                  knows the invariants, no framework imports
draupnir/interfaces         ports         knows interfaces, never implementations
```

`.importlinter` holds the contracts and `make imports` runs them. A domain
module that imports `fastapi`, or a core module that names a driver, fails the
build. The contracts were written before the first feature module so the rule
is enforced from the first import rather than asserted afterwards.

### The database enforces its own invariants (SAD 11C)

| Constraint | Enforcement |
|---|---|
| `ledger_entry` accepts INSERT only | Trigger rejecting UPDATE, DELETE and TRUNCATE |
| `release` requires an `approval_id` | Foreign key, NOT NULL |
| Site scope on every scoped query | Row level security plus a session variable |

A constraint that lives only in the application is a constraint a migration
script can bypass. `tests/integration/test_schema_constraints.py` attacks each
one with raw SQL.

The development database connects as an unprivileged role on purpose. A
PostgreSQL superuser bypasses every row level security policy, so seeding as
one would make site isolation pass locally and fail in production.

### Generated clients stay generated (AC-Q2)

`draupnirctl/_generated.py` and `web/packages/api-client/src/generated/` are
written by `make clients` from `docs/api/openapi.json`. The pipeline
regenerates them and fails on any diff. Editing either by hand fails the build,
because a hand edited client is the most common way a generated interface
quietly stops being generated.

If you change an endpoint:

```bash
make openapi     # re-export the contract from the application
make clients     # regenerate both clients
```

and commit all three. `make openapi-diff` then checks the change against
`docs/api/openapi.released.json` and refuses a breaking one: within `/v1`,
changes are additive only.

---

## 3. Testing

| Level | Command | Scope |
|---|---|---|
| Unit | `make test-unit` | Pure domain logic. 90% statement coverage on `core/` |
| Property | `make test-property` | Ledger invariants. 500 examples minimum |
| Contract | `make test-contract` | Every driver against the conformance harness |
| Integration | `make test-integration` | Ephemeral PostgreSQL and MinIO, real migrations |
| Frontend | `make test-frontend` | Vitest and Testing Library |
| End to end | `make test-e2e` | Playwright, the four journeys |
| Accessibility | `make test-a11y` | axe on every route, zero serious or critical |
| Visual | `make test-visual` | Storybook snapshots, diff gate |

Only the integration level needs Docker. Everything else runs on a machine with
nothing but Python and Node.

`make ci` runs every stage in pipeline order. Run it before opening a pull
request; it is the same set of commands the runner executes.

### Visual regression baselines

Screenshots are platform specific, so a baseline recorded on a developer's
machine is not the one the runner will diff against. The visual spec records a
missing baseline rather than failing on it, and annotates the file to commit;
every run after that is a real diff gate. Commit the baselines the runner
records on its first green build.

To check the gate still bites, change a component and run `make test-visual`.

### Dependency audit

`make audit` runs `pip-audit` against the exported lockfile, not the
environment, and `pnpm audit` at `--audit-level high`.

`web/package.json` carries a `pnpm.overrides` block. Those are security fixes
pulled into transitive dependencies that their parents have not yet picked up,
not convenience pins. Each one exists because the audit failed without it;
remove one only when the parent has moved on, and re-run `make audit` to prove
it.

The CycloneDX SBOM for the frontend is produced by `cdxgen`, run through `npx`
at a pinned version rather than installed as a devDependency. Two reasons: it
is a build tool rather than something DRAUPNIR ships, and `cyclonedx-npm`
shells out to `npm ls`, which cannot read a pnpm workspace at all.

### Empty harnesses

Several harnesses exist before the thing they test. The four Playwright journey
specs, the conformance harness and the Storybook visual regression project are
all wired and running against nothing yet. This is deliberate: an empty harness
that runs is worth more than a good harness added at the end.

The journey specs are marked `fixme` rather than left failing. UX Prompt UX-0
asks for specs that fail with a clear "not implemented" message; a permanently
red pipeline teaches people to ignore it, and AC-Q1 requires every stage to run
on main. The specs exist, run, and report by name. Prompts UX-2 to UX-4 replace
the bodies and remove the markers.

---

## 4. Making a change

```bash
git switch -c <topic>
make format          # ruff and prettier
make ci              # everything the runner will run
git commit
```

Conventions worth knowing before the first review comment:

- **Timestamps are offset-aware.** `ruff` rejects `datetime.now()` without a
  timezone. RFC 3339 with an explicit offset, everywhere (SAD 11E.2).
- **Identifiers are UUIDv7.** `draupnir.core.domain.identifiers.new_id()`.
  Use `id_at()` where an identifier must be reproducible.
- **Errors are RFC 9457 problem documents.** Raise `ProblemError` from the edge.
  No bare 500 reaches a client.
- **A forge is a site; an appliance is a node.** They are not interchangeable
  (SAD 11A.2, Decision S12). The eslint configuration refuses the confusion in
  user-facing strings.
- **Migrations are forward only.** The generated `downgrade()` raises. Recovery
  is a restore plus a new forward migration.

### Commits and secrets

`make hooks` installs the pre-commit hooks, including the gitleaks scan
required on every commit by AC-Q3. Install them once, on first clone.

---

## 5. The pipeline

`.github/workflows/ci.yaml` implements SAD 11H stage by stage, each named for
the figure so that a red build names the stage that failed. No stage carries
`continue-on-error` and none is conditional: AC-Q1 requires that none is
skippable on main.

```
1 STATIC    ruff -> mypy -> eslint + tsc -> import-linter -> gitleaks -> audit -> SBOM
2 TEST      unit -> property -> contract -> integration -> API contract ->
            frontend -> e2e -> a11y -> visual regression
3 BUILD     aarch64 distroless images -> client regeneration -> SBOM -> sign
4 DEPLOY    migrate (dry run first) -> deploy -> smoke -> rollback on failure
```

Stage 4 lives in `.github/workflows/deploy.yaml` and runs only after a green
pipeline on main.

### Images

`make images` builds `linux/arm64` images from a distroless base, running as
uid 65532 (AC-Q7). On an x86-64 development machine this needs QEMU via
`docker buildx`, and is slow; the runner on ALVISS is aarch64 and builds
natively. Nothing in the development stack depends on these images, so a slow
image build never blocks local work.

---

## 6. Layout

```
draupnir/           domain and application, the edge, and the eleven modules
  core/             ledger, state machine, sites, registry, plug-in loader
  interfaces/       the seven Protocols and the conformance harness
  api/              FastAPI edge: routers, schemas, guards, error mapping
  hodd/ gleipnir/ motsognir/ hamarr/ brisingamen/ raun/ skidbladnir/
  svalinn/ gullinbursti/ megingjord/
plugins/            first-party drivers, each an installable package
draupnirctl/        the generated CLI
web/                JARNGREIPR design system and the console
skills/             development skills (SAD 11G)
migrations/         forward-only Alembic migrations
scripts/            seed, OpenAPI export and diff, client generation, smoke
deploy/             rollout and rollback
docs/               architecture, this guide, acceptance evidence
tests/              unit, property, contract, integration
```

Each module package carries its responsibility and its "must not" in the
package docstring, taken from SAD 5.2. Read it before adding to one.
