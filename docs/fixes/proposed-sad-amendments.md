# Change proposal: VLD-SAD-DRAUPNIR-001 amendments

**For** VLD-SAD-DRAUPNIR-001 Rev 1.4 → Rev 1.5
**Raised by** [remedial-fixes-estate.md](remedial-fixes-estate.md)
**Status** Proposed. Not applied — the SAD is a signed architecture document
and amending it bumps a revision, which is the owner's to do.

Companion to [proposed-procedure-s13.md](proposed-procedure-s13.md), which
carries the amendments to VLD-INF-SINDRI-001. This file collects the ones that
belong to DRAUPNIR's own architecture instead.

---

## Amendment 1 — a resolved decision: `export` is a venue, not a partition

**Raised by RF-E08. Implemented in the code; recorded here for section 15.**

### What was wrong

`draupnir/motsognir/placement.py` declared three Slurm partitions:

```python
ADAPTERS = "adapters"
RING = "ring"
EXPORT = "export"  # "Evaluation, merge and quantisation on ALVISS"
```

VLD-INF-SINDRI-001 Rev 3.3 section 34 declares two, both over
`dvalin,durin,dain`:

```
PartitionName=adapters Nodes=dvalin,durin,dain Default=YES MaxTime=48:00:00
PartitionName=ring     Nodes=dvalin,durin,dain MaxTime=336:00:00 OverSubscribe=EXCLUSIVE
```

There is no `export` partition, and ALVISS is not a Slurm node — it is a Mac
mini running macOS with no `slurmd`. `sbatch --partition=export` is answered
with `Invalid partition name specified`.

The SAD does not name an `export` partition anywhere; §6.2's worked example
uses `adapters` and AC-F4 names `ring`. So this was DRAUPNIR's own invention,
and the comment beside it named the contradiction without anybody noticing:
*"Evaluation, merge and quantisation **on ALVISS**"* — a partition of a cluster
ALVISS is not part of.

### The decision

Two options were weighed.

**A. Make `export` real** — install `slurmd` on ALVISS and declare
`PartitionName=export Nodes=alviss`. Rejected: Slurm on macOS is not a
supported build, and it would be the only such component on the estate. It also
puts the control plane on the cluster, which SAD Decision S3 deliberately
avoided for the appliances and which is no better here.

**B. Make `export` a venue.** Adopted. Export-class work runs on the control
plane, which is what VLD-INF-SINDRI-001 already does by hand: Procedure M7
merges on ALVISS, M9 quantises there, and §32 runs the MLX evaluation there.
So `export` names a *venue* rather than a Slurm partition, and a placement in
it resolves to a local scheduler driver rather than to the cluster.

### Proposed text, for section 15

| Decision | Statement | Rationale |
|---|---|---|
| **S15** | A partition names a venue as well as a queue. `adapters` and `ring` are Slurm partitions on the appliances; `export` is the control plane and is never sent to a scheduler. | Merge, quantisation and MLX evaluation run on ALVISS, which is not a Slurm node. A partition name is not only a string to submit: it decides *which* scheduler. Holding that as one table keeps a placement from being sent somewhere that cannot accept it. |

### Two consequences worth stating with it

**Export work survives the estate.** An export placement has no appliances to
lose, so merge and quantisation keep working with every appliance switched
off — which is exactly when an operator wants to finish packaging a release.
Before this, an export placement went through the appliance-availability path
and `NoCapacityError` would have refused it.

**The local subprocess driver becomes a production component.** It was
described as "a local runner for development" (SAD 8.2). It is now the driver
an export placement resolves to, and 8.2's table should say so:

| Entry point group | Interface | Example implementations |
|---|---|---|
| `draupnir.schedule` | `ScheduleDriver` | Slurm (`sbatch`, or `slurmrestd` where the host has no client tools), Ray, local subprocess for development **and for control-plane work** |

### Also for 8.2: two Slurm drivers, not one

Raised by RF-E05 and worth a line in the same table. `motsognir.slurm/v1`
shells out to `sbatch`; `motsognir.slurmrest/v1` submits over `slurmrestd`.
They are alternative installations of one interface — a host with the Slurm
client tools installs the first, a host without them installs the second — and
at Sindri the control plane is the second case: ALVISS has no client tools and
the container has no shell to run them from.

---

## Amendment 2 — the estate's time limits are the estate's

**Raised by RF-E08. Implemented; recorded because it is a constraint the SAD
does not currently mention.**

`MaxTime` is 48 hours on `adapters` and 336 on `ring`
(VLD-INF-SINDRI-001 §34). DRAUPNIR knew neither, so a specification asking for
more was registered, queued, and rejected by Slurm afterwards — the refusal
arriving after the run existed in the chain.

Placement now refuses it, naming the limit and saying whose it is. Proposed
addition to §6.2, after the run specification:

> A run's time limit is bounded by its partition's `MaxTime`, which is the
> estate's rather than the specification's. A run asking for more is refused at
> planning, before it is recorded: the alternative is a run in the chain that
> the scheduler will never accept.

---

## Amendment 3 — the accelerator is the estate's, not the specification's

**Raised by RF-E09. Implemented; recorded because it settles where a fact
lives.**

The two Slurm drivers asked for a GPU differently — `--gpus-per-node=1` from
the sbatch shim, a typed `tres_per_node` from the `slurmrestd` one — so the
same run asked for different things depending on which host submitted it.
VLD-INF-SINDRI-001 §34 declares `Name=gpu Type=gb10` and every batch script in
Part 5 asks for `gpu:gb10:1`; an untyped count against a typed GRES resolves on
some Slurm configurations and not others, and that cluster has never been run
with one.

The question underneath is where the accelerator type belongs, and there were
three candidates.

**Not the run specification.** SAD §6.2 makes the specification the unit of
reproduction, and the Forge Matrix means it has to be portable: a specification
naming `gb10` would not run at a forge with different hardware, which is the
opposite of what portability is for.

**Not the driver.** A driver renders and submits; what machines the estate has
is not its to know, and putting it there would have given two drivers two
copies of one fact — which is how they came to disagree in the first place.

**MOTSOGNIR, which owns placement.** Adopted. `Appliance.accelerator` is a
property of a machine, `Estate.gres()` is the string the estate offers, and a
`Placement` carries it into the plan the driver submits. Proposed addition to
§5.2's MOTSOGNIR row:

> Also owns what the estate's machines *are*, to the extent a scheduler needs
> telling: the generic resource a job asks for is the estate's fact, carried on
> the placement, and not something a specification or a driver decides.

**A mixed estate is refused rather than guessed at.** Where the appliances do
not agree on an accelerator — or where one declares none — `Estate.gres()`
returns nothing rather than picking. Inventing an answer would submit a job
asking for hardware half the appliances do not have. An estate like that needs
a partition per accelerator, which is a configuration decision.

**And it is a setting, not a constant.** `placement.SINDRI` names the machines
and their ranks, which are the estate; the accelerator type comes from
`DRAUPNIR_ACCELERATOR`, which `install.sh` writes. A second forge is then a
variable rather than a patch.

---

## Amendment 4 — which executor model is in force, and what closes T7 today

**Raised by RF-E16.** §9.x describes a container-per-job sandbox and threat T7
is mitigated against it. Nothing applies it, and the estate cannot honour it.

### What was wrong

The mitigation for T7 reads "executing it in a rootless container with no
outbound network and a read only artefact mount", and T11 adds that "executors
run with no outbound network at all". `draupnir/svalinn/sandbox.py` implements
exactly that, completely, with tests. It is called from nowhere.

VLD-INF-SINDRI-001 Rev 3.3 Procedure M6 runs
`python /forge/tools/LLaMA-Factory/src/train.py` inside `/forge/venv` under
`slurmd`, as the `nvidia` account, on a shared appliance. The work needs the
two properties the profile forbids: it reports to MLflow on ANDVARI, and it
writes checkpoints under `/forge/vault/models/adapters`.

This is the failure mode worth naming, because it is not a bug and nothing was
skipped. The design is right, the module is complete, the tests pass, and an
auditor reading the threat model finds T7 closed. What is absent is the estate
that would apply it — and a control described as in force is one nobody
implements, precisely because it reads as already done.

### The decision

**The shared-venv model is in force for Release 1.** The container model is a
specification with a recorded gap, not an implied current state.

### Proposed text, for §9.x

> **Executor containment.** At a forge running the Sindri reference build,
> training executes as a process in a shared virtual environment under Slurm,
> as the site's job account. The containment in force is process tracking
> (`proctrack/cgroup`, so a job's processes are killed with it), resource and
> device constraint via `cgroup.conf`, a job account distinct from the
> administrator's, and `--export=NONE` so a job inherits no environment. The
> job reaches the site fabrics, because it reports to the experiment tracker
> and reads corpora over NFS.
>
> The rootless container profile in `svalinn.sandbox` is the specification for
> a container-per-job estate. It is not applied at a forge built to this
> reference, and `svalinn.containment.shortfall()` enumerates the difference.

### What T7 and T11 should say instead

T7's mitigation cannot claim a container. Proposed:

> **T7** — a malicious or compromised plug-in. Mitigated by signature
> verification at load (`svalinn.pki`), a job account with no administrative
> authority, device and memory constraint, and an environment the job does not
> inherit. **Residual:** the job shares a filesystem and a network with the
> appliance's other work. A container-per-job estate would close this and is
> gap-listed.

T11's "executors run with no outbound network at all" is not true at this
estate and should say so:

> **T11** — unapproved egress. Mitigated for the control plane by the egress
> broker, which decides and records every outbound call. **Residual:** the
> executor is not brokered; a training job reaches the site fabrics, because
> the work requires it. Gap-listed with T7.

### Why this is better than leaving it

A residual risk that is written down gets an owner, a review date and a
compensating control. A residual risk recorded as mitigated gets none of those,
and is discovered by whoever is investigating something else.

Four of the seven differences close with about fifteen lines of Slurm
configuration and cost nothing architecturally; see amendment 2f of the
procedure proposal. The remaining three need the container model.

---

## Amendment 5 — the federation link terminates on REGIN, not on the software's host

**Raised by RF-E21.** §11A describes GULLINBURSTI reaching MEGINGJORD and does
not say where the link is. The two documents happen to disagree already:
`wireguard-tools` is installed on REGIN by Procedure S11 step 1, and
GULLINBURSTI runs inside DRAUPNIR on ALVISS. So the software that needs the
link and the machine that has the tooling are different hosts, and nothing
records that as a decision.

### The decision: REGIN

Three reasons, in the order they matter.

**FileVault.** Procedure S10 ends with `sudo fdesetup enable` on ALVISS, so it
boots to an unlock screen and nothing runs until somebody types a password at
the console (RF-E22). A federation link terminating there is a link that is
down after every power cut until a site visit. REGIN has no such gate and
returns on its own.

**REGIN is already on both fabrics** — 10.10.0.5 on management and 10.20.0.5 on
Fabric 2 — so it can route for the whole forge. A tunnel on ALVISS serves one
host, and the next thing at Sindri that needs the federation link would need a
second one.

**The tooling is already there.** Procedure S11 installs `wireguard-tools` on
REGIN. Adding it to Procedure S10 as well, for a Mac, means `wireguard-go`
rather than the kernel module and a launchd agent to bring it up — more moving
parts on the host that is hardest to bring back.

### What this means for the control plane: almost nothing

Worth stating because it looks like it should mean more. DRAUPNIR still calls
`https://megingjord.veldris.internal/…`; the egress broker still decides that
call against the same allow-list entry, under the same approving policy. A
route is not a destination, and the broker has no opinion about hops.

Three things have to exist, none of them in this repository:

1. **A `dnsmasq` record on REGIN.** `megingjord.veldris.internal` is outside
   the `sindri.veldris.internal` zone REGIN is authoritative for, so it is
   forwarded upstream and fails. It needs an explicit
   `address=/megingjord.veldris.internal/<tunnel address>`.
2. **A route on ALVISS** to the MEGINGJORD subnet via 10.20.0.5.
3. **An egress rule for the tunnel itself.** §10.5's allow list is a list of
   *hostnames* — implicitly outbound web traffic. WireGuard is UDP to a
   specific endpoint on port 51820, and nothing in §10.5 permits it. This is
   the same family as the nine hosts of RF-E17 and is listed with them.

### Proposed text, for §11A

> **Where the link is.** The federation link between a forge and MEGINGJORD
> terminates on the forge's infrastructure host, not on the host running
> GULLINBURSTI. At Sindri that is REGIN. GULLINBURSTI reaches MEGINGJORD as a
> routed destination and the egress broker decides the call by destination, so
> the termination point is an estate decision rather than an application one.
>
> The forge's DNS must resolve the registry's name explicitly: it is outside
> the zone the forge is authoritative for, and forwarding it upstream fails.

---

## §11.3 — the observability table has no row for the control plane itself

**Raised by** RF-18
**Severity** minor, and a gap rather than an error

§11.3 gives eight signals a source and a surface. Every one of them is about
the *estate* or about the *work*: appliance temperature, fabric bandwidth,
vault capacity, chain integrity, anchor freshness, run state, gate outcomes,
mains and battery. None of them is about the control plane.

So an operator asking "is the API slow, and which route" has no signal to read.
That question has an acceptance criterion behind it — AC-N4 requires a 500-run
list to answer in under 300 ms at the 95th percentile — and until RF-18 there
was no way to observe it outside a test. It is also the first question anybody
asks when a console feels slow, and the answer decides whether to look at
PostgreSQL, at the appliances, or at neither.

RF-18 adds `draupnir_http_request_duration_seconds`, a histogram labelled by
method, route *template* and status. The template matters and is worth saying
in the document rather than only in the code: labelling by path would put a run
identifier in a label, which is an unbounded series set and a list of this
forge's work published on an endpoint served without a credential.

This is per API process rather than per site, unlike every other row: it
describes the work that process did, so it aggregates with `sum by` where the
site-wide gauges aggregate with `max by`. That distinction has caught people
out on every Prometheus deployment ever built and the table is the place to
record it.

### Proposed text, as a ninth row of §11.3

| Signal | Source | Surfaced on |
|---|---|---|
| Control plane request latency and status | Core, per API process | CON-B dashboard 3, alarming above AC-N4's 300 ms at the 95th percentile |

### And a note under the table

> Rows describing the estate or the work are site-wide: every API process
> reports the same value, so aggregate them with `max by (site)`. The request
> latency row is per process and aggregates with `sum by`. No metric carries a
> label naming an actor, a run or an artefact: two of those are unbounded and
> all three would publish what is being built on an endpoint that carries no
> credential (§8.1).

---

## Amendment 6 — §9.4: two actions the role table gives to nobody

**Raised by RF-27. Implemented; recorded because §9.4 is the whole of what each
role may do, and it now says less than the code enforces.**

### What was wrong

Two primary actions of VLD-UX-DRAUPNIR-001 §8 had no operation, and when they
were built each needed a permission §9.4 does not name. SAD 7.3 makes a raw
corpus deletion "an approved and ledgered retention action" and no row of §9.4
says who approves one. S15 makes choosing a merge point an operator's action and
§9.4's operator row reads "Submit, cancel, retry runs".

### What the code now does

`svalinn.roles` grants `APPROVE_RETENTION` to `approver` and adds it to the
actions that require a hardware authenticator (AC-S15), because it is the one
decision in the system that cannot be undone: a release can be withdrawn, and a
deleted corpus cannot be re-read. It grants `SELECT_MERGE_POINT` to `operator`,
because BRISINGAMEN runs the sweep, RAUN decides which points are acceptable,
and choosing among those is not a release decision.

Decision S6 is unaffected. No role both submits a run and approves its release,
and neither grant brings one closer.

### Proposed text for §9.4

| Role | Permissions |
|---|---|
| `operator` | Submit, cancel, retry runs; choose a merge point among those that passed. Cannot approve or publish |
| `approver` | Decide gates, publish releases and approve retention deletions. Cannot alter run specifications |

### And one sentence under the table

> Approving a retention deletion requires hardware-backed multi-factor
> authentication, as publishing and deciding a gate do.

---

## Amendment 7 — §7.1 and §7.3: retention is recorded in the chain, not in `retention_action`

**Raised by RF-27. Implemented as a deviation, recorded so the table and the
code stop disagreeing silently.**

### What was wrong

§7.1 lists `retention_action` as the entity a deletion is recorded in. Nothing
wrote it. The daily duty recorded its proposals as ledger entries, S06 read the
empty table, and `hodd.retention.execute` was called by unit tests alone. The
table also cannot hold what it is for: it has no site, and it keys the subject by
UUID where a corpus is identified by its digest.

### What the code now does

A retention action is folded from its entries against the corpus subject —
`retention.due`, `retention.approved`, `retention.executed`,
`retention.refused` — by `hodd.retention.fold`, the way RF-13 folds the array.
The duty proposes; an approver approves (Amendment 6); the duty carries the
approval out, asking the store whether the curated manifest is held, and
refuses a deletion that would leave none, naming the releases (AC-F20).
`retention_action` is unwritten.

### Proposed text for §7.1

> `retention_action` — superseded. A retention action is the fold of its ledger
> entries against the corpus: proposed, approved, executed or refused. A table
> beside the chain would be a second record of a deletion, and the two would
> disagree the first time either was rebuilt.

And §7.3's last sentence gains a clause:

> A retention action that would break a lineage chain is refused, **and the
> refusal is recorded, naming the releases it would have orphaned.**

---

## Amendment 8 — §8.1: the table lists thirteen groups, and the console calls more

**Raised by RF-27.**

§8.1 is not closed and the SAD never says it is, but it is the table a reader
checks against, and the operations RF-13 and RF-27 added are absent from it:

| Method and path | Purpose | Role |
|---|---|---|
| `POST /v1/arrays/{name}/elements/{index}/requeue` | Requeue one array element that stopped without completing | operator |
| `POST /v1/retention/{action_id}/approve` | Approve a retention deletion, conditional on its state | approver |
| `POST /v1/sweeps/{run_id}/select` | Choose the merge point a run is quantised from | operator |
| `GET /v1/releases/{artefact}/documents/{document}` | Download one document of a release package | viewer |

The reads the console already used and the table never listed — retention,
sites, policy, roles, sweeps, the array — belong beside them.

### And a conflict worth resolving rather than listing

§7.2 places RBAC in PostgreSQL; §11A.1 makes MEGINGJORD "the OIDC issuer and
the RBAC source of truth"; §9.4 gives `admin` "Manage users, plug-ins and
policy". The code follows §11A.1: roles arrive in the token, `MANAGE_USERS`,
`MANAGE_PLUGINS` and `MANAGE_POLICY` are granted to admin and required by no
route, and the UX proposal states S22 to S25 read only at the forge. §7.2 and
§9.4 should say where each of those is managed, and it is not the forge.

---

## Amendment 9 — §6.1: MERGED waits for a choice of merge point

**Raised by RF-27. Implemented.**

### What was wrong

Prompt 5 asks for "the selected point recorded in the model card", and AC-F8 for
a sweep of at least five points "comparable side by side in the console". The
worker built a five point sweep, merged once, and recorded how many points the
sweep had; `getSweep` served five points invented by scaling one run's gate
values and called the first that passed selected.

### What the code now does

In MERGED the worker merges and re-gates every point, recording the evaluated
sweep against a `sweep` subject whose identifier is the run — not a `run`
entry, which the projector would refuse. The run then waits. An operator
chooses a point that cleared every blocking gate (Amendment 6); the worker
quantises that point's verified bytes and records, on MERGED → QUANTISED, the
chosen point's configuration hash and the whole comparison it was chosen from.
Procedure M7 stands in for the operator, and records that it did.

### Proposed note under §6.1's MERGED → QUANTISED row

> The run rests in MERGED from the moment its sweep is evaluated until an
> operator chooses a merge point. The choice is recorded in the chain, and the
> merge configuration hash is the chosen point's.

---

## Pending — raised and not yet drafted

These are identified in the estate register and need SAD amendments when the
corresponding work lands. Listed so the revision can collect them in one pass
rather than several.

| Raised by | What needs saying |
|---|---|
| RF-E21 | §11A assumes a three-appliance ring. The ring is now declared by membership rather than counted, so §7.4's two-node recovery configuration can be represented; §11A should say the ring is a forge property rather than a constant. |
| RF-30 | §9.5's "TLS 1.3 only, mTLS between control plane components" is NOT BUILT, and the reconciliation marks §9.1–9.5 IMPLEMENTED. |
| RF-E15 | §11.3's thermal and throttle rows name the DCGM exporter as source and Grafana as surface. The control plane now reads them too, and CON-B renders them; the surface column should say both. The wire is built — this is a one-line correction, not a design change. |
| RF-27 | The release record (`release`) is written by the seed and by nothing in the publication path, so on an estate a published artefact has no release package to read or download. §9A.2 and §10.2 describe the package; neither says what writes it. |
| RF-27 | A release does not record the licence policy version its sources were judged under, so its copyright policy can only be rendered under the version in force now. §10.2 asks for the version in force at the release date, which needs the release to record it. |
