# `veldris-draupnir-slurm`

The Slurm `ScheduleDriver` (`motsognir.slurm/v1`).

Writes a rendered `JobPlan` as a batch script and submits it with `sbatch`,
observes it with `squeue` and then `sacct`, cancels it with `scancel`. It knows nothing about what a job computes:
placement, array concurrency and retry belong to `draupnir.motsognir`, and the
import contracts make that structural rather than a convention.

## Where it runs

On a host with the Slurm client tools on the path. The driver talks to Slurm
through its command line rather than its C API so that the control plane image
carries no Slurm build.

## A job is a script, not an argument list

This used `--wrap` and `--export`, and both were wrong in ways that only appear
against a real Slurm.

`--wrap` takes one shell command and a plan's command is an argument vector, so
joining it with spaces turned `sh -c 'a && b'` into words a shell read
separately. `--export` separates entries with commas and a training
configuration is JSON, so the one variable carrying it arrived as several
malformed ones. And naming variables in `--export` without `ALL` propagates
only those, leaving the job with no `PATH`.

Writing a script removes all three questions rather than answering them, and
leaves the artefact an operator wants when asking what actually ran — beside
the log, in the run's own directory, still there afterwards.

**The `preamble` setting decides where the job's environment comes from**, and
the obvious answer is wrong here. `sbatch` propagates the submitting
environment by default; the submitter is the DRAUPNIR worker in a distroless
container and the job runs on an appliance, so inheriting that `PATH` gives the
job directories that exist nowhere it can land. A site that configures a
preamble — at Sindri, `source /forge/venv/bin/activate` — gets `--export=NONE`
and an environment that is exactly what the script sets. A site that configures
none keeps the default, which is what makes a development machine work.

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
