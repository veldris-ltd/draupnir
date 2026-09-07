# Deploying DRAUPNIR to the Sindri Forge

How the control plane reaches ALVISS, and what an operator does when it will
not. For what to do when a running system misbehaves, read
[the runbook](runbook.md) instead: this document ends where that one begins.

> **Sindri is a forge, not a host.** A forge is a site — a set of machines that
> together run one estate. What deploys *at* Sindri is the control plane, and
> SAD Decision S3 puts that on ALVISS rather than on an appliance. Nothing in
> this document installs an appliance.

---

## 1. What runs where

| Host | Runs | Installed by |
|---|---|---|
| **ALVISS** | `draupnir-api`, `draupnir-worker`, `draupnir-web` | `deploy/install.sh`, this document |
| **ANDVARI** | PostgreSQL 16, MinIO, the HODD vault, the image registry | VLD-INF-SINDRI-001, not here |
| **REGIN** | Slurm controller, Loki | VLD-INF-SINDRI-001, not here |
| **DVALIN, DURIN, DAIN** | Executor shims, one container per job | Slurm, per job |
| **Veldris_NXT** | MEGINGJORD federation registry | Its own deployment |

The three units on ALVISS are the whole of what this repository deploys. They
are rootless Podman containers under the user systemd instance (SAD 11.1 step
1, AC-Q7), and they hold no state: everything is in PostgreSQL and MinIO on
ANDVARI, which is why a control plane can be destroyed and rebuilt without
losing a run.

**Two images, three units.** Stage 3.1 builds `draupnir-api` and
`draupnir-web`. The worker is the API image with a different entry point —
the same application running a different process — so there is no third image
to build or sign. `deploy/lib.sh` holds that mapping and the deployment
scripts share it.

### The estate does not exist yet

SAD 1.3 puts the hardware build in VLD-INF-SINDRI-001 and out of scope. Until
it is commissioned there is no three-appliance ring, no Slurm controller, no
NFS vault and no WireGuard link. A control plane installed ahead of the estate
is a legitimate state and the system is built for it:

- `DRAUPNIR_VAULT_ROOT` empty means this installation has no vault, and the
  vault checks are skipped rather than alarming hourly about an export that
  was never there.
- Dispatch suspends with no Slurm controller. Queued runs stay QUEUED and
  nothing is lost (runbook §2).
- Releases refuse to anchor with no federation link, and say so (runbook §7).

Install with `--skip-dependency-check` if ANDVARI is not up yet, and expect
`/readyz` to report the missing dependency rather than the API to be absent.

---

## 2. Before the window

Run the preflight from ALVISS, as the service account. It changes nothing:

```bash
./deploy/install.sh --check
```

It verifies, and stops on the first thing that is wrong:

| Check | Why it is fatal |
|---|---|
| `podman` present | The units are rootless containers by requirement (AC-Q7) |
| Not running as root | Root's systemd instance is not the service account's |
| A user systemd instance exists | There is nothing to install the units into |
| PostgreSQL answers on ANDVARI | The ledger lives there and this installer does not create one |
| MinIO answers on ANDVARI | Artefacts live there, likewise |

Lingering is reported as a warning rather than an error, because a
commissioning session is usually the session still logged in. Enable it, or
the control plane stops when you log out:

```bash
loginctl enable-linger "$(id -un)"
```

### What you need to hand

- **The revision.** A git SHA the pipeline built, signed and pushed to the
  registry. Not a branch name: the units run an immutable reference.
- **The credentials**, from SVALINN. They go in `secrets.env` (§4) and are
  never written by any script here.
- **A maintenance window.** Restarting the control plane interrupts the
  console and the API. It does not interrupt training: runs in flight continue
  on the appliances and nothing on the estate notices (runbook §1).

---

## 3. Commissioning a new ALVISS

```bash
./deploy/install.sh --revision <sha> --site sindri
```

That creates, in order: the configuration directory, `draupnir.env`, an empty
`secrets.env`, the wrapper, the three unit files, then pulls the images, seeds
the revision, enables and starts the units, and waits for `/healthz`.

Nothing here is destructive and every step is safe to repeat. **Re-running is
the supported way to finish a partial install** — commissioning is the one
operation nobody gets to rehearse, so it is written to be resumable rather
than to be got right first time.

Add `--dry-run` to print every action without taking it. Read that output
once before the first real run on a new host.

### Where it puts things

| Path | Contents |
|---|---|
| `~/.config/draupnir/draupnir.env` | Non-secret configuration. Generated; safe to regenerate |
| `~/.config/draupnir/secrets.env` | Credentials. Created empty at 0600, never written to |
| `~/.config/systemd/user/draupnir-*.service` | The three units |
| `~/.local/libexec/draupnir/draupnir-run.sh` | The wrapper the units start |
| `~/.local/state/draupnir/image-*` | The image reference each unit last started |

---

## 4. Secrets

The installer creates `secrets.env` empty at 0600 and never writes to it.
Secrets are brokered by SVALINN and are not configuration (SAD 11.1 step 5),
so the file is the seam between the two rather than a place to keep them.

It needs four values:

```
DRAUPNIR_DATABASE_URL=postgresql+asyncpg://draupnir:...@andvari.veldris.internal:5432/draupnir
DRAUPNIR_DATABASE_URL_SYNC=postgresql+psycopg://draupnir:...@andvari.veldris.internal:5432/draupnir
DRAUPNIR_OBJECT_STORE_ACCESS_KEY=...
DRAUPNIR_OBJECT_STORE_SECRET_KEY=...
```

Two database URLs because the API is async and Alembic is not; they name the
same database through different drivers.

**Connect as the unprivileged `draupnir` role, never as a superuser.** A
superuser bypasses row level security, which silently defeats the site
isolation of SAD 11C constraint 3 — the failure is invisible until a forge
reads another forge's runs.

Restart the units after editing:

```bash
systemctl --user restart draupnir-api draupnir-worker draupnir-web
```

---

## 5. Migrations

Forward only (AC-Q6). Render before applying:

```bash
python tasks.py migrate-dry    # alembic upgrade head --sql
python tasks.py migrate        # alembic upgrade head
```

The pipeline's stage 4.1 does exactly this, in that order, and keeps the
rendered SQL as deployment evidence for 90 days.

**There is no downgrade path and this is deliberate.** Every migration is
additive within a version, so the previous release runs against the newer
schema — which is what makes a rollback of the units safe without a rollback
of the schema. A schema fault is recovered by a restore and a new forward
migration, not by a downgrade nobody has exercised.

---

## 6. Releasing a revision

Normally nobody runs this by hand. A merge to `main` runs the pipeline, and a
green pipeline triggers the deployment (stage 4):

```
4.1  migrate      render, then apply
4.2  rollout      pull the image per unit, restart the units
4.3  smoke        healthz, readyz, ledger chain verification
4.4  rollback     on smoke failure only
```

By hand, on ALVISS, the same operations:

```bash
./deploy/rollout.sh <sha>
python scripts/smoke.py --base-url http://127.0.0.1:8000
```

The rollout deliberately does not wait for the application to be healthy. That
is the smoke stage's job, and conflating the two hides which of the deployment
and the application failed.

### Rolling back

```bash
./deploy/rollback.sh <previous-sha>
```

The units return to the previous revision. The schema stays forward, for the
reason in §5. Confirm with `python scripts/smoke.py --skip-ledger`, which
probes HTTP without asserting the chain — appropriate when you have just
changed what is running underneath it.

---

## 7. Verifying a deployment

```bash
curl -s http://127.0.0.1:8000/healthz     # the process is up
curl -s http://127.0.0.1:8000/readyz      # every dependency, individually
python tasks.py smoke                     # both, plus a chain verification
```

`/healthz` answers as soon as the process is up and says nothing about whether
the control plane can reach anything. `/readyz` reports each dependency
separately and is the one worth reading. Service is expected within 30 seconds
of the process starting (AC-N6).

Then confirm the ledger, which is the only thing whose loss would matter:

```bash
python tasks.py verify-chain --site sindri
```

100,000 entries verify in about four seconds against the sixty second budget
of AC-N5. If it fails, stop and read runbook §6 — a chain that does not verify
puts the site into read only, and the recovery is not a redeploy.

---

## 8. When the deployment is the problem

| Symptom | Cause | Action |
|---|---|---|
| `no image reference` on start | Manager environment empty and no recorded reference | `./deploy/rollout.sh <sha>` |
| Unit restarts every five seconds | Container exits immediately | `journalctl --user -u draupnir-api -n 50` |
| `/healthz` answers, `/readyz` does not | A dependency is unreachable | Read `/readyz`; it names which |
| API 503 on every `/v1` path | PostgreSQL unreachable | Runbook §5 |
| `podman pull` fails during rollout | Revision not in the registry, or unsigned | Confirm the pipeline published it |
| Units vanish after logout | Lingering disabled | `loginctl enable-linger "$(id -un)"` |

### After a reboot

`rollout.sh` publishes a revision by setting `DRAUPNIR_IMAGE_<unit>` in the
user manager's environment, and that environment does not survive a reboot.
The wrapper records every reference it starts and falls back to the recorded
one, so a host that comes back from a power cut comes back on the revision it
was running. If both are absent — a genuinely new host — the unit refuses to
start and says exactly which command fixes it, rather than starting something
arbitrary.

### Removing the control plane

```bash
./deploy/install.sh --uninstall
```

Stops and removes the units, the wrapper and the generated configuration. It
keeps `secrets.env` and the state directory, because it did not create their
contents. It touches neither the database nor the object store: the ledger is
the system of record and an uninstaller is not the right place to lose it.

---

## 9. What is not automated

Honest gaps, so nobody discovers them during a window.

- **Artefact signing needs a key.** Stage 3.4 is a hard failure on `main`
  without `DRAUPNIR_SIGNING_KEY`, deliberately: signing is required there
  (Decision S9) and reporting the absence beats skipping the stage.
- **The deploy environment needs configuring.** `deploy.yaml` targets the
  `sindri` GitHub environment and reads `DRAUPNIR_DATABASE_URL_SYNC` from it.
- **GULLINBURSTI needs a site certificate** from the MEGINGJORD internal PKI
  (SAD 11.1 step 6). Not issued by anything here.
- **The estate is out of scope.** VLD-INF-SINDRI-001 builds the appliances,
  the fabric, the vault export and the Slurm controller.

---

## See also

| Document | For |
|---|---|
| [runbook.md](runbook.md) | A running system misbehaving. Nine failure modes, with what the system already did |
| [CONTRIBUTING.md](CONTRIBUTING.md) | A development machine |
| [deploy/README.md](../deploy/README.md) | The scripts themselves |
| `docs/build/draupnir-sad.md` | §11.1 deployment, §11.2 degraded modes, §5.1 deployable units |
