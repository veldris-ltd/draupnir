# skills

The six development skills of SAD 11G. Authored during the build so that they
match the conventions being decided, rather than afterwards, when they would
document what someone remembers.

Decision S14: a skill is a deliverable, not a by-product. So each one ships an
**executable scaffold**, not a description of one, and CI runs that scaffold
and puts the output through the real gate. A skill whose conventions have moved
fails `tests/contract/test_skills.py` rather than failing the next person who
uses it.

| Skill | Use it when | The gate its output passes |
|---|---|---|
| [draupnir-driver](draupnir-driver/SKILL.md) | adding a train, merge, eval or export driver | `check_job_driver`, the published conformance harness |
| [draupnir-endpoint](draupnir-endpoint/SKILL.md) | adding an operation to the API | mounted on a real `FastAPI`, guarded, and described in the OpenAPI document |
| [draupnir-migration](draupnir-migration/SKILL.md) | changing the schema | forward-only, no destructive DDL, row level security on anything site scoped |
| [jarngreipr-component](jarngreipr-component/SKILL.md) | adding a design-system component | the token linter, the story-shape test, and a real axe run in jsdom |
| [raun-suite](raun-suite/SKILL.md) | a jurisdiction needs its own gates | registered, resolved alongside `general-core`, and immutable under its version |
| [cim-release](cim-release/SKILL.md) | releasing a CIM-56 model | `skidbladnir.publish`, with every refusal it raises |

## The shape

```
skills/<name>/
  SKILL.md            frontmatter (name, description), then what it does and what it refuses
  references/*.md     the conventions, as the code has them
  scripts/*.py        the scaffold, or the check
```

Each script is a command:

```bash
python skills/draupnir-driver/scripts/new_driver.py --help
python skills/draupnir-endpoint/scripts/new_endpoint.py --help
python skills/draupnir-migration/scripts/new_migration.py --check
python skills/jarngreipr-component/scripts/new_component.py --help
python skills/raun-suite/scripts/new_suite.py --help
python skills/cim-release/scripts/preflight.py --demo
```

Every script name is distinct rather than six copies of `scaffold.py`: `mypy`
resolves a bare script by its basename, and six modules called `scaffold` are
six modules with one name.

## AC-Q8

```bash
make test-skills
```

Runs each skill's demonstration and checks the artefact it produced with the
same gate the pipeline runs — no edit in between, and no second copy of the
rules. `tests/contract/test_skills.py` is the whole of it, and it runs as part
of `make test-contract`.

Two worked examples are committed rather than generated into a temporary
directory, because they are more useful checked in:

- `web/packages/jarngreipr/src/example/PoolStatus.*` — the component scaffold's
  output, unedited. It is inside the package, so the token linter, `tsc`,
  Storybook and Vitest all cover it; `PoolStatus.a11y.test.tsx` runs real axe
  over all seven states; and the contract test regenerates the three files and
  compares them byte for byte.
- The release demonstration in `cim-release`, which builds a complete release
  from the real modules and then breaks it five ways.

## Reading order

If you are new to the codebase, the reference documents are the fastest way in:

1. `draupnir-endpoint/references/conventions.md` — the API, the read model, and
   what is generated from what.
2. `draupnir-migration/references/conventions.md` — the schema and the three
   constraints the database enforces.
3. `draupnir-driver/references/conventions.md` — the seven extension points and
   the purity contract.
4. `jarngreipr-component/references/conventions.md` — the design system and its
   seven states.
5. `raun-suite/references/conventions.md` — the gates, and who decides what.
6. `cim-release/references/procedure.md` — what a release is, and every refusal
   between a gated artefact and a published one.
