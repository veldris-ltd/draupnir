"""MOTSOGNIR, the first of dwarves: job dispatch and placement.

Scheduler drivers, array concurrency, retry policy. SAD 5.2.

Owns: Scheduler drivers, placement policy, array concurrency, retry and backoff.
Must not: Know what a job computes.

| Module | What it decides |
|---|---|
| `placement` | Where a run may execute, and when it must not run at all |
| `arrays` | N elements, M concurrent, and how one is retried alone |
| `retry` | Whether to try again, and after how long |

The drivers themselves are plug-ins (`plugins/motsognir_slurm`,
`plugins/local_subprocess`), because SAD 8.2 makes `draupnir.schedule` an
extension point and an import contract forbids a driver reaching back into
this package. A driver submits, polls and cancels; every decision above is
made here, where it can be tested without a scheduler.
"""
