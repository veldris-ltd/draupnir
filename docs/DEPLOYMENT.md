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

Open your terminal and connect to ALVISS. Replace `alviss.veldris.internal`
if you were given a different address, and `SERVICE-ACCOUNT` with the username
you were given:

```bash
ssh SERVICE-ACCOUNT@alviss.veldris.internal
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

**What you should see** — the word `preflight`, some lines about podman and
lingering, then two lines saying the database and file storage are answering,
and finally `check complete`:

```
==> preflight
    podman: podman version 5.0.0
    lingering: enabled

==> dependencies on ANDVARI
    PostgreSQL at andvari.veldris.internal:5432: answering
    MinIO at andvari.veldris.internal:9000: answering

==> check complete
    nothing was changed.
```

Now compare what you saw against this table:

| If you see | It means | Do this |
|---|---|---|
| `check complete` | Everything is ready | Continue to Part 4 |
| `podman not found` | A required program is missing | **Stop.** Ask the engineer to install podman |
| `run this as the service account, not as root` | Wrong user | **Stop.** Go back to Part 1 |
| `no user systemd instance` | The account is not set up to run software | **Stop.** Ask the engineer |
| `PostgreSQL … no answer` | The database machine is not reachable | **Stop.** Ask whoever runs the infrastructure |
| `MinIO … no answer` | The file storage is not reachable | **Stop.** Same as above |
| `warning: lingering is off` | The software would stop when you log out | Run the command in the note below, then run `--check` again |

> **If lingering is off**, run this once, then run the `--check` command again
> and confirm the warning is gone:
>
> ```bash
> loginctl enable-linger "$(id -un)"
> ```

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
    wrote ~/.config/systemd/user/draupnir-api.service
    wrote ~/.config/systemd/user/draupnir-worker.service
    wrote ~/.config/systemd/user/draupnir-web.service

==> revision
    draupnir-api <- registry.veldris.internal/draupnir-api:…
    draupnir-worker <- registry.veldris.internal/draupnir-api:…
    draupnir-web <- registry.veldris.internal/draupnir-web:…

==> enable and start

==> verify
    draupnir-api.service: active
    draupnir-worker.service: active
    draupnir-web.service: active
```

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
DRAUPNIR_DATABASE_URL=postgresql+asyncpg://draupnir:PASSWORD@andvari.veldris.internal:5432/draupnir
DRAUPNIR_DATABASE_URL_SYNC=postgresql+psycopg://draupnir:PASSWORD@andvari.veldris.internal:5432/draupnir
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

Restart the three programs so they pick up the credentials:

```bash
systemctl --user restart draupnir-api draupnir-worker draupnir-web
```

**What you should see:** nothing at all. No output means it worked.

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

```bash
systemctl --user status draupnir-api --no-pager
journalctl --user -u draupnir-api -n 50 --no-pager
```

Copy everything both commands print. Send it with your message. It is far more
useful than a description of what happened.

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
| **ANDVARI** | PostgreSQL 16, MinIO, the HODD vault, the image registry | VLD-INF-SINDRI-001, not here |
| **REGIN** | Slurm controller, Loki | VLD-INF-SINDRI-001, not here |
| **DVALIN, DURIN, DAIN** | Executor shims, one container per job | Slurm, per job |
| **Veldris_NXT** | MEGINGJORD federation registry | Its own deployment |

Sindri is a forge — a site — rather than a host. What deploys at Sindri is the
control plane, and SAD Decision S3 puts that on ALVISS rather than on an
appliance, because the appliances are the scarce resource and a control plane
on one is a control plane an out-of-memory kill takes with it.

The units are rootless Podman containers under the user systemd instance (SAD
11.1 step 1, AC-Q7) and hold no state. Everything is in PostgreSQL and MinIO
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
