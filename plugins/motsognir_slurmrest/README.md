# `veldris-draupnir-slurmrest`

The Slurm `ScheduleDriver` over `slurmrestd` (`motsognir.slurmrest/v1`).

The same interface as `veldris-draupnir-slurm` and a different way of reaching
the scheduler. That one shells out to `sbatch`, `squeue`, `sacct` and
`scancel`; this one submits over HTTP. Placement, array concurrency and retry
belong to `draupnir.motsognir` either way, and the import contracts make that
structural rather than a convention.

## Where it runs

On a host with no Slurm client tools, which at Sindri is the host the control
plane actually runs on. VLD-INF-SINDRI-001 Rev 3.3 installs `slurm-wlm` on
REGIN and `slurmd` on the three appliances; ALVISS gets neither, and the
control plane runs in a distroless container with no shell to run them from.

Installing the shim instead is the right answer on a host that does have the
binaries. They are alternatives, not a replacement.

## Three details worth knowing

**The token is never held.** It arrives as a callable, asked for a value at the
moment of each request, so the object can be constructed and logged without a
credential in it. SVALINN issues it as a lease with an expiry; a driver that
cached the string would outlive the lease and turn an expiry into a 401 nobody
could explain.

**The HTTP client is injected.** An outbound call declares a destination, a
purpose, a run and an approving policy, and that broker is SVALINN's, which a
driver may not import. So the caller supplies the client and where it came from
is the composition root's business. The default is a plain one.

**The queue forgets.** `GET /slurm/…/job/{id}` answers about the queue, and a
job finished more than `MinJobAge` ago is not in it. Every poll falls through
to `GET /slurmdb/…/job/{id}`, which remembers, and only a job neither knows is
reported as unknown. This is the same two-step the shim performs against
`squeue` and `sacct`.

## What it does not do

Read logs over the API: `slurmrestd` has no endpoint for them. It reads the
file the job was told to write, which works when the plan's working directory
is on the shared vault — where VLD-INF-SINDRI-001 puts run output — and
returns nothing when the directory is local to an appliance.
