# Remedial fixes — estate integration

DRAUPNIR at `e38758a` against the Sindri Forge as it is actually built and
wired.

| Source | Revision |
|---|---|
| `VLD-INF-SINDRI-001` Build, Configuration and Operations Manual | Rev 3.3, 28 August 2026 |
| `VLD-WIR-SINDRI-001` Wiring and Power Sequence Document | Rev 2.0, 28 August 2026 |
| Repository | `veldris/draupnir` @ `e38758a`, branch `dev` |

> **Companion to [remedial-fixes.md](remedial-fixes.md).** That register asks
> whether the platform is internally complete. This one asks a narrower and
> more immediate question: *when somebody follows the build manual and the
> wiring document, does what they end up with run DRAUPNIR?*
>
> The answer is no, and the reasons are specific. Most of them are not defects
> in either document — they are places where the two describe different
> systems and nobody has reconciled them. Each finding therefore says **which
> side the fix belongs on**: the repository, the manual, or both.

---

## 1  The estate, as built

Stated here so this document stands alone. Everything below is from the two
source documents, not from the repository.

| Host | Hardware | Operating system | Role | Fabric 2 |
|---|---|---|---|---|
| **DVALIN** | DGX Spark | DGX OS (Ubuntu) | Appliance, NCCL rank 0, drives CON-A | 10.20.0.11 |
| **DURIN** | DGX Spark | DGX OS | Appliance, rank 1 | 10.20.0.12 |
| **DAIN** | DGX Spark | DGX OS | Appliance, rank 2 | 10.20.0.13 |
| **ANDVARI** | Mac mini M4 Pro | **macOS** | Vault host, NFS, PostgreSQL 16, MinIO, MLflow | 10.20.0.21 |
| **ALVISS** | Mac mini M4 Pro | **macOS** | **DRAUPNIR application host**, MLX eval, CI, Ansible | 10.20.0.22 |
| **REGIN** | Raspberry Pi 5 | Raspberry Pi OS | Slurm controller, dnsmasq, chrony, Prometheus, Grafana, drives CON-B | 10.20.0.5 / 10.10.0.5 |

Facts that matter to the control plane:

- **DNS zone is `sindri.veldris.internal`.** `dnsmasq` on REGIN serves
  `local=/sindri.veldris.internal/` and nothing else. There is no
  `veldris.internal` zone at the site.
- **Slurm partitions are `adapters` and `ring`**, both over
  `dvalin,durin,dain`. `GresTypes=gpu`, `gres.conf` declares
  `Name=gpu Type=gb10 File=/dev/nvidia0`. `MaxTime` is 48 h on `adapters`,
  336 h on `ring`. Neither Mac is a Slurm node.
- **Jobs are not containerised.** They run under `/forge/venv` on the
  appliance, launched by `sbatch`, writing to `/forge/vault/...` and reporting
  to MLflow at `http://10.20.0.21:5000`.
- **The vault is `/forge/vault`**, an NFS mount of `10.20.0.21:/Volumes/forge`,
  mounted on DVALIN, DURIN and DAIN by Procedure S9. **Not on ALVISS.**
- **Observability is Prometheus and Grafana on REGIN.** Scrape targets are the
  three appliances (`:9100`, `:9400`), REGIN itself and MLflow. ALVISS is not
  a target. There is no Loki.
- **CON-A** is an `xterm` on DVALIN running
  `watch nvidia-smi; ibdev2netdev; sinfo`. **CON-B** is Chromium in kiosk mode
  on REGIN showing Grafana dashboard 1 at `http://localhost:3000/d/thermal`.
- **Cold start order** (wiring §7.2): PDU-C, REGIN, ANDVARI, ALVISS and
  DRAUPNIR, PDU-A, then the three appliances one at a time. **DRAUPNIR starts
  before any appliance exists.** Shutdown reverses it: appliances down and
  PDU-A off, *then* DRAUPNIR stops.
- **No UPS** (gap G1, on order). **No image registry anywhere.** No WireGuard
  link to MEGINGJORD; `wireguard-tools` is installed on REGIN only.

---

## 2  The one that has to be decided before the rest

### RF-E01 — Blocker — ALVISS runs macOS; the deployment is systemd and Podman

> **Status: repository side implemented.** Option A was chosen. `deploy/lib.sh`
> now carries the platform and five service verbs (`set_image`, `unset_image`,
> `enable`, `restart`, `is_active`, plus `reload` and a logs hint);
> `rollout.sh` and `rollback.sh` name no service manager at all and run
> unchanged on both; `install.sh` branches only in its preflight and where it
> renders a template. Three launchd agent templates sit beside the three
> systemd units, and `units/draupnir-run.sh` gained the stale-name clean that
> was an `ExecStartPre` and a `podman machine` start for a Mac returning from a
> power cut. 27 new assertions in `tests/contract/test_deploy.py` render each
> plist and parse it, and check it against the systemd unit it mirrors rather
> than against a copy of the same expectation.
>
> **Still outstanding on the manual's side:** Procedure S10 installs no
> container runtime. It needs `brew install podman` and
> `podman machine init --cpus 2 --memory 4096 --now`, and the machine needs to
> start at login. Until that lands, `install.sh --check` on the real ALVISS
> stops at `the podman machine is not running`, which is the correct answer and
> not a working host. Tracked into RF-E19's Procedure S13.

**Repository and manual.**

`deploy/install.sh:128` requires `systemctl`, `:129` requires a user systemd
instance, and `:37` installs units into `~/.config/systemd/user`.
`deploy/units/draupnir-run.sh` execs `podman run`. `docs/DEPLOYMENT.md:182`
lists `no user systemd instance` as a stop condition.

The manual puts DRAUPNIR on ALVISS (§6 line 219 "DRAUPNIR application host";
§12 L6b "Runs on ALVISS"), and ALVISS is a Mac mini M4 Pro. Procedure S10
installs `xcode-select`, Homebrew, `uv`, `git`, `ansible`, `ollama`, MLX and
enables FileVault with `fdesetup`. It is macOS, and macOS has no systemd.

Procedure S10 also **does not install a container runtime**. There is no
`podman` and no Docker on ALVISS. The only `docker run` in the manual is the
DCGM exporter on the appliances.

So `./deploy/install.sh --check` on the real ALVISS stops at the first
preflight check, and every remaining finding in this document is downstream of
that.

Three ways out, and this is a decision rather than an implementation:

| Option | What changes | Keeps AC-Q7 |
|---|---|---|
| **A. `podman machine` + launchd** *(recommended)* | Install Podman on ALVISS. The three units become launchd agents that drive `podman` inside the machine VM. `install.sh` grows a macOS branch. | Yes — same distroless aarch64 images, still rootless |
| B. Native launchd | Run the API, worker and web from a `uv` venv under launchd. No containers. | No — forfeits the image, the distroless base and the read-only root |
| C. Move the control plane | Put DRAUPNIR on a small Linux host and amend SAD Decision S3. | Yes, but it is a hardware and a design change |

Option A is recommended because it changes one script and one document, keeps
every property AC-Q7 asserts, and leaves the SAD's Decision S3 intact. It costs
a `podman machine` VM on ALVISS, which is the honest price of running Linux
containers on a Mac.

**Prompt**

> Make `deploy/install.sh` support the host DRAUPNIR is specified to run on.
>
> 1. Detect the platform at the top of the preflight. On Darwin, require
>    `podman` and a running `podman machine` (`podman machine list --format
>    json`, refuse if none is running and say `podman machine init && podman
>    machine start`), and require `launchctl` instead of `systemctl`.
> 2. Add `deploy/units/*.plist.in` — launchd agent templates for
>    `com.veldris.draupnir.{api,worker,web}` — alongside the existing
>    `.service.in` units, both driving the same `draupnir-run.sh`. Install into
>    `~/Library/LaunchAgents` and load with `launchctl bootstrap gui/$(id -u)`.
>    `KeepAlive` replaces `Restart=on-failure`; `RunAtLoad` plus the machine's
>    own start replaces lingering.
> 3. `draupnir-run.sh` needs one change only: on Darwin the published address
>    is forwarded out of the machine VM, so keep `--publish
>    127.0.0.1:PORT:PORT` and document that the VM must be started before the
>    agents load. Everything else — `--rm`, `--pull=never`, `--read-only`,
>    `--cap-drop=ALL`, `--security-opt=no-new-privileges`, the tmpfs and the
>    env files — is identical.
> 4. `rollout.sh` and `rollback.sh` set `DRAUPNIR_IMAGE_<unit>` through
>    `launchctl setenv` on Darwin and restart with `launchctl kickstart -k`.
>    Keep `deploy/lib.sh` as the single place that knows the unit-to-image
>    mapping.
> 5. Update `docs/DEPLOYMENT.md`'s preflight expectations and its "what you
>    should see" transcript for both platforms.
>
> On the manual's side, extend Procedure S10 step 1 with `brew install podman`
> and `podman machine init --cpus 2 --memory 4096 && podman machine start`,
> and add the machine to the launchd start-at-login configuration Procedure S8
> step 8 already establishes for ANDVARI's services.

**Acceptance criteria**

- `tests/contract/test_deploy.py` asserts the installer has a Darwin branch and
  that both unit templates resolve to the same `draupnir-run.sh` invocation
  and the same image reference for a given revision.
- A test asserts `install.sh --check` on Darwin fails with a named reason when
  `podman machine` is not running, and passes when it is.
- The plist templates are validated with `plutil -lint` in the test.
- Manual: Procedure S10 installs a container runtime, and its completion check
  includes `podman machine list` showing a running machine.
- Gated in stage 2.3.

---

## 3  Naming, addressing and dependencies

### RF-E02 — P1 — Every default hostname is in a zone the site does not serve

> **Status: implemented.** `deploy/lib.sh` gained `draupnir_site_domain` and
> `draupnir_host_for`, and is now the only file in `deploy/` that contains the
> string `veldris.internal` — once, as a suffix rather than a hostname. The
> five scripts derive every name from the forge, so a second one needs
> `--site <name>` and no edits, and an estate whose naming differs sets
> `DRAUPNIR_SITE_DOMAIN`.
>
> Two things came out of doing it that were not in the prompt. The derivation
> had to move **after** the argument loop, because `--site` decides the zone and
> anything derived above it would ignore the flag — a bug invisible on the page,
> since both orderings read identically, so there is a test that runs
> `--site bergelmir` and reads the answer back. And `install.sh` now runs `main`
> only when executed, so `resolves` can be called by a test rather than
> pattern-matched; before that, the only thing assertable about it was its
> source text.
>
> The registry is site-scoped along with the rest: a forge partitioned from the
> estate still has to be able to roll out (Decision S8), and it cannot do that
> against a registry on the other side of the partition.
>
> **One name deliberately left alone.** `megingjord.veldris.internal` in
> `draupnir/svalinn/egress.py` is estate-wide, not per-forge: SAD 11A makes
> MEGINGJORD one registry for the whole Forge Matrix and the manual puts it on
> Veldris_NXT, so a site-scoped name there would denote something that should
> not exist. The listing below is corrected accordingly, and the reasoning is
> now a comment beside the destination so the next hostname audit does not
> "fix" it. That it does not resolve at Sindri is RF-E21's tunnel question, not
> a naming error.

**Repository.**

| Where | Default | Resolves at Sindri |
|---|---|---|
| `deploy/install.sh:41` | `andvari.veldris.internal` | **No** |
| `deploy/install.sh:43` | `andvari.veldris.internal` | **No** |
| `deploy/install.sh:31`, `lib.sh:22`, `rollout.sh:12`, `rollback.sh:11`, `draupnir-run.sh:23` | `registry.veldris.internal` | **No** |
| `docs/DEPLOYMENT.md:92` | `alviss.veldris.internal` | **No** |
| `docs/DEPLOYMENT.md:278–279` | `andvari.veldris.internal` | **No** |
| `draupnir/svalinn/egress.py:94` | `megingjord.veldris.internal` | **No** — and correctly so; estate-wide, reached over the federation link (RF-E21) |

The zone is `sindri.veldris.internal`. `dnsmasq` on REGIN declares
`local=/sindri.veldris.internal/`, which makes it authoritative for that zone
and that zone only; a query for `andvari.veldris.internal` is forwarded to the
site router and denied by the §10.5 egress policy. Every default in the
installer points at a name that will not resolve on the machine it is meant to
run on.

**Prompt**

> Make the site's zone part of the configuration rather than baked into a
> hostname.
>
> In `deploy/install.sh`, derive the defaults from the site: introduce
> `DRAUPNIR_SITE_DOMAIN`, defaulting to `${SITE_ID}.veldris.internal`, and
> build `POSTGRES_HOST` and `OBJECT_STORE_HOST` as
> `andvari.${DRAUPNIR_SITE_DOMAIN}`. A second forge then needs one variable
> rather than three edits. Do the same in `docs/DEPLOYMENT.md`, and correct
> `alviss.veldris.internal` to `alviss.sindri.veldris.internal` throughout.
>
> Add a preflight step that resolves each dependency name before it tries to
> connect, and reports "does not resolve — check the site domain" separately
> from "does not answer". The two have different remedies and the current
> check conflates them.

**Acceptance criteria**

- `tests/contract/test_deploy.py` asserts every default host in `install.sh`,
  `lib.sh`, `rollout.sh`, `rollback.sh` and `draupnir-run.sh` is derived from
  `DRAUPNIR_SITE_DOMAIN`, and that no `*.veldris.internal` literal remains
  outside that one default.
- A test asserts the preflight distinguishes an unresolvable name from an
  unreachable one.
- `docs/DEPLOYMENT.md` carries no unqualified hostname.
- Gated in stage 2.3.

---

### RF-E03 — P1 — ANDVARI has no `draupnir` database, no role, and no bucket

> **Status: repository side implemented; manual side drafted.**
>
> `scripts/preflight.py` asks each dependency whether it will accept this
> application, using the drivers the application itself uses, and
> `install.sh --check` reports the answer per dependency. Five verdicts:
> `unreachable`, `auth-refused`, `missing`, `ok`, and `unverified`.
>
> **The fifth was not in the prompt and the flow needs it.** The documented
> order creates `secrets.env` empty at Part 4 and fills it in at Part 5, so at
> the first `--check` there is no credential to authenticate with. Failing
> there would make the documented order impossible to follow, and passing
> silently would report "checked" for something not checked. It reports
> `unverified` with the reason, and `docs/DEPLOYMENT.md` now has a second
> `--check` in Part 6 — the run that actually proves the credentials the
> operator just typed. Nothing verified those before; the first thing to find
> out was the API failing to start.
>
> **SQLSTATE does not work for this and the obvious implementation is wrong.**
> psycopg 3 raises a bare `OperationalError` with `sqlstate` unset for every
> connection-time failure, because the failure happens before a session exists
> to carry one — so `28P01` against `3D000` is not available. The server's
> message says which, and PostgreSQL localises it through `lc_messages`. The
> check therefore probes: port open, then the target database, then the
> maintenance database with the same credentials. Credentials that work against
> `postgres` and not against `draupnir` mean the database is missing; neither
> working means the credentials are. The server's own first line is carried in
> the detail either way, so a misclassification is still diagnosable.
>
> `tests/integration/test_preflight.py` drives all five verdicts against the
> real PostgreSQL and MinIO containers — twelve assertions, including that
> `unverified` does not stop a commissioning and that every failing verdict
> carries a remedy.
>
> **Manual side:** drafted as
> [proposed-procedure-s13.md](proposed-procedure-s13.md), covering the S8 and
> S10 amendments, the new Procedure S13, three acceptance tests and two
> operational steps. Not applied: VLD-INF-SINDRI-001 is a controlled document.

**Manual, with a repository consequence.**

`install.sh` checks that PostgreSQL and MinIO *answer* and stops if they do
not — which is right — but nothing at the site ever creates what DRAUPNIR
needs inside them. Procedure S8 step 7 is:

```
minio server /Volumes/forge/minio --address ":9000" --console-address ":9001" &
brew services start postgresql@16
createdb mlflow
```

That creates the `mlflow` database and nothing else. There is no `draupnir`
database, no `draupnir` role, and no bucket. `DRAUPNIR_DATABASE_URL` in
`docs/DEPLOYMENT.md:278` expects `draupnir:PASSWORD@…/draupnir`.

Two further problems in the same three lines:

- **PostgreSQL is not listening off-host.** Homebrew's `postgresql@16` binds
  `localhost` by default and ships a `pg_hba.conf` with no host entry for
  10.20.0.0/24. DRAUPNIR connects from ALVISS. The manual never sets
  `listen_addresses` or adds an `hba` rule, so `install.sh`'s dependency check
  will fail at a correctly-built site.
- **MinIO runs with default credentials and no bucket.** No
  `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` is set, so it comes up as
  `minioadmin`. `DRAUPNIR_OBJECT_STORE_BUCKET` defaults to `draupnir` and
  nothing creates it.

**Prompt**

> Add a **Procedure S13, DRAUPNIR control plane** to VLD-INF-SINDRI-001,
> placed after S12, covering the provisioning DRAUPNIR needs and nothing more:
>
> 1. On ANDVARI: create the `draupnir` role and database; set
>    `listen_addresses = '10.20.0.21'` and add
>    `host draupnir draupnir 10.20.0.22/32 scram-sha-256` to `pg_hba.conf`;
>    restart the service. Bind to the Fabric 2 address rather than `*`, because
>    Fabric 2 is the only segment ALVISS reaches and a wildcard bind on a host
>    holding the vault is a wider surface than the site needs.
> 2. On ANDVARI: run MinIO under `launchd` with `MINIO_ROOT_USER` and
>    `MINIO_ROOT_PASSWORD` from a keychain item rather than the defaults, and
>    create the `draupnir` bucket with versioning enabled — versioning is what
>    makes the object store's artefact seal a property of the store rather than
>    of a process (see RF-08 in the companion register).
> 3. On ALVISS: run `deploy/install.sh --check`, then `--revision <sha> --site
>    sindri`, then the smoke test.
>
> On the repository's side, extend `install.sh --check` so it does not merely
> open a socket: have it authenticate, confirm the `draupnir` database exists
> and confirm the bucket exists, reporting each separately. "PostgreSQL is
> answering" and "PostgreSQL will accept this application" are different
> facts, and the installer currently checks the one that cannot fail
> usefully.

**Acceptance criteria**

- Manual: Procedure S13 exists, and its completion check is `install.sh
  --check` reporting every dependency green.
- `install.sh --check` distinguishes: port closed, authentication refused,
  database absent, bucket absent — four messages, four remedies.
- `tests/integration/` asserts the check reports each of the four against a
  container fixture.
- Gated in stages 2.3 and 2.4.

---

### RF-E04 — P2 — The vault is not mounted on the host that checks its capacity

> **Status: repository side implemented; manual side drafted.**
>
> The vault is now a dependency in its own right. `scripts/preflight.py` gained
> `check_vault`, so `install.sh --check` reports it alongside the database and
> the object store, in four states: `ok` with a capacity figure, `unreachable`
> when nothing is mounted, `missing` for a directory that is not the vault, and
> `unverified` for a forge that legitimately has none.
>
> **The middle two had to be told apart, and the obvious call conflates them.**
> `reconcile.mounted()` answers False both for an absent root and for a bare
> directory on the mount point, which are opposite problems: the first is a
> mount to restore, the second is a directory that has to be removed *before*
> the mount can go back, and that writes land on local disk until it is.
> `require_vault` already draws that line and raises a different type for each,
> so the check asks it and translates rather than re-deriving the distinction.
> Doing so found that `reconcile.__all__` exported `VaultNotInitialisedError`
> and not `VaultUnavailableError` — half of a pair whose entire point is that a
> caller tells them apart. Both are exported now.
>
> `install.sh` writes `DRAUPNIR_VAULT_ROOT` into `draupnir.env`, defaulting to
> the estate's `/forge/vault`, with `${VAR-default}` rather than `${VAR:-default}`
> so a forge with no vault can say so with an empty string instead of getting
> the default back.
>
> **The container needed a mount, and that was missing entirely.**
> `draupnir-run.sh` bind-mounted nothing, so configuring a vault root would have
> pointed the worker at a path its container could not see. It now mounts the
> vault into the worker and only the worker, **read-only**: the capacity duty
> reads, and `vault_admin.py reconcile --apply` — the thing that writes — runs
> on the host. The host mount is therefore read-write and the container's is
> not, which is the split the runbook's section 4 actually needs. A missing
> vault warns and starts anyway, because SAD 11.2 row 4 makes it a degraded
> mode rather than a stop.
>
> `scripts/vault_admin.py` defaulted to `/mnt/hodd`, a path this repository
> invented that exists on no host in the estate; `docs/runbook.md` section 4
> sent an operator there during the incident that section is written for. Both
> now say `/forge/vault`, and a test asserts nothing sends an operator to the
> old one.
>
> **Manual side:** amendment 2a in
> [proposed-procedure-s13.md](proposed-procedure-s13.md) extends Procedure S9
> to ALVISS with an `autofs` map — macOS has no `/etc/fstab` — and adds the
> `podman machine set --volume /forge:/forge` that the container needs, because
> the machine shares the user's home directory and little else.

**Manual and repository.**

Procedure S9 mounts `/forge/vault` on DVALIN, DURIN and DAIN. ALVISS is not in
that procedure. But the worker — which runs on ALVISS — owns the vault
capacity duty (`draupnir/worker/loop.py:545`, `duties.capacity`, alarming at
85 % per SAD 11.3), and `scripts/vault_admin.py reconcile` is an operator tool
the runbook tells you to run on the control plane.

With no mount, `DRAUPNIR_VAULT_ROOT` must stay empty, and
`config.py:42` then skips the vault checks entirely — the honest behaviour, and
it means the SAD 11.3 vault-capacity signal has no source at Sindri.

The runbook compounds it: `docs/runbook.md:147` names the vault as
`/mnt/hodd`. The site's path is `/forge/vault`.

**Prompt**

> Mount the vault on ALVISS and point DRAUPNIR at it.
>
> Manual: extend Procedure S9 to include ALVISS. On macOS the fstab mechanism
> differs — use an `autofs` map or a `launchd` agent running `mount -t nfs -o
> resvport,hard,nconnect=8 10.20.0.21:/Volumes/forge /forge/vault`; `resvport`
> is required because macOS NFS clients default to a non-reserved source port
> and the export is not configured with `insecure`. Read-only is sufficient
> for the capacity duty and is the safer mount for the control plane; use
> read-write only if `vault_admin.py reconcile --apply` is to be run there,
> and state which was chosen.
>
> Repository: set `DRAUPNIR_VAULT_ROOT=/forge/vault` in the installer's
> generated `draupnir.env`, replace every `/mnt/hodd` in `docs/runbook.md` with
> `/forge/vault`, and have `install.sh --check` report the vault as a
> dependency in its own right — mounted, is-a-vault (the `.hodd-vault`
> marker), and capacity — since a control plane whose vault checks are silently
> skipped is exactly the state §4 of the runbook is written about.

**Acceptance criteria**

- Manual: Procedure S9 covers ALVISS, and its completion check includes
  `df -h /forge/vault` on all four hosts.
- `make vault-status` on ALVISS reports `MOUNTED` with a capacity figure.
- `install.sh --check` reports the vault, and reports `NOT CONFIGURED`
  distinctly from `PRESENT BUT NOT A VAULT`.
- No occurrence of `/mnt/hodd` remains outside a test fixture; asserted by
  `tests/unit/test_documentation.py`.
- Gated in stages 2.1 and 2.3.

---

## 4  The scheduler

### RF-E05 — P1 — There is no Slurm client on ALVISS, and none in the image

> **Status: repository side implemented; manual side drafted.**
>
> `plugins/motsognir_slurmrest/` is a second `ScheduleDriver` for the same
> scheduler, reached over `slurmrestd`. It is a sibling of the sbatch shim
> rather than a replacement: a host with the binaries installs one, a host
> without them installs the other, and the core does not change either way.
> That is what the plug-in system is for, and this is the first time two
> installations of one interface have both been real.
>
> **It passes the whole published conformance suite, which the shim cannot.**
> The only thing the shim needs a live Slurm for is its command layer; the only
> thing this needs one for is its HTTP client, and a client is an argument. A
> stub that answers the way slurmrestd answers puts submit, poll, cancel and
> logs through the real code path, so `TestSlurmRestConformance` is the suite
> itself rather than the half of it that can be run without a scheduler.
>
> **The token is never held.** It arrives as a callable asked for a value at
> the moment of each request, so a lease's expiry is respected rather than
> cached past. Writing the test for that found a real hole: `token` and
> `client` were in the dataclass repr, and `functools.partial(fetch, "the-
> token")` is a natural way to wire the first — its repr carries the value. Both
> are `repr=False` now. A test asserts the token reaches
> `X-SLURM-USER-TOKEN` and appears in no URL, no job payload and nothing stored
> on the driver, because a credential in a job description is a credential in
> Slurm's accounting database.
>
> **The two defects of RF-E06 and RF-E07 are not repeated here**, and there are
> tests naming them as such: the script is built with `shlex.join` so a shell
> reassembles the plan's argument vector, and the environment is a list, so a
> JSON value full of commas is one value rather than several. Those findings
> stay open against the shim.
>
> `svalinn/egress.py` declares REGIN as a destination with a purpose and an
> approving policy. The driver takes its HTTP client as an argument precisely
> so that broker can be what supplies it; that wiring is RF-09, and the
> declaration is the half that is meaningful without it.
>
> `install.sh --check` reports the scheduler as a fourth dependency.
> Reachability only, and it says so: whether Slurm will *accept* us depends on
> a JWT that SVALINN issues as a lease, and an installer carrying a scheduler
> credential in order to check one would be a worse outcome than an unchecked
> one. It does verify it is talking to slurmrestd rather than to whatever
> answers the port — MinIO on the wrong port replies to that ping quite
> happily, and a check that called it a success would earn being ignored.
>
> **Still to do, and tracked elsewhere:** nothing yet constructs the driver
> with a token and a brokered client. That is the composition-root work of
> RF-09, and until it lands the driver is installed and conformant and not yet
> the one the worker dispatches through — which is RF-E11's estate reading and
> RF-10's dispatch path in the companion register.
>
> **Manual side:** amendment 2b in
> [proposed-procedure-s13.md](proposed-procedure-s13.md) adds `slurmrestd` to
> Procedure S11 with `AuthAltTypes=auth/jwt` alongside the existing munge, a
> signing key, a system user, a bind to REGIN's fabric address rather than
> `0.0.0.0`, and a token for the control plane's account. It carries a note for
> the author on the two decisions that are the site's: token lifetime, and what
> re-issues it.

**Repository and manual.**

`plugins/motsognir_slurm` shells out to `sbatch`, `squeue`, `sacct` and
`scancel` with `subprocess.run`. The manual installs `slurm-wlm` on REGIN
(Procedure S11) and `slurmd` on the three appliances (S12 step 4). **No Slurm
client is installed on ALVISS or ANDVARI**, and the DRAUPNIR worker runs
inside a distroless container, which by construction has no shell and no Slurm
binaries.

So there is no path from the control plane to the scheduler at all. This is the
mechanism behind `docs/DEPLOYMENT.md:483` — "Dispatch suspends with no Slurm
controller" — being the permanent state rather than a degraded one.

Three options, and unlike RF-E01 there is a clear answer: **`slurmrestd`**.
Installing Slurm client binaries and `munge` inside a distroless image forfeits
the point of a distroless image, and shelling out over SSH from a container
means a key in the control plane.

**Prompt**

> Move the Slurm driver onto Slurm's REST interface.
>
> Manual: add `slurmrestd` to Procedure S11's package list, run it on REGIN
> bound to `10.20.0.5` on the management fabric, with `AuthType=auth/jwt`
> alongside the existing `auth/munge` for `slurmctld`, and issue a scoped JWT
> for the DRAUPNIR service account. Add `AuthAltTypes=auth/jwt` and
> `AuthAltParameters=jwt_key=/etc/slurm/jwt_hs256.key` to `slurm.conf`.
>
> Repository: add a `motsognir.slurmrest/v1` schedule driver implementing the
> same `ScheduleDriver` protocol against the REST API — `POST /slurm/v0.0.40/
> job/submit`, `GET /job/{id}`, `DELETE /job/{id}` — with the token supplied as
> a SVALINN lease rather than an environment value (see RF-09 in the companion
> register). Keep `motsognir.slurm/v1` as the shim for a host that does have
> the binaries; the two are alternative installations of one interface, which
> is what the plug-in system exists for.
>
> Every outbound call goes through the egress allow list with
> `regin.sindri.veldris.internal` declared (see RF-E13).

**Acceptance criteria**

- The new driver passes the published conformance harness unchanged.
- A contract test asserts it submits, polls and cancels against a stubbed
  `slurmrestd`, and that a token never appears in a log line or a job payload.
- `install.sh --check` reports scheduler reachability as a dependency.
- Manual: Procedure S11's completion check includes a `slurmrestd` job
  submission from ALVISS.
- Gated in stage 2.3.

---

### RF-E06 — P1 — `--wrap` is built by joining an unquoted argument vector

> **Status: implemented.** Taken together with RF-E07: both are consequences of
> squeezing a job through `sbatch`'s argument list, so the job is now a script.
> `SlurmDriver.render_script` builds it with `shlex.join` for the command and
> `shlex.quote` for every environment value, writes it into the run's working
> directory as `draupnir-job.sbatch`, and submits it by path.
>
> The defect, reproduced before it was fixed: `" ".join(("sh", "-c", 'printf
> "%s" "$CONFIG" > lf.json && llamafactory-cli train lf.json'))` reparses into
> **eleven** words rather than three. `sh -c` would have received `printf`
> alone, and the redirect and the `&&` would have run in the submitting shell.
> The configuration file was never written and the trainer ran against a file
> that was not there.
>
> The test round-trips rather than pattern-matches: a command containing
> spaces, both kinds of quote, `$`, `>` and `&&` goes through the driver and
> back out of `shlex.split`, and must come back as exactly the vector that went
> in.
>
> Two things came with taking the script route rather than keeping `--wrap`.
> The script is **kept** after submission, because it is the exact thing that
> ran and the first question after a surprising result is what was actually
> submitted — a script in a temporary directory answers that only until the job
> finishes. And it carries `#SBATCH` directives rather than command line
> arguments, so it is self-contained and can be resubmitted by hand during an
> incident without reconstructing an argument list, which is the same shape as
> the estate's own scripts in VLD-INF-SINDRI-001 Part 5.
>
> `max_script_bytes` replaces the per-variable limit the original prompt asked
> for, which no longer exists: a shell assignment has no length rule. What does
> is Slurm's `max_script_size`, and Slurm **truncates** a script over it rather
> than refusing it — a job that runs a prefix of what was meant. The driver
> refuses instead, naming the largest environment value.

**Repository.**

`plugins/motsognir_slurm/…/__init__.py:122`:

```python
arguments.append("--wrap=" + " ".join(plan.command))
```

`plan.command` from the LLaMA-Factory driver is:

```python
(
    "sh",
    "-c",
    'printf "%s" "$DRAUPNIR_LF_CONFIG" > lf-config.json && llamafactory-cli train lf-config.json',
)
```

Joining those three elements with spaces and no quoting produces a `--wrap`
string in which the `sh -c` argument is no longer one argument. Slurm hands
the whole thing to a shell, so `sh -c printf "%s" "$DRAUPNIR_LF_CONFIG" >
lf-config.json && llamafactory-cli …` runs `sh -c printf` with stray operands
and then redirects — the configuration is never written and the trainer runs
against a file that does not exist.

This cannot be caught by the conformance harness, which checks that `render` is
pure, nor by the local subprocess driver, which passes the vector to `exec`
without a shell. It only appears against real `sbatch`.

**Prompt**

> Quote the command properly, or stop passing it through a shell.
>
> Preferred: write the plan's argument vector to a job script in the run's
> working directory and submit that script, rather than using `--wrap` at all.
> `sbatch <script>` takes a path and no quoting question arises; the script is
> also the artefact an operator needs when asking what actually ran, and it can
> carry the `#SBATCH` directives the estate's own scripts use.
>
> If `--wrap` is kept, build the string with `shlex.join(plan.command)` and add
> a test that round-trips a command containing spaces, quotes, `$`, `>` and
> `&&` through the driver and back out of a shell.

**Acceptance criteria**

- A test asserts a plan whose command contains `"`, `$`, `>` and `&&` is
  submitted such that a shell re-parses it into exactly the original argument
  vector.
- A test asserts the rendered job script, if that route is taken, is written
  into the run's working directory and is readable after the run.
- Gated in stage 2.3.

---

### RF-E07 — P1 — `--export` corrupts the job environment and drops `PATH`

> **Status: implemented**, with RF-E06 and by the same change: the environment
> is now a set of quoted shell assignments in the job script rather than a
> comma-separated command line argument.
>
> Both halves reproduced before the fix. The commas:
> `--export=CONFIG={"stage":"sft","lora_rank":64,"targets":["q","k","v"]}`
> splits into **five** entries, so the one variable carrying the training
> configuration arrived as several malformed ones. And the `PATH`: naming
> variables without `ALL` propagates only those, so the job had none — every
> batch script in Part 5 begins by sourcing a virtual environment and
> `llamafactory-cli` lives in it.
>
> **The prompt's answer was `--export=ALL`, and that is wrong on this estate.**
> `sbatch` propagates the *submitting* environment, and the submitter is the
> DRAUPNIR worker, which runs in a distroless container; the job runs on an
> appliance. Inheriting that `PATH` gives the job directories that exist in the
> container and on no machine it can land on. It would have failed the same
> way, for a reason one step harder to see.
>
> So the driver takes a `preamble` — site configuration, at Sindri
> `("source /forge/venv/bin/activate",)` — and a site that configures one gets
> `#SBATCH --export=NONE` and an environment that is exactly what its own
> script sets. A site that configures none keeps the default, because that is
> what makes a development machine work. The original code's instinct, in the
> comment at the `--export` line, was right: a run should not depend on who
> submitted it. What it lacked was a way for the job to establish an
> environment of its own.
>
> The manual side is a one-line setting, recorded in
> [proposed-procedure-s13.md](proposed-procedure-s13.md).

**Repository.**

`…/__init__.py:118–120`:

```python
exported = ",".join(f"{key}={value}" for key, value in sorted(plan.environment.items()))
arguments.append(f"--export={exported}")
```

Two independent defects.

**Commas.** The LLaMA-Factory driver puts the entire training configuration
into one environment variable as JSON produced with
`separators=(",", ":")` — a value that is mostly commas. `sbatch --export`
uses the comma as its own separator, so the JSON is split into dozens of
malformed entries and the configuration the job receives is not the
configuration that was rendered.

**`PATH`.** `submit_arguments` defaults to `()`, so nothing adds `ALL`. Slurm's
documented behaviour for `--export=<list>` without `ALL` is to propagate only
`SLURM_*` and the named variables. The job therefore runs with no `PATH`, and
`llamafactory-cli` — which on this estate lives in `/forge/venv/bin` and is on
`PATH` only after `source /forge/venv/bin/activate` — is not found. Every
sbatch script in the manual begins with that `source` line for exactly this
reason.

**Prompt**

> Stop passing job configuration through `--export`.
>
> 1. Use `--export=ALL` plus the plan's own variables, so the job inherits a
>    usable `PATH` while still receiving what the plan declares. The comment at
>    `:117` argues for keeping the submitter's login environment out of the
>    job; that is a real concern, and the answer is to set the variables inside
>    the job script (RF-E06) rather than to strip `PATH`.
> 2. Write any value containing a comma, a newline or a character outside
>    `[A-Za-z0-9_./:-]` to a file in the working directory and export the path,
>    not the value. The LLaMA-Factory configuration is the case in point: it
>    should be a `lf-config.json` the plan writes, which also removes the
>    `printf` gymnastics `render` currently needs to stay pure.
> 3. Have the driver refuse a plan whose exported environment would exceed a
>    conservative length limit, naming the variable, rather than submitting
>    something Slurm will truncate.
>
> Coordinate with the manual: the estate's own scripts `source
> /forge/venv/bin/activate` before running anything. Either the rendered job
> script does the same, or Procedure S13 installs the training stack so it is
> on the default `PATH` for the service account. State which.

**Acceptance criteria**

- A test asserts a plan whose environment contains a JSON value with commas
  reaches the job intact, byte for byte.
- A test asserts the submitted job has a `PATH`.
- A test asserts an over-long export is refused at submission, naming the
  variable, rather than silently truncated.
- Gated in stage 2.3.

---

### RF-E08 — P2 — DRAUPNIR names a partition the estate does not have

> **Status: implemented, option B.** `export` is now a *venue* rather than a
> Slurm partition. `placement.Venue` distinguishes `CLUSTER` — Slurm, on the
> appliances — from `CONTROL_PLANE`, and `POLICY` is the one table that says
> which each partition is, what its `MaxTime` is, and whether it is
> all-or-nothing. `ALL_OR_NOTHING` is derived from that table rather than
> declared beside it, so the two cannot disagree.
>
> **The consequence is better than the fix.** An export placement has no
> appliances, so merge and quantisation now plan with the whole estate
> switched off — which is exactly when an operator wants to finish packaging a
> release. Before, an export placement went through the appliance-availability
> path and `NoCapacityError` would have refused it for want of machines it
> never needed. That is a case the old code would have got wrong in the other
> direction too.
>
> **The estate's time limits are now known.** `MaxTime` is 48 hours on
> `adapters` and 336 on `ring`; DRAUPNIR knew neither, so a specification
> asking for more was registered, queued, and rejected by Slurm afterwards.
> `TimeLimitError` refuses it at planning, names the limit, and says whose it
> is — the refusal points at `slurm.conf` rather than at the specification.
>
> **`verify_partitions` is the commissioning check.** `install.sh --check`
> enumerates the scheduler's partitions when a token is configured and refuses
> a scheduler that lacks one, naming it. Without a token it says the partitions
> were not checked rather than implying they were — the same shape as the
> database and object store checks, and for the same reason. Reading the
> partition list needs a credential, and the token comes from `secrets.env`
> exactly like the database password does.
>
> A test asserts a correctly built Sindri passes with only `adapters` and
> `ring`, which is the property that would have caught the original defect:
> before this, `export` was in the set a scheduler had to have.
>
> **Decision recorded** in
> [proposed-sad-amendments.md](proposed-sad-amendments.md) as a new resolved
> decision S15, with two consequences the SAD should state alongside it: that
> export work survives the estate, and that the local subprocess driver is
> therefore a production component rather than "a local runner for
> development" as SAD 8.2 has it. The SAD never declared an `export` partition
> — that was this repository's own invention — so nothing has to be removed
> from it, only decided.

**Repository and manual.**

`draupnir/motsognir/placement.py:39` defines a third partition:

```python
#: Evaluation, merge and quantisation on ALVISS.
EXPORT = "export"
```

and `draupnir/worker/stages.py:415` submits merge and quantise work with
`partition="export"`.

The estate's `slurm.conf` (manual §34 step 1) defines two partitions,
`adapters` and `ring`, both `Nodes=dvalin,durin,dain`. **There is no `export`
partition, and ALVISS is not a Slurm node** — no `slurmd`, and it is macOS.
`sbatch --partition=export` returns `Invalid partition name specified`.

There is a real design question underneath: the manual does merge, quantisation
and MLX evaluation *by hand on ALVISS* (Procedures M7, M9, §32), not through
Slurm at all.

**Prompt**

> Reconcile the third partition. Two coherent answers; pick one and record it.
>
> **A. Make `export` real.** Add `slurmd` to ALVISS and a
> `PartitionName=export Nodes=alviss` to `slurm.conf`. Slurm on macOS is
> possible but is not a supported build and would be the only such component on
> the estate. Not recommended.
>
> **B. Make `export` local.** Recognise that export-class work runs on the
> control plane itself, and route it through the
> `motsognir.local_subprocess/v1` driver rather than through Slurm — which is
> what the manual already does by hand. `Partition.EXPORT` then stops being a
> Slurm partition name and becomes a placement class the dispatcher resolves to
> a different scheduler driver. Recommended: it matches how the estate works,
> keeps ALVISS off the cluster, and gives the local driver a purpose beyond
> development.
>
> Under either answer, add a startup check that every partition
> `Partition` names exists in the scheduler, and refuse to start when one does
> not. A run submitted to a partition that has never existed should be a
> commissioning failure, not a 48-hour queue wait ending in a rejection.
>
> Also carry the estate's `MaxTime` into placement: `adapters` is capped at
> 48 h and `ring` at 336 h, and a plan requesting more is rejected at
> submission. DRAUPNIR currently knows neither figure.

**Acceptance criteria**

- A test asserts every member of `Partition` resolves to a real scheduler
  destination, and that startup fails naming the partition when one does not.
- A test asserts a plan exceeding the partition's `MaxTime` is refused at
  submission with the limit named, rather than submitted.
- The decision between A and B is recorded in the SAD and in the manual.
- Gated in stages 2.1 and 2.3.

---

### RF-E09 — P2 — GPU request syntax differs from the estate's

> **Status: implemented**, and the fix is not where the prompt put it.
>
> The prompt said to add a `gres` field to `ResourceRequest` and default it
> from *driver* configuration. Half of that is right. The field is there and it
> is the interface a driver reads. But the accelerator cannot be driver
> configuration: putting it there gives two drivers two copies of one fact,
> which is exactly how they came to disagree — the shim sending
> `--gpus-per-node=1` while `motsognir.slurmrest/v1` sent a typed
> `tres_per_node`, so the same run asked for different things depending on
> which host submitted it.
>
> It cannot be the specification either. SAD 6.2 makes the specification the
> unit of reproduction and the Forge Matrix means it has to be portable; one
> naming `gb10` would not run at a forge with different hardware.
>
> So it is **MOTSOGNIR's**, which owns placement and therefore owns what the
> estate's machines are. `Appliance.accelerator` is a property of a machine,
> `Estate.gres()` is the string the estate offers, and a `Placement` carries it
> into the plan a driver submits and into the ledger payload. Both drivers read
> `plan.resources.gres` and each spells it for its own surface — `--gres=` wants
> `gpu:gb10:1` and `tres_per_node` wants `gres/` in front — with a test
> asserting they carry the same estate string rather than identical text. That
> test is the thing that was missing: neither spelling was wrong in isolation;
> what was wrong was that nothing compared them.
>
> **A mixed estate is refused rather than guessed at.** Where the appliances do
> not agree — or where one declares an accelerator and another says nothing —
> `Estate.gres()` returns nothing. Inventing an answer would submit a job asking
> for hardware half the appliances do not have. The half-declared case needed
> its own check: the set of declared types has one member, which a careless
> implementation reads as unanimity while an appliance has said nothing at all.
>
> **It is a setting, not a constant.** `placement.SINDRI` names the machines and
> their ranks, which are the estate; the accelerator comes from
> `DRAUPNIR_ACCELERATOR`, which `install.sh` writes and which the worker and the
> dry run both read through `estate_for`. Leaving `gb10` in the module constant
> would have made a second forge a patch rather than a variable — the same
> mistake RF-E02 fixed for hostnames.
>
> **The dry run shows it.** AC-F14 promises the compose screen renders the plan
> that would actually run, and it was showing the train driver's half: a GPU
> count with no type. It now carries the estate's `--gres` string, which is what
> an operator will later see in `scontrol show job`. Asked of the estate rather
> than of `place()`, deliberately — `place()` can refuse, and a dry run that
> failed because an appliance was down would be answering a different question
> from the one it was asked.
>
> **Decision recorded** as amendment 3 in
> [proposed-sad-amendments.md](proposed-sad-amendments.md), with a proposed
> addition to SAD 5.2's MOTSOGNIR row; the value is in
> [proposed-procedure-s13.md](proposed-procedure-s13.md) step 2b, with the
> `gres.conf` line to check it against.

**Repository.**

The driver emits `--gpus-per-node=1` (`…/__init__.py:113`). Every sbatch script
in the manual uses `--gres=gpu:gb10:1`, and `gres.conf` declares the GRES as
typed: `Name=gpu Type=gb10`.

With `SelectType=select/cons_tres` the `--gpus-per-node` form generally
resolves, but it selects an untyped count against a typed resource, the
estate's `AccountingStorageTRES` does not name `gres/gpu`, and none of it has
been exercised on this cluster. The estate's own form is known to work because
every procedure in Part 5 uses it.

**Prompt**

> Emit the GPU request in the form the estate is configured for. Add an
> optional `gres` field to `ResourceRequest` carrying the typed name
> (`gpu:gb10`), default it from configuration rather than hard-coding `gb10` —
> a second forge may have a different accelerator — and have the Slurm driver
> emit `--gres=gpu:<type>:<count>` when it is set and `--gpus-per-node` when it
> is not. Record the estate's value in the installer's generated configuration.

**Acceptance criteria**

- A test asserts the driver emits `--gres=gpu:gb10:1` when the type is
  configured and `--gpus-per-node=1` when it is not.
- The value appears in `draupnir.env` and in Procedure S13.
- A dry run on the console shows the same `--gres` string an operator would
  see in `scontrol show job`.
- Gated in stage 2.3.

---

### RF-E10 — P2 — The array is the estate's whole placement strategy, and DRAUPNIR cannot express it

> **Status: implemented.** `ArraySpec` is part of `ResourceRequest`, so an
> array is something a plan *is* rather than something a driver is told
> separately. Both Slurm drivers emit it, and a test asserts fifty six
> elements are **one** submission rather than fifty six.
>
> `ArraySpec.element()` is the only place the element identifier is spelled,
> and it refuses an index the array does not have. Passing the array's own
> identifier where an element's was meant cancels fifty six jobs instead of
> one, so the two are never the same string by accident. `find` strips any
> element suffix for the same reason.
>
> **Requeue uses the estate's mechanism.** `scontrol requeue` on the element,
> not a fresh submission. `retry.py` described resubmitting a single-element
> array, which gives the element a new job identifier and severs it from the
> array, losing both the throttle and the accounting record that ties the
> fifty six together. That description was wrong and is corrected.
>
> **One thing this found that was not in the finding.** slurmrestd v0.0.40
> exposes no requeue at all. Both approximations are worse than a refusal, so
> the REST driver refuses and names what to do instead. That is a real
> limitation of the transport the estate is going to use, and it is RF-E24.

**Repository.**

The manual is unambiguous (§41 step 2): "The directive `--array=0-55%3` is the
whole placement strategy: fifty six jobs are queued, exactly three execute at
any moment, one per appliance, with no collective communication and no fabric
contention."

`SlurmDriver.submit` has no `--array` argument. `draupnir/motsognir/arrays.py`
and `retry.py` — which model exactly this — are imported only by tests
(companion register, RF-13).

The retry mechanism also differs. The manual's §41 step 5 is `scontrol requeue
<arrayjobid>_<taskid>`, which requeues the element within its array. DRAUPNIR's
`retry.py` describes resubmitting `--array=<index>`, which creates a *new* job
with a new identifier and severs the element from the array it belongs to —
losing the `%3` throttle for the resubmitted element and the array's accounting
record.

**Prompt**

> Give the schedule driver array support, and use the estate's requeue.
>
> 1. Add `array` to `ResourceRequest` — a range and an optional concurrency
>    throttle — and have the Slurm driver emit
>    `--array=<first>-<last>%<throttle>`. `JobHandle` gains the array job id;
>    an element is addressed as `<jobid>_<index>` in `squeue`, `sacct` and
>    `scancel`, which the poll and cancel paths must already understand.
> 2. Implement element requeue as `scontrol requeue <jobid>_<index>` rather
>    than as a fresh submission, so the element stays inside its array and its
>    accounting record stays continuous. Amend `retry.py`'s docstring, which
>    currently describes the wrong mechanism.
> 3. Wire `draupnir.motsognir.arrays` into the submission path so the `%3`
>    throttle is derived from available appliances rather than written down.
>
> This is the estate-side half of RF-13 in the companion register; do them
> together.

**Acceptance criteria**

- A test asserts a 56-element submission emits one `--array=0-55%3` and one
  `sbatch`.
- A test asserts polling `<jobid>_17` returns that element's state, not the
  array's.
- A test asserts requeue emits `scontrol requeue <jobid>_17` and that the job
  identifier is unchanged afterwards.
- Manual: §41 step 5 and DRAUPNIR's requeue are the same command.
- Gated in stage 2.3.

---

### RF-E11 — P3 — Appliance availability is never read from the scheduler

> **Status: implemented.** `ClusterScheduleDriver.nodes()` reports every node
> and whether it can take work, and the worker rebuilds its `Estate` from that
> each tick. Until now the estate was a constant with everything marked
> available, so the behaviour SAD 11.2 row 3 describes — concurrency reduced,
> ring runs refused — could not happen. The runbook's section 3 documented a
> response to a state the code could not reach.
>
> A node is unavailable in any state that will not accept a job, and Slurm's
> not-responding suffix counts whatever the word in front of it says.
>
> **Two failures that had to be kept apart.** A scheduler that cannot be
> reached says nothing about the appliances, so the estate is left as it was:
> dispatch suspends on its own (SAD 11.2 row 2), and reporting every appliance
> as down would refuse every ring run for the length of a controller restart.
> A scheduler that answers and reports a node drained is evidence, and it is
> taken.
>
> `Estate.with_all_available()` was needed and missing: `without()` had no
> inverse, so an estate could only ever shrink and the first drained node
> would have taken ring runs out of reach until the worker restarted.

**Repository.**

`draupnir/worker/loop.py:365` constructs `Estate(site=settings.site_id)`, which
takes the default `SINDRI` tuple — three appliances, all marked available.
Nothing reads `sinfo` or `scontrol show nodes`, and nothing marks an appliance
down.

So the behaviour the README leads with — "a ring run refuses to plan when an
appliance is down rather than running on two nodes of a three node
specification" — cannot fire on the real estate, because the estate's
availability is not an input.

The wiring document makes this concrete twice. Cold start (§7.2) brings
DRAUPNIR up at step 9 and the appliances at steps 11–13, so for the first
several minutes of every start the estate is entirely down and DRAUPNIR
believes it is entirely up. Controlled shutdown (§7.3) halts all three
appliances and switches off PDU-A *before* DRAUPNIR is stopped, so the same is
true in reverse. And the manual's §48.2 monthly patch window drains the nodes
deliberately.

**Prompt**

> Make the estate's availability an observation.
>
> Add a duty that reads node state from the scheduler each tick — `sinfo
> --noheader --format=%n|%T` or the REST equivalent — and rebuilds the `Estate`
> with `DOWN`, `DRAIN`, `DRAINING` and `FAIL` nodes marked unavailable. Record
> a state change as a finding, because an appliance leaving the estate is
> something an operator wants to be told rather than to infer from a refused
> plan.
>
> Treat "the scheduler is unreachable" as distinct from "every node is down":
> the first suspends dispatch (SAD 11.2 row 2 — a queued run is not a failed
> run), the second refuses a ring plan. Conflating them turns a controller
> restart into fifty-six refusals.
>
> Then assert the two sequences that will actually happen: a worker ticking
> with the whole estate down must defer quietly and not alarm, and must resume
> without intervention when the appliances come back.

**Acceptance criteria**

- A test asserts a drained node makes a `ring` plan refuse and an `adapters`
  plan reduce concurrency to two.
- A test asserts an unreachable scheduler defers rather than refusing.
- A test replays the cold-start order — control plane up, estate down, estate
  arriving one node at a time — and asserts no alarm and no failed run.
- A test replays the shutdown order and asserts the same.
- Gated in stage 2.4.

---

### RF-E12 — P3 — A run pending in the Slurm queue is recorded as TRAINING

> **Status: implemented, and it needed no specification change.**
>
> The finding offered two routes: keep the run at QUEUED, or add a `PLACED`
> state to SAD 6.1. On looking properly the first needs no argument at all —
> **QUEUED already means waiting to run, which is exactly what a Slurm pending
> job is.** No state was missing. What was wrong is that `dispatch`
> transitioned at submit time rather than at start time.
>
> So the transition moved. A run stays QUEUED while the scheduler holds it
> pending, and reaches TRAINING on the first tick that sees it running. On
> this estate that is not an edge case: the throttled array means fifty three
> of fifty six elements are pending at any moment by design, and the board
> would have shown fifty six runs training against three appliances.
>
> **The hard part was not the transition; it was not submitting twice.** With
> nothing recorded at submit time the next tick would resubmit, which is worse
> than the bug. And nothing *could* be recorded: `Orchestrator.record` refuses
> a `run` subject outright, because the projector folds every run entry and
> one free-form entry would stop the registry rebuilding for every run at the
> site.
>
> The answer is that the scheduler already knows. The job carries a name
> derived from the run, and `find(name)` asks the thing that holds the queue.
> Nothing is written until there is something true to write, and the worker
> still holds nothing between ticks (SAD 11.2 row 1).
>
> A job **rejected** before it ran — an invalid partition, an unsatisfiable
> resource — also leaves the run QUEUED, with the reason reported every tick.
> The run has not failed at anything it did.
>
> A driver without `find` still dispatches: a local runner has no queue to
> search and starts immediately, so requiring it of every driver would break
> the development path to fix a problem it does not have.

**Repository, with a specification consequence.**

`draupnir/worker/stages.py:174` transitions the run to `RunState.TRAINING` as
soon as `sbatch` returns an identifier. The Slurm driver maps `PENDING`
correctly (`_STATES`), but SAD 6.1 has no state between `QUEUED` and
`TRAINING`, so a submitted-but-pending job has nowhere else to be.

On this estate that is not an edge case, it is the normal condition. With
`--array=0-55%3`, fifty-three of fifty-six elements are `PENDING` at any
moment by design. The run board and the S12 array monitor would show
fifty-six runs training against three appliances.

**Prompt**

> Distinguish "placed" from "running".
>
> The cleanest fix within SAD 6.1 is not a new state but an honest one: keep
> the run at `QUEUED` until the scheduler reports `RUNNING`, and record the
> allocation as a fact on the `QUEUED` run rather than as a transition. The
> `dispatch` stage then records the scheduler job id without moving state, and
> `observe` performs the `QUEUED → TRAINING` transition when Slurm says the
> element started. This keeps the lifecycle table unchanged and makes the board
> true.
>
> If a distinct `PLACED` state is preferred instead, that is a SAD 6.1 change
> with a new row, a guard, its required ledger fields and a projector case —
> larger, and it should be argued for rather than slipped in.
>
> Either way, the array monitor's element vocabulary must show pending
> elements as pending. That is the screen's whole purpose.

**Acceptance criteria**

- A test asserts a submitted job reported `PENDING` leaves the run at `QUEUED`
  with the scheduler job id recorded, and that the run moves to `TRAINING` on
  the first tick that sees `RUNNING`.
- A test asserts a 56-element array with three running reports three
  `TRAINING` and fifty-three pending.
- The Playwright S12 test asserts the same on screen.
- Gated in stages 2.4 and 2.7.

---

## 5  The fabric probe

### RF-E13 — P2 — The probe checks the wrong machine, has no launcher, and duplicates Grafana

> **Status: repository side implemented; manual side drafted.**
>
> `FabricProbe` carries the estate's probe — binary, interface, RoCE devices,
> baseline — as configuration, because every one of them is the estate's.
> Section 48.2 warns that a driver update can rename an interface, and a name
> compiled into this file would be one nobody could correct without a release.
>
> **The local `which` is gone.** It asked the control plane whether
> `all_reduce_perf` was on its PATH; the binary is built on the appliances
> under `/forge/tools` by Procedure S5 and is on no PATH anywhere. So the
> answer was always no, and the fabric would have reported itself unmeasured
> for ever on a fully commissioned estate, with a reason that sounded
> plausible. What gates it now is whether the forge has a probe configured;
> where it does, the probe is dispatched and the failure is read.
>
> **It is now a collective.** The plan rendered a bare binary name with no
> launcher, which would have run one process on whichever node Slurm picked
> and reported it as the fabric — the exact thing the docstring says a probe
> must not do. It renders `srun` across three nodes with `NCCL_SOCKET_IFNAME`,
> `NCCL_IB_HCA` and `NCCL_IB_DISABLE=0`; without those NCCL falls back to
> sockets over Fabric 2 and measures the Ethernet, which is a reading and a
> wrong one.
>
> **One thing found while fixing it that was not in the finding.** The probe
> swept from 8 bytes; acceptance test A3, which is where the baseline comes
> from, sweeps `-b 512M -e 8G`. `Avg bus bandwidth` is an average over
> whatever range was swept, so averaging in the small-message sizes where the
> collective is latency bound gives a figure several times lower than the
> baseline it is compared against. The 80 per cent alarm would have fired on a
> healthy fabric, hourly, from the first tick. The probe now sweeps A3's
> range, and there is a test saying why.
>
> The alarm logic already distinguished 'no baseline configured' from 'no
> reading', which was right and is untouched. What was missing was anywhere
> for the baseline to come from: `install.sh` now writes
> `DRAUPNIR_FABRIC_BASELINE_GBPS`, defaulting to zero, and A3 is asked to
> record the measured figure there.
>
> **Duplication resolved in DRAUPNIR's favour**, drafted as amendment 2c in
> [proposed-procedure-s13.md](proposed-procedure-s13.md). Two hourly probes on
> an `OverSubscribe=EXCLUSIVE` partition is worse than either: the second
> queues behind the first and behind any training job, and each takes the whole
> estate for its duration. The reading belongs in the chain beside the runs it
> describes — 'was the fabric healthy when this model was trained' is asked
> months later about a specific release — and the alarm belongs beside the run
> it would stop, which is a decision Grafana cannot make.

**Repository and manual.**

Three things, all in `draupnir/worker/duties.py`.

**It gates on the control plane.** `probe_installed()` (`:231`) is
`shutil.which("all_reduce_perf")`. The binary is built by Procedure S5 at
`/forge/tools/nccl-tests` **on the appliances**, and is not on `PATH` even
there. ALVISS will never have it. So the probe reports "unmeasured" for ever,
including on a fully commissioned estate — which is recorded as a deviation in
the reconciliation, but the reason given ("not installed on a control plane")
describes a check that should not have been on the control plane in the first
place. `probe_plan` dispatches the benchmark as a `ring` job; the binary needs
to exist where that job lands.

**It has no launcher and no NCCL environment.** `probe_plan` (`:213`) renders
`all_reduce_perf -b 8 -e 8G -f 2 -g 1` with `NCCL_DEBUG=WARN` alone. The
estate's nccl-tests is built `MPI=1` and the manual's own A3 acceptance test
runs it across three appliances; the ring needs
`NCCL_SOCKET_IFNAME=enp1s0f0np0`,
`NCCL_IB_HCA=rocep1s0f0,rocep1s0f1,roceP2p1s0f0,roceP2p1s0f1`,
`NCCL_IB_DISABLE=0` and an `srun`/`mpirun` launcher, exactly as §40's ring
script does. As rendered, the probe measures one machine — the thing its own
docstring says a probe must not do.

**It duplicates something already built.** Grafana dashboard 2 (§34 step 8) is
specified as "hourly synthetic all-reduce bandwidth probe" with an alert below
80 per cent of the S4 baseline. That is the same duty, on the same interval,
with the same threshold, in a different system.

**Prompt**

> Fix the probe, then decide who owns it.
>
> 1. Replace `probe_installed()` with a check performed where the job runs, or
>    delete it: dispatching the probe and reading the failure is a better
>    signal than a `which` on the wrong host. If a pre-check is kept, it must
>    ask the scheduler, not the local filesystem.
> 2. Render the probe as the estate runs it: `srun` across three nodes on the
>    `ring` partition, with the NCCL interface and HCA variables and an
>    absolute path to `all_reduce_perf`, all from configuration rather than
>    from constants — the interface names are the estate's, and §48.2 warns
>    that a driver update can rename them.
> 3. Resolve the duplication. Either DRAUPNIR owns the probe and Grafana
>    dashboard 2 reads the result from DRAUPNIR, or Grafana owns it and
>    DRAUPNIR's duty reads Prometheus. Two hourly `ring`-partition jobs both
>    claiming exclusive use of all three appliances is worse than either: the
>    `ring` partition is `OverSubscribe=EXCLUSIVE`, so the second probe queues
>    behind the first and behind any training job, and its reading will be
>    stale by an unknown amount. Recommended: DRAUPNIR owns it, because the
>    result belongs in the chain and the alarm belongs beside the run it would
>    stop.
> 4. Take the baseline from Procedure S4 rather than leaving it at `0.0`. The
>    80 per cent alarm cannot fire without it, and the manual already measures
>    and records the number at commissioning.

**Acceptance criteria**

- A test asserts the rendered probe carries an `srun` launcher, three nodes,
  the configured NCCL interface and HCA values, and an absolute binary path.
- A test asserts the probe is not gated on a local `which`.
- A test asserts the alarm fires at 80 per cent of a configured baseline and
  reports "no baseline configured" distinctly from "no reading".
- Manual: dashboard 2's data source is stated, and it is not a second probe
  job.
- Gated in stages 2.1 and 2.4.

---

## 6  The consoles

### RF-E14 — P2 — Neither console the repository builds is the console the manual installs

> **Status: CON-A resolved, repository side and drafted. CON-B deliberately
> left alone.**
>
> **The reason the `xterm` is on the panel was not an oversight.** Nothing
> built or shipped `stedi-view`: not a workspace member, not in an image, not
> in `deploy/`, not in the pipeline. It was written, tested against a refusing
> network and an address that does not route, cited in the reconciliation as
> IMPLEMENTED — and there was no artefact for anyone to install on DVALIN.
> Procedure S12 step 10 uses the `xterm` because the alternative could not be
> installed, whatever this repository said about it. That is the finding
> underneath the finding, and it is fixed: `python tasks.py con-a` builds the
> wheel, the pipeline builds it at stage 3.1b and uploads it with the rest of
> the evidence, and a test asserts both so it cannot quietly stop.
>
> A wheel rather than a container, and the test says why: CON-A's whole value
> is depending on nothing beyond the appliance it is attached to (Decision
> U2), and a container runtime is a dependency that would have to survive the
> same total network failure the view is bought for. A second test asserts its
> dependency list is still empty.
>
> The manual amendment is drafted, including A11's pass criterion — which
> currently says only that CON-A 'continues to display appliance state', and a
> panel showing nothing but `scheduler unreachable` satisfies that reading.
>
> **CON-B is blocked, and the block is the point.** Rotating the wall panel
> between Grafana dashboard 1 and DRAUPNIR's `/kiosk` is the right answer —
> neither carries what the other does, and S31 already specifies three
> dashboards on a timer. But pointing the panel at the DRAUPNIR console means
> publishing that console on the fabric the appliances share, and there is
> today no authentication on the API at all (RF-01) and no ingress or TLS
> (RF-03). `draupnir-run.sh` binds the console to `127.0.0.1`; changing that
> before those two land would put an unauthenticated read surface — run names,
> model identifiers, the forge's anchor state — on the segment DVALIN, DURIN
> and DAIN are on.
>
> So the proposal adopts the CON-A half now and leaves step 9 as it is, with
> the bind address and the authentication path to be stated alongside the
> change when RF-01 and RF-03 are done. Deploying it sooner would be the
> smaller job and the wrong one.

**Manual and repository.**

| Console | Manual §34 | Repository |
|---|---|---|
| **CON-A** on DVALIN | `xterm -e watch -n5 'nvidia-smi …; ibdev2netdev; sinfo'` | `tools/stedi-view/`, the S30 local view, "imports no HTTP client and nothing from `draupnir`", tested with every source unavailable |
| **CON-B** on REGIN | `chromium --kiosk http://localhost:3000/d/thermal` — Grafana dashboard 1 | The console's `/kiosk` route, S31, three rotating dashboards with a staleness banner |

Both repository consoles are built, tested and accessible; neither is deployed.
Acceptance test A11 ("Power off REGIN, then read CON-A") passes against the
`xterm`, so nothing at the site would notice.

For CON-B there is a further constraint: REGIN is on Fabric 3 *and* Fabric 2,
so it can reach the console on ALVISS at `http://10.20.0.22:8080` — but
`draupnir-run.sh` publishes the web unit on `127.0.0.1` by default, which is
ALVISS's loopback. The kiosk cannot reach it without `DRAUPNIR_WEB_BIND`
being set to the Fabric 2 address, and doing that exposes the console with no
TLS and no authentication (companion register, RF-01 and RF-03).

**Prompt**

> Deploy the consoles that were built, or delete them.
>
> 1. **CON-A.** Amend Procedure S12 step 10 to launch `tools/stedi-view` on
>    DVALIN instead of the `xterm` one-liner, and amend acceptance test A11 to
>    assert against it. The view was specified precisely because it works when
>    everything else is down, and an `xterm` running `sinfo` does not: it prints
>    "scheduler unreachable" and shows nothing else about the run.
> 2. **CON-B.** Decide which dashboard the wall panel shows. Grafana dashboard
>    1 carries thermal and throttle, which DRAUPNIR has no source for (see
>    RF-E15); DRAUPNIR's `/kiosk` carries run state, queue depth and anchor
>    state, which Grafana has no source for. The honest answer is that the
>    panel should rotate both, and Chromium can be given two URLs. State the
>    decision in the manual.
> 3. Before pointing anything at `http://10.20.0.22:8080`, resolve RF-01 and
>    RF-03 in the companion register. Publishing an unauthenticated console on
>    a fabric shared with the appliances is not a smaller decision than it
>    looks.

**Acceptance criteria**

- Manual: Procedure S12 step 10 launches `stedi-view`; A11's pass criterion
  names it.
- A test asserts `stedi-view` renders with the API, the scheduler and the
  network all unavailable — it exists, extend it to be the acceptance evidence
  for A11.
- Manual: CON-B's source is stated, and if it includes the DRAUPNIR console,
  the bind address and the authentication path are stated with it.
- Gated in stage 2.1.

---

### RF-E15 — P3 — The thermal and fabric signals live in Prometheus, and DRAUPNIR is not connected to it

> **Status: done. Two defects found on the way, both in the egress broker.**
>
> The wire is built. `draupnir/gullinbursti/telemetry.py` reads the estate's
> own Prometheus — GULLINBURSTI, because SAD 11A.1 gives it "report capacity
> and health" — and `GET /v1/estate/telemetry` puts the readings where CON-B
> can render them. The thermal panel showed `under load` or `idle` derived
> from run state; an appliance that is idle *because* it is thermally
> throttled read `idle`, in grey, which is the one case the panel exists for.
> It now shows a temperature.
>
> **Read only, and it never invents a reading.** A `Reading` is a value or a
> reason and the constructor refuses both and neither, so `unmeasured` cannot
> be rendered without a cause and a value cannot be rendered with an excuse.
> Zero is a measurement: a throttle bitmask of zero means nothing is
> throttling, which is exactly why an *absent* one must not also be zero. Six
> failure paths are tested separately — no client, unreachable, non-200, a
> body that is not JSON, a refused query, and an appliance Prometheus has
> never heard of — and every one produces a reason a panel can show.
>
> **The exporter's metric names are configuration**, not constants. §48.2
> already warns that an update can rename things underneath this estate, and
> a name only a release could correct is a name nobody corrects.
>
> **First defect: the broker could not tell two services on one host apart.**
> `Destination.matches` compared host and scheme and ignored the port, so the
> Prometheus entry on REGIN also matched slurmrestd on 6820 — a console
> holding the telemetry approval could have cancelled a job under it. A
> destination now carries a port.
>
> **Second defect, which the first was hiding: the scheduler destination did
> not match the URL the driver calls.** It declared `https` on any port;
> `motsognir.slurmrest/v1` calls `http://regin.sindri.veldris.internal:6820`.
> A brokered submission would have been refused as undeclared egress, and the
> refusal would have presented as the scheduler being down. Both entries are
> now scheme- and port-exact, with separate approving policies, and a test
> asserts the driver's own `base_url` resolves to one.
>
> **And a client that cannot skip the broker.** `EgressBroker` was a decision
> procedure nobody was obliged to invoke — which is the shape a dependency
> phoning home slips past. `BrokeredClient` wraps the transport, so a caller
> holding one has no route to the network that does not go through the
> broker first. The composition root is the only place the declaration
> (GULLINBURSTI) and the decision (SVALINN) are put together; they are
> siblings in the layering and neither imports the other, and a test reads
> both to assert they still agree.
>
> **The manual half is drafted as amendment 2e, and it is not one line.**
> Adding `10.20.0.22:8000` to `scrape_configs` publishes `/metrics` on the
> segment DVALIN, DURIN and DAIN share — and SAD 8.1's control for that
> endpoint *is* the loopback binding, not a credential. So the amendment
> carries the packet filter rule limiting port 8000 to REGIN, rather than
> leaving it to whoever does the work. It does not move the API's bind
> address: that is amendment 2d, still blocked on RF-01 and RF-03.
>
> Also removed: amendment 2c had been written into the proposal twice during
> RF-E13.
>
> **What this does not deliver.** The scrape target is the *prerequisite* for
> RF-18, not a substitute: `/metrics` still exposes only Python GC counters,
> so the fabric panel will read `unmeasured` — with that reason — until
> RF-18 registers the probe's reading. Thermal and throttle work today,
> because DCGM is already collecting them.

**Repository and manual.**

SAD 11.3 gives eight signals a source and a surface. On this estate, four of
them are already collected — by Prometheus on REGIN, scraping
`prometheus-node-exporter` and the DCGM exporter on each appliance:

| Signal | Where it is | Where DRAUPNIR looks |
|---|---|---|
| Appliance thermal and throttle | DCGM exporter → Prometheus on REGIN | Nowhere. `Kiosk.tsx:104` derives "under load / idle" from run state |
| Fabric bandwidth | Grafana dashboard 2 | Its own probe (RF-E13) |
| Job queue | Slurm → Grafana dashboard 3 | Its own projection |
| Vault free space | Grafana dashboard 4 | `duties.capacity`, on a host with no mount (RF-E04) |

And in the other direction: `.github/workflows` aside, the estate's Prometheus
scrape configuration (§34 step 7) lists the three appliances, REGIN and MLflow.
**ALVISS is not a target**, so DRAUPNIR's `/metrics` — which currently exposes
nothing but Python GC counters anyway (companion register, RF-18) — is scraped
by nothing.

**Prompt**

> Connect the control plane to the estate's observability rather than beside
> it.
>
> 1. Manual: add `10.20.0.22:8000` to the `forge-nodes` scrape configuration,
>    or add a `draupnir` job for it. This is a one-line change and it is the
>    prerequisite for every metric RF-18 asks for being visible anywhere.
> 2. Repository: add a read-only Prometheus query client so the thermal,
>    throttle and fabric panels of CON-B render measurements from the estate's
>    own collector rather than inventing a source or showing run state in place
>    of temperature. It goes through the egress allow list with
>    `regin.sindri.veldris.internal` declared.
> 3. Where a reading is unavailable, say `unmeasured` and why — never a zero
>    and never a green tile. This is the same requirement as RF-28 in the
>    companion register; the estate answer is that the data exists and DRAUPNIR
>    is not reading it.

**Acceptance criteria**

- Manual: ALVISS appears in `prometheus.yml`, and §34 step 7's completion check
  includes it.
- A test asserts the Prometheus client is declared in the egress allow list and
  that a query failure renders `unmeasured` with a reason, not a zero.
- A Playwright test asserts CON-B renders a real temperature when the query
  returns one.
- Gated in stages 2.3 and 2.7.

---

## 7  Executors, egress and security

### RF-E16 — P2 — The sandbox profile describes an execution model the estate does not use

> **Status: done, model 1 adopted as the register recommended. Reading
> `slurm.conf` closely turned up a second finding underneath it.**
>
> **The gap was invisible because nothing was broken.** The design in
> `sandbox.py` is right, the module is complete, its tests pass, and an
> auditor reading the threat model finds T7 closed. What was absent was the
> estate that would apply it — and a control described as in force is one
> nobody implements, precisely because it reads as already done. That is the
> failure mode worth naming; it is not a bug and nothing was skipped.
>
> Model 1 is adopted. `sandbox.py` now says in its first line that it is a
> specification and not a control in force; `containment.py` states what is;
> `docs/DEPLOYMENT.md` no longer says "one container per job"; and a test
> reads the AST of every module on the dispatch path and fails if any of them
> imports the profile. That test is written to fail in both directions — when
> a container executor is eventually built it will break, and ask whether the
> documents were updated with it.
>
> **The finding underneath: the cgroup containment is not switched on.** This
> register said the estate had "cgroup containment from
> `ProctrackType=proctrack/cgroup` and `TaskPlugin=task/cgroup` and nothing
> else". The manual has no `cgroup.conf` at all, and that is what "nothing
> else" turns out to include:
>
> - `proctrack/cgroup` tracks a job's processes so they are **killed** with
>   it. A real guarantee, and cleanup rather than a limit.
> - `task/cgroup` places tasks in cgroups **so that `cgroup.conf` can
>   constrain them**. With no such file every constraint takes its default,
>   and `ConstrainRAMSpace`, `ConstrainDevices`, `ConstrainCores` and
>   `ConstrainSwapSpace` all default to `no`.
>
> So `RealMemory=122880` is scheduling arithmetic that enforces nothing — a
> job that allocates past it takes the appliance's other work with it, and on
> the `ring` partition that is the estate — and `Gres=gpu:gb10:1` sets
> `CUDA_VISIBLE_DEVICES` for a job that chooses to read it.
>
> **And there is no job account.** Section 25 creates `nvidia`, puts it in
> `sudo`, and section 26 gives it `/forge`. Nothing introduces a second
> account, so a training job runs as the administrator — a compromised
> dependency inherits `sudo` and ownership of `/forge/tools`, which is the
> training stack for every later run.
>
> **Four of the seven differences are configuration, not architecture**, and
> that is only visible once the difference is enumerated rather than
> described. `containment.shortfall()` returns them as a list: a dedicated
> account, `ConstrainRAMSpace`, `ConstrainDevices` and `--export=NONE` need no
> container runtime, no image for the training stack and no change to the
> batch scripts beyond one `#SBATCH` line. Amendment 2f drafts them. The
> remaining three — read-only root, no-new-privileges, no outbound network —
> need the container model, and the network one cannot be closed at all
> without it, because the job must reach MLflow.
>
> **AC-S11's deviation was pointing at the wrong thing.** It read "enforcing
> it needs the appliance's kernel", which locates the gap in hardware that is
> on order — and the appliances arriving would not close it. It now says the
> execution model, and it is the one DEVIATED entry that no longer carries the
> "measured at commissioning" sentence, because commissioning the estate as
> specified produces a job with a network rather than a sandbox to test.
>
> SAD amendment 4 drafts §9.x, and rewrites T7 and T11 to carry a residual
> risk rather than a mitigation that is not in place. A residual risk that is
> written down gets an owner, a review date and a compensating control; one
> recorded as mitigated gets none of those and is found by whoever is
> investigating something else.

**Repository and manual.**

`draupnir/svalinn/sandbox.py` generates an executor profile with no outbound
network, read-only artefact mounts and no privilege escalation, and
`docs/DEPLOYMENT.md:457` describes the appliances as running "Executor shims,
one container per job".

On the estate, jobs are not containers. Procedure M6 runs
`python /forge/tools/LLaMA-Factory/src/train.py` inside `/forge/venv` under
`slurmd`, with cgroup containment from `ProctrackType=proctrack/cgroup` and
`TaskPlugin=task/cgroup` and nothing else. And the profile's two strongest
properties are incompatible with the work as specified:

- **No outbound network.** The job reports to MLflow at
  `http://10.20.0.21:5000` and reads the corpus over NFS from the same host.
- **Read-only artefact mounts.** The job writes checkpoints to
  `/forge/vault/models/adapters/${ISO3}/${RUN}` — on the vault.

The profile is not wrong as a design; it describes a container-per-job estate
that has not been built. What is wrong is a document saying it is in force.

**Prompt**

> Reconcile the executor model, and say which is being built.
>
> **If the estate stays as specified** — Slurm jobs in a shared venv — then
> amend `docs/DEPLOYMENT.md:457` and SAD 9.x to describe the containment that
> actually exists (Slurm cgroups, a dedicated service account, the vault
> mounted read-write for outputs and read-only for corpora), and rewrite
> `sandbox.py`'s profile as the *specification for a future container
> executor*, clearly marked, rather than as a control in force.
>
> **If the container-per-job model is intended**, it is a substantial addition
> to the manual: a container runtime on each appliance beyond the DCGM
> exporter, an image for the training stack, `--container-image` style
> integration through Pyxis or a wrapper, a writable output mount and an egress
> exception for MLflow. That is a Procedure S5 change and a change to every
> sbatch script in Part 5.
>
> The first is recommended for Release 1. The second is a real improvement and
> should be a recorded gap with an owner rather than an implied current state.

**Acceptance criteria**

- One of the two models is documented as in force, in both the SAD and the
  manual, with the other recorded as a gap.
- If the profile stays as a specification, a test asserts it is not applied to
  any dispatched plan and that its module docstring says so.
- If containers are adopted, the manual's sbatch scripts and Procedure S5 are
  amended, and a test asserts a rendered plan carries the image reference.
- Gated in stage 2.1.

---

### RF-E17 — P3 — The two egress allow lists disagree, and DRAUPNIR's entries are incomplete

> **Status: done. The disagreement is nine hosts, not two, and three of them
> are reached by the manual's own procedures.**
>
> **What makes this worth more than a list.** A destination the DRAUPNIR
> broker refuses produces a log line naming the destination and the policy. A
> destination the *router* refuses produces nothing on the host at all: the
> packet leaves and is dropped elsewhere, and the library reports a timeout.
> One of those is diagnosed in a minute; the other is diagnosed as the
> internet being slow. Every finding below is of the second kind.
>
> **The estate cannot commission itself under its own policy.** §10.5 says
> "all other outbound traffic is denied", and the manual's own procedures
> reach three hosts it does not list:
>
> - `astral.sh` — Procedure S6 step 1 and S10 step 1 install uv from it, on
>   all five machines.
> - `download.pytorch.org` — Procedure S6 step 2, `--index-url
>   https://download.pytorch.org/whl/cu130`. This is the training stack, and
>   the CUDA build is not on PyPI.
> - `nvidia.github.io` — Procedure S5, the libnvidia-container signing key and
>   apt source, installed before the DCGM exporter that §34 step 7 scrapes.
>
> **The control plane's images cannot be built.** `docker/` starts from
> `ghcr.io` (uv), `gcr.io` (distroless) and `cgr.dev` (Chainguard), plus the
> two blob hosts those redirect to. §10.5 permits Docker Hub — which nothing
> in the programme is recorded as using — and forbids all five.
>
> **And the corpus cannot be acquired**: `www.legislation.gov.uk`, named in
> the Part 5 GBR manifest, is not listed either.
>
> **Four of the nine are blob hosts** — the second request of a two-request
> operation. That is the same shape as the original finding: DRAUPNIR declared
> `huggingface.co` and not `cdn-lfs.huggingface.co`, so the metadata request
> succeeded, the transfer started, and the transfer stopped. It presents as a
> corrupt download rather than a policy decision, and the usual first response
> is to retry it. Every redirect target is now a declared entry in its own
> right, carrying `redirect_from` so the second row reads as a reason rather
> than duplication.
>
> **The prompt's design was wrong in one respect and it is worth recording
> why.** It asked for the router's allow list to be *generated* from
> `egress.py`. The two are not the same list: §10.5 governs everything on the
> estate, including apt and pip traffic DRAUPNIR never makes, and `egress.py`
> holds internal destinations — REGIN, MEGINGJORD — which are on the site
> fabrics and never reach the router at all. Generating one from the other
> would have put internal hostnames into an internet egress policy and
> stripped the appliances' ability to patch. What is generated instead is a
> *reconciliation*, in both directions, and `Destination.traverses_site_router`
> is what keeps the three internal entries from reporting as blocked.
>
> **The gate is on movement, not on the state.** None of the nine can be fixed
> from this repository — §10.5 is in a controlled document. A gate on "nothing
> is blocked" would have been red from the day it was added and would
> therefore be ignored; a build that is always red is not a signal. So the
> baseline is recorded with a reason per entry, and stage 2.4b fails on either
> kind of movement: a tenth blocked host, which somebody introduced here, or a
> baseline entry that stops being blocked, which means §10.5 was amended and
> the transcription is a revision behind. Both are tested.
>
> **What the pipeline still cannot check, stated rather than left to be
> discovered.** The reconciliation compares against a *transcription* of
> §10.5, because a test cannot read a controlled document that lives outside
> the repository and is marked CONFIDENTIAL. So CI catches every change on
> this side and none on that one. Acceptance test A15 closes it: eighteen
> hostnames, in order, about a minute. A transcription nobody re-checks would
> be this same finding one level down.
>
> Also corrected: the register said seventeen hosts. §10.5 has eighteen, and a
> test asserts the count — a line dropped while copying eighteen hostnames is
> not something anyone notices by reading.
>
> AC-S3 holds: the teacher destination is absent from both policies, its test
> remains, and the generated report names the absence — evidence that does not
> record what is missing on purpose reads as an oversight.

**Repository and manual.**

The site router enforces seventeen hosts (§10.5). `draupnir/svalinn/egress.py`
declares three. They overlap in two, and both of those are incomplete for their
stated purpose:

| DRAUPNIR declares | Purpose | Problem |
|---|---|---|
| `huggingface.co` | "base model and tokeniser acquisition" | Weights are served from `cdn-lfs.huggingface.co`, which the router permits and DRAUPNIR does not |
| `pypi.org` | "dependency resolution at image build time" | Wheels come from `files.pythonhosted.org`, likewise |
| `megingjord.veldris.internal` | anchoring, policy, release metadata | Not in the router list, in a zone the site does not serve (RF-E02), and reached over a WireGuard link that is not built |

A run declaring `huggingface.co` and following a redirect to `cdn-lfs` is
refused by DRAUPNIR's broker and permitted by the router; the reverse holds for
`registry-1.docker.io`. Neither list is the authority, which means neither is
evidence.

**Prompt**

> Make one list the source and generate the other.
>
> `svalinn/egress.py`'s comment says the list "is evidence rather than
> configuration", which is the right instinct and the reason to make it the
> authoritative one. Extend it to cover every destination the programme
> actually reaches, each with its purpose and approving policy, add the
> redirect targets (`cdn-lfs.huggingface.co`, `files.pythonhosted.org`,
> `codeload.github.com`, `production.cloudflare.docker.com`) that the bare
> hostnames imply, and generate the router's allow list from it as a build
> artefact — the same relationship the cryptographic inventory has to the
> constants it is generated from.
>
> Then add a test that reads §10.5 of the manual and fails when the two lists
> diverge, so a destination added to one is added to both.
>
> `megingjord.veldris.internal` should carry a note that the link is gap-listed
> and the destination is not yet reachable, rather than sitting in the list
> looking like a live permission.

**Acceptance criteria**

- The generated router allow list matches §10.5, and the test fails when
  either changes alone.
- Every entry names a purpose and an approving policy.
- The teacher-model destination remains absent and its test remains.
- Gated in stages 2.1 and 2.3.

---

### RF-E18 — P3 — The UPS status file has no contract, and no host is nominated to receive the signal

> **Status: repository half done. The routing problem is worse than this
> entry said, and the fix is a different cable.**
>
> **This register said the UPS "feeds PDU-A and PDU-B". It does not.**
> VLD-WIR-SINDRI-001 §5.1 cable P-01 reads "UPS inserts here when fitted, gap
> G1", and P-01 is Wall outlet A → **PDU-A only**. PDU-B and PDU-C are both on
> Wall outlet B and neither is protected. So on mains loss, as specified:
>
> - the three appliances stay up on battery;
> - **ALVISS dies**, so DRAUPNIR — the thing §7.4 says will force the
>   checkpoint — is not powered when the signal arrives;
> - **ANDVARI dies**, so `/forge/vault` is gone and a job writing a checkpoint
>   blocks in uninterruptible sleep on a hard NFS mount. There is nowhere for
>   the forced checkpoint to go;
> - **REGIN dies** (GaN hub P-10, on PDU-B), so no scheduler, no DNS, no clock;
> - **NAIN and NORI die** (P-11, P-12), so the three survivors cannot reach
>   each other;
> - **every fan dies** — FAN-A1/B1/B2 on the GaN hub, both GDSTIME sets on
>   PDU-C — leaving 720 W of compute at full training load with no extraction.
>
> The UPS as placed keeps alive exactly the three machines that can do nothing
> alone, kills the one machine that is supposed to act, and removes the
> destination the checkpoint would be written to.
>
> **So the prompt's own recommendation cannot be followed as written.** It
> says "ALVISS is the natural choice — it is on PDU-B". True, and PDU-B is
> not on the UPS. The proposal moves the insertion point to P-02: if only one
> PDU can be protected it must be the one holding the control plane, the
> vault, the scheduler and the network, because that is the only choice under
> which a graceful stop is possible at all.
>
> **And a 1500 VA unit cannot hold both.** Roughly 720 W (PDU-A at training
> load) plus 300 W (PDU-B) against about 900 W real for a 1500 VA
> line-interactive unit. So §7.4's "forces an immediate checkpoint on every
> running job" is only achievable on a larger unit; on the one on order, the
> honest text is that the appliances stop at once and jobs resume from their
> last periodic checkpoint. `proposed-wiring-amendments.md` puts the three
> options and their consequences side by side rather than choosing for the
> site.
>
> **The repository half is done, and one defect was found doing it.** A status
> file whose daemon has died is well formed, reports `OL` and reports a full
> battery — and if the daemon died with the power, that was true at the moment
> it was written and has not been true since. `read_status_file` believed it.
> A reading older than 180 seconds is now refused: no supply signal is a worse
> position than a working one and a much better position than a wrong one.
>
> Also fixed: an unreadable status file killed the worker tick, taking run
> dispatch, the duties and every alarm down with it — over a signal for
> hardware that is not fitted. It is now an action of its own kind. Reported,
> **not acted on**: a drain on an unreadable file would stop the estate for a
> daemon restart, would outlast its cause, and would be disabled by somebody
> within a month.
>
> The contract is `draupnir/supply-status/v1`, versioned, and it **is** the
> block `upsc` prints — chosen rather than invented, so a site running NUT
> redirects a command it already has and needs no translation layer. A
> translation layer nobody needs is one nobody notices has stopped running.
> `supply_adapters.from_apcaccess` converts the other common daemon, tested
> against a captured sample.
>
> `draupnir-run.sh` bind-mounts the file read-only into the worker only —
> read-only being the whole relationship here rather than a precaution, since
> a writable mount would let the control plane edit the evidence it acts on.
> Guarded on the file existing, so every estate today starts unchanged.
>
> The estate half is drafted in
> [proposed-wiring-amendments.md](proposed-wiring-amendments.md), against
> VLD-WIR-SINDRI-001 Rev 2.0 — a third proposal document, because §5.1, §7.1,
> §7.4 and gap G1 all belong to the wiring document rather than the manual.
>
> **Still the site's to decide:** the insertion point, the unit size, and
> whether PDU-C joins the protected circuit or the drain threshold is set by
> thermal headroom instead. All three are drafted with their consequences.

**Repository and manual.**

`draupnir/motsognir/supply.py` reads a status file written by the supply's
daemon and decides what to do — forced checkpoint on transfer to battery, then
drain, then halt at the low-battery threshold. `worker/loop.py:554` reads
`DRAUPNIR_SUPPLY_STATUS`.

The estate has no UPS: gap G1, "on order", a 1500 VA line-interactive unit
feeding PDU-A and PDU-B, with "USB signalling drives a forced checkpoint in
DRAUPNIR". The wiring document's §7.4 abnormal-conditions table describes the
same behaviour.

Nothing on either side says: **which host the USB cable goes to**, which daemon
writes the file (`apcupsd`? `nut`?), what format DRAUPNIR expects, or where the
file lives. DRAUPNIR runs in a container, so the file must be bind-mounted in,
and `draupnir-run.sh` mounts nothing.

There is also a routing problem worth settling now. The UPS feeds PDU-A (the
three appliances) and PDU-B (ANDVARI, ALVISS, the GaN hub). PDU-C — the fans
and CON-B — is **not** on the UPS. So on mains loss, cooling stops while the
appliances run on battery, which inverts the §7.1 rule that "cooling starts
first and stops last".

**Prompt**

> Specify the supply signal end to end before the hardware arrives, while it is
> still cheap.
>
> 1. Manual: nominate the host the UPS USB connects to. ALVISS is the natural
>    choice — it is on PDU-B, it runs the worker, and it is the host that must
>    act. Name the daemon (`nut` with `usbhid-ups` is the portable answer on
>    macOS) and the file it writes.
> 2. Repository: document the status file's schema in `supply.py`'s docstring
>    as a stable contract, and provide a small adapter that renders the chosen
>    daemon's output into it, so the estate is not obliged to produce
>    DRAUPNIR's private format.
> 3. `draupnir-run.sh` must bind-mount the file read-only into the worker
>    container. Add it, guarded on the path existing, so a site with no UPS
>    starts unchanged.
> 4. Manual: settle the cooling question. Either PDU-C joins the UPS, or the
>    drain threshold is set so that the appliances stop before the room heats,
>    and §7.1's ordering rule gains an exception stating why. Record the
>    decision against gap G1 rather than discovering it during the first
>    outage.

**Acceptance criteria**

- The status file schema is documented and versioned, and a test asserts the
  adapter produces it from a captured sample of the chosen daemon's output.
- A test asserts the worker container receives the file read-only when the path
  exists and starts normally when it does not.
- Manual: gap G1 names the host, the daemon, the file, and the PDU-C decision.
- Gated in stages 2.1 and 2.4.

---

## 8  Documents

### RF-E19 — P2 — The build manual has no procedure that installs DRAUPNIR

> **Status: done. The procedure was already drafted; what was missing was the
> sentence saying which document wins, and a test that the guide's values are
> the ones the scripts produce.**
>
> Procedure S13 and acceptance tests A12 to A15 were drafted across RF-E01 to
> RF-E17 as each finding added a prerequisite to it. The two criteria that
> were still open are the ones this entry is really about.
>
> **Which document is authoritative, now stated on both sides.** The answer
> adopted is the register's own: **by hand at commissioning, through DRAUPNIR
> thereafter.** It is not a compromise — it is what each document is good for.
> At commissioning you are proving hardware, and a control plane between the
> operator and the hardware makes a fault harder to locate; A1 to A11 should
> stay hand-driven. After acceptance the balance reverses, because a run
> driven by hand produces no ledger entry and the ledger is what the Article
> 53 obligation is discharged against. A hand-driven run after commissioning
> is not a shortcut, it is an artefact with no provenance.
>
> `docs/DEPLOYMENT.md` gains a *Which document is in charge* section saying
> so, and naming S13 as the seam — including that neither document repeats
> the other, which is the property that stops them drifting. Amendment 3a
> drafts the matching paragraph for §5.
>
> **The value test found a dead end.** `install.sh --check` has reported on
> the scheduler since RF-E05, and `docs/DEPLOYMENT.md` named neither the
> dependency, the host it reaches, nor what to do when it fails. An operator
> seeing `scheduler: unreachable` had nowhere to look — and the guide's own
> instruction is to stop when the output does not match the page. It now
> carries both failure lines with their remedies, and says the address is
> derived rather than configured so nobody goes looking for a setting.
>
> **And I made exactly the error the finding is about, which is why there are
> two tests rather than one.** The first scheduler line I wrote into the guide
> was in the wrong section and worded plausibly rather than correctly.
> `test_the_guide_only_shows_check_lines_the_preflight_can_produce` reads the
> verdicts the guide tells an operator to look for and fails when
> `preflight.py` cannot emit one; it was verified by injecting a bad verdict
> and watching it fail. The second asserts every dependency the check reports
> is mentioned somewhere in the guide at all.
>
> The value test proper reads the five figures from the places that decide
> them — `lib.sh`'s own `draupnir_host_for`, `Settings`, and `install.sh`'s
> defaults — rather than from a second list, so it fails when the guide drifts
> from the scripts *or* when the scripts change under a guide nobody updated.
>
> Not done here: reconciling the guide's estate table against the manual's,
> which is RF-E20 and is the next finding.

**Manual.**

Part 4 runs S1 to S12 and ends with Slurm and observability. DRAUPNIR is named
eleven times across the manual — as the control software (C12), as the
discharge of the Article 53 obligation (C14), as the residency check at job
planning (C19), as the ALVISS host role (§6), as layer L6b (§12) — and there
is no procedure that installs it, no acceptance test that exercises it, and no
operational step that touches it.

Section 47's schedule runs A1 to A11 and none concerns the control plane.
Section 48's weekly, monthly and per-release lists are entirely manual
operations — `scontrol`, Grafana, `sacct`, purging scratch — which is
consistent, because the manual's Part 5 does the pipeline by hand and §5's
schedule note says as much: "The figures also assume the pipeline is driven by
hand per Parts 4 and 5."

That is a legitimate position for Release 1. What is not legitimate is that
neither document says which of the two is in force at commissioning, so an
operator following the manual builds an estate with no control plane and an
operator following `docs/DEPLOYMENT.md` installs a control plane onto a host
the manual never prepared.

**Prompt**

> Add **Procedure S13, DRAUPNIR control plane** to VLD-INF-SINDRI-001, after
> S12, and make it the join between the two documents. It should carry the
> ANDVARI provisioning of RF-E03, the ALVISS runtime of RF-E01, the vault mount
> of RF-E04, the scheduler access of RF-E05, and then defer to
> `docs/DEPLOYMENT.md` for the installation itself rather than duplicating it —
> a procedure copied into two documents is a procedure that is wrong in one of
> them.
>
> Add acceptance tests to §47:
>
> - **A12 Control plane reachable.** `install.sh --check` reports every
>   dependency green; `/readyz` reports ready.
> - **A13 Ledger integrity.** `make verify-chain` verifies the site's chain and
>   a rebuilt projection is byte-identical.
> - **A14 End-to-end through DRAUPNIR.** The A10 worked example driven through
>   the control plane rather than by hand, producing the same release package.
>
> Add to §48: a weekly chain verification and a per-release confirmation that
> the gate evidence in DRAUPNIR matches the artefacts in the registry.
>
> And state, once, in both documents, which is authoritative for Release 1:
> the manual's by-hand procedures or DRAUPNIR's automation of them. The
> honest answer is probably "by hand at commissioning, DRAUPNIR thereafter",
> and saying so removes the ambiguity from every procedure in Part 5.

**Acceptance criteria**

- Procedure S13 exists and does not duplicate `docs/DEPLOYMENT.md`.
- A12, A13 and A14 are in §47 with pass criteria.
- Both documents state which is authoritative for Release 1.
- `tests/unit/test_documentation.py` asserts `docs/DEPLOYMENT.md` cites S13 and
  that the host, database, bucket, vault path and scheduler endpoint it names
  match the values in the manual.
- Gated in stage 2.1.

---

### RF-E20 — P3 — The deployment document describes an estate that differs from the built one in four places

> **Status: done. Reading the two tables side by side turned up four places
> the estate documents disagree with *themselves*.**
>
> The table is corrected and now held against a transcription of §2 by a test,
> so it cannot drift on its own. The transcription lives in
> `draupnir/gullinbursti/roster.py` with its provenance, on the same terms as
> RF-E17's copy of §10.5 and with the same limit: CI cannot read a controlled
> document, so the transcription is checked by hand at acceptance and
> everything downstream of it is checked by machine.
>
> **On Loki, this register was slightly wrong and the truth is worse.** It said
> "REGIN runs Prometheus, Alertmanager and Grafana. There is no Loki" — as
> though the deployment guide had invented it. §2's role table gives REGIN
> "Prometheus, Grafana, Loki" and §12's diagram repeats it; Procedure S11
> installs `prometheus prometheus-alertmanager grafana` and nothing anywhere
> installs a shipper. The guide inherited a claim from a role table the
> procedures do not carry out. Deleting the word from one document would have
> left the other two saying it.
>
> **The log-shipping decision: no aggregation, recorded as a decision.** REGIN
> is a Pi 5 already running slurmctld, slurmdbd, MariaDB, Prometheus,
> Alertmanager, Grafana, dnsmasq, chrony and a kiosk browser; ingesting six
> hosts is not a small addition to it. DRAUPNIR's lines already carry run id,
> site id and actor (SAD 11E), which is what makes them worth reading one host
> at a time. Both documents now say so, and a test asserts the guide keeps
> saying it — dropping the row silently would leave the estate with no
> aggregation and no document admitting it, which is worse than the wrong row,
> because at least a wrong row is falsifiable.
>
> **Three more contradictions, recorded in `roster.CONTRADICTIONS` with the
> half this repository follows:**
>
> - **CON-B.** §2 says REGIN "Drives CON-B"; §6.3 says "CON-B held as bench
>   spare". RF-E13's dashboard-2 amendment and RF-E14's console rotation both
>   target it. Followed: fitted — VLD-WIR-SINDRI-001 gives it D-03, D-04 and
>   PDU-C socket 1, and a bench spare is not given a power feed and two leads.
> - **Which machine drives CON-A.** §2 says DVALIN; §6.2 lists the one HDMI
>   lead as "REGIN to CON-A". This is not a cable label: Decision U2 makes
>   CON-A the console that survives a total network failure, which holds only
>   if the appliance it watches drives it. Driven from REGIN it is a second
>   REGIN console — and REGIN is the machine most likely to be gone in the
>   outage CON-A is bought for. Followed: DVALIN, with the wiring document.
> - **The image registry.** Between the manual and this repository rather than
>   inside either. `lib.sh` derives `registry.<site>.veldris.internal` and
>   `rollout.sh` pulls from it; no machine in §2 runs a registry and dnsmasq
>   has no record for one. The default names a host that does not resolve, so
>   a rollout fails at the pull with a DNS error rather than at a stage that
>   explains itself. The guide now says the registry is not part of the estate
>   and that `--registry` must name a host that exists. RF-04 is the other
>   half: nothing pushes an image either, so there would be nothing to pull.
>
> No manual amendment is proposed for the registry — adding one is a service,
> a machine, storage and a lifecycle, which is a larger decision than a
> role-table edit. The other three are drafted as amendment 3b.
>
> Every contradiction records which half is followed, and a test asserts it
> does. A contradiction written down without a decision is a decision
> deferred; recording the half in force means that when the author picks the
> other one, what has to change here is already written down.

**Repository.**

`docs/DEPLOYMENT.md:454–457`:

| Claim | At Sindri |
|---|---|
| "**ANDVARI** — PostgreSQL 16, MinIO, the HODD vault, **the image registry**" | No registry exists anywhere on the estate (see also RF-04 in the companion register: nothing pushes an image either) |
| "**REGIN** — Slurm controller, **Loki**" | REGIN runs Prometheus, Alertmanager and Grafana. There is no Loki, and DRAUPNIR ships no log shipper |
| "**DVALIN, DURIN, DAIN** — Executor shims, **one container per job**" | Slurm jobs in a shared venv (RF-E16) — **corrected** |
| "ssh SERVICE-ACCOUNT@**alviss.veldris.internal**" | `alviss.sindri.veldris.internal` (RF-E02) |

Each is small. Together they mean the operator's reference table for the estate
is wrong in every row but one, and it is the table someone reads at 3 a.m.

**Prompt**

> Correct the estate table in `docs/DEPLOYMENT.md` against VLD-INF-SINDRI-001
> Rev 3.3 §6 and §34, and add a test that reads the host-and-role table out of
> the manual and fails when the two disagree. The manual is the authority on
> the estate; the deployment guide should not be able to describe a different
> one.
>
> Decide the log-shipping question rather than renaming Loki to Grafana: SAD
> 11E requires every log line to carry run id, site id and actor, and the
> estate has no log aggregation at all. Either add Loki or Promtail to
> Procedure S11 — REGIN already runs Grafana, so Loki is a small addition —
> or record that logs stay on the host and that the runbook's diagnosis steps
> assume `journalctl` on ALVISS.

**Acceptance criteria**

- The estate table matches the manual, asserted by test.
- The registry row states where images come from once RF-04 is resolved.
- The log-shipping decision is recorded in both documents.
- Gated in stage 2.1.

---

### RF-E21 — P3 — Two irreducible gaps should be recorded against DRAUPNIR, not only against the estate

> **Status: done. The ring is declared by membership rather than by size,
> which is a deliberate departure from this prompt.**
>
> **Why a size is not enough.** The prompt asked for a declared ring *size*.
> A size cannot say which two, and the answer is not derivable: ring
> membership is which machines have a DAC cable between them. Consider DVALIN
> failing, DURIN and DAIN being recabled as a pair per §7.4, and DVALIN later
> being repaired. The estate then has three appliances, all three usable for
> adapter work, and a ring of exactly DURIN and DAIN — and a size of two would
> have picked DVALIN and DURIN by rank, which is a run submitted across a
> cable that does not exist. Slurm's own recovery configuration names them
> (`PartitionName=ring Nodes=durin,dain`) and `Estate.ring_members` mirrors it.
> A test covers exactly the repaired-DVALIN case.
>
> **The refusal is the other half, and it matters as much.** A `RingSizeError`
> is now distinct from a `DegradedRingError`, because they should send an
> operator to different places. A degraded ring is a fault: a machine is down
> and somebody should look at the rack. A declared ring is a configuration:
> nothing is wrong, the forge is a two-node ring because its third machine
> failed and gap G6 holds zero spare QSFP56 cables, and nothing at the rack
> will change that. Reporting the second as the first sends somebody to find
> nothing.
>
> The declaration is written to the chain, once per declaration rather than
> once per tick — a substrate run across two ranks is not comparable to one
> across three (different collectives, different step times, different
> numerics), and "was this a two-node ring" is a question asked months later
> about a specific release. An undeclared ring writes nothing: a row per
> worker start on every ordinary forge would be noise in the one place a
> reader assumes every row was worth writing.
>
> **The tunnel terminates on REGIN, and FileVault is the deciding reason.**
> Procedure S10 ends with `sudo fdesetup enable` on ALVISS, so it boots to an
> unlock screen; a federation link terminating there is down after every power
> cut until somebody visits the site (RF-E22). REGIN has no such gate, is
> already on both fabrics so it can route for the whole forge, and already
> carries `wireguard-tools` from Procedure S11.
>
> **This changes almost nothing in the control plane, which is worth saying
> because it looks like it should.** DRAUPNIR still calls
> `https://megingjord.veldris.internal/…`, and the broker still decides that
> call against the same entry under the same policy. A route is not a
> destination and the broker has no opinion about hops. What has to exist is a
> `dnsmasq` record on REGIN — MEGINGJORD is outside the zone REGIN is
> authoritative for, so the query is forwarded upstream and fails — and a
> route on ALVISS. Both are drafted; the allow-list entry now records where
> the link terminates rather than only that it does not exist.
>
> **And §10.5 cannot express the tunnel at all.** Its allow list is a list of
> hostnames, which is outbound web traffic. WireGuard is UDP to a specific
> endpoint on port 51820. That is the same family as the nine hosts of
> RF-E17 and is drafted beside them, separately, because a list that only
> understands names cannot hold a protocol and a port.
>
> One thing the amendment says that is easy to leave out: **remove the
> declaration when the ring is restored.** A forge left declared at two after
> a third appliance is recabled keeps placing two-node runs and nothing
> complains, because two of two is not degraded.

**Both.**

Two of the estate's gaps have consequences inside the control plane that are
currently recorded in neither document.

**G6, zero spare QSFP56 cables.** The wiring document's §7.4 says that on an
appliance failure the two survivors are recabled as a direct pair and "the ring
partition [is set] to two nodes". DRAUPNIR's `placement.py` treats `ring` as
all-or-nothing over the full estate (`ALL_OR_NOTHING`), which is correct for a
three-appliance ring and wrong for the recovery configuration the wiring
document specifies. There is no way to tell DRAUPNIR the ring is now two nodes.

**No federation link.** `wireguard-tools` is installed on REGIN, not ALVISS,
and no tunnel is configured. GULLINBURSTI runs inside DRAUPNIR on ALVISS, so
even once the link exists it terminates on a different host than the software
that needs it. That routing decision should be made deliberately.

**Prompt**

> 1. Make the ring size configurable rather than implied by the estate's
>    length. `Estate` should carry a declared ring size that an operator can
>    set to two during the recovery configuration, and `place()` should refuse
>    a ring plan against fewer than *that* rather than against `len(appliances)`.
>    Record the change as a ledger entry, because a forge that quietly became a
>    two-node ring is a forge whose later runs are not comparable to its
>    earlier ones.
> 2. Decide where the WireGuard tunnel terminates. If on REGIN, DRAUPNIR
>    reaches MEGINGJORD through it as a route and the egress allow list must
>    name the gateway; if on ALVISS, add `wireguard-tools` to Procedure S10 and
>    the interface to Procedure S13. Record the decision against the federation
>    section of the SAD, which currently assumes the software and the link are
>    on the same host.

**Acceptance criteria**

- A test asserts a two-node ring configuration plans successfully and that a
  three-node specification against it is refused, naming the declared size.
- A test asserts the ring-size change is recorded in the chain.
- The tunnel termination is recorded in both documents, and the egress list
  reflects it.
- Gated in stages 2.1 and 2.4.

---

### RF-E22 — P3 — FileVault means the control plane never returns unattended, and ALVISS holds nothing worth encrypting

> **Status: decided. FileVault stays on, and the reason is that the trade
> this finding described is not available.**
>
> **The heading is half wrong, and the half that is wrong is the important
> one.** "ALVISS holds nothing worth encrypting" is defensible — the control
> plane holds no state, and what is on that disk is a rotatable database
> password and an object-store key. "FileVault means the control plane never
> returns unattended" is true and incomplete: FileVault is one of **three**
> things stopping an unattended return, and removing it changes nothing.
>
> 1. **FileVault.** Procedure S10 enables it; the machine boots to an unlock
>    screen.
> 2. **The `gui` launchd domain.** The agents exist only while the service
>    account is logged in. This is the macOS counterpart of systemd lingering
>    and macOS has no counterpart of `loginctl enable-linger` — and it
>    disables automatic login while FileVault is on anyway, so the two are
>    not even independent.
> 3. **`podman machine`.** On macOS it is a per-user virtual machine tied to
>    that user's session. Even a system-domain LaunchDaemon would have no
>    runtime to talk to.
>
> So option B in the prompt — move `secrets.env` into the keychain and take
> FileVault off — buys nothing and costs a plaintext credential on an
> unencrypted disk. It is not a lopsided trade; it is not a trade.
>
> Two of the three were already in `preflight_launchd` from RF-E01, which is
> where the answer came from: the installer had been saying the `gui` domain
> needs a logged-in session since that finding, and nobody had put it beside
> the FileVault warning and read them together.
>
> **What landed.** `docs/runbook.md` section 1 gains *After a power event,
> somebody has to be at ALVISS*, naming all three walls, the recovery
> sequence, `fdesetup authrestart` for a planned restart only, and the floor
> this puts on every incident beginning with a power event. Amendment 6
> drafts the same for §11. `draupnir-run.sh` no longer justifies its
> state-directory fallback by "a control plane that comes back from a power
> cut" — the fallback is right and stays, but the reason is an attended
> restart or a `launchctl kickstart` after a rollout.
>
> Four tests, one of which is deliberately positive-only: the corrected
> comment *quotes* the old justification while explaining why it was wrong,
> so a test for that phrase's absence would fire on the correct file and pass
> on one that had merely reworded the claim. The tests assert the three walls
> are named, that the FileVault warning carries `fdesetup authrestart` **and**
> says it is for a planned reboot — without the second half it reads as though
> it would help after a power cut — and that the runbook says ANDVARI keeps
> FileVault, which is how a reader talks themselves into turning it off on the
> host that actually holds the vault.
>
> **And the UPS would not help either, as specified.** Gap G1's supply inserts
> at P-01, which feeds PDU-A; ALVISS is on PDU-B. RF-E18 proposes moving it,
> and this is one more reason to.

**Manual, and a design note in the repository.**

Found while implementing RF-E01 rather than by reading. Procedure S10 step 1
ends with `sudo fdesetup enable`, so ALVISS boots to a FileVault unlock screen.
Nothing on that host runs until somebody types a password at the console:
not launchd, not the podman machine, not the control plane. `fdesetup
authrestart` covers a *planned* reboot and there is no equivalent for a power
cut.

The wiring document is already consistent with this. VLD-WIR-SINDRI-001 §7.4
says that on mains loss with no UPS, "on restoration, follow the cold start
from step 1" — an attended procedure. What is inconsistent is the repository:
`deploy/units/draupnir-run.sh` writes the image reference to the state
directory specifically because "a control plane that comes back from a power
cut with no image reference is a control plane that does not come back", which
is reasoning about an unattended return that cannot happen on this host.

Worth weighing rather than accepting silently, because the cost and the benefit
are lopsided:

- **What FileVault protects on ALVISS.** `docs/DEPLOYMENT.md` and the README
  both say the control plane holds no state: everything is in PostgreSQL and
  MinIO on ANDVARI. What is on the ALVISS disk is the images, the generated
  `draupnir.env`, and `secrets.env` — the last of which is the only thing worth
  encrypting, and it is a database password and an object-store key, both
  rotatable and both scoped to a host on an isolated fabric.
- **What it costs.** Every unplanned power event becomes a site visit before
  the control plane returns, and it is the machine an operator would want back
  first, because it is the one that says what state everything else is in.

ANDVARI is the opposite case and should certainly keep FileVault: the vault,
the ledger and the object store are all on it.

**Prompt**

> Decide FileVault on ALVISS deliberately and record the reasoning either way.
>
> **If it stays on**, say so in VLD-INF-SINDRI-001 §11 and in
> `docs/runbook.md`: after any unplanned power event the control plane requires
> a person at the ALVISS console before it returns, and the recovery time for
> every incident that starts with a power event is bounded by that. Amend
> `draupnir-run.sh`'s comment so it stops reasoning about an unattended return
> that this host cannot perform, and keep the state-directory fallback, which
> still earns its place across an attended reboot.
>
> **If it comes off**, move `secrets.env` into the macOS keychain rather than
> leaving it as a 0600 file on an unencrypted disk — that is the one thing
> FileVault was protecting, and it should not simply be dropped. `install.sh`
> would then create the keychain item instead of the empty placeholder, and the
> wrapper would read it at start. ANDVARI keeps FileVault regardless.
>
> Either way, this belongs with gap G1: a UPS feeding PDU-A and PDU-B is what
> turns a hard stop into a clean shutdown, and a clean shutdown is the only
> version of this that a person can plan around.

**Acceptance criteria**

- The decision is recorded in VLD-INF-SINDRI-001 §11 and in `docs/runbook.md`,
  with the recovery-time consequence stated in words.
- `install.sh --check` already warns when FileVault is on; a test asserts the
  warning names `fdesetup authrestart` as the planned-reboot remedy.
- If FileVault comes off ALVISS, a test asserts `install.sh` creates no
  world-readable file holding a credential and that `secrets.env` is not
  written to disk in plain text.
- Gated in stage 2.3.

---

### RF-E23 — P1 — The synchronous database driver is not a runtime dependency, so the image cannot start

**Repository.** Found while making the dependency check connect for real.

`psycopg` was in the `dev` dependency group. It is not a test dependency:

- `draupnir/api/app.py:114` builds a second, synchronous engine on
  `database_url_sync` inside `lifespan`, because the repositories are
  synchronous by SAD 11B.
- `draupnir/core/infrastructure/config.py:36` makes that URL
  `postgresql+psycopg://…`.
- `migrations/env.py:21` uses the same URL, so every alembic run needs it too.

SQLAlchemy imports the DBAPI when the engine is **created**, not when it is
first used:

```
$ python -c "import sqlalchemy; sqlalchemy.create_engine('postgresql+psycopg2://u:p@h/d')"
ModuleNotFoundError: No module named 'psycopg2'
```

So this is not a slow failure on the write path. It is the process refusing to
start, inside the lifespan, before the first request.

`docker/api.Dockerfile:34,41` builds with `uv sync --frozen --no-dev`.
Confirmed absent from the production resolution:

```
$ uv export --frozen --no-dev --no-emit-project | grep -c '^psycopg'
0
```

**The api and worker images could not come up**, and the deploy workflow's
migration step only worked because `uv sync --frozen` on a runner includes the
default dev group. Nothing caught it because every test environment installs
the dev group by definition.

> **Status: done. The instance was fixed during RF-E01; the class is now
> guarded, which is what this finding actually asked for.**
>
> `psycopg[binary]` moved from `dependency-groups.dev` into
> `project.dependencies`, with the reasoning recorded beside it, and the lock
> regenerated. `uv export --no-dev` resolves it.
>
> **Two guards, and the second is the one that matters.**
>
> `test_every_configured_dbapi_is_a_runtime_dependency` resolves the set the
> images are actually built from — `uv export --frozen --no-dev` — and asserts
> every driver named in a configured URL is in it. Verified by moving
> `psycopg[binary]` back out of `project.dependencies`, re-locking, and
> watching it fail; then restored. Its mirror asserts no test framework ships
> in a distroless image that has no shell to run one from.
>
> The `uv` it uses is resolved the way `tasks.py` resolves one, including the
> project-local `.uv-bootstrap/`. A `shutil.which` alone skipped on this
> machine, and a guard that skips wherever the defect gets introduced is not a
> guard.
>
> **Stage 3.1a starts the image.** Stage 3.1 built two and never ran either, so
> a dependency resolving in the checkout and not in the image was invisible
> until deployment. `--output type=cacheonly` became `--load`, and
> `docker/setup-qemu-action` was added because the images are linux/arm64 and
> the runner is not.
>
> **The obvious smoke would not have caught this.** The finding suggested
> `python -c "import draupnir.api.app"`. That passes on the broken image:
> SQLAlchemy imports a DBAPI when the engine is **created**, and both engines
> are created inside the lifespan rather than at import. So the smoke builds
> both engines — the operation that actually failed, and one that needs no
> database to perform — and imports the worker, which shares the image. A test
> asserts the smoke does that rather than merely importing a module, because
> the weaker version is the one somebody would write next time.

**Prompt**

> Guard the class of defect rather than the instance. Add a check that resolves
> the production dependency set (`uv export --frozen --no-dev`) and imports the
> application's entry points against it — or, more cheaply, asserts that every
> DBAPI named in a configured URL is present in that set. A dependency group is
> not a place to discover an import at deploy time.
>
> The deeper version is a smoke stage that runs the built image rather than the
> checkout: stage 3.1 builds the images and nothing ever starts one. `docker
> run --rm <image> python -c "import draupnir.api.app"` in the pipeline would
> have caught this the day it was introduced, costs seconds, and is the only
> check that exercises what actually ships.

**Acceptance criteria**

- A test asserts every DBAPI in `config.py`'s default URLs resolves in the
  production dependency set.
- The pipeline starts the built API image and imports the application, failing
  the build if it cannot.
- Gated in stage 3.1.

---

### RF-E24 — P2 — slurmrestd has no requeue, so AC-F6 is unavailable over the transport the estate will use

> **Status: decided. Option 3 adopted — the step stays manual, AC-F6 is
> DEVIATED, and both documents say so.**
>
> **This entry claimed a test existed that did not.** Its own acceptance
> criteria say "A test asserts the REST driver's refusal names the
> alternative rather than approximating it. **This exists.**" It did not. The
> refusal was written during RF-E10 and nothing covered it; every `requeue`
> in the test tree was the run-level retry, which is a lifecycle transition
> and a different thing entirely. Checked rather than believed, which is the
> only reason it was found — the claim was mine and it was wrong.
>
> Three tests now cover it. Two are the criterion; the third is the one worth
> having: **the refusal must reach the scheduler with no request at all.** A
> driver that cancelled the element and then discovered it could not requeue
> would have destroyed the element and reported a failure, which is a worse
> outcome than either approximation this finding rejects.
>
> **AC-F6 is DEVIATED**, with the reason: `motsognir.slurm/v1` implements the
> mechanism and cannot run at Sindri (RF-E05 — no Slurm client tools on
> ALVISS, no shell in a distroless container), so the transport is slurmrestd,
> which exposes submit, read and cancel and no requeue.
>
> **The console offers nothing that would raise, and now cannot start to.**
> Its retry is a *run* retry — a lifecycle transition through the state
> machine, unrelated to array elements. A test reads the console source and
> fails if an element requeue appears, because the screen where somebody
> would add one is the failure-diagnosis screen and the control would look
> identical to the one already there. No Playwright test is needed: the
> criterion asks for one *if* the console offers it, and it does not.
>
> **No change is proposed to Procedure M6 step 5** — it is already correct.
> Amendment 3d adds a sentence saying the step stays manual after S13,
> because amendment 3a makes DRAUPNIR authoritative for operating the estate
> and a reader would reasonably assume this was included. Both routes to
> closing it — a `scontrol` bridge on REGIN, or Slurm client tools on ALVISS —
> are recorded with what each costs, and neither is proposed.

**Repository and manual.** Found while implementing RF-E10.

AC-F6 requires a failed array element to be retried individually without
disturbing the other fifty five, and the mechanism is `scontrol requeue` on the
element. The sbatch shim does exactly that. **slurmrestd v0.0.40 exposes no
requeue**: it can submit a job, read one and cancel one, and it cannot put one
back on the queue.

That matters because RF-E05 established the shim cannot run at Sindri — ALVISS
has no Slurm client tools and the container has no shell — so the transport the
estate will actually use is the one that cannot do this.

Both approximations are worse than a refusal, which is why the driver refuses:

- **Cancel and resubmit the element.** It gets a new job identifier, severing
  it from its array and losing both the throttle and the accounting record
  tying the fifty six together.
- **Requeue the array.** Restarts all fifty six, discarding the compute of the
  ones that succeeded — the exact failure AC-F6 exists to prevent.

**Prompt**

> Give the control plane a way to requeue one array element at Sindri. Three
> options, and the choice is the site's:
>
> 1. **A `scontrol` bridge on REGIN.** A small authenticated endpoint that runs
>    the requeue and nothing else. Smallest change; adds a component, and one
>    that can restart jobs, so its authorisation matters.
> 2. **Slurm client tools on ALVISS.** The client package plus a munge key, and
>    the worker uses `motsognir.slurm/v1` instead. Undoes part of RF-E05's
>    reasoning — the container is distroless — and needs the binaries in the
>    image.
> 3. **Accept it and say so.** A failed element is requeued by hand on REGIN,
>    and the S12 array monitor tells the operator that rather than offering a
>    control that cannot work. AC-F6 is then marked DEVIATED with the reason
>    rather than IMPLEMENTED.
>
> Whichever is chosen, the console must not offer a requeue that raises. Option
> 3 is the honest default until one of the others is funded.

**Acceptance criteria**

- The choice is recorded in the reconciliation against AC-F6, and in
  `proposed-procedure-s13.md` if it adds anything to REGIN.
- A test asserts the REST driver's refusal names the alternative rather than
  approximating it. ~~This exists.~~ **It did not — see the status note.**
- If the console offers element requeue, a Playwright test asserts it is
  unavailable with the reason given when the driver cannot perform it.
- Gated in stages 2.3 and 2.7.

---

## 9  Summary

| ID | Severity | Side | Finding |
|---|---|---|---|
| RF-E01 | Blocker | Both | ALVISS runs macOS; the deployment is systemd and Podman — **repo side done**, Procedure S10 outstanding |
| RF-E02 | P1 | Repo | Every default hostname is in a zone the site does not serve — **done** |
| RF-E03 | P1 | Both | ANDVARI has no `draupnir` database, role or bucket; PostgreSQL is not listening off-host — **repo side done**, S13 drafted |
| RF-E04 | P2 | Both | The vault is not mounted on the host that checks its capacity — **repo side done**, S9 amendment drafted |
| RF-E05 | P1 | Both | No Slurm client on ALVISS, and none in the image — **repo side done**, S11 amendment drafted |
| RF-E06 | P1 | Repo | `--wrap` is built by joining an unquoted argument vector — **done** |
| RF-E07 | P1 | Repo | `--export` corrupts the job environment and drops `PATH` — **done** |
| RF-E08 | P2 | Both | DRAUPNIR names an `export` partition the estate does not have — **done**, option B |
| RF-E09 | P2 | Repo | GPU request syntax differs from the estate's typed GRES — **done** |
| RF-E10 | P2 | Repo | The `--array=0-55%3` strategy cannot be expressed; requeue uses the wrong mechanism — **done** |
| RF-E11 | P3 | Repo | Appliance availability is never read from the scheduler — **done** |
| RF-E12 | P3 | Repo | A run pending in the Slurm queue is recorded as TRAINING — **done**, no SAD change needed |
| RF-E13 | P2 | Both | The fabric probe checks the wrong machine, has no launcher, and duplicates Grafana — **repo side done**, dashboard 2 drafted |
| RF-E14 | P2 | Both | Neither console the repository builds is the console the manual installs — **CON-A done**, CON-B blocked on RF-01 and RF-03 |
| RF-E15 | P3 | Both | Thermal and fabric signals are in Prometheus; DRAUPNIR is not connected to it — **done**; fabric half waits on RF-18 |
| RF-E16 | P2 | Both | The sandbox profile describes an execution model the estate does not use — **done**; found that the cgroup containment is not switched on |
| RF-E17 | P3 | Both | The two egress allow lists disagree, and DRAUPNIR's entries are incomplete — **done**; the disagreement is nine hosts |
| RF-E18 | P3 | Both | The UPS status file has no contract and no nominated host — **repository done**; the UPS feeds the wrong PDU |
| RF-E19 | P2 | Manual | The build manual has no procedure that installs DRAUPNIR — **done**; S13 drafted, and both documents now say which is authoritative |
| RF-E20 | P3 | Repo | The deployment document's estate table is wrong in four rows — **done**; found four contradictions inside the estate documents |
| RF-E21 | P3 | Both | The two-node recovery ring and the federation link have no DRAUPNIR-side record — **done**; declared by membership, not by size |
| RF-E22 | P3 | Both | FileVault means the control plane never returns unattended, and ALVISS holds nothing worth encrypting — **decided**: it stays, because the trade is not available |
| RF-E23 | P1 | Repo | The synchronous database driver is not a runtime dependency, so the image cannot start — **fixed** — **done**; the class is guarded and the image is started |
| RF-E24 | P2 | Both | slurmrestd exposes no requeue, so AC-F6 is unavailable over the transport the estate will use — **decided**: manual on REGIN, AC-F6 DEVIATED |

---

## 10  Sequencing

**RF-E01 is decided and half-built.** Option A: Podman machine plus launchd.
The repository side is done and tested; what remains is Procedure S10 installing
a container runtime on ALVISS, which belongs with RF-E19's Procedure S13 rather
than on its own. Until that lands there is still no host the installer can
finish on, so the ordering below is unchanged.

**Then the four that make a commissioned site work at all:** RF-E02 (names,
**done**), RF-E03 (ANDVARI provisioning, **repo side done**, manual drafted),
RF-E05 (scheduler access, **repo side done**, manual drafted), RF-E19
(Procedure S13, **drafted**). Together these
are the difference between an estate that has DRAUPNIR installed and one that
does not.

RF-E23 belongs at the front of this group rather than in it: until the image
can start, none of the rest is observable on the estate at all. It is fixed.

**Then the three Slurm defects that only appear against real `sbatch`:**
RF-E06 and RF-E07 are **done**; RF-E09 remains. They were small and certain, and
no test in the suite could have found them — the local subprocess driver does
not go through a shell and the conformance harness only checks that `render` is
pure. Both were reproduced before being fixed, which is worth keeping as the
habit: a fix for a defect nobody has watched fail is a fix nobody knows works.

**Then the placement work:** RF-E08, RF-E09, RF-E10, RF-E11 and RF-E12 are all
**done**, taken together because they were one piece of work seen from five
sides. RF-E24 came out of it and is open: it needs a decision rather than an
implementation.

**Then the placement work:** RF-E08, RF-E10, RF-E11, RF-E12. These are where
DRAUPNIR stops being a control plane that submits single jobs and becomes one
that runs the CIM-56 array as the manual describes it. RF-E10 should be done
together with RF-13 in the companion register — they are one piece of work seen
from two sides.

**Then the observability and console reconciliation:** RF-E13 and RF-E15 are
**done**, RF-E14 is done as far as it can go, and RF-E20 remains. All four are
cases of two systems doing the same job, and the fix in each is to pick one.
RF-E13's dashboard-2 amendment depended on RF-E15 putting ALVISS in Prometheus's
scrape configuration; both are now drafted in the same proposal, and the panel
it feeds waits on RF-18 in the companion register rather than on anything here.

**RF-E16 to RF-E24 are done** as far as this repository can take them. What
remains in this register is the manual and wiring amendments, which belong to
those documents' authors, and the CON-B work that waits on RF-01 and RF-03 in
the companion register.
They should be settled and written down now, while the estate is still being
commissioned and the UPS has not yet arrived, rather than discovered during the
first incident that depends on them.

---

## 11  What this review did not find

Worth recording, because absence of a finding is a result:

- **The addressing is consistent.** The wiring document's `dig @10.10.0.5` and
  the manual's `10.20.0.5` are the same host on its two fabrics, not a
  contradiction. The BAUGR ring's 192.168.0.0/24–192.168.5.0/24 range is
  deliberately disjoint from Fabrics 2 and 3, and DRAUPNIR encodes no ring
  address anywhere, which is correct.
- **The checkpoint rule agrees.** The manual's §40 step 3 — "Set `--save_steps`
  so that no more than thirty minutes of work is ever unwritten. At an observed
  step time of t seconds, `save_steps = 1800 / t`" — is exactly what
  `draupnir/core/domain/checkpoints.py` derives, including the recomputation
  once real step times are observed.
- **The power domains are right.** PDU-A carries only the three appliances, so
  acceptance test A7 (switch off PDU-A, monitoring and the queue stay up) holds
  with the control plane on PDU-B, and DRAUPNIR's "dispatch suspends, running
  work continues" behaviour is the correct response to the abnormal conditions
  in wiring §7.4.
- **The tier table matches.** The manual's §41 step 4 tiering — nine Tier A
  jurisdictions on Qwen3.6-27B dense, forty-seven Tier B on 35B-A3B — is the
  same partition `draupnir/hamarr/tiers.py` enforces, including the requirement
  that the two lists enumerate all fifty-six. That module is not wired to
  anything (companion register, RF-11), but what it says is right.
