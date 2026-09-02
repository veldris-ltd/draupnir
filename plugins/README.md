# plugins

First-party drivers, each an installable package with its own `pyproject.toml`.

A driver implements one of the seven Protocols in `draupnir/interfaces/`,
declares its capabilities, and passes the conformance harness unmodified. The
`draupnir-driver` skill (SAD 11G) scaffolds a conforming package.

Nothing here may import from `draupnir.api`, and the
`A driver sees the interfaces, never the core` contract in `.importlinter`
forbids a driver reaching `draupnir.core` or any module package at all. That
is the line between a driver and the policy it serves: MOTSOGNIR owns
placement, array concurrency and retry, and HAMARR owns base selection and the
checkpoint budget, so neither the Slurm driver nor the LLaMA-Factory driver
can make those decisions even by accident.

| Package | Interface | Entry point |
|---|---|---|
| `local_subprocess` | `ScheduleDriver` | `motsognir.local_subprocess/v1` |
| `motsognir_slurm` | `ScheduleDriver` | `motsognir.slurm/v1` |
| `hamarr_llamafactory` | `TrainDriver` | `hamarr.llamafactory/v1` |
| `targz_export` | `ExportDriver` | `skidbladnir.targz/v1` |

Adding a package here and installing it puts it in front of the conformance
suite automatically. There is no list in the tests to keep up to date.
