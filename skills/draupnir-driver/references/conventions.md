# Driver conventions

What the code in this repository actually does, as of the JARNGREIPR and
console builds. Where this differs from the SAD, the code is right and the
specification is amended.

## The seven extension points

Entry point group, Protocol, and what is installed today.

| Group | Protocol | Installed |
|---|---|---|
| `draupnir.train` | `TrainDriver` | `hamarr.llamafactory/v1` |
| `draupnir.merge` | `MergeDriver` | `brisingamen.mergekit/v1` |
| `draupnir.eval` | `EvalDriver` | `raun.lmeval/v1` |
| `draupnir.export` | `ExportDriver` | `skidbladnir.quantise/v1`, `skidbladnir.targz/v1` |
| `draupnir.schedule` | `ScheduleDriver` | `motsognir.slurm/v1`, `motsognir.local_subprocess/v1` |
| `draupnir.store` | `StoreDriver` | — |
| `draupnir.policy` | `PolicyDriver` | — |

`draupnir.store` and `draupnir.policy` have no installed driver. The Protocols
exist and the loader knows the groups; nothing is registered, because HODD
addresses its own store and GLEIPNIR decides its own policy. Writing one is a
larger piece of work than this skill covers — the scaffold refuses those two
groups rather than generating a driver whose shape it is guessing at.

`draupnir.schedule` is also refused. A `ScheduleDriver` submits, polls, cancels
and reads logs; it has no `render` and no `collect`, and `check_schedule_driver`
is a different suite. Copy `plugins/motsognir_slurm/` instead.

## Names

`namespace.implementation/vMAJOR`, lowercase, parsed by
`draupnir.interfaces.naming.InterfaceName`. The major version is in the name so
that two majors can be installed at the same time, and the loader accepts the
current one and the one before it (`supported_majors`). A breaking change is a
new entry point, not an edit to an existing one.

The namespace is the module the driver serves — `hamarr` trains, `raun`
evaluates, `skidbladnir` exports, `motsognir` places, `brisingamen` merges.

Distribution names are `veldris-draupnir-<thing>`; the importable module drops
the vendor prefix and uses underscores, so `veldris-draupnir-zip-export`
imports as `draupnir_zip_export`.

## Capabilities

`RunSpec.capabilities_for(group)` is a small, readable table, not an inference.
It says what a specification demands of a driver in each group:

| Group | Demands |
|---|---|
| `draupnir.train` | the method, the precision, and `multinode` above one node |
| `draupnir.export` | every format in `spec.release.formats` |
| `draupnir.eval` | every suite in `spec.evaluate.suites` |
| `draupnir.schedule` | `multinode` above one node, `array` above one concurrent |
| everything else | nothing |

Two consequences worth stating, because both have surprised someone:

- A **train driver must declare a precision**. `bf16` is a capability, not a
  parameter, and a driver that declares `lora` alone can never be selected for
  the SAD 6.2 sample. The scaffold refuses to generate one.
- A **merge driver's capabilities are never demanded**. Declare them anyway;
  they are how an operator finds the driver, and the table can grow.

`capabilities` is a `frozenset` on the driver so that the core can check a
specification against it without being able to change it.

## The four methods

```python
def validate(self, spec: RunSpec) -> list[ValidationError]: ...
def render(self, spec: RunSpec, workdir: Path) -> JobPlan: ...
def parse_progress(self, line: str) -> ProgressEvent | None: ...
def collect(self, workdir: Path) -> RunArtefacts: ...
```

**`validate` returns; it never raises.** It returns *every* problem, not the
first. The harness calls it and turns an exception into a finding.

**`render` is pure.** No file, no socket, no clock, no environment. The harness
renders twice back to back, renders a third time after 50 ms, renders inside a
`no_network()` block that replaces `socket.socket`, and diffs the working
directory. The third render is there because the Windows clock advances in
about 15.6 ms steps, so a wall-clock read passes a back-to-back pair.

If `render` needs a value the specification does not carry, the value belongs
in the specification — where it is hashed into the run identity and can be
replayed. That is the whole argument.

**`parse_progress` is total and pure, and stamps no time.** The harness feeds
it the empty string, a well-formed line, prose, `"\x00\x01 binary rubbish"` and
4096 bytes of `a`, twice each. Returning `None` is the right answer for a line
that carries no event. The caller stamps the event, so replaying a captured log
produces the events it produced live.

**`collect` reads and never writes.** The harness snapshots the working
directory before and after. A missing artefact is an empty `RunArtefacts`, not
an exception and not a fabricated entry.

## Types

Everything is frozen, and lives in `draupnir.interfaces.types`. A driver
depends on `draupnir.interfaces` and nothing else in DRAUPNIR — if the
vocabulary lived in the core, every driver would depend on the core and the
extension points would be extension points in name only.

`ProducedArtefact(path, kind, sha256, size)`. `kind` is one of the eight the
database knows: `corpus_raw`, `corpus_curated`, `base_model`, `substrate`,
`adapter`, `merged`, `quantised`, `report`. A ninth is refused when the row is
written, which is after the allocation has been spent.

`JobPlan.canonical()` is what two renders are compared on. `ResourceRequest`
carries the partition, the node count and the GPUs per node.

## Registration

Four places outside the distribution have to learn the driver's name, and only
two of them fail loudly when they do not:

| Where | What | Fails if missing? |
|---|---|---|
| `pyproject.toml` `[tool.uv.sources]` | `<distribution> = { workspace = true }` | yes, at `uv sync` |
| `pyproject.toml` isort `known-first-party` | the module name | yes, at `ruff check` |
| `.importlinter` `root_packages` | the module name | no |
| `.importlinter` `drivers-depend-on-interfaces-only` `source_modules` | the module name | **no** |

The last is the dangerous one. That contract lists its drivers by name rather
than matching a pattern, so a driver missing from it is simply not checked, and
nothing goes red. The scaffold writes all four.

`[tool.uv.workspace] members = ["plugins/*"]` is a glob and needs nothing.

## Signing

A distribution loads only if the signing manifest covers its wheel (AC-S7).
`[tool.hatch.build.targets.wheel] packages = ["<module>"]` is what the manifest
is computed over. `DRAUPNIR_DEV=1` is the only way to load an unsigned driver,
and it names itself in the log on every start that uses it.

## What a driver may import

`draupnir.interfaces` and the standard library. The import contract forbids
`draupnir.core`, `draupnir.api`, and every module package by name. This is
AC-N9 — "a new export format in under two hundred lines with no core file
modified" — held as a rule rather than observed as a result.

Third-party dependencies are allowed but rarely wanted: the control plane has
no GPU and must validate a specification without one, so the trainer, the
merger and the quantiser are installed on the appliance that runs the job, not
here. `plugins/hamarr_llamafactory/` does not depend on LLaMA-Factory.
