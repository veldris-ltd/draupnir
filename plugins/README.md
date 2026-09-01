# plugins

First-party drivers, each an installable package with its own `pyproject.toml`.

A driver implements one of the seven Protocols in `draupnir/interfaces/`,
declares its capabilities, and passes the conformance harness unmodified. The
`draupnir-driver` skill (SAD 11G) scaffolds a conforming package.

Nothing here may import from `draupnir.api`; `.importlinter` gains a contract
for this directory with the first plugin.
