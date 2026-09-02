# plugins

First-party drivers, each an installable package with its own `pyproject.toml`.

A driver implements one of the seven Protocols in `draupnir/interfaces/`,
declares its capabilities, and passes the conformance harness unmodified. The
`draupnir-driver` skill (SAD 11G) scaffolds a conforming package.

Nothing here may import from `draupnir.api`, and the
`A driver sees the interfaces, never the core` contract in `.importlinter`
forbids a driver reaching `draupnir.core` or any module package at all. That
is the line between a driver and the policy it serves. MOTSOGNIR owns placement,
array concurrency and retry; HAMARR owns base selection and the checkpoint
budget; GLEIPNIR owns every gate threshold; SKIDBLADNIR owns what may be
published. No driver can make those decisions even by accident.

The evaluation driver is the clearest case. `raun.lmeval/v1` reports the value it
measured and leaves `passed` False, because deciding whether a measurement passes
needs the threshold, the baseline and the comparison -- all GLEIPNIR's. A driver
that filled that field in would be a driver that could pass its own evaluation.

| Package | Interface | Entry point |
|---|---|---|
| `local_subprocess` | `ScheduleDriver` | `motsognir.local_subprocess/v1` |
| `motsognir_slurm` | `ScheduleDriver` | `motsognir.slurm/v1` |
| `hamarr_llamafactory` | `TrainDriver` | `hamarr.llamafactory/v1` |
| `brisingamen_mergekit` | `MergeDriver` | `brisingamen.mergekit/v1` |
| `raun_lmeval` | `EvalDriver` | `raun.lmeval/v1` |
| `skidbladnir_quantise` | `ExportDriver` | `skidbladnir.quantise/v1` |
| `targz_export` | `ExportDriver` | `skidbladnir.targz/v1` |

Adding a package here and installing it puts it in front of the conformance
suite automatically. There is no list in the tests to keep up to date.
