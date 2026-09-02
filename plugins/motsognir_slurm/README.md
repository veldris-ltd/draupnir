# `veldris-draupnir-slurm`

The Slurm `ScheduleDriver` (`motsognir.slurm/v1`).

Submits a rendered `JobPlan` with `sbatch`, observes it with `squeue` and then
`sacct`, cancels it with `scancel`. It knows nothing about what a job computes:
placement, array concurrency and retry belong to `draupnir.motsognir`, and the
import contracts make that structural rather than a convention.

## Where it runs

On a host with the Slurm client tools on the path. The driver talks to Slurm
through its command line rather than its C API so that the control plane image
carries no Slurm build.

## Three details worth knowing

`squeue` forgets a job once `MinJobAge` has passed, and its silence is not
evidence that a job never existed. Every poll falls through to `sacct`, and
only a job neither knows is reported as unknown.

`sacct` returns the job and its `.batch` and `.extern` steps. The job's own row
is the one whose `JobID` has no dot; the batch step's state diverges from the
job's exactly when something has gone wrong.

An array element is `<job>_<index>` and that is the identifier throughout.
Passing the array's identifier where an element's belongs cancels fifty six
jobs instead of one.

## Configuration

| Field | Default | For |
|---|---|---|
| `sbatch`, `squeue`, `sacct`, `scancel` | the bare names | pointing at a non-standard install |
| `timeout` | `30.0` | how long before a Slurm command is presumed wedged |
| `submit_arguments` | `()` | site arguments such as `--account` |
