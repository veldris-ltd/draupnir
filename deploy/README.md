# deploy

Commissioning and stage 4 of the pipeline in SAD 11H, run on ALVISS.

| File | Stage | Notes |
|---|---|---|
| `install.sh` | commissioning | Puts the three control plane units on a host that has never had them |
| `rollout.sh` | 4.2 | Pulls the image per unit and restarts the rootless units |
| `rollback.sh` | 4.4 | Returns the units to the previous revision. The schema stays forward |
| `lib.sh` | — | The units, which image each runs, and the service manager. Sourced by all three |
| `units/` | — | Two templates per unit, systemd and launchd, and the wrapper both start |

The units are rootless Podman containers under the host's user service
manager, matching AC-Q7 and SAD 11.1. The scripts take a revision rather than
reading one, so a manual rollback from a terminal is the same operation the
pipeline performs.

**Two service managers, one container.** SAD Decision S3 puts the control
plane on ALVISS, and VLD-INF-SINDRI-001 Rev 3.3 section 6 makes ALVISS a Mac
mini M4 Pro. So there is a systemd user unit and a launchd user agent for each
of the three, and `install.sh` renders whichever the host has. Everything
AC-Q7 asserts is a property of the container `units/draupnir-run.sh` starts,
which is identical on both, and `lib.sh` is the only file that names a manager
at all: `rollout.sh` and `rollback.sh` run unchanged on either.

On macOS the agents live in `~/Library/LaunchAgents`, are labelled
`com.veldris.draupnir.<part>`, and write to `~/Library/Logs/draupnir` because
launchd has no journal. `podman` there talks to a Linux virtual machine, so
`podman machine` must be running; the wrapper starts it if it is not, and
`install.sh --check` refuses to proceed without it.

## Commissioning a site

`rollout.sh` swaps the image on units that must already exist. `install.sh`
is what creates them, and it is the first thing run on a new ALVISS:

```bash
./deploy/install.sh --check                      # preflight only, changes nothing
./deploy/install.sh --revision <sha> --site sindri
```

Then put the credentials in `~/.config/draupnir/secrets.env` and restart the
units. The installer creates that file empty at 0600 and never writes to it:
secrets are brokered by SVALINN and are not configuration (SAD 11.1 step 5).

`--dry-run` prints every action without taking it. Re-running is safe and is
the supported way to finish a partial install.

### What it installs

`draupnir-api`, `draupnir-worker` and `draupnir-web`, and nothing else.

It does **not** install PostgreSQL or MinIO. SAD 7.2 puts both on ANDVARI as
existing instances, so `install.sh` checks that they answer and stops if they
do not. An installer that stood up its own database would give the site a
second one, and the ledger would be in whichever the API happened to reach.

It does **not** build the estate. The appliances, the fabric and the Slurm
controller on REGIN are VLD-INF-SINDRI-001 and out of scope (SAD 1.3). Sindri
is a site, not a host: what installs here is the control plane that SAD 468
places on "ALVISS at Sindri".

### Which image each unit runs

Two images, three units. The pipeline's stage 3.1 builds `api` and `web`; the
worker is the API image with a different entry point, because it is the same
application running a different process.

That mapping lives in `lib.sh` and nowhere else. It used to live in two
scripts independently, both of which had it wrong -- they resolved
`draupnir-worker` to an image of that name, which nothing builds, so a rollout
would have failed on the pull during a release.
`tests/contract/test_deploy.py` now asserts every unit resolves to an image
the workflow actually builds.

### After a reboot

`rollout.sh` publishes a revision by setting `DRAUPNIR_IMAGE_<unit>` in the
user manager's environment, and that environment does not survive a reboot.
The wrapper in `units/draupnir-run.sh` therefore records each reference it
starts and falls back to the recorded one when the variable is absent, so a
host that comes back from a power cut comes back on the revision it was
running.
