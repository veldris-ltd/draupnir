# Setting up DRAUPNIR on the Sindri Forge

A step-by-step guide. Follow it in order, top to bottom, and check the result
of each step before moving to the next.

You do not need to understand the system to do this correctly. You do need to
read what each command prints and stop when it does not match what this page
says it should.

**If anything does not match, stop.** Do not continue, do not retry a command
more than once, and do not improvise. [Part 8](#part-8-when-something-is-wrong)
tells you what to do instead. Stopping is always safe. Guessing is not.

---

## What this does

It installs the DRAUPNIR **control plane** on a machine called **ALVISS**.

The control plane is the part people use: the website operators log into, and
two background programs that do the work behind it. It does not do the model
training itself — that happens on other machines.

**It is safe to run this more than once.** If you get halfway and something
goes wrong, running it again from the start is the normal way to finish. It
does not delete data, and it does not touch the database.

**It will not delete anything.** Nothing in this guide removes runs, models or
records. Those live on a different machine (ANDVARI) and this guide does not
write to it.

### Which document is in charge

Two documents describe how work gets done at Sindri, and until now neither said
which one wins. That is worth two paragraphs, because the answer changes what
you should do when they disagree.

**VLD-INF-SINDRI-001**, the build manual, is authoritative for **commissioning**.
Its Part 5 drives the training pipeline by hand — `sbatch`, `sacct`,
`scontrol` — and that is deliberate: at commissioning you are proving the
hardware, and a control plane between you and the hardware makes a fault harder
to locate rather than easier. Acceptance tests A1 to A11 are all of that kind.

**DRAUPNIR is authoritative afterwards.** Once the estate has passed
acceptance, runs are submitted, gated and released through the control plane,
because that is what produces the ledger the Article 53 obligation is
discharged against. A run driven by hand after commissioning is a run with no
record, and the record is the point.

So: **by hand at commissioning, through DRAUPNIR thereafter.** If a procedure
in Part 5 and a screen in the console appear to do the same thing, they do —
and which one you should use depends only on whether the estate has been
accepted yet.

**Where the two documents meet** is Procedure S13, *DRAUPNIR control plane*,
which the manual runs after S12. It covers what the estate owes the control
plane — the host, the database, the object store, the vault mount and the
scheduler token — and then sends you here for the installation itself. This
guide does not repeat S13 and S13 does not repeat this guide: a procedure
written down twice is a procedure that is wrong in one of the two places.

### Words used here

| Word | Means |
|---|---|
| **Sindri** | The site. A group of machines that work together. Not one computer |
| **ALVISS** | The one machine you are installing on |
| **ANDVARI** | A different machine, holding the database and the files. You will not log into it |
| **The terminal** | The black window where you type commands |
| **A revision** | A specific version of the software, written as a long code like `d0508ff9c4a1…` |
| **The service account** | The username the software runs as. Not your personal login |

---

## Part 0. Before you start

### Time and timing

Allow **45 minutes**. Do it at a time agreed with the team, because the
website will be unavailable for a few minutes near the end.

Training runs already in progress are **not** affected. They continue on the
other machines and nothing is lost.

### Five things you must have first

Do not start until you have all five. Chasing a missing one halfway through is
how a 45-minute job becomes an afternoon.

| # | What | Who gives it to you |
|---|---|---|
| 1 | Login details for **ALVISS**, for the service account | Whoever runs the infrastructure |
| 2 | The **revision** — a long code identifying the version to install | The engineer who prepared the release |
| 3 | The **four credentials** (Part 5) | The engineer who holds them. See the warning below |
| 4 | Confirmation that **ANDVARI is running** | Whoever runs the infrastructure |
| 5 | A phone number or chat handle for **one engineer**, available while you work | Agree this in advance |

> **About the credentials.** These are passwords. Ask for them to be placed on
> the machine for you, or to be given to you in whatever secure tool your team
> uses for passwords. **Never accept them by email or chat message, and never
> send them to anyone that way**, including to the person helping you. If
> someone offers to paste them into a chat, say no and ask for the secure tool
> instead.

### Two things you must not do

1. **Do not run anything as `root`** or with the word `sudo` in front of it,
   unless this guide explicitly says to. The installer refuses to run as root
   on purpose.
2. **Do not create or handle the signing key.** If anyone asks you to, say
   that it is not part of this task, and refer them to the engineer.

---

## Part 1. Get onto the machine

Open your terminal and connect to ALVISS. Replace `alviss.sindri.veldris.internal`
if you were given a different address, and `SERVICE-ACCOUNT` with the username
you were given:

```bash
ssh SERVICE-ACCOUNT@alviss.sindri.veldris.internal
```

**What you should see:** a line of text ending in `$` or `%`, waiting for you
to type. That is the machine's prompt, and it means you are connected.

**If you see** `Permission denied` or `Could not resolve hostname` — stop.
Your login details or the address are wrong. Go back to item 1 in Part 0.

Now confirm you are the right user:

```bash
whoami
```

**What you should see:** the service account name you were given.

**If it says `root`** — stop. You are the wrong user. Log out by typing
`exit`, and ask for the service account login.

---

## Part 2. Get the software onto the machine

You need a copy of the DRAUPNIR repository on ALVISS. Check whether it is
already there:

```bash
ls ~/draupnir
```

**If you see a list of names** including `deploy` and `docs` — it is already
there. Move to the update command below.

**If you see** `No such file or directory` — fetch it:

```bash
git clone git@github.com:veldris-ltd/draupnir.git ~/draupnir
```

Either way, now make sure it is current and go into the folder:

```bash
cd ~/draupnir
git fetch --all
git checkout main
git pull
```

**What you should see:** `Already up to date.` or a short list of updated
files.

**If you see** `Permission denied (publickey)` — stop. This machine does not
have access to the repository. That is for the engineer to fix.

---

## Part 3. The safety check

This step **changes nothing**. It only looks at the machine and reports
whether the real install would work. Always run it first.

```bash
./deploy/install.sh --check
```

**What you should see** — the word `preflight`, some lines about podman, then
two lines saying the database and file storage are answering, and finally
`check complete`.

ALVISS is a Mac, so the middle lines look like this:

```
==> preflight
    platform: darwin
    podman: podman version 5.0.0
    podman machine: running
    launchd gui domain: available

==> dependencies on ANDVARI
    site domain: sindri.veldris.internal
    PostgreSQL at andvari.sindri.veldris.internal:5432: answering
    MinIO at andvari.sindri.veldris.internal:9000: answering

==> will they accept us
    warning: postgresql: not verified. the URL carries no password, so this is a first commissioning
      put the credentials in secrets.env and run --check again
    warning: object-store: not verified. no access key is configured, so this is a first commissioning
      put the credentials in secrets.env and run --check again
    hodd-vault: mounted, 41% used, 2360.0 GB free
    scheduler: slurmrestd is answering on regin.sindri.veldris.internal:6820; partitions not checked, no token

==> check complete
    nothing on this host was changed.
```

The two `not verified` warnings are **expected the first time**. There are no
credentials yet — you put them in at Part 5 — so the deeper checks cannot run.
They are warnings rather than silence because "we did not check" and "we
checked and it was fine" are different things, and only one of them is a
reason to carry on confidently.

After Part 5 you run `--check` once more and those two lines become:

```
==> will they accept us
    postgresql: connected to draupnir
    object-store: bucket draupnir is present
    hodd-vault: mounted, 41% used, 2360.0 GB free
    scheduler: slurmrestd is answering, partitions adapters, ring
```

The scheduler line gains the partition names once a token is in
`secrets.env`. Before that it can only tell you something is listening and
speaking the right protocol; afterwards it has actually asked the cluster what
it has, and compared the answer against the two partitions the control plane
places work into.

The vault line does not wait for credentials: it is a mount on this machine
rather than something to log in to, so it is checked from the very first run.

**The scheduler line is about REGIN**, which is the machine that runs Slurm.
The control plane reaches it over HTTP at
`http://regin.sindri.veldris.internal:6820` — it has no `sbatch` command and is
not getting one, because it runs in a container with no shell. You do not
configure that address: the installer derives it from the site name. It is
written here so that when the line says `unreachable` you know what it was
trying to reach.

On a Linux host the same command reports `platform: linux` and a line about
`lingering` instead of the two launchd lines. Both are correct; which one you
get depends only on the machine.

Now compare what you saw against this table:

| If you see | It means | Do this |
|---|---|---|
| `check complete` | Everything is ready | Continue to Part 4 |
| `podman not found` | A required program is missing | **Stop.** Ask the engineer to install podman |
| `run this as the service account, not as root` | Wrong user | **Stop.** Go back to Part 1 |
| `the podman machine is not running` | On a Mac, podman needs a small helper running | Run the command in note A below, then `--check` again |
| `no launchd gui domain` | You are connected in a way that cannot start software | **Stop.** See note B below |
| `no user systemd instance` | On Linux, the account is not set up to run software | **Stop.** Ask the engineer |
| `PostgreSQL … no answer` | The database machine is not reachable | **Stop.** Ask whoever runs the infrastructure |
| `MinIO … no answer` | The file storage is not reachable | **Stop.** Same as above |
| `postgresql: auth-refused` | The database is there and would not let us in | The password in `secrets.env` is wrong, or nobody has allowed this machine. See note E |
| `object-store: auth-refused` | Same, for the file storage | The access key or secret key in `secrets.env` is wrong |
| `postgresql: missing` | The database server is running and has no `draupnir` database in it | **Stop.** Nobody has done the one-time setup. See note F |
| `object-store: missing` | Same, for the bucket | **Stop.** See note F |
| `scheduler: unreachable` | Nothing answered on `regin.sindri.veldris.internal:6820`. That is REGIN, and `slurmrestd` is the program that should be answering | **Not a stop.** The control plane installs and runs; it cannot dispatch training until this is fixed. Tell whoever runs REGIN — VLD-INF-SINDRI-001 Procedure S11 |
| `scheduler: unverified` | No scheduler address is configured at all | **Not a stop** on a machine with no estate behind it, which is normal for a first install. On ALVISS it means `DRAUPNIR_SCHEDULER_URL` is missing from `draupnir.env` |
| `not verified` | The check could not run | Expected before Part 5. After Part 5, it means `secrets.env` is empty or unreadable |
| `hodd-vault: unreachable` | Nothing is mounted where the vault should be | **Stop.** The NFS mount from ANDVARI is not there. See note G |
| `hodd-vault: missing` | There is a folder where the vault should be, and it is not the vault | **Stop.** This one matters more than it looks. See note G |
| `hodd-vault: not verified` | This machine is not configured to have a vault | Fine if this forge has none. At Sindri it is not: ask the engineer |
| `warning: lingering is off` | On Linux, the software would stop when you log out | Run the command in note C below, then `--check` again |
| `warning: FileVault is on` | After a power cut this machine needs somebody at the keyboard | Read note D. This is not a reason to stop |

> **Note A — the podman machine (Mac only).** Run this once. It takes a couple
> of minutes the first time and is instant afterwards:
>
> ```bash
> podman machine init --cpus 2 --memory 4096 --now
> ```
>
> If it says a machine already exists, start it instead:
>
> ```bash
> podman machine start
> ```

> **Note B — the gui domain (Mac only).** macOS will only start this software
> for an account that is properly logged in. If you connected with plain `ssh`
> and nobody is logged in at the machine itself, you will see this. Ask the
> engineer to log the service account in at the console, or to enable
> automatic login for it, then try again.

> **Note C — lingering (Linux only).** Run this once, then run the `--check`
> command again and confirm the warning is gone:
>
> ```bash
> loginctl enable-linger "$(id -un)"
> ```

> **Note D — FileVault (Mac only).** The disk on ALVISS is encrypted, which is
> correct and should stay that way. It means that after a power cut the machine
> stops at a password screen and **nothing starts until somebody unlocks it**,
> including DRAUPNIR. Nothing is broken and nothing is lost; a person has to be
> there. For a *planned* restart, use `sudo fdesetup authrestart`, which unlocks
> the disk on the way back up so the machine returns on its own.

> **Note E — refused.** The machine is up and answering, and it will not
> accept us. That is almost always a typo in `~/.config/draupnir/secrets.env`,
> so check that first: it is the one file you edited by hand. If the values are
> right, the database may not have a rule permitting this machine, which is for
> whoever runs ANDVARI (VLD-INF-SINDRI-001 Procedure S8).

> **Note F — missing.** The server is running and the thing DRAUPNIR needs
> inside it — a database called `draupnir`, or a bucket called `draupnir` — was
> never created. This is one-time setup on ANDVARI, not something to fix on
> this machine, and not something re-running the installer will resolve. Send
> whoever runs the infrastructure to VLD-INF-SINDRI-001 Procedure S13.

> **Note G — the vault.** DRAUPNIR watches how full the storage on ANDVARI is
> and warns at 85 per cent, so this machine has to be able to see it. Two
> different answers, and the second is the serious one:
>
> `unreachable` means nothing is mounted — the connection to ANDVARI's storage
> is not there. Ask whoever runs the infrastructure to restore it.
>
> `missing` means there **is** a folder in that place and it is not the
> storage. That usually means somebody created the folder while the real one
> was disconnected. Do not put anything in it and do not try to set it up: the
> real storage cannot be reconnected over the top of it, and anything written
> there goes onto this machine's own disk where nobody is backing it up. Say
> exactly that when you report it.

Do not continue past this point until `--check` ends with `check complete`.

---

## Part 4. Install

Take the revision code from Part 0 item 2 and put it where `PASTE-REVISION-HERE`
is. Keep the rest of the line exactly as it is:

```bash
./deploy/install.sh --revision PASTE-REVISION-HERE --site sindri
```

This takes a few minutes. It prints as it goes.

**What you should see** — six headed sections, in this order, ending with
`commissioned`:

```
==> preflight
==> dependencies on ANDVARI
==> configuration
    wrote ~/.config/draupnir/draupnir.env
    created empty ~/.config/draupnir/secrets.env (0600)

==> units
    wrote ~/Library/LaunchAgents/com.veldris.draupnir.api.plist
    wrote ~/Library/LaunchAgents/com.veldris.draupnir.worker.plist
    wrote ~/Library/LaunchAgents/com.veldris.draupnir.web.plist

==> revision
    draupnir-api <- registry.sindri.veldris.internal/draupnir-api:…
    draupnir-worker <- registry.sindri.veldris.internal/draupnir-api:…
    draupnir-web <- registry.sindri.veldris.internal/draupnir-web:…

==> enable and start

==> verify
    draupnir-api: active
    draupnir-worker: active
    draupnir-web: active
```

> On a Linux host the `units` section names
> `~/.config/systemd/user/draupnir-api.service` and the two beside it instead.
> The three names under `verify` are the same on both.

> **`draupnir-worker` showing `draupnir-api` is correct**, not a mistake. Two
> of the three programs share one package. If you see that, it is working.

At the very end you will see a warning that the health check got no answer.
**This is expected at this stage.** The software has no credentials yet, so it
cannot reach the database. You fix that next.

| If you see | Do this |
|---|---|
| `commissioned` at the end | Continue to Part 5 |
| `error:` anywhere | **Stop.** Note the exact wording and go to Part 8 |
| `not active` for any of the three | **Stop.** Go to Part 8 |

---

## Part 5. Put in the credentials

The installer made an empty file for the four passwords and deliberately did
not fill it in. Passwords are never written by any automatic process here.

Open the file in a simple text editor:

```bash
nano ~/.config/draupnir/secrets.env
```

**What you should see:** an empty screen with a menu of shortcuts at the
bottom. That is the editor.

**If you see** `nano: command not found`, use this instead and follow the
same steps — the save keys differ, so read the note under the block below:

```bash
vi ~/.config/draupnir/secrets.env
```

Type or paste the four lines below, replacing each `...` with the value you
were given. Keep the names on the left exactly as they are — no spaces around
the `=` sign:

```
DRAUPNIR_DATABASE_URL=postgresql+asyncpg://draupnir:PASSWORD@andvari.sindri.veldris.internal:5432/draupnir
DRAUPNIR_DATABASE_URL_SYNC=postgresql+psycopg://draupnir:PASSWORD@andvari.sindri.veldris.internal:5432/draupnir
DRAUPNIR_OBJECT_STORE_ACCESS_KEY=...
DRAUPNIR_OBJECT_STORE_SECRET_KEY=...
```

Three things to check before you save:

1. **The first two lines are nearly identical.** They differ only in the word
   `asyncpg` and `psycopg`. That is correct — do not make them the same.
2. **The username in the first two lines must be `draupnir`**, not `postgres`
   or `admin`. If you were given a different one, stop and ask. Using an
   administrator account here quietly breaks the separation between sites, and
   nothing will appear wrong until it matters.
3. **No line starts with a space.**

Save and close **in nano**: hold **Ctrl** and press **O**, then press
**Enter**, then hold **Ctrl** and press **X**.

Save and close **in vi**: press **Esc**, then type `:wq` and press **Enter**.
(In vi you must press **i** before typing anything, to start inserting.)

Now confirm the file is private to you:

```bash
ls -l ~/.config/draupnir/secrets.env
```

**What you should see:** the line begins with `-rw-------`. Those dashes mean
nobody else on the machine can read it.

**If it begins with anything else**, run this and check again:

```bash
chmod 600 ~/.config/draupnir/secrets.env
```

---

## Part 6. Start it properly and check it works

Restart the three programs so they pick up the credentials.

On ALVISS, which is a Mac:

```bash
for part in api worker web; do launchctl kickstart -k "gui/$(id -u)/com.veldris.draupnir.$part"; done
```

On a Linux host:

```bash
systemctl --user restart draupnir-api draupnir-worker draupnir-web
```

**What you should see:** nothing at all. No output means it worked.

Now run the safety check one more time. This is the run that proves the
credentials you just typed actually work — before Part 5 it could only say it
had not checked:

```bash
./deploy/install.sh --check
```

Under `will they accept us` you should now see three lines with no `warning:`
in front of them:

```
==> will they accept us
    postgresql: connected to draupnir
    object-store: bucket draupnir is present
    hodd-vault: mounted, 41% used, 2360.0 GB free
```

If either says `auth-refused`, the credential is wrong: go back to Part 5. Use
the table in Part 3 for anything else.

Wait about 30 seconds, then ask the software whether it is running:

```bash
curl -s http://127.0.0.1:8000/healthz
```

**What you should see:** a short line of text containing the word `ok`.

**If you see nothing, or `Connection refused`** — wait another 30 seconds and
try once more. If it is still empty, stop and go to Part 8.

Now the more important check. This one asks whether it can reach everything it
needs:

```bash
curl -s http://127.0.0.1:8000/readyz
```

**What you should see:** a line containing `"status":"ready"`.

**If you see `"status":"degraded"`** — the software is running but cannot
reach something. The same line lists each thing it checked with `true` or
`false` beside it; the one saying `false` is the problem. This is almost
always a typo in the credentials from Part 5. Go back, check the three points
in that part, save, and run the restart command again.

Finally, check the website itself answers:

```bash
curl -s -o /dev/null -w '%{http_code}
' http://127.0.0.1:8080/
```

**What you should see:** `200`. That number means the console is being served.

**If you see `000` or `connection refused`** — the console is not running.
Go to Part 8.

---

## Part 7. Finish

You are done when all four of these are true:

- [ ] `install.sh` ended with `commissioned`
- [ ] All three services said `active`
- [ ] `/healthz` contained `ok`
- [ ] `/readyz` said `"status":"ready"`
- [ ] The console answered `200`

Tell the team it is complete, and include:

- the machine (ALVISS at Sindri),
- the revision you installed,
- the date and time you finished,
- anything at all that did not match this guide, even if it seemed to fix
  itself.

That last one matters. A step that worked on the second try is worth
mentioning.

Log out:

```bash
exit
```

---

## Part 8. When something is wrong

**Stopping is always the right answer.** Nothing here gets worse by waiting.
The system is built so a half-finished install can be resumed, and so a
control plane that is down does not disturb training already running.

### Before you contact anyone, collect this

On ALVISS, which is a Mac:

```bash
launchctl print "gui/$(id -u)/com.veldris.draupnir.api"
tail -n 50 ~/Library/Logs/draupnir/draupnir-api.log
```

On a Linux host:

```bash
systemctl --user status draupnir-api --no-pager
journalctl --user -u draupnir-api -n 50 --no-pager
```

Copy everything both commands print. Send it with your message. It is far more
useful than a description of what happened.

> Change `api` to `worker` or `web` to ask about one of the other two. If you
> are not sure which is at fault, collect all three: it is cheap, and the one
> you leave out is often the one that mattered.

### Common cases

| What you saw | What it means | What to do |
|---|---|---|
| `no image reference` | The version to run was not recorded | Run Part 4 again with the same revision |
| A service keeps restarting | It starts and immediately stops | Collect the output above; contact the engineer |
| `/healthz` gives nothing | The website part is not running | Wait 60 seconds, try once more, then Part 8 |
| `/readyz` shows `false` | Running, but cannot reach the database or storage | Re-check Part 5, then restart |
| `podman pull` failed | That version is not available to install | Confirm the revision with the engineer |
| Everything stopped after logout | Lingering is off | Run the `enable-linger` command in Part 3 |

### Starting over

If you want a clean slate, this removes what you installed and keeps your
credentials file and all data:

```bash
./deploy/install.sh --uninstall
```

Then start again from Part 3.

### What is never your job

Escalate these rather than attempting them:

- Anything asking for a signing key.
- Anything asking you to log into ANDVARI or a database.
- Any instruction to run a command as `root` or with `sudo` that is not
  written on this page.

---

## Reference

The rest of this page is for engineers. You do not need it to complete the
setup above.

### What runs where

| Host | Runs | Installed by |
|---|---|---|
| **ALVISS** | `draupnir-api`, `draupnir-worker`, `draupnir-web` | `deploy/install.sh`, above |
| **ANDVARI** | PostgreSQL 16, MinIO, the HODD vault and its NFS export, MLflow | VLD-INF-SINDRI-001, not here |
| **REGIN** | Slurm controller, Prometheus, Alertmanager, Grafana, dnsmasq, chrony | VLD-INF-SINDRI-001, not here |
| **DVALIN, DURIN, DAIN** | Training jobs, as processes in the shared `/forge/venv` | Slurm, per job |
| **Veldris_NXT** | MEGINGJORD federation registry | Its own deployment |

This table is held against VLD-INF-SINDRI-001 §2 by a test, so it cannot drift
from the manual on its own. Three rows were wrong before that test existed, and
two of them are worth explaining rather than just correcting.

**There is no image registry on the estate.** This table used to give one to
ANDVARI. Nothing in the manual installs a container registry on any machine,
and REGIN's `dnsmasq` is given no record for one — so the name the scripts
derive by default, `registry.sindri.veldris.internal`, does not resolve. A
rollout that relies on the default fails at the image pull with a DNS error,
which is a long way from the thing that is actually wrong. **Pass `--registry`
with a host that exists** until this is resolved; the companion register tracks
it as RF-04, whose other half is that nothing pushes an image to a registry
either.

**REGIN does not run Loki.** The manual's §2 role table says it does, and this
guide copied that. Procedure S11 installs `prometheus prometheus-alertmanager
grafana`, and nothing anywhere installs Loki or any other log shipper. So the
estate has no log aggregation, and the next section says what that means for
you.

### Where the images come from

The pipeline builds two images on `main` and pushes them to the registry named
in the `DRAUPNIR_REGISTRY` repository variable, tagged with the commit SHA.
`rollout.sh` pulls that tag. Until RF-04 nothing pushed at all — the images
were built to the build cache and tagged locally, so the pull ran against a
registry that had never received one, and `draupnir-run.sh` uses
`--pull=never`, so the unit could not recover either.

| What | Where it is set |
|---|---|
| The registry host | `DRAUPNIR_REGISTRY`, a repository **variable** |
| The credential | `DRAUPNIR_REGISTRY_USER` and `DRAUPNIR_REGISTRY_TOKEN`, repository **secrets** |
| The tag | The commit SHA. Nothing else is ever used as a tag |

**On this host**, `--registry` overrides it. That matters today because **the
estate has no registry**: no machine in VLD-INF-SINDRI-001 §2 runs one and
REGIN's `dnsmasq` has no record for the derived name, so the default resolves
to nothing. See the note in *What runs where*.

**Images are not signed.** `rollout.sh` used to say it "pulls the signed
image"; nothing signs a container image and nothing verifies one at pull. The
word has been removed rather than the claim left standing — plug-in
distributions are signed (RF-02) and the SBOM is signed, and an image is
neither.

### Rolling back

The revision to roll back to comes from the host, not from the runner:

```bash
./deploy/current-revision.sh draupnir-api
```

That prints the tag the unit is actually running, read from the state file
`draupnir-run.sh` writes after an image resolves — so a rollout that named an
image which could not be pulled leaves the last good revision in place.

It used to come from `draupnirctl version`, which prints `draupnirctl 0.1.0
(OpenAPI 1.0.0)`. That whole sentence became the rollback tag, so the reference
was `.../draupnir-api:draupnirctl 0.1.0 (OpenAPI 1.0.0)` — the step that runs
when a deployment has already gone wrong could not work. It was also the wrong
question: `draupnirctl` is a client, and its version is the version of the
thing asking rather than of the thing running here.

`rollback.sh` now refuses anything that is not an image tag, naming what it
received, before it changes anything.

### Getting a certificate

**The installer will not commission a host without one.** SAD 9.5 is "TLS 1.3
only", and the reason this is a refusal rather than a warning is that the
failure it prevents is silent: the session cookie is marked `Secure`, a browser
will not send a `Secure` cookie over plain HTTP, so signing in appears to work
and every request after it arrives anonymous. You would see a console that
loads, a sign-in that succeeds, and an empty run board.

Ask whoever runs the **Veldris internal CA** for a server certificate for this
host's name — `alviss.sindri.veldris.internal` — and put the two files on
ALVISS at `0600`, owned by the service account:

| Setting | What it is |
|---|---|
| `DRAUPNIR_TLS_CERTIFICATE` | The certificate, PEM, with any intermediates |
| `DRAUPNIR_TLS_PRIVATE_KEY` | Its private key, PEM |

Both go in `~/.config/draupnir/draupnir.env`, and `install.sh --check` reports
them:

```
==> preflight
    tls: certificate and key present
```

**If you see `tls: not required, DRAUPNIR_DEV is set`**, this is a development
machine. That is correct there and wrong anywhere else.

**A path that is set and missing is refused too**, and separately, because it
is the worse case: the first check passes, so the deployment reports itself
configured for a transport it cannot terminate.

### What the console origin serves

The console image is also the reverse proxy. That is why the API is reachable
at the same address as the console and why the browser makes same-origin
requests: `/v1/`, `/auth/`, `/healthz`, `/readyz` and `/openapi.json` are
proxied to the API unit, and everything else is the built bundle.

**`/metrics` is deliberately not proxied** and answers `404` here. It is
unauthenticated *because* it is loopback-bound, and publishing it through this
origin would retire that justification without changing the endpoint that
relies on it. Prometheus scrapes it directly from the host.

### Registering with MEGINGJORD

**The control plane will not start until it can authenticate somebody.** That is
deliberate. It used to start and answer `401` to every request, which looks
like a broken deployment: you would spend an afternoon on the network and the
database before finding out that nothing was wrong with either.

What it needs is two settings, and they come from whoever runs MEGINGJORD.
Ask them to register this control plane as an OIDC client, and to tell you:

| Setting | What to ask for | Example |
|---|---|---|
| `DRAUPNIR_OIDC_ISSUER` | The issuer URL, exactly as it appears in the `iss` claim | `https://megingjord.veldris.internal` |
| `DRAUPNIR_OIDC_AUDIENCE` | The audience this control plane is registered under | `draupnir-control-plane` |
| `DRAUPNIR_OIDC_CLIENT_ID` | The client identifier for the console's sign-in flow | `draupnir-console` |

Both go in `~/.config/draupnir/draupnir.env`, and `install.sh` puts defaults
there for you. **None of these is a secret** — an issuer and an audience are public
identifiers, and nothing here holds a client secret, because the control plane
verifies tokens and never requests one.

Tell them the **redirect URI** as well: `https://<this host>/auth/callback`.
The provider will refuse the flow if it is not registered, and the refusal
happens at their end, so it is not something `--check` can find for you.

The control plane is registered as a **public client with PKCE**, so there is
no client secret to distribute — which is the right shape here, because the
thing signing in is a static bundle in a browser and a secret given to it would
not be one.

Two more, only if the answer is not the ordinary one:

| Setting | When you need it |
|---|---|
| `DRAUPNIR_OIDC_JWKS_URL` | The provider publishes its keys somewhere other than `<issuer>/.well-known/jwks.json` |
| `DRAUPNIR_OIDC_LEEWAY_SECONDS` | Clocks are further apart than a minute, which on an estate running `chrony` means something else is wrong |

**What good looks like.** After Part 5, a request with no token is refused and a
request with one is not:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/v1/sites
```

**What you should see:** `401`. That is the correct answer to an unauthenticated
request, and seeing it here means the middleware is in the path.

**If you see `500`**, the control plane could not reach MEGINGJORD for its
signing keys. That is a network or a name problem rather than a configuration
one: check `install.sh --check` and Part 8.

**On a machine with no MEGINGJORD** — a developer's laptop — set `DRAUPNIR_DEV=1`
instead and neither OIDC setting. That installs a fixed development principal
that verifies nothing and reads no header. It logs a warning naming itself on
every startup. **Never set it where real data is held**, and note that it wins
over a configured verifier if somebody sets both.

### Where the logs are

**There is no log collector at Sindri, and that is the decision rather than an
oversight.** REGIN is a Raspberry Pi already running the scheduler, its
database, Prometheus, Alertmanager, Grafana, DNS, NTP and a kiosk browser;
ingesting logs from six hosts is not a small addition to it. So logs stay where
they are written, and both this guide and the runbook read them there.

DRAUPNIR's own log lines carry the run id, the site id and the actor (SAD 11E),
which is what makes them worth reading one host at a time — you can find a run
in them without needing to join across machines.

On ALVISS, which is a Mac:

```bash
tail -n 50 ~/Library/Logs/draupnir/draupnir-api.log
```

On a Linux host, the same output is in the journal:

```bash
journalctl --user -u draupnir-api -n 50 --no-pager
```

If log aggregation is added later, the thing to add is a shipper on each host
rather than a change here: the lines are already structured, and they already
carry the fields a query would select on.

Sindri is a forge — a site — rather than a host. What deploys at Sindri is the
control plane, and SAD Decision S3 puts that on ALVISS rather than on an
appliance, because the appliances are the scarce resource and a control plane
on one is a control plane an out-of-memory kill takes with it.

**Nothing on the appliances is a container.** That row used to read "one
container per job", and it was wrong in a way worth stating plainly rather than
correcting quietly: `draupnir/svalinn/sandbox.py` specifies a rootless
container with no outbound network and read-only artefact mounts, threat T7 is
closed against it, and **nothing applies it**. VLD-INF-SINDRI-001 Rev 3.3
Procedure M6 runs `python /forge/tools/LLaMA-Factory/src/train.py` inside
`/forge/venv` under `slurmd`, as the `nvidia` account, on a shared appliance.

The two models cannot be reconciled by relaxing the profile, because the work
needs the two properties the profile forbids — the job reports to MLflow on
ANDVARI, and it writes checkpoints under `/forge/vault/models/adapters`. So the
shared-venv model is what Release 1 documents as in force, and the container
model is a recorded gap rather than an implied current state.

`draupnir/svalinn/containment.py` holds both, and `containment.shortfall()`
lists the difference: seven properties, of which four — a dedicated account,
`ConstrainRAMSpace`, `ConstrainDevices` and `--export=NONE` — need no container
runtime at all. See `docs/fixes/proposed-procedure-s13.md`.

**Every name is site scoped.** The DNS zone at Sindri is
`sindri.veldris.internal`, which is what REGIN's dnsmasq is authoritative for
and nothing wider (VLD-INF-SINDRI-001 Rev 3.3 section 10.4). So the database is
`andvari.sindri.veldris.internal`, not `andvari.veldris.internal`: a name one
label short of the zone is forwarded to the site router and denied by the
egress policy, so it does not resolve at all.

Nothing in the scripts spells a hostname. `deploy/lib.sh` derives all of them
from the forge, so a second forge needs `--site <name>` and no edits. An estate
whose naming differs sets `DRAUPNIR_SITE_DOMAIN`. If `--check` reports
`does not resolve`, that is this: check the site, not the infrastructure team.

The units are rootless Podman containers under the host's user service manager
(SAD 11.1 step 1, AC-Q7) and hold no state. That manager is launchd on ALVISS,
which VLD-INF-SINDRI-001 Rev 3.3 section 6 makes a Mac mini M4 Pro, and systemd
on a Linux host. The container is the same on both, so everything AC-Q7 asserts
holds either way; `deploy/lib.sh` is the only file that knows the difference. Everything is in PostgreSQL and MinIO
on ANDVARI, which is why the control plane can be destroyed and rebuilt
without losing a run, and why a restart interrupts the console and not the
training.

**Two images, three units.** Stage 3.1 builds `draupnir-api` and
`draupnir-web`. The worker is the API image with a different entry point, so
there is no third image to build or sign. `deploy/lib.sh` holds that mapping
and the deployment scripts share it.

### The estate does not exist yet

SAD 1.3 puts the hardware in VLD-INF-SINDRI-001 and out of scope. A control
plane installed ahead of the estate is a legitimate state:

- `DRAUPNIR_VAULT_ROOT` empty means no vault, and the vault checks are skipped
  rather than alarming hourly about an export that was never there.
- Dispatch suspends with no Slurm controller. Queued runs stay QUEUED and
  nothing is lost (runbook §2).
- Releases refuse to anchor with no federation link, and say so (runbook §7).
- `DRAUPNIR_PROMETHEUS_URL` empty means no collector, and CON-B's thermal and
  fabric panels read `unmeasured` with the reason rather than zero. A zero
  there would render `0 °C` in green on a wall panel nobody stands close
  enough to question, which is worse than a panel admitting it does not know.

Install with `--skip-dependency-check` if ANDVARI is not up yet, and expect
`/readyz` to report the missing dependency rather than the API to be absent.

### Verifying the ledger from the host

The walkthrough above deliberately does not include a chain verification,
because it cannot work as written for the reader: `secrets.env` is passed to
the containers by `podman --env-file`, and a process on the host does not read
it. `tasks.py verify-chain` on ALVISS would fall back to the development
defaults and fail to connect, which to someone following a numbered list looks
like a broken deployment rather than a missing environment.

With the credentials in the environment it is the right check, and `/readyz`
does not replace it: readiness proves the database answers, and this proves
what is in it has not been rewritten.

```bash
DRAUPNIR_DATABASE_URL_SYNC='postgresql+psycopg://…' python tasks.py verify-chain --site sindri
```

100,000 entries verify in about four seconds against the sixty second budget
of AC-N5. A failure here is never routine: it puts the site into read only and
the recovery is not a redeploy. Runbook §6.

### Migrations

Forward only (AC-Q6). Render before applying:

```bash
python tasks.py migrate-dry    # alembic upgrade head --sql
python tasks.py migrate        # alembic upgrade head
```

Stage 4.1 does exactly this, in that order, and keeps the rendered SQL as
deployment evidence for 90 days.

There is no downgrade path, deliberately. Every migration is additive within a
version, so the previous release runs against the newer schema — which is what
makes a rollback of the units safe without a rollback of the schema. A schema
fault is recovered by a restore and a new forward migration, not by a
downgrade nobody has exercised.

### Releasing a revision

A merge to `main` runs the pipeline, and a green pipeline triggers stage 4:

```
4.1  migrate      render, then apply
4.2  rollout      pull the image per unit, restart the units
4.3  smoke        healthz, readyz, ledger chain verification
4.4  rollback     on smoke failure only
```

By hand, on ALVISS:

```bash
./deploy/rollout.sh <sha>
python tasks.py smoke --base-url http://127.0.0.1:8000
```

The rollout deliberately does not wait for the application to be healthy. That
is the smoke stage's job, and conflating the two hides which of the deployment
and the application failed.

### Rolling back

```bash
./deploy/rollback.sh <previous-sha>
```

The units return to the previous revision; the schema stays forward. Confirm
with `python tasks.py smoke --skip-ledger`, which probes HTTP without
asserting the chain — appropriate when you have just changed what is running
underneath it.

### After a reboot

`rollout.sh` publishes a revision by setting `DRAUPNIR_IMAGE_<unit>` in the
user manager's environment, and that environment does not survive a reboot.
The wrapper records every reference it starts and falls back to the recorded
one, so a host that comes back from a power cut comes back on the revision it
was running. If both are absent the unit refuses to start and names the
command that fixes it, rather than starting something arbitrary.

### What is not automated

- **Artefact signing needs a key.** Stage 3.4 is a hard failure on `main`
  without `DRAUPNIR_SIGNING_KEY`, deliberately: signing is required there
  (Decision S9) and reporting the absence beats skipping the stage.
- **The deploy environment needs configuring.** `deploy.yaml` targets the
  `sindri` GitHub environment and reads `DRAUPNIR_DATABASE_URL_SYNC` from it.
- **GULLINBURSTI needs a site certificate** from the MEGINGJORD internal PKI
  (SAD 11.1 step 6).
- **The estate is out of scope.** VLD-INF-SINDRI-001 builds the appliances,
  the fabric, the vault export and the Slurm controller.

### See also

| Document | For |
|---|---|
| [runbook.md](runbook.md) | A running system misbehaving. Nine failure modes |
| [CONTRIBUTING.md](CONTRIBUTING.md) | A development machine |
| [deploy/README.md](../deploy/README.md) | The scripts themselves |
| `docs/build/draupnir-sad.md` | §11.1 deployment, §11.2 degraded modes, §5.1 deployable units |
