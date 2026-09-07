# deploy

Commissioning and stage 4 of the pipeline in SAD 11H, run on ALVISS.

| File | Stage | Notes |
|---|---|---|
| `install.sh` | commissioning | Puts the three control plane units on a host that has never had them |
| `rollout.sh` | 4.2 | Pulls the image per unit and restarts the rootless systemd units |
| `rollback.sh` | 4.4 | Returns the units to the previous revision. The schema stays forward |
| `lib.sh` | — | The units, and which image each one runs. Sourced by all three |
| `units/` | — | The unit templates and the wrapper they start |

The units are rootless Podman containers under the user systemd instance,
matching AC-Q7 and SAD 11.1. The scripts take a revision rather than reading
one, so a manual rollback from a terminal is the same operation the pipeline
performs.

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
