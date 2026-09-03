"""DRAUPNIR Core: workflow state machine, run registry, ledger, event bus, loader.

Layered per SAD 11B. Dependencies point inward only, and `.importlinter`
enforces it rather than convention.

Owns: The lifecycle of SAD 6.1, the hash chained ledger, the run projection,
the site registry, the plug-in loader, and the orchestrator that turns a
requested state change into a guarded, recorded, projected one.
Must not: Know any framework, format or merge method by name (SAD 5.2). The
core names no driver implementation, and an import contract holds it to that.

    domain/          the invariants. Pure, no framework imports
    application/     the orchestrator: guard, act, write ledger, project
    infrastructure/  models, repositories, configuration -- substitutable
"""
