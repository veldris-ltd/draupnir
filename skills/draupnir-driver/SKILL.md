---
name: draupnir-driver
description: >
  Scaffold a conforming DRAUPNIR plug-in driver — package layout, Protocol
  implementation, capability declaration, conformance test wiring and signing
  manifest. Use when adding a training, merge, evaluation, export, schedule,
  store or policy driver: "add a training driver", "new export format",
  "support framework X", "write a driver for Y".
---

# draupnir-driver

Scaffold a plug-in driver that passes the conformance harness on the first
run, with no file under `draupnir/core/` modified.

## Why a scaffold rather than a description

There are seven extension points and a driver is the shape of work that recurs
most. A driver that looks correct and violates the purity contract is the
expensive failure: it passes review, ships, and produces a run that cannot be
reproduced from its specification. The scaffold removes that class of error by
construction — the generated `render` cannot reach the network or write a file
because it does neither, and the generated test runs the same harness the
pipeline runs.

**Do not hand-write a driver from this document.** Run the scaffold, then edit
the two methods that carry your logic. The parts you would be tempted to write
from memory — the entry point spelling, the capability declaration, the
`ValidationError` shape, the conformance wiring — are the parts that are wrong
when written from memory.

## Use it

```bash
python skills/draupnir-driver/scripts/new_driver.py --group draupnir.export --name skidbladnir.zip/v1 --distribution veldris-draupnir-zip-export --capability zip --artefact-kind quantised --output export.zip --out plugins/zip_export
```

It writes a distribution and registers it:

```
plugins/zip_export/
  pyproject.toml                          entry point, workspace source, wheel target
  README.md                               what it is and what it refuses
  draupnir_zip_export/__init__.py         the driver
tests/contract/test_draupnir_zip_export.py  the conformance run
pyproject.toml                            workspace source, isort first-party name
```

The last one is not tidiness. `[tool.uv.sources]` is what makes `uv sync`
install the member, and `known-first-party` is what puts the conformance
test's import in the right block; without both, the output needs a manual edit
before it lints, which is the one thing this skill claims it will not. Pass
`--no-register` when scaffolding outside the repository.

Then:

```bash
uv sync                    # install the new distribution into the workspace
make test-contract         # the harness runs against it
```

## What the generated driver already gets right

| Contract | How the scaffold satisfies it |
|---|---|
| Decision S5, `render` is pure | The generated `render` builds a command from the specification and returns it. It opens no file, resolves no name and takes no clock reading. |
| SAD 10.3 rule 4, capabilities | `capabilities` is a `frozenset` declared once and used by `validate`, so a specification asking for something undeclared is refused with the demand named. |
| `validate` returns, never raises | It appends to a list and returns it. An operator gets every problem in one round trip, not the first of five. |
| `parse_progress` is total and pure | It returns `None` for anything it does not recognise, and carries no timestamp, so replaying a captured log yields the events it yielded live. |
| `collect` does not mutate | It walks the working directory and hashes what it finds. It creates nothing. |
| AC-N9, no core file modified | The driver is discovered by entry point. Nothing under `draupnir/` changes to admit it. |
| AC-S7, signing | `pyproject.toml` carries the wheel target the signing manifest needs. An unsigned distribution fails to load, and `DRAUPNIR_DEV=1` is the only way past that. |

## What you then write

Two methods, and only two:

- **`validate`** — every reason this driver cannot run the specification. Add
  your checks to the list the scaffold starts. Never raise.
- **`render`** — the command, environment and resources. Stay pure: if you
  need a value that is not in the specification, it belongs in the
  specification.

`parse_progress` and `collect` are generated with a working implementation for
the common shape; change them when your tool's output differs.

## Refusals

**Do not reach outside the specification in `render`.** Looking up the current
tag of a base image, reading an environment variable, stamping the time — each
makes the plan a function of when it was rendered rather than of what was
asked for, and the harness catches all three. If you find yourself wanting one,
the value belongs in the run specification, where it is hashed into the run
identity.

**Do not import from `draupnir.core`.** The import linter enforces it
(`drivers-depend-on-interfaces-only`), and the scaffold adds the new module
to that contract's `source_modules` — a contract that does not name a driver
does not check it. A driver sees `draupnir.interfaces` and
nothing else, which is what makes AC-N9's "no core file modified" hold as a
rule rather than as a result.

**Do not catch and swallow in `collect`.** An artefact that is missing is a
finding, not a silence.

## References

- `references/conventions.md` — the seven extension points, the entry point
  spelling, and what each Protocol requires.

## Verified

`tests/contract/test_skills.py` runs this scaffold into a temporary directory
and puts the result through `check_job_driver` — the same harness the pipeline
runs, with no edit in between. If the conventions move and this skill does not,
that test fails.
