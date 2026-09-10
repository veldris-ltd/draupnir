# Change proposal: Procedure S13, DRAUPNIR control plane

**For** VLD-INF-SINDRI-001 Rev 3.3 → Rev 3.4
**Raised by** RF-E01, RF-E03, RF-E04, RF-E05, RF-E13, RF-E14, RF-E15, RF-E16,
RF-E17, RF-E19, RF-E20, RF-E21, RF-E22 and RF-E24 of
[remedial-fixes-estate.md](remedial-fixes-estate.md)
**Status** Proposed. Not applied — VLD-INF-SINDRI-001 is a controlled document
and this is a draft for its author to accept, amend or reject.

---

## Why

Part 4 of the manual runs S1 to S12 and ends with Slurm and observability.
DRAUPNIR is named eleven times across the document — as the control software
(C12), as the discharge of the Article 53 obligation (C14), as the residency
check at job planning (C19), as the ALVISS host role (§6), as layer L6b (§12) —
and no procedure installs it.

Five concrete gaps follow, each of which stops a correctly built Sindri from
running the control plane:

1. **ALVISS has no container runtime.** Procedure S10 installs `xcode-select`,
   Homebrew, `uv`, `git`, `ansible`, `ollama` and MLX. `deploy/install.sh` runs
   rootless Podman containers.
2. **ANDVARI has no `draupnir` database, role or bucket.** Procedure S8 step 7
   creates `mlflow` and stops. It also leaves Homebrew's PostgreSQL bound to
   localhost, and starts MinIO with default credentials and no bucket.
3. **The vault is not mounted on ALVISS.** Procedure S9 covers DVALIN, DURIN
   and DAIN. SAD 11.3 gives the control plane a vault capacity alarm at 85 per
   cent, and the host that raises it cannot see the vault.
4. **There is no way for the control plane to reach Slurm.** Procedure S11
   installs `slurm-wlm` on REGIN and S12 installs `slurmd` on the appliances.
   Neither Mac gets a Slurm client, and the control plane runs in a distroless
   container with no shell to run one from.
5. **Nothing joins the two documents.** An operator following the manual builds
   an estate with no control plane; an operator following `docs/DEPLOYMENT.md`
   installs one onto a host the manual never prepared.

`deploy/install.sh --check` now reports each of these separately and by name —
`unreachable`, `auth-refused`, `missing` — so the procedure below has a
completion check that means something.

---

## Proposed amendment 1 — Procedure S10, step 1

Add a container runtime to the tool set. ALVISS is the DRAUPNIR application
host (§6) and the control plane runs as rootless containers, which on macOS
means Podman with a Linux virtual machine behind it.

Replace step 1's command block with:

```
xcode-select --install
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install uv git ansible podman
brew install --cask ollama
uv tool install mlx-lm
uv pip install --system mlx mlx-lm mlx-vlm lm-eval
sudo fdesetup enable

# The control plane runs as rootless linux/arm64 containers, so the podman
# client needs a Linux VM behind it. Two cores and 4 GB is ample: the three
# units are an API, a worker and an nginx serving static assets.
podman machine init --cpus 2 --memory 4096 --now
podman machine start   # after a reboot, if it is not started at login
```

Add to the completion check: *"`podman machine list` reports a running
machine, and `podman run --rm docker.io/library/alpine true` succeeds."*

> **Note for the author.** The machine does not start at login by default. Either
> add a launchd agent for it, or accept that a reboot needs
> `podman machine start` before the control plane returns. This interacts with
> FileVault (see the note at the end), which already makes an unattended return
> impossible, so accepting it costs nothing extra today.

---

## Proposed amendment 2 — Procedure S8, step 7

Three defects in three lines, all of which pass a socket test.

**As written:**

```
minio server /Volumes/forge/minio --address ":9000" --console-address ":9001" &
brew services start postgresql@16
createdb mlflow
```

**Proposed:**

```
# PostgreSQL. Bound to the Fabric 2 address rather than to `*`: Fabric 2 is the
# only segment ALVISS reaches, and a wildcard bind on the host holding the
# vault is a wider surface than the site needs.
brew services start postgresql@16
psql -d postgres -c "ALTER SYSTEM SET listen_addresses = '10.20.0.21'"

# MLflow, as before.
createdb mlflow

# DRAUPNIR. One role, one database, and a password generated here rather than
# chosen: it is typed once into ALVISS's secrets.env and never again.
DRAUPNIR_PASSWORD="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32)"
psql -d postgres <<SQL
CREATE ROLE draupnir LOGIN PASSWORD '${DRAUPNIR_PASSWORD}';
CREATE DATABASE draupnir OWNER draupnir;
SQL
echo "DRAUPNIR database password: ${DRAUPNIR_PASSWORD}"   # record it, then clear the scrollback

# One rule, for one host. ALVISS is 10.20.0.22 and nothing else needs this
# database, so the rule names the host rather than the subnet.
cat >> "$(psql -tAd postgres -c 'SHOW hba_file')" <<'HBA'
host    draupnir    draupnir    10.20.0.22/32    scram-sha-256
HBA
brew services restart postgresql@16
```

and for the object store:

```
# MinIO. Root credentials from the keychain rather than the `minioadmin`
# default, and under launchd so it survives a reboot: `minio server ... &` in a
# login shell does not.
security add-generic-password -a minio -s draupnir-minio -w "$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40)"

sudo tee /Library/LaunchDaemons/internal.minio.plist >/dev/null <<'PLIST'
  ... standard launchd wrapper for:
      MINIO_ROOT_USER=veldris-minio
      MINIO_ROOT_PASSWORD=<from the keychain item above>
      minio server /Volumes/forge/minio --address ":9000" --console-address ":9001"
PLIST

# The bucket DRAUPNIR writes artefacts into, with versioning ON. Versioning is
# what makes an artefact seal a property of the store rather than of whichever
# process happens to be running (see RF-08 in the companion register): without
# it, `hodd.stores.ObjectStoreDriver` can only refuse an overwrite it is asked
# to make, and cannot stop one it is not.
mc alias set forge http://10.20.0.21:9000 veldris-minio "<password>"
mc mb --ignore-existing forge/draupnir
mc version enable forge/draupnir
```

Amend the completion check to add: *"`psql -h 10.20.0.21 -U draupnir -d
draupnir -c 'SELECT 1'` succeeds from ALVISS, and `mc ls forge/draupnir`
succeeds."*

> **Note for the author.** The password handling above is a sketch. Veldris may
> already have a convention for where a service credential is generated and
> recorded; if so, use it. What matters to DRAUPNIR is only that the credential
> is not `minioadmin` and not the default, and that it ends up in
> `~/.config/draupnir/secrets.env` on ALVISS at 0600.

---

## Proposed amendment 2a — Procedure S9, the vault on ALVISS

Procedure S9 mounts the vault on the three appliances. ALVISS needs it too, for
two reasons: the worker reads vault capacity for the SAD 11.3 alarm at 85 per
cent, and `docs/runbook.md` section 4 sends an operator to run
`vault_admin.py reconcile` there after an outage. Neither works on a host that
cannot see the vault.

**Read-write on the host, read-only into the container.** The capacity duty
only reads, so the worker's bind mount is read-only. The host mount has to be
read-write because `vault_admin.py reconcile --apply` ingests staged artefacts
*into* the vault, which is the recovery the runbook documents.

Add to Procedure S9, after the appliance steps:

```
# on ALVISS. macOS has no /etc/fstab, so this is an automount map.
# `resvport` because macOS NFS clients use a non-reserved source port by
# default and the export in S8 step 6 does not permit one; without it the
# mount fails with a permission error that reads like an export problem.
sudo mkdir -p /forge/vault
echo '/-  auto_forge' | sudo tee -a /etc/auto_master
echo '/forge/vault -fstype=nfs,resvport,nconnect=8 10.20.0.21:/Volumes/forge' | sudo tee /etc/auto_forge
sudo automount -vc
df -h /forge/vault
```

**And the podman machine has to share it.** The worker runs in a container, and
on macOS that container lives in a Linux VM which shares the user's home
directory and little else. `/forge` is not under it:

```
podman machine stop
podman machine set --volume /forge:/forge
podman machine start
```

Without that the container sees an empty `/forge/vault`, which DRAUPNIR reports
as `missing` rather than as working — the `.hodd-vault` marker exists for
exactly this confusion — but it is better not to arrive there.

Amend the S9 completion check to: *"Vault mounted on all four hosts. `df -h
/forge/vault` answers on DVALIN, DURIN, DAIN and ALVISS, and
`./deploy/install.sh --check` on ALVISS reports `hodd-vault: mounted` with a
capacity figure."*

---

## Proposed amendment 2b — Procedure S11, `slurmrestd` on REGIN

The control plane cannot run `sbatch`. VLD-INF-SINDRI-001 installs the Slurm
client tools on REGIN and the appliances; ALVISS gets neither, and the control
plane runs in a distroless container with no shell in it. Installing Slurm
client binaries and a `munge` key into that image would forfeit the point of a
distroless image, and reaching Slurm by `ssh` from a container would put a
private key in the control plane. `slurmrestd` is the remaining answer and the
one Slurm provides for it.

DRAUPNIR ships `motsognir.slurmrest/v1` for this. It is a sibling of
`motsognir.slurm/v1`, not a replacement: a host with the binaries installs the
shim, a host without them installs this, and the core does not change either
way.

**Add to Procedure S11 step 1**, the package list:

```
sudo apt install -y slurm-wlm slurm-wlm-doc slurmrestd mariadb-server munge   prometheus prometheus-alertmanager grafana   dnsmasq chrony wireguard-tools chromium-browser unclutter
```

**Add to Procedure S11, after the cluster configuration.** JWT alongside munge
rather than instead of it: `slurmctld` keeps `auth/munge` for the appliances,
and JWT is the alternative authentication that a REST caller with no munge key
can use.

```
# The signing key. Readable by slurm alone: anyone who can read it can mint a
# token for any user.
sudo dd if=/dev/random of=/etc/slurm/jwt_hs256.key bs=32 count=1
sudo chown slurm:slurm /etc/slurm/jwt_hs256.key
sudo chmod 0600 /etc/slurm/jwt_hs256.key

# Alongside AuthType=auth/munge, which stays as it is.
sudo tee -a /etc/slurm/slurm.conf >/dev/null <<'EOF'
AuthAltTypes=auth/jwt
AuthAltParameters=jwt_key=/etc/slurm/jwt_hs256.key
EOF
sudo systemctl restart slurmctld

# slurmrestd on the management fabric, as its own unmerited user. Bound to
# REGIN's address rather than to 0.0.0.0: Fabric 3 is where ALVISS reaches it
# and a wildcard bind offers the scheduler to the site network as well.
sudo useradd --system --no-create-home --shell /usr/sbin/nologin slurmrest
sudo systemctl edit --full --force slurmrestd.service   # ExecStart with:
#   /usr/sbin/slurmrestd -a rest_auth/jwt 10.20.0.5:6820
sudo systemctl enable --now slurmrestd

# A token for the control plane's account. Lifetime in seconds: SVALINN
# re-issues it as a lease rather than holding one indefinitely (SAD 9.4).
sudo -u slurm scontrol token username=draupnir lifespan=3600
```

Amend the S11 completion check to add: *"From ALVISS,
`curl -s -H \"X-SLURM-USER-NAME: draupnir\" -H \"X-SLURM-USER-TOKEN: <token>\"
http://10.20.0.5:6820/slurm/v0.0.40/ping` returns a ping, and a one-line job
submitted through it appears in `squeue` on REGIN."*

> **One setting the estate owns.** DRAUPNIR renders each job as a batch script
> and runs a configured preamble before the command. At Sindri that is
> `source /forge/venv/bin/activate`, the first line of every batch script in
> Part 5. It matters more than it looks: `sbatch` propagates the submitting
> environment by default, and the submitter here is a distroless container
> while the job runs on an appliance, so a job that inherited that `PATH` would
> be looking for `llamafactory-cli` in directories that exist nowhere on the
> machine it landed on. With a preamble configured the driver emits
> `#SBATCH --export=NONE` and the job's environment is exactly what its own
> script sets.

> **Note for the author.** Two decisions here are the site's rather than
> DRAUPNIR's. **Token lifetime**: an hour is a starting point; shorter is safer
> and costs a re-issue. **Who re-issues it**: `scontrol token` needs to run on
> REGIN as the slurm user, so something has to bridge that to ALVISS's SVALINN.
> Until that exists the token is a manually rotated secret in `secrets.env`,
> which works and should be recorded as a gap rather than left implicit.

**And the driver's API version is a contract.** It speaks `v0.0.40` and says so
rather than negotiating: an OpenAPI version decides what is sent, and picking
whichever the server happens to offer would let a Slurm upgrade change that
without anybody choosing to. `install.sh --check` reports the scheduler as
`unreachable` when `/slurm/v0.0.40/ping` is not there, which is what a version
mismatch looks like from here.

---

## Proposed amendment 2c — Grafana dashboard 2 reads the probe, it does not run one

Section 34 step 8 specifies CON-B dashboard 2 as "per interface packet rate and
error counters, **hourly synthetic all-reduce bandwidth probe**", alarming below
80 per cent of the S4 baseline. SAD 11.3 gives DRAUPNIR's worker the same duty,
on the same interval, against the same threshold.

**Two hourly probes is worse than either.** The `ring` partition is
`OverSubscribe=EXCLUSIVE`, so the second probe queues behind the first — and
behind any training job — and reports a number that is stale by an unknown
amount. Worse, each probe takes the whole estate for its duration, so two of
them is twice the training time given up to measurement.

**DRAUPNIR should own it**, for two reasons that are about where the answer
needs to be rather than about which tool is better. The reading belongs in the
chain, beside the runs it describes, because "was the fabric healthy when this
model was trained" is a question asked months later about a specific release.
And the alarm belongs beside the run it would stop: a fabric at 70 per cent is a
reason not to place a ring job, which is a decision the control plane makes and
Grafana cannot.

Proposed change to step 8's dashboard table:

| # | Dashboard | Panels | Alert |
|---|---|---|---|
| 2 | Fabric health | Per interface packet rate and error counters. **Bus bandwidth as recorded by DRAUPNIR's hourly probe, read from its metrics endpoint** | Bus bandwidth below 80 per cent of the S4 baseline, **raised by DRAUPNIR** |

This depends on ALVISS being a Prometheus scrape target, which is RF-E15 and a
one-line change to step 7's `scrape_configs`.

**And the baseline has to be recorded somewhere DRAUPNIR reads.** Acceptance
test A3 measures it at commissioning and the manual currently records it only in
the commissioning log. Add to A3's method:

> Record the measured collective bandwidth as `DRAUPNIR_FABRIC_BASELINE_GBPS` in
> `~/.config/draupnir/draupnir.env` on ALVISS. Until it is there the probe takes
> a reading every hour and cannot raise the 80 per cent alarm against it, and
> says so rather than alarming against a number nobody took.

## Proposed amendment 2d — the two consoles

Section 34 steps 9 and 10 put something on each panel, and in both cases it is
not the thing this repository built for it.

### CON-A, on DVALIN

**As written**, step 10 launches:

```
xterm -fs 11 -e watch -n5 'nvidia-smi ...; ibdev2netdev; sinfo 2>/dev/null || echo "scheduler unreachable"'
```

**The repository builds `tools/stedi-view` for this** — S30, and Decision U2:
"CON-A is not a small version of the console." The DGX Spark has no baseboard
management controller, so this is the only console that survives a total
network failure, and its entire value is depending on nothing beyond the
appliance it is attached to. It imports no HTTP client and nothing from
`draupnir`, and a test reads its source and asserts that, because "we did not
import that" is a property that decays.

The difference matters exactly when the panel does. With the scheduler down the
`xterm` prints `scheduler unreachable` and shows nothing else about the run;
`stedi-view` renders all eight lines and marks the unreachable ones, which is
the state it was bought for. It is tested against a refusing network and
against an address that does not route.

**Why the `xterm` is there is not an oversight.** Nothing built or shipped
`stedi-view`: it was not a workspace member, not in an image, not in `deploy/`,
and not in the pipeline. There was no artefact to install. That is fixed —
`python tasks.py con-a` builds the wheel and the pipeline uploads it — so the
amendment now has something to point at.

Proposed replacement for step 10:

```
# on DVALIN. A wheel, because CON-A depends on nothing and a container runtime
# would be a dependency that has to survive the same outage the view does.
uv tool install ./stedi_view-0.1.0-py3-none-any.whl

mkdir -p ~/.config/autostart
cat > ~/.config/autostart/stedi-console.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=STEDI Local Console
Exec=xterm -fa Monospace -fs 11 -e stedi-view --watch
EOF
```

And to acceptance test **A11**, whose pass criterion currently reads "CON-A
continues to display appliance state from DVALIN":

> Power off REGIN, then read CON-A. `stedi-view` renders all eight lines, with
> the scheduler and API lines marked unreachable and the local readings — GPU,
> throttle, fabric, ring, current run, vault — still showing. A panel that has
> gone blank, or that shows only `scheduler unreachable`, is a fail.

### CON-B, on REGIN

**As written**, step 9 shows Grafana dashboard 1 and nothing else.

The two candidates carry different things and neither is a superset. Grafana
dashboard 1 has thermal and throttle, which DRAUPNIR has no source for
(RF-E15). DRAUPNIR's `/kiosk` route has run state, queue depth and anchor
state, which Grafana has no source for — and it states its own staleness in
words, which a Grafana panel whose datasource died does not.

**The honest answer is both**, rotating. Chromium takes more than one URL, and
S31's own specification is three dashboards on a timer:

```
Exec=chromium-browser --kiosk --noerrdialogs --disable-infobars \
  http://localhost:3000/d/thermal \
  http://<console>/kiosk
```

> **This one is blocked, and the block is the point.** Pointing the panel at
> DRAUPNIR's console means publishing that console on a fabric the appliances
> share. Today there is no authentication on the API at all (RF-01 in the
> companion register: no OIDC middleware exists, every `/v1` route answers 401
> in the only configuration it is meant to be deployed in) and no ingress or
> TLS (RF-03). `draupnir-run.sh` binds the console to `127.0.0.1` by default,
> and changing that to a fabric address before those two are resolved would put
> an unauthenticated read surface — run names, model identifiers, the anchor
> state of the forge — on the segment DVALIN, DURIN and DAIN are on.
>
> So: **adopt the CON-A half now** and leave step 9 as it is until RF-01 and
> RF-03 land. When they do, the bind address and the authentication path are
> stated here with the change, not assumed.

---

## Proposed amendment 2e — ALVISS is a scrape target, on one port, from one host

Section 34 step 7 stands up Prometheus on REGIN with a `forge-nodes` job
scraping the three appliances. ALVISS is not in it, and so nothing the control
plane measures reaches the collector the estate reads.

Three things depend on that one omission and they are worth naming together,
because the change looks trivial and is not:

- **Amendment 2c** puts DRAUPNIR's hourly bus-bandwidth probe on dashboard 2.
  It is read "from its metrics endpoint" — this is that endpoint.
- **CON-B's fabric panel** renders the same reading, from the same place.
- **Every SAD 11.3 signal DRAUPNIR raises** — queue depth, vault headroom, the
  ledger chain — has nowhere to be seen otherwise.

Proposed addition to step 7's `scrape_configs`:

```yaml
  # The control plane. Its own metrics only: the appliance thermal and throttle
  # readings come from the DCGM exporter on each appliance, above, and DRAUPNIR
  # reads them back out of this Prometheus rather than collecting them again.
  - job_name: draupnir
    static_configs:
      - targets: ['10.20.0.22:8000']
```

And to step 7's completion check, which currently confirms only that the three
appliances are `UP`:

> `http://10.20.0.5:9090/targets` shows four targets `UP`, not three. A
> `draupnir` target that is `DOWN` means the control plane is not running or
> the rule below is not in place, and dashboard 2's bandwidth panel will be
> empty rather than wrong.

### The part that is not one line

SAD 8.1 lists `/metrics` with `/healthz` and `/readyz` as unauthenticated, and
**the binding is the control**: the endpoint carries no credential precisely
because it is not published beyond the host. Adding a scrape target changes
that. `10.20.0.22:8000` is ALVISS on Fabric 2, which is the segment DVALIN,
DURIN and DAIN are on.

So the scrape target needs a rule beside it, and the rule is the amendment —
not an implementation detail to be left to whoever does the work:

```
# on ALVISS. Port 8000 answers REGIN and nothing else on Fabric 2.
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /opt/homebrew/bin/podman
echo "block in proto tcp from any to any port 8000
pass in proto tcp from 10.20.0.5 to any port 8000
pass in proto tcp from 127.0.0.1 to any port 8000" | sudo pfctl -f - -e
```

A DRAUPNIR metric carries no run specification, corpus path or actor identity
— a metric labelled by actor has an unbounded label set, and one labelled by
artefact leaks what is being built — so what is exposed is counters and
histograms. That is why one rule is a proportionate control here and would not
be for the `/v1` surface, which is why **this amendment does not move the API's
bind address**. CON-B reading DRAUPNIR's console is amendment 2d, and it stays
blocked on authentication (RF-01) and ingress (RF-03).

### What the control plane needs told

Nothing, if it is installed at the site: `install.sh` derives the collector
from the site's own zone and writes `DRAUPNIR_PROMETHEUS_URL` into
`draupnir.env`. A forge whose Prometheus is elsewhere sets that variable
before installing. A control plane with no collector configured is a supported
configuration — a developer machine is one — and every panel then reads
`unmeasured` with the reason, rather than zero.

---

## Proposed amendment 2f — `cgroup.conf`, and an account that is not the administrator

This one is short, and it is the amendment with the best ratio of lines to
consequence in the whole document. It follows from reading Procedure S11's
`slurm.conf` closely.

### What the configuration actually does

```
ProctrackType=proctrack/cgroup
TaskPlugin=task/cgroup
```

It is easy to see the word `cgroup` twice and conclude the jobs are
constrained. They are not:

- `proctrack/cgroup` tracks a job's processes so they can all be **killed** at
  the end of it. That is a real guarantee and a valuable one — a job that forks
  and detaches does not survive its allocation — but it is cleanup, not a
  limit.
- `task/cgroup` places tasks in cgroups **so that `cgroup.conf` can constrain
  them**. There is no `cgroup.conf` anywhere in VLD-INF-SINDRI-001 Rev 3.3, so
  every constraint takes its default, and `ConstrainRAMSpace`,
  `ConstrainDevices`, `ConstrainCores` and `ConstrainSwapSpace` all default to
  **no**.

Two consequences follow directly from the cluster configuration as written:

- `RealMemory=122880` is scheduling arithmetic. A job that allocates past it is
  not killed; it takes the appliance's other work with it, and on the `ring`
  partition — `OverSubscribe=EXCLUSIVE`, three nodes — that is the estate.
- `Gres=gpu:gb10:1` sets `CUDA_VISIBLE_DEVICES` for a job that chooses to read
  it. A job that does not is not prevented from reaching another job's
  accelerator.

### And the account

Section 25 creates `nvidia` on all three appliances, puts it in `sudo`, and
section 26 gives it `/forge`. Nothing introduces a second account, so a
training job runs as the administrator — which means a compromised dependency
in the training stack inherits `sudo`, ownership of `/forge/tools` (the
training stack itself, for every later run), and whatever is in that account's
shell history and configuration.

### Proposed addition to Procedure S11, after step 2

```
# on all three appliances. Without this file, task/cgroup constrains nothing.
sudo tee /etc/slurm/cgroup.conf >/dev/null <<'EOF'
CgroupPlugin=autodetect
ConstrainCores=yes
ConstrainRAMSpace=yes
ConstrainSwapSpace=yes
ConstrainDevices=yes
EOF

# A job account that is not the administrator's. It owns nothing in /forge
# except what it writes, and it is not in sudo.
sudo useradd -r -m -d /home/forgejob -s /usr/sbin/nologin forgejob
sudo chown -R nvidia:forgejob /forge/vault/models/adapters /forge/runs
sudo chmod -R g+w /forge/vault/models/adapters /forge/runs
```

And to every batch script in Part 5, one line under the `#SBATCH` block:

```
#SBATCH --export=NONE
```

> `--export=NONE` stops the job inheriting the submitting shell's environment,
> which is how a token exported for one purpose reaches a training process. The
> scripts set the variables they need — `NCCL_*`, `MASTER_ADDR`,
> `MLFLOW_TRACKING_URI` — explicitly already, so nothing is lost.
>
> DRAUPNIR's own Slurm driver renders `--export=NONE` for the same reason,
> though only when a preamble is configured — the two go together, because a
> job that inherits nothing needs to be told how to find its environment. At
> Sindri the preamble is `source /forge/venv/bin/activate`, so both are set.
> A site that configures neither gets the inherited environment, which is the
> behaviour these hand-written scripts have today.

### What this closes and what it does not

Four of the seven properties `draupnir/svalinn/containment.py` records as
missing: a dedicated account, memory constraint, device constraint, and
environment isolation. Three remain and none of them is configuration —
a read-only root, no-new-privileges, and no outbound network. Those need the
container-per-job model, and whether the estate is heading there is the section
below.

---

## The executor model — the decision amendment 2f rests on

Not a numbered amendment: it is the statement the documents currently disagree
about, and the reason 2f is worth doing at all. The corresponding SAD text is
amendment 4 of `proposed-sad-amendments.md`.

`draupnir/svalinn/sandbox.py` specifies a rootless container with no outbound
network and read-only artefact mounts. Threat T7 is closed against it. Nothing
applies it, and the estate could not honour it if something did: Procedure M6
runs `python /forge/tools/LLaMA-Factory/src/train.py` inside `/forge/venv`
under `slurmd`. `docs/DEPLOYMENT.md` described the appliances as running "one
container per job" until this finding, which is how the assumption propagated.

The two are not reconcilable by relaxing the profile. The work needs the two
properties the profile forbids — the job reports to MLflow at
`http://10.20.0.21:5000`, and it writes checkpoints to
`/forge/vault/models/adapters/${ISO3}/${RUN}`. A profile relaxed until the job
runs is not a control.

**Recommended for Release 1: the shared-venv model is in force**, amended as
above, and the container model is a recorded gap. The repository side is done:
`sandbox.py` now says it is a specification, `containment.py` states what is in
force, a test asserts nothing on the dispatch path applies the profile, and
AC-S11's deviation says the execution model rather than the missing hardware.

**What adopting containers would cost**, for whoever owns that gap: a container
runtime on each appliance beyond the DCGM exporter; an image for the training
stack (PyTorch, LLaMA-Factory, the NCCL and RoCE user-space, pinned against the
DGX OS driver); `--container-image` integration through Pyxis or a wrapper; a
writable output mount for the vault; and an egress exception for MLflow. That
is a Procedure S5 change and a change to every batch script in Part 5. It is a
real improvement and it is not a Release 1 change.

---

## Proposed amendment 2g — section 10.5 is short of nine hosts

Section 10.5 enforces eighteen hosts at the site router and states that "all
other outbound traffic is denied". Nine hosts that this programme reaches are
not among them — and the first three are reached by **the manual's own
procedures**, which is why this is worth reading before the list.

### The estate cannot commission itself under its own policy

| Host | Where the manual reaches it |
|---|---|
| `astral.sh` | Procedure S6 step 1 and Procedure S10 step 1: `curl -LsSf https://astral.sh/uv/install.sh \| sh`. All five machines. |
| `download.pytorch.org` | Procedure S6 step 2: `uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130`. This is the training stack; the CUDA build is not on PyPI. |
| `nvidia.github.io` | Procedure S5: the libnvidia-container signing key and apt source, installed before the DCGM exporter that section 34 step 7 scrapes. |

An operator who applies section 10.5 before Part 3 finds that Part 3 does not
complete. An operator who applies it after finds that the next appliance, or the
next `apt upgrade`, does not.

**This matters more than the count suggests**, because of how it fails.
A destination the DRAUPNIR broker refuses produces a log line naming the
destination and the policy that refused it. A destination the *router* refuses
produces nothing on the host at all: the packet leaves and is dropped
elsewhere, and `curl` reports a timeout. One of those is diagnosed in a minute
and the other is diagnosed as the internet being slow.

### The control plane's images cannot be built

`docker/api.Dockerfile` and `docker/web.Dockerfile` start from three registries,
none of which is Docker Hub:

| Host | What it serves |
|---|---|
| `ghcr.io` | the `astral-sh/uv` builder image |
| `pkg-containers.githubusercontent.com` | where `ghcr.io` serves its layers from |
| `gcr.io` | `distroless/cc-debian12`, the runtime |
| `storage.googleapis.com` | where `gcr.io` serves its layers from |
| `cgr.dev` | the Chainguard node and nginx images |

Section 10.5 permits `registry-1.docker.io`, `auth.docker.io` and
`production.cloudflare.docker.com`. Nothing in the programme is recorded as
using them. The list permits the registry the build does not use and forbids
the three it does.

### And the corpus cannot be acquired

| Host | Why |
|---|---|
| `www.legislation.gov.uk` | GBR primary legislation under the Open Government Licence v3.0, named in the Part 5 corpus manifest. |

### The pattern worth noticing

Four of the nine are **blob hosts** — the second request of a two-request
operation. `huggingface.co` and `cdn-lfs.huggingface.co` are already both in
section 10.5, correctly; `pypi.org` and `files.pythonhosted.org` likewise. The
same pairing was missing from DRAUPNIR's own allow list until this finding, and
it is missing from section 10.5 for the two container registries.

The failure is specific and easy to misread: the metadata request succeeds, the
transfer starts, and then the transfer stops. It presents as a corrupt download
rather than as a policy decision, and the usual first response is to retry it.

### Proposed replacement for the section 10.5 block

```
github.com                       api.github.com
codeload.github.com              raw.githubusercontent.com
huggingface.co                   cdn-lfs.huggingface.co
pypi.org                         files.pythonhosted.org
registry.npmjs.org
archive.ubuntu.com               security.ubuntu.com
ports.ubuntu.com
nvcr.io                          api.ngc.nvidia.com
developer.download.nvidia.com    nvidia.github.io
registry-1.docker.io             auth.docker.io
production.cloudflare.docker.com
astral.sh                        download.pytorch.org
ghcr.io                          pkg-containers.githubusercontent.com
gcr.io                           storage.googleapis.com
cgr.dev
www.legislation.gov.uk
```

> **Two notes for the author.**
>
> `storage.googleapis.com` is broader than the rest of this list — it is
> Google's general object-store front end, not a registry. It is what
> `gcr.io` redirects layer fetches to, so permitting `gcr.io` without it
> permits the manifest and not the image. If that breadth is unacceptable, the
> alternative is to mirror the distroless base into the site registry at
> commissioning and change `docker/api.Dockerfile` to pull from there — which
> is the better answer for an air-gapped forge anyway, and a change to this
> repository rather than to the router.
>
> `registry-1.docker.io`, `auth.docker.io` and
> `production.cloudflare.docker.com` are permitted and nothing in the
> programme is recorded as using them. They may serve a consumer nobody wrote
> down. If not, they can go — an allow list that only ever grows is one nobody
> can defend.

### Where this is now checked

`docs/egress-policy.md` is generated in the pipeline at stage 2.4b from
`draupnir/svalinn/egress.py` and a transcription of section 10.5. It fails the
build when the divergence *changes* — a new blocked host, or a baseline entry
that stops being blocked because this section was amended and the transcription
is a revision behind. It does not fail on the nine above, which cannot be fixed
from that repository.

**The transcription is the weak link and the artefact says so.** A test in the
pipeline cannot read this manual: it is controlled, it is outside that
repository and it is marked CONFIDENTIAL. So re-checking the transcription
against the current revision belongs in the acceptance schedule — see the
addition to §47 in amendment 4 — rather than in CI.

---

## Proposed amendment 3 — a new Procedure S13

Placed after S12. It deliberately does not restate the installation: that is
`docs/DEPLOYMENT.md`, which is written for an operator rather than an engineer
and is kept current with the scripts. A procedure copied into two documents is
a procedure that is wrong in one of them.

> ### 35 Procedure S13 DRAUPNIR control plane
>
> **Prerequisites.** S8 complete with amendment 2 applied, S10 complete with
> amendment 1 applied, S12 complete.
>
> **What this is.** DRAUPNIR is specified in VLD-SAD-DRAUPNIR-001 and delivered
> from its own repository. This procedure covers what the *estate* owes it: the
> host, the database, the object store and the vault mount. The installation
> itself is `docs/DEPLOYMENT.md` in that repository.
>
> **Steps.**
>
> 1. Confirm the prerequisites from amendments 1 and 2:
>
> ```
> # on ALVISS
> podman machine list
> psql -h 10.20.0.21 -U draupnir -d draupnir -c 'SELECT 1'
> mc ls forge/draupnir
> ```
>
> 2. Confirm the vault is mounted and shared with the podman machine, per
>    amendment 2a:
>
> ```
> df -h /forge/vault
> podman machine inspect --format '{{.Resources.Volumes}}'   # /forge must appear
> ```
>
> 2a. Put the Slurm token in `~/.config/draupnir/secrets.env` alongside the
>    database and object store credentials, per amendment 2b. The control plane
>    reaches Slurm over `slurmrestd`; it has no `sbatch` and is not getting one.
>
> 2b. Confirm the accelerator type matches `gres.conf` on REGIN. `install.sh`
>    writes `DRAUPNIR_ACCELERATOR=gb10` into `draupnir.env`, and every job then
>    asks for `--gres=gpu:gb10:1` — the form every batch script in Part 5 uses.
>    If §34's `gres.conf` ever declares a different `Type=`, this is the one
>    place to change:
>
> ```
> grep -h 'Type=' /etc/slurm/gres.conf                    # on REGIN
> grep DRAUPNIR_ACCELERATOR ~/.config/draupnir/draupnir.env   # on ALVISS
> ```
>
> 3. Clone the repository and run the preflight. It reports each dependency
>    separately, and a failure names which of the four it is:
>
> ```
> git clone <repository> ~/draupnir && cd ~/draupnir
> ./deploy/install.sh --check
> ```
>
> 4. Install, following `docs/DEPLOYMENT.md` from Part 4. In outline:
>    `./deploy/install.sh --revision <sha> --site sindri`, then put the database
>    and object store credentials into `~/.config/draupnir/secrets.env` at 0600,
>    then restart the three agents.
>
> 5. Run `./deploy/install.sh --check` **again**. Before the credentials were in
>    place the deep checks reported `unverified`; this is the run that confirms
>    the database and the bucket will actually accept the control plane.
>
> 6. Apply the migrations and run the smoke test, per stage 4.1 of SAD 11H.
>
> **Completion check.** `./deploy/install.sh --check` reports `postgresql: ok`,
> `object-store: ok`, `hodd-vault: ok` and `scheduler: ok`, all three units are
> active, `/readyz` reports ready, and `make verify-chain` verifies the site's
> chain.

---

## Proposed amendment 3a — which document is authoritative, stated once

The gap RF-E19 is really about is not the missing procedure. It is that two
documents describe how work gets done at Sindri and neither says which one
wins, so an operator following the manual builds an estate with no control
plane and an operator following `docs/DEPLOYMENT.md` installs one onto a host
this manual never prepared.

Procedure S13 joins them. This says which one is in charge, and when.

**The recommendation: by hand at commissioning, through DRAUPNIR thereafter.**
That is not a compromise between the two positions; it is what each document is
actually good for.

Part 5 drives the pipeline by hand — `sbatch`, `sacct`, `scontrol` — and §5's
schedule note already says the figures assume it. At commissioning that is
right: you are proving hardware, and a control plane between the operator and
the hardware makes a fault harder to locate rather than easier. Acceptance
tests A1 to A11 are all of that kind, and they should stay that way.

After acceptance the balance reverses. A run driven by hand produces no ledger
entry, and the ledger is what C14's Article 53 obligation is discharged
against. So a hand-driven run after commissioning is not a shortcut, it is an
artefact with no provenance.

Proposed text, for §5 beside the existing schedule note:

> **Which document is authoritative.** This manual is authoritative for
> commissioning: Parts 3 to 5 build the estate and prove it, and acceptance
> tests A1 to A11 are run by hand against the hardware. From the point the
> estate passes acceptance, **DRAUPNIR is authoritative for operating it** —
> runs are submitted, gated and released through the control plane, because
> that is what produces the record the Article 53 obligation is discharged
> against.
>
> Where a Part 5 procedure and a control plane screen appear to do the same
> thing, they do. Which one applies depends only on whether the estate has been
> accepted. Procedure S13 is the point at which authority passes from this
> document to `docs/DEPLOYMENT.md` and the control plane it installs.

And the corresponding paragraph is already in `docs/DEPLOYMENT.md` under
"Which document is in charge", so the statement exists on both sides rather
than in the one an operator did not happen to open. A test asserts that guide
keeps saying it.

---

## Proposed amendment 3b — four places the documents disagree with themselves

Found while reconciling `docs/DEPLOYMENT.md`'s estate table against §2, which
is the only reason anybody read the two tables side by side. None is a fault in
the estate; all four are two statements that cannot both be true.

They are listed with the half this repository behaves as though were true, so
that if the author picks the other one it is clear what has to change here.

### 1. Loki, on REGIN

**§2's role table** gives REGIN "Prometheus, Grafana, Loki".
**Procedure S11 step 1** installs `prometheus prometheus-alertmanager grafana`,
and no procedure anywhere in the manual installs Loki or any other log shipper.

So the estate has no log aggregation, while three documents say it has —
§2, §12's diagram, and `docs/DEPLOYMENT.md`, which inherited the claim.

**Recommended: strike Loki, and say so.** REGIN is a Raspberry Pi already
running `slurmctld`, `slurmdbd`, MariaDB, Prometheus, Alertmanager, Grafana,
`dnsmasq`, `chrony` and a kiosk browser. Ingesting logs from six hosts is not a
small addition. DRAUPNIR's lines already carry the run id, the site id and the
actor (SAD 11E), which is what makes them worth reading one host at a time.

Proposed §2 role cell for REGIN:

> Slurm controller. Prometheus, Alertmanager, Grafana. DNS and NTP. Drives
> CON-B. **No log aggregation: logs stay on the host that wrote them, and the
> runbook's diagnosis steps read them there.**

`docs/DEPLOYMENT.md` now carries the matching statement under *Where the logs
are*, and a test asserts it keeps carrying it — deleting the word from one
table would leave the estate with no aggregation and no document saying so,
which is worse than the wrong row, because at least a wrong row is falsifiable.

### 2. CON-B: fitted, or a bench spare?

**§2** says REGIN "Drives CON-B". **§6.3** says "CON-B held as bench spare".

This one has consequences already drafted against it: amendment 2c puts
DRAUPNIR's probe reading on Grafana dashboard 2, and amendment 2d rotates the
CON-B panel between Grafana and the control plane's console. Both target a
display one section says is not fitted.

**Recommended: fitted.** VLD-WIR-SINDRI-001 cables it — D-03 HDMI and D-04
touch to REGIN, P-13 to PDU-C socket 1 — and a bench spare is not given a power
feed and two leads. §6.3's allocation cell should read "CON-A fitted in Rack C,
CON-B fitted in Rack B".

### 3. Which machine drives CON-A

**§2** says DVALIN "Drives CON-A". **§6.2** lists the single UGREEN HDMI lead as
"REGIN to CON-A".

**Recommended: DVALIN**, and §6.2's allocation cell is the error.
VLD-WIR-SINDRI-001 D-01 runs DVALIN HDMI 2.1a to CON-A and D-05 powers the
console from DVALIN's USB-C 2. §2 agrees.

It matters more than a cable label. SAD Decision U2 makes CON-A the console
that survives a total network failure, which holds only if the appliance it
watches is the one driving it. Driven from REGIN it is a second REGIN console
and not a local view at all — and REGIN is the machine most likely to be gone
in the outage CON-A is bought for.

### 4. The image registry that is nowhere

Not a contradiction inside the manual — a contradiction between it and this
repository, and this repository's to own.

`deploy/lib.sh` derives `registry.<site>.veldris.internal` and `rollout.sh`
pulls the unit images from it. No machine in §2 runs a container registry, and
§34's `dnsmasq` configuration is given no record for one. So the default image
reference names a host that does not resolve, and a rollout fails at the pull
with a DNS error rather than at a stage that explains itself.

**No manual amendment is proposed for this**, because adding a registry to the
estate is a larger decision than a role-table edit — it is a service, on a
machine, with storage and a lifecycle. The deployment guide now says the
registry is not part of the estate and that `--registry` must name a host that
exists. RF-04 in the companion register is the other half: nothing pushes an
image to a registry either, so today there is nothing to pull even if one
resolved.

---

## Proposed amendment 3c — the federation link, and the two-node recovery ring

Two of the estate's gaps have consequences inside the control plane that were
recorded in neither document. Both are small to fix and neither is fixable
after the event.

### The federation link terminates on REGIN

Procedure S11 step 1 already installs `wireguard-tools` on REGIN. Nothing
configures a tunnel, and §11A of the SAD assumes GULLINBURSTI and the link are
on the same host — which they are not, because GULLINBURSTI runs inside
DRAUPNIR on ALVISS.

**Recommended: REGIN**, and the deciding reason is FileVault. Procedure S10
ends with `sudo fdesetup enable` on ALVISS, so it boots to an unlock screen; a
federation link terminating there is down after every power cut until somebody
visits the site. REGIN has no such gate, is already on both fabrics, and
already has the tooling.

Proposed addition to Procedure S11, after the observability steps:

```
# on REGIN. The forge end of the federation link. The peer, its public
# endpoint and the allocated addresses come from the Veldris_NXT side.
sudo tee /etc/wireguard/wg-federation.conf >/dev/null <<'EOF'
[Interface]
Address    = <forge tunnel address>/32
PrivateKey = <generated here, never transcribed>
[Peer]
PublicKey  = <Veldris_NXT public key>
Endpoint   = <Veldris_NXT endpoint>:51820
AllowedIPs = <MEGINGJORD subnet>
PersistentKeepalive = 25
EOF
sudo chmod 600 /etc/wireguard/wg-federation.conf
sudo systemctl enable --now wg-quick@wg-federation

# MEGINGJORD is outside the zone this dnsmasq is authoritative for, so a query
# for it is forwarded upstream and fails. It needs an explicit record.
echo 'address=/megingjord.veldris.internal/<MEGINGJORD tunnel address>' \
  | sudo tee -a /etc/dnsmasq.d/forge.conf
sudo systemctl restart dnsmasq
```

and to Procedure S13, so ALVISS routes to it:

```
# on ALVISS. REGIN carries the tunnel; this host reaches through it.
sudo route -n add -net <MEGINGJORD subnet> 10.20.0.5
```

> **And §10.5 does not permit the tunnel.** The allow list is a list of
> *hostnames*, which is outbound web traffic. WireGuard is UDP to a specific
> endpoint on port 51820, and nothing in §10.5 covers it. This belongs with the
> nine hosts of amendment 2g; it is listed separately because it is a protocol
> and a port rather than a name, and an allow list that only understands
> hostnames cannot express it at all.

**Nothing changes in the control plane.** DRAUPNIR still calls
`https://megingjord.veldris.internal/…` and the egress broker still decides
that call against the same entry under the same approving policy. A route is
not a destination.

### Telling DRAUPNIR the ring became two nodes

§7.4 of the wiring document says that on an appliance failure the two survivors
are recabled as a direct pair and "the ring partition [is set] to two nodes".
That sets Slurm's partition. It does not tell the control plane, which until
now treated `ring` as all-or-nothing over the whole estate — so a substrate run
would have been refused as *degraded*, which sends an operator to a rack to
find nothing wrong.

The control plane now takes a declared ring, **by membership rather than by
count**, because a count cannot say which two: ring membership is which
machines have a DAC cable between them. Consider DVALIN failing, DURIN and DAIN
being recabled, and DVALIN later being repaired — the estate then has three
usable appliances and a ring of exactly DURIN and DAIN, and a count of two
would have picked DVALIN and DURIN by rank. That is a run submitted across a
cable that does not exist.

Proposed addition to §7.4's "One appliance fails" row, after the Slurm change:

> Then tell the control plane which two machines are cabled together, and
> restart the worker so it takes effect:
>
> ```
> # on ALVISS
> echo 'DRAUPNIR_RING_MEMBERS=durin,dain' >> ~/.config/draupnir/draupnir.env
> launchctl kickstart -k gui/$(id -u)/com.veldris.draupnir.worker
> ```
>
> The names are the survivors, in the order they are cabled. The worker writes
> the declaration to the ledger on its next tick: a substrate run across two
> ranks is not comparable to one across three — different collectives,
> different step times, different numerics — and "was this a two-node ring" is
> a question asked months later about a specific release.
>
> **Remove the line when the ring is restored.** A forge left declared at two
> after a third appliance is recabled will keep placing two-node runs and
> nothing will complain, because two of two is not degraded.

---

## Proposed amendment 3d — element requeue stays on REGIN, and M6 should say so

**No change is proposed to Procedure M6 step 5.** It is already right:

```
scontrol requeue <arrayjobid>_<taskid>
```

What is proposed is a sentence saying that this remains a **manual** step after
the control plane is installed, because everything else in Part 5 becomes
DRAUPNIR's job once the estate is accepted (amendment 3a) and a reader would
reasonably assume this did too.

### Why it cannot be automated as things stand

AC-F6 requires a failed array element to be retried individually without
disturbing the other fifty five. `--array=0-55%3` is the whole placement
strategy, and the mechanism for retrying one is exactly step 5's command.

DRAUPNIR's `motsognir.slurm/v1` driver implements it and **cannot run at
Sindri**: RF-E05 established that ALVISS has no Slurm client tools and the
control plane's container is distroless with no shell to run one from. So
submission goes over `slurmrestd` — and slurmrestd v0.0.40 exposes submit, read
and cancel, and no requeue at all.

The driver refuses rather than approximating, because both approximations are
worse than a refusal:

- **Cancel the element and submit a replacement.** It gets a new job
  identifier, which severs it from the array and loses both the `%3` throttle
  and the accounting record tying the fifty six together.
- **Requeue the array.** Restarts all fifty six and discards the compute of
  every element that succeeded — which is the precise failure AC-F6 exists to
  prevent.

AC-F6 is recorded **DEVIATED** for this reason rather than IMPLEMENTED.

### Proposed addition to Procedure M6, after step 5

> **This step stays manual after Procedure S13.** The control plane submits and
> monitors the array, and it cannot requeue an element: it reaches Slurm over
> `slurmrestd`, which has no requeue. A failed element is requeued here, on
> REGIN, by the command above. The console will not offer a control for it.

### Closing it, if the site wants to

Two routes, and the choice is the site's rather than this document's:

1. **A `scontrol` bridge on REGIN** — a small authenticated endpoint that runs
   the requeue and nothing else. The smaller change, and it adds a component
   that can restart jobs, so its authorisation is not an afterthought.
2. **Slurm client tools on ALVISS** — the client package and a munge key, with
   the worker using `motsognir.slurm/v1`. This undoes part of the reasoning
   behind amendment 2b: the container is distroless by requirement (AC-Q7), so
   the binaries would have to go into the image.

Neither is proposed here. The honest default is that the step is manual and
both documents say so, which is what the addition above does.

---

## Proposed amendment 4 — the acceptance schedule, §47

Add four rows. The current schedule runs A1 to A11 and none concerns the
control plane, so an estate can pass acceptance with DRAUPNIR absent.

| Ref | Test | Method | Pass criterion |
|---|---|---|---|
| A12 | Control plane dependencies | `./deploy/install.sh --check` on ALVISS | `postgresql: ok`, `object-store: ok`, `hodd-vault: ok` and `scheduler: ok`; every unit active; `/readyz` ready |
| A13 | Ledger integrity | `make verify-chain`, then rebuild the projection | The chain verifies and the rebuilt projection is byte-identical |
| A14 | End to end through the control plane | The §46 worked example, driven through DRAUPNIR rather than by hand | The same release package as A10, with a complete ledger chain behind it |
| A15 | Egress policy reconciliation | Compare §10.5 against the transcription in `draupnir/svalinn/site_egress.py`, then run `python tasks.py egress-policy` | The transcription matches §10.5 host for host, and the task exits zero |

A14 depends on findings still open in the companion register (RF-E05, RF-E10,
RF-E12) and should be added as *specified* now and *tested* when those land.

**A15 is a manual step and has to be**, which is worth stating because
everything around it is automated. The reconciliation compares what DRAUPNIR
reaches against a *copy* of §10.5 held in that repository. Nothing in the
pipeline can check the copy against this document: it is controlled, it is not
in that repository, and it is marked CONFIDENTIAL. So the pipeline catches
every change on its own side and none on this one, and A15 is the step that
closes that. It takes about a minute — eighteen hostnames, in order — and
without it a transcription that silently goes a revision out of date makes
every conclusion drawn from it wrong in the same quiet way the finding was
about.

---

## Proposed amendment 5 — §48 operational procedures

**Weekly**, add: *"Confirm `make verify-chain` verifies the site's chain. The
worker does this hourly and alarms; this is the check that the alarm works."*

**Per release**, add: *"Confirm the gate evidence recorded in DRAUPNIR matches
the artefacts in the registry, and that every quantised build carries its own
evidence."*

---

## Proposed amendment 6 — §11, what a power event costs

**FileVault on ALVISS stays on**, and the consequence is written down. RF-E22
put it as an open question; the answer turned out to be settled by something
the question did not consider.

### The question as it was put

Procedure S10 step 1 ends with `sudo fdesetup enable`, so ALVISS boots to an
unlock screen and nothing runs until somebody types a password at the console.
Against that: ALVISS holds no state by design — everything is in PostgreSQL and
MinIO on ANDVARI — so what FileVault protects there is `secrets.env`, a
database password and an object-store key, both rotatable and both scoped to a
host on an isolated fabric. Small protection, large cost. It looked like a
trade worth making.

### Why it is not a trade

**Turning FileVault off would not deliver an unattended return.** Three
separate things stop one on this host, and removing any one changes nothing:

1. **FileVault**, as above.
2. **The `gui` launchd domain.** The control plane's agents exist only while
   the service account is logged in. This is the macOS counterpart of systemd
   lingering, and macOS has no counterpart of `loginctl enable-linger` — the
   remedies are a logged-in session or automatic login, and macOS disables
   automatic login while FileVault is on anyway.
3. **`podman machine`.** On macOS the podman client talks to a per-user Linux
   virtual machine tied to that user's session. Even a system-domain
   LaunchDaemon would have no runtime to reach.

So the benefit FileVault would be traded for does not exist, and the cost —
`secrets.env` as a plain file on an unencrypted disk — is real. It stays on.

### Proposed text, for §11

> **Recovery after an unplanned power event, ALVISS.** The control plane does
> not return until a person is at the ALVISS console. This sets the floor on
> recovery time for every incident that begins with a power event and should be
> planned for rather than discovered.
>
> Three things require attendance and all three must be satisfied: FileVault
> requires the disk to be unlocked at the console; the control plane's launchd
> agents run in the `gui` domain and exist only while the service account is
> logged in; and `podman machine` is a per-user virtual machine tied to that
> session.
>
> For a **planned** restart, `sudo fdesetup authrestart` holds the unlock key
> in memory for exactly one boot, so the machine returns to the login window
> without a password at the console. It cannot help after a power cut, because
> nothing asked for it beforehand.
>
> **ANDVARI keeps FileVault unconditionally.** The vault, the ledger and the
> object store are all on it, and none of the reasoning above applies.

### And it belongs with gap G1

A UPS is what turns a hard stop into a clean shutdown somebody can plan around,
and a clean shutdown is the only version of this a person can schedule. Note
that as the cable schedule stands the UPS inserts at P-01, which feeds PDU-A —
and **ALVISS is on PDU-B**, so the supply as specified would not help here at
all. `proposed-wiring-amendments.md` proposes moving the insertion point for
exactly this family of reasons.

---

## Nothing left open

This document ended with FileVault on ALVISS as an open question. It is now
**amendment 6 above**, and the answer is that it stays on — not because the
trade was judged acceptable, but because the benefit it would have been traded
for does not exist. Turning it off leaves two other things requiring a person
at the console, and it would put `secrets.env` on an unencrypted disk for
nothing.

The decisions this document still asks its author for are all inside the
amendments: the executor model (2f and the section after it), the nine egress
hosts (2g), the four contradictions (3b), the federation link's termination
(3c), and CON-B's bind address (2d), which waits on authentication and ingress
in the companion register rather than on anything here.
