# Contributing to DRAUPNIR

DRAUPNIR is the CIM-56 model factory control plane. The architecture it
implements is VLD-SAD-DRAUPNIR-001 (`docs/build/draupnir-sad.md`); the console
and design system are VLD-UX-DRAUPNIR-001 (`docs/build/draupnir-ux.md`). When
this document and the SAD disagree, the SAD is right and this document is a
bug.

---

## 1. One command

```bash
make dev
```

That is the whole of it. On a clean machine it installs the toolchain, starts
PostgreSQL and MinIO, applies the migrations, seeds a realistic dataset, and
leaves the API and the console running.

| | |
|---|---|
| API | <http://127.0.0.1:8000> |
| API docs | <http://127.0.0.1:8000/docs> |
| Console | <http://127.0.0.1:5173> |
| MinIO console | <http://127.0.0.1:9001> |

On Windows, where `make` is not present:

```powershell
.\make.ps1 dev
```

Both are thin wrappers around `tasks.py`, which holds the single
implementation of every task. `make`, `make.ps1` and the pipeline therefore run
identical commands, and a task that works locally works on the runner.

Run `make` with no target, or `python tasks.py --list`, for the full list.

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12 or later | Only to bootstrap. `uv` installs and pins 3.12 itself |
| Node | 20 or later | |
| pnpm | 9.12 | Pinned in `web/package.json` under `packageManager` |
| Docker | any recent | PostgreSQL, MinIO, the integration tests and the image build |
| uv | 0.5 or later | Optional. `make dev` installs a project-local copy into `.uv-bootstrap/` if it is missing |

The recommended way to install `uv` properly is the official installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

pnpm does not have to be on `PATH`. The task runner uses one if it finds one,
falls back to `corepack`, and falls back again to `npm exec` at the pinned
version. All three routes end at the version in `packageManager`, which is the
single source of truth for it.

The fallbacks earn their place. A globally installed pnpm lives in the npm
global prefix, which a Python installed from the Microsoft Store cannot see at
all — its file APIs are redirected by the app container, so the task runner
finds nothing while `pnpm` works perfectly in the same terminal. Corepack
covers that case, but Node 25 removed corepack from the distribution, leaving
npm as the floor.

### The seeded dataset

`make seed` writes a deterministic dataset: 2 sites, 6 sources, 12 runs
covering every run state, 3 releases and 400 hash-chained ledger entries. It is
reproducible, so two developers see identical identifiers and a screenshot in a
bug report means something.

To start over:

```bash
make reset-db && make seed
```

`reset-db` drops and recreates the schema. It has to: the `ledger_entry` table
refuses `DELETE` and `TRUNCATE` by design, so there is no gentler way to clear
it, and that is the correct behaviour rather than an inconvenience.

---

## 2. The rules the build enforces

Three things are enforced mechanically rather than by review. Each has a reason
recorded in the SAD.

### Dependencies point inward (SAD 11B)

```
draupnir/api                edge          knows HTTP, no domain logic
draupnir/<module>           modules       HODD, GLEIPNIR, MOTSOGNIR, ...
draupnir/core
    infrastructure          knows the technology, substitutable
    application             knows the workflow
    domain                  knows the invariants, no framework imports
draupnir/interfaces         ports         knows interfaces, never implementations
```

`.importlinter` holds the contracts and `make imports` runs them. A domain
module that imports `fastapi`, or a core module that names a driver, fails the
build. The contracts were written before the first feature module so the rule
is enforced from the first import rather than asserted afterwards.

### The database enforces its own invariants (SAD 11C)

| Constraint | Enforcement |
|---|---|
| `ledger_entry` accepts INSERT only | Trigger rejecting UPDATE, DELETE and TRUNCATE |
| `release` requires an `approval_id` | Foreign key, NOT NULL |
| Site scope on every scoped query | Row level security plus a session variable |

A constraint that lives only in the application is a constraint a migration
script can bypass. `tests/integration/test_schema_constraints.py` attacks each
one with raw SQL.

The development database connects as an unprivileged role on purpose. A
PostgreSQL superuser bypasses every row level security policy, so seeding as
one would make site isolation pass locally and fail in production.

### The ledger is the source of truth, and `run` is derived (SAD 11B)

`ledger_entry` is the only authoritative table. The run registry is a
projection of it, produced by `draupnir/core/domain/projector.py`, and
`RunProjection.rebuild()` replays the chain from sequence 1 to reproduce it
byte for byte.

That has three consequences worth knowing before you write a query.

- **Never write to `run` by hand.** A rebuild discards anything the chain does
  not account for, so an edit survives exactly until the next rebuild and then
  vanishes without trace. The table carries a comment in the database saying
  so, for the benefit of whoever finds it through `psql`.
- **A state change is one transaction.** Guard, append the entry, project.
  `draupnir.core.domain.states.apply` performs the first half: it refuses a
  transition its guard rejects, and refuses a ledger payload that omits a field
  SAD 6.1 says the entry must record.
- **The projector is pure.** It takes entries and returns rows, reading nothing
  and calling nothing. That is what makes a rebuild reproducible on another
  machine years later, and it is enforced: `draupnir.core.domain` may not
  import a framework.

To rebuild by hand:

```bash
make reset-db && make seed
```

The seed itself writes chains rather than rows, so it exercises the same path
a live transition takes.

### A driver is an installation, never a core change (SAD 10.2)

The seven interfaces of SAD 8.2 are Protocols in `draupnir/interfaces/`, and a
driver is a separate distribution that registers an entry point. Nothing in
DRAUPNIR names a driver, so adding one changes no file here:

```toml
[project.entry-points."draupnir.export"]
"skidbladnir.targz/v1" = "draupnir_targz_export:driver"
```

The entry point *name* is the versioned interface name of SAD 10.3, and four
rules follow from it. The core takes the current and immediately previous
major version and refuses anything else, naming both what it found and what it
expected. A specification resolves the version it recorded, and never a newer
one. A driver declares its capabilities as a frozenset, and the core refuses to
plan a job needing one it has not declared -- before an allocation is consumed,
because scheduler time is the scarce resource here.

Some groups are chosen by name and some by capability. A specification names
its train and schedule drivers; it asks the `release` block for *formats* and
never for an exporter, so the core finds a driver declaring them. That is what
makes a new export format an installation: no table in the core lists which
driver produces what.

Two reference drivers live in `plugins/` and are installed for development:
`local_subprocess` (a `ScheduleDriver`) and `targz_export` (an `ExportDriver`,
and the AC-N9 demonstration at 75 lines against a 200 line budget).

`.importlinter` forbids a driver from importing `draupnir.core` at all, so
"no core file modified" is a rule rather than an observation.

### HODD records, GLEIPNIR judges (SAD Decision S4)

The licence register holds facts: an SPDX identifier as a string, an
attribution flag, a personal data determination. It holds no verdict, no rule
and no allow list, because a verdict stored there is a stale verdict stored
forever.

That separation is what makes a policy change cheap. Re-evaluating every
source under a new licence policy reads the recorded facts and writes
decisions; no corpus is re-ingested and no hash recomputed, because nothing
about the source changed -- only the question being asked of it.

The two modules cannot import each other: the module layering makes them
independent siblings, and `tests/unit/test_hodd_records_and_does_not_judge.py`
additionally fails the build if an SPDX identifier appears anywhere in `hodd/`
outside a docstring. An allow list is the shape licence logic actually arrives
in, and one constant is all it takes.

The whole interface between them is two functions: `LicenceRegister.
facts_for_policy()` renders records as mappings, and `PolicyEngine.reassess()`
consumes mappings. Anything that needs more than that is in the wrong module.

### Ingest is atomic, and artefacts are sealed

    stage -> hash -> manifest -> publish -> seal -> register

Everything before the publish happens under a name no `hodd://` URI resolves
to, so a crash leaves rubbish in a staging directory and nothing else. Publish
is one directory rename. Registration is last, so a failure anywhere leaves
the register exactly as it was.

Sealing is a filesystem permission change rather than a database flag, because
the thing that must fail is a write by a curation script that never consulted
the database.

One window cannot be closed: a crash between the rename and the registration
leaves a sealed artefact nothing names. `Ingestor.orphans()` finds those, and
reports rather than deletes them -- an artefact HODD sealed is exactly the sort
of thing a process should not remove on its own initiative.

### A ring run refuses rather than degrades (SAD Decision S8)

Two partitions, and they behave differently when the estate is short of a
machine.

`adapters` runs independent single-node jobs, so losing an appliance costs
throughput and nothing else. Concurrency follows the appliances that are up,
and the array runs slower.

`ring` runs one job across all three appliances over the BAUGR ring, ranks 0 to
2. Losing an appliance there does not make the job slower, it makes it a
different job: two nodes of a three node specification produces a model that is
not the model the specification describes, and it would be discovered at
evaluation, after days of compute. So `plan` raises `DegradedRingError` instead.

`ALL_OR_NOTHING` is the set of partitions this applies to. Adding one is a line
there, not a branch somewhere else.

### The array throttle is a throttle, not a count

    --array=0-55%3

All fifty six elements exist from the moment of submission and Slurm runs three
of them. The alternative -- submitting three and topping up as they finish --
would make the estate's utilisation depend on the control plane being awake,
which SAD 11.2 does not assume.

A failed element is retried as `--array=<index>` on a fresh submission, never
by resubmitting the array. Resubmitting restarts every element, discarding the
compute of the fifty five that succeeded, which for CIM-56 is most of a week.
`ArrayPlan.with_element` replaces one element and leaves the others as the
objects they were, which is what makes that testable rather than asserted.

An element that failed within its retry budget is `AWAITING_RETRY`, not
`FAILED`. An operator reading fifty six rows needs FAILED to mean "will not be
tried again" more than they need the scheduler's vocabulary.

### The checkpoint interval is derived, not authored

No more than thirty minutes of work may ever be unwritten. An interval fixed in
a specification cannot deliver that, because it is a guess about hardware: too
high and a node failure eighteen hours in loses eighteen hours; too low and a
27B run spends its allocation writing optimiser state.

So `hamarr/checkpoints.py` derives the largest `save_steps` that keeps exposure
inside the budget, from the step time. At submission the step time is assumed
and the policy says so (`provisional`); after fifty steps the measurement
replaces the guess and the driver's configuration is rewritten. Fifty, because
earlier steps carry compilation, allocator warm-up and the first optimiser
allocation, and are not representative of the next eighteen hours.

A specification may still name `save_steps`. It is checked against the same
budget at submission rather than trusted.

### Progress is structure, and the regular expressions live in the driver

`TrainDriver.parse_progress` turns one line of executor output into a
`ProgressEvent`. `hamarr/progress.py` folds those into a `Progress` record of
numbers, and the API serves that. Nothing downstream ever sees a line of
executor output, so no component can come to depend on the wording of a log
message and an upgrade that changes LLaMA-Factory's log format is a plug-in
version bump.

`Progress` is derived, never stored: fold the events again and you get the same
record, the same property the run projection has.

### Tier drives base selection, and an unknown base raises

Tier A -- GBR, CYP, MLT, IND, CAN, AUS, NGA, ZAF, SGP -- trains on the 27B
dense base; Tier B, the remaining forty seven, on the 35B-A3B MoE base.
`tiers.validate()` runs at submission and refuses unless the two lists
enumerate all fifty six with no duplicate and no omission (AC-F16).
`tiers.tier_of` raises for a jurisdiction nobody assigned, because a default
would silently train a fifty seventh model.

The chat template is resolved from a versioned map in
`plugins/hamarr_llamafactory/`, and an unknown base model raises. There is no
default and there must not be one: applying the wrong chat template does not
crash, it trains a model against a conversation format nobody will send it. The
loss curve looks ordinary and the damage surfaces at evaluation, days later,
looking like a data problem.

Two things the source documents left open, both now settled and both recorded
in the code rather than in anyone's memory.

The SAD does not enumerate the fifty six. `draupnir/hamarr/tiers.py` derives
them from Commonwealth of Nations membership and records the derivation twice:
as the tier split, 9 and 47, and region by region -- Africa 21, Asia 8,
Caribbean and Americas 13, Europe 3, Pacific 11. The regional grouping is what
makes the list auditable. A missing jurisdiction is invisible in a flat list of
fifty six and obvious in a region whose count is one short, so keep the
grouping if you touch those tuples.

Suspended members are in scope. Gabon has been suspended from the Councils of
the Commonwealth since August 2023 and still receives a model, decided 2
September 2026. That is a delivery decision rather than a fact about the
Commonwealth, so it has a test of its own: reversing it makes the programme
fifty five and changes `EXPECTED_TOTAL` and AC-F16 with it, which should take an
argument rather than an edit.

SAD 6.2's worked example is `cim-gbr-v0.1`, `tier: A`, pointing at the 35B-A3B
MoE base -- which SAD 13.5 and SAD Q2 both assign to Tier B. The rule is taken
as authoritative and the example as predating it, so `config.prepare` refuses
SAD 6.2's example verbatim. See the note at the top of
`draupnir/hamarr/config.py`.

### Gate results bind to bytes, never to a path (AC-S8, threat T8)

`draupnir/core/domain/evidence.py` is small and it is the spine of the release
half of the system. Evidence names a SHA-256 and carries no path, no URI and no
bucket, and `test_evidence_carries_no_location` walks the dataclass fields and
fails if one appears.

That test is not fastidiousness. The way this control decays is not somebody
removing the hash; it is somebody adding `artefact_uri` because a console needs
it, and the next person resolving the URI instead of the hash. A path is a name
for wherever the bytes happen to be now, and evidence bound to one stays true
after the bytes are replaced.

It lives in the core rather than in RAUN because BRISINGAMEN and SKIDBLADNIR
both need it and the modules cannot import each other. Evidence defined in one
would mean evidence redefined in the others, and a second definition of "which
bytes passed" is the whole of threat T8.

`publish.verify_artefact` re-hashes the artefact it is about to publish. It does
not look the hash up. A lookup answers "what did we record about this artefact";
the question at publication is "what do we know about these bytes".

### GLEIPNIR judges, RAUN executes, and they cannot import each other

SAD 5.2 gives GLEIPNIR "gate definitions" and RAUN "gate suite execution,
baseline management, comparison, regression detection". The module independence
contract makes that structural, so the seam is a shape rather than a call:
`raun.judging.Judge` is a protocol, and RAUN hands over measurements, baselines
and gate identifiers, receiving outcomes and a verdict.

RAUN therefore never sees a threshold, a comparison operator or a blocking flag.
There is nowhere in RAUN for a gate to be softened.

The wiring lives in `draupnir/api/assurance.py`, which is the layer above both
and the only place permitted to know two modules at once. Anything that grows
there beyond translation is policy that has escaped GLEIPNIR, and it will be
visible in one file rather than dispersed through call sites.

### Every built format is re-gated (AC-F9)

`publish.verify_formats` is driven by the set of formats that were **built**,
not by the set that has evidence. Iterating the evidence would confirm that
everything evaluated passed -- which is true of an empty set, and true of a set
missing the one format nobody ran.

A format that was built and never evaluated is reported as failing rather than
omitted, for the same reason: an omission is invisible to the guard, and the
guard would then approve a set of builds smaller than the set that exists.

### The sweep is an object (AC-F8)

Five runs sharing a naming convention are not a sweep. Comparing them means
reconstructing which five, from names, at the point of asking, and a sixth with
a typo is either silently missing or silently included -- so the selected point
is chosen from a set nobody can reproduce.

`Sweep` holds its points, the comparison is a method on it, and the selection is
recorded on it. An unevaluated point appears in the matrix with a null rather
than being dropped: a comparison that omits the points that failed to build is a
comparison of the survivors.

`Sweep.select` refuses a point whose gates did not pass. BRISINGAMEN runs the
sweep and RAUN decides whether a merge is acceptable (SAD 5.2), so selection
reads the verdict rather than forming one.

### The model card says what it does not know

Every value on a card is a `Fact`: either recorded with a source, or absent with
a reason. The reason this is a type rather than a convention is `None` -- a
renderer handed a mapping cannot tell a measured absence from a missing key from
a misspelling upstream, and the natural rendering of a missing key is to skip the
row. That is exactly the silent omission the requirement forbids.

A recorded `False` is a fact, not a gap. A release where the approver did not
also submit the run has `soleApproverException: false`, and rendering it as "not
recorded" would be a different and worse claim.

### Article 53 is generated; Article 50 is not ours

`skidbladnir/article53.py` renders the training content summary from the licence
register, on a versioned AI Office template recorded with the release (SAD 10.2
keeps a published release on the version in force at its date). No template with
blanks exists anywhere; a fact the register does not hold is stated as absent.

**The template version and the text of the Regulation must be checked at
implementation and at each release.** The SAD says so and it is true; both are
being revised actively.

Article 50 belongs to the Midgard Suite (SAD 9A.1). `test_this_package_implements
_no_watermarking` parses this package's own AST and fails if any function, class,
name or import mentions watermarking or content credentials. It reads names and
imports rather than text, because the modules discuss Article 50 at length in
order to place it with the Suite, and saying where a duty belongs is not
discharging it.

### The MLX build is checked against the NVFP4 build

A quantisation defect does not look like a defect. Both builds load, both
generate fluent text, and both score within the loss a gate was written to
tolerate. What catches it is the two disagreeing: they came from the same
weights, so a divergence between them is not model quality, it is one of the two
conversion pipelines being wrong.

`DIVERGENCE_THRESHOLD` is therefore tighter than any relative gate margin, and a
divergence **raises** rather than failing a gate. A gate failure would send
somebody to look at training data for a problem that is in a conversion script.

The check is only worth anything across hosts, so `BUILDS` records that MLX is
built on ALVISS and NVFP4 on the forge, and the export driver refuses a
specification asking for MLX without NVFP4.

### Route A and Route B

**This is an interpretation and it needs confirming.** SAD 5.2 gives BRISINGAMEN
"release route selection" and SAD 6.2 carries `route: B`, but nothing in the
document says what A and B are. `brisingamen/routes.py` reads A as publishing the
adapter and B as publishing merged dense weights, quantised -- which is what
BRISINGAMEN's "adapter to dense export" and SAD 6.2's pairing of `route: B` with
`[nvfp4, gguf-q4km, mlx4]` both point at.

Both routes merge, because the state machine has no path around MERGED and
because an adapter's capability is a claim about base plus adapter. What differs
is which artefact reaches the customer. If the programme means something else,
the change is one table and two functions.

### A route with no role declaration prevents startup (AC-B6)

`draupnir/api/guards.py`. `create_app` sweeps the routes it actually registered
and raises before returning, so a missing declaration is a startup failure and
therefore a CI failure.

Not a runtime check, because a runtime check on an undeclared route has to
decide something and every answer is wrong: allow, and the route is open; deny,
and a route that should be public breaks in production rather than in CI.

The declaration lives on the endpoint function, not in a registry keyed by
path. A registry drifts — somebody renames a path and you have a route with no
declaration and a declaration for no route.

**Two lessons from getting this wrong.** The first version walked `app.routes`
looking for `APIRoute`, and FastAPI does not flatten included routers into it:
it wraps each `include_router`. The sweep found nothing and reported that every
route was declared. A check that passes because it examined nothing is worse
than no check.

So there are now two tests rather than one.
`test_the_sweep_actually_finds_the_applications_routes` asserts the sweep finds
something, and `test_an_unrecognised_route_object_prevents_startup` asserts it
refuses rather than passes when it cannot read the structure. If a FastAPI
upgrade changes the shape again, the second one fails loudly instead of the
control quietly evaporating.

### Everything in SVALINN fails closed

The pattern is uniform and deliberate. No principal is a refusal, not an
anonymous read. No role is a refusal, not a viewer. No declared requirement is
a programming error that raises, not an open route. An unknown plug-in signer
is a refusal, not a warning. A specification with no expected hash is a
refusal, not a verified load.

In each case the alternative is a safe default, and a default that is safe is
still a default: it means a decision was skipped, and the skipped decision is
the one the control exists to record.

### Secrets are leases, and the value has one way out

Secrets do not leak from a vault being broken into. They leak from being
*placed somewhere durable* on the way to being used: an environment file, a
rendered YAML in a working directory, a `JobPlan.environment` that gets logged
when a submission fails.

So a `Lease` has no payload carrying its value, its `__repr__` and `__str__`
are redacted, and the value comes out through `reveal(now)` — named so that a
reviewer stops at the call site. `brokered_environment` puts lease *references*
in the job environment, so the control plane never holds the values in a form
it could write.

`assert_no_secrets` checks the serialised form of a rendered plan rather than
its values, because a secret interpolated into a command string is the case a
values-only check misses and the one that actually happens.

### The egress broker, and the destination that is not on it

Every outbound call declares four things: destination, purpose, run id, and the
approving policy. A firewall answers "may this host reach that host"; the
question that matters is "why is this run reaching that host, and who said it
could", and a dependency that starts phoning home in a minor version bump
satisfies the first and fails the second.

**The teacher-model destination is deliberately absent from `ALLOW_LIST`, and
`test_the_teacher_destination_is_not_allow_listed` fails if it is added.**
Distillation is out of scope for Release 1 (SAD Q3, threat T3). Adding that
host is a decision with a threat model attached, not a configuration change.

Executors have no outbound network namespace at all, so the broker governs the
control plane and the sandbox governs the executor. Two layers, because the
sandbox is the one an escaping dependency cannot argue with and the broker is
the one that produces a record.

### The sandbox has no argument that weakens it

`SandboxProfile` has no `network`, no `privileged`, no `user`, no
`capabilities` field. Those are properties computed from constants. A profile
with an `allow_network` argument is a profile that gets relaxed for one job
that needed it, and the relaxation outlives the job.

`violations()` exists so a profile arriving from elsewhere — a runbook, a
future site with its own runtime — can be held to the same statement rather
than trusted.

### The signature envelope is a list from version one (Decision S10)

Not an optional second field. One list, which happens to have one element today
and two during a migration; a format that special-cases the single-signature
shape is a format that changes when the second algorithm arrives.

Verification succeeds on **any one accepted** signature. Requiring all of them
would make an envelope unverifiable by a party holding only the older
algorithm, which is exactly what stops a migration being incremental. Not *any*
signature though: the caller states which algorithms it accepts, so an envelope
signed only under a withdrawn algorithm does not verify.

One digest, hashed once, signed by every algorithm — so two signatures cannot
be over subtly different serialisations of one object. That is why the ECDSA
P-384 signer prehashes with SHA-256 rather than SHA-384, and why the inventory
records that the effective security level is 128-bit rather than 192. Stated
rather than hidden: it matches Ed25519, so the envelope has one security level
rather than two.

### The cryptographic inventory is generated (AC-S16)

`make crypto-inventory`, and it runs in the static stage. Generated from the
constants the system uses, because an inventory maintained by hand describes
what somebody believed the system did when they last looked.

`validate()` checks both directions: every row cites NCSC guidance or an
ISO/IEC standard, and every algorithm the envelope knows has a row. Adding an
algorithm and forgetting the inventory fails the build. It caught a real
mismatch on the first run.

SAD 9.5's framing is restated in the artefact itself, because it is the thing
an auditor most needs to see stated plainly: NCSC operates guidance, not a
validation scheme comparable to CMVP, and the claim is weaker than "FIPS
validated" and is presented as what it is.

**The template versions and algorithm recommendations must be re-checked at
implementation and at each release.** The SAD says so, and it is true.

### Anchoring, partitions and what the federation may hold

The forge keeps working through a partition and release does not (Decision S8).
GULLINBURSTI therefore has no notion of failing: a submission that cannot reach
MEGINGJORD is queued in order, a policy pull returns what was last pulled, and
`may_release` says no with a reason that distinguishes a partition (wait) from
a divergence (escalate).

Capacity reports are dropped rather than queued. A queue of stale reports
delivered on reconnect describes a forge as it was three days ago, which is
worse than a gap.

MEGINGJORD verifies narrowly and deliberately. It holds no entries, so it
cannot check that entry 1,000 follows from entry 999; it checks that a site is
not contradicting itself, which is detectable from hashes alone. That
narrowness is what lets the federation hold hashes alone.

An identical head already anchored is a **duplicate**, accepted wherever it
sits in the chain. A retry is idempotent, and rejecting it as "behind the head"
would turn a network hiccup into an operator incident. A *different* hash at an
anchored sequence is a divergence, which raises, puts the forge read-only, and
is not retried.

### `ChainHead` is the ledger's, and there is only one

`core/domain/federation.py` reuses `ledger.ChainHead` rather than defining its
own. `AnchorSubmission` wraps it with the anchoring envelope — previous hash,
timestamp, signature — and delegates `signing_payload()` straight through.

This was two classes briefly, and that is precisely how a signature ends up
covering different bytes at each end.

### The edge: what each convention costs and why it is not optional

`draupnir/api/`. SAD 11E.2's table is short and every row is load bearing.

**Idempotency is required, not accepted.** A key is *reserved* before the work
starts, which is what lets a replay arriving while the first request is still
running return 409 rather than acting twice — a store that only recorded
completed responses could not answer that case at all. The same key with a
different body is 422 rather than a replay of the first response: replaying
would tell the caller a request they did not make had succeeded. Keys are
scoped per site and per actor, because two operators both using `retry-1` is
not a collision anybody should have to think about.

**Cursors, never offsets.** A cursor is a position in the `(created_at, id)`
order. UUIDv7 makes that a total order, so a cursor names exactly one row;
ordering by a timestamp alone leaves ties, and a tie straddling a page boundary
is a row returned twice or not at all. A cursor we did not issue is refused
rather than silently resetting to page one, which would loop a paginating
client forever.

**`If-Match` is required on a mutating request.** Missing is 428, stale is 412.
Two operators on one run board, one cancelling and one retrying, is the
ordinary case, and the retry must not silently undo the cancellation. The ETag
is derived from the state rather than stored beside it: a version column has to
be incremented by every writer, and the one that forgets produces a tag that
says nothing changed when something did.

**202, always, for anything long.** Nothing blocks an HTTP request on training
or on hashing a corpus. The response carries the run identifier and the URL of
its event stream.

### Server-sent events carry deltas

An event says what changed about one subject; a client merges it into what it
holds. `Delta` refuses to be constructed with no changed fields, because an
event carrying no delta is a refresh instruction and this stream carries
deltas.

Every event has a monotonic sequence, so a reconnecting client sends
`Last-Event-ID` and receives what it missed. A client asking for a point the
buffer has dropped is told to **resynchronise** rather than served from the
oldest event still held: a silent gap leaves the client's state wrong for as
long as the page is open, with nothing to detect it.

### The guard is a dependency, not middleware

`declare` records what a route requires; `deps.guard` enforces the same
declaration at request time. Both read one attribute on the endpoint function,
so the enforced rule, the startup check and the published permissions table
cannot disagree.

It is a dependency because a dependency runs *after* path resolution and
therefore knows which route matched. Middleware runs before and would have to
re-derive the route from the path — a second router that disagrees with the
first one the day somebody adds a path parameter.

A matched route with no declaration is a 500, not a permit. It should be
unreachable, and if it is reached the startup check has a hole, which is not a
thing to serve a request through.

### The request context binds in a `yield` dependency

A context variable must be reset in the task that set it, and Starlette runs
middleware in a different task from the endpoint. Taking the token in a
dependency and resetting it in middleware raises — and because the raise
happens in the response path, *every* status the API meant to return becomes a
500. That is how it was written the first time, and every contract test failed
with the same wrong number.

So `deps.context` is a `yield` dependency: it binds, yields, and unbinds in one
task. The middleware only reads what the dependency recorded, to echo the
request and correlation identifiers.

### Redaction is in the emitter

"No secret, token or corpus excerpt in any log line" is a claim about lines
this code does not write, and a rule that only applies to careful callers is
not a rule. So everything goes through `telemetry.log`, which merges the
request context and scrubs the fields; a caller cannot emit a line that skips
either step without reaching past the module.

Scrubbing is by name *and* by shape. By name catches the field somebody added
called `access_token`; by shape catches the token that arrived inside a message
somebody was helpfully including. Long free text is truncated, because a corpus
excerpt is a long string in a field called `text` and it looks exactly like a
useful diagnostic.

Context is merged *after* the caller's fields, so a field named `actor` cannot
forge one.

### Two hazards worth knowing about

**A module-level singleton imported by name.** The routers did
`from deps import STORE`, which captures the object. Replacing `deps.STORE` — a
test does, and a deployment could — then left reservations landing in one store
and completions in another, which surfaces as "the key was never reserved" and
a 500. Store access now goes through `deps.store()`, `deps.complete()` and
`deps.release()`, which resolve at call time.

**`get_settings` is `lru_cache`d process-wide.** One request to `/healthz`
populates it. A test that makes an HTTP request before the integration
conftest exports the container's database URL pins the default
`localhost:5432` for the whole session, and every later fixture times out —
looking exactly like a broken container. `migrate_and_grant` now clears the
cache after exporting the URL, and the latency test that triggered this lives
at the contract level, where it belongs: it measures the edge and touches no
database, so it has no business sharing a session with the suite that owns the
containers.

### Coverage is measured where the code is exercised

The edge's pure mechanisms — idempotency, cursors, entity tags, event deltas,
redaction — are unit tested. The routers are exercised by requests and are
measured at the contract level. Measuring the routers at the unit level would
report them as uncovered while the contract level exercises every one of them,
and the number would be describing the test layout rather than the code.

### `hodd://` URIs, and why nothing records a path

A run specification records `hodd://sindri/corpora/GBR/curated`. Moving the
vault, or replacing it with an object store, changes which driver resolves that
URI and changes nothing about the specification -- which is the point, because
SAD 7.4 exists so that a run recorded in 2026 still resolves in 2030.

One wrinkle worth knowing. SAD 6.2 writes `hodd://models/core/...`, where the
authority is not a site; SAD 7.4 says the authority carries the site. Both
spellings resolve: an authority is taken as a site only when it *is* one, which
is why the store drivers need to know which forges exist. The cost is that a
mistyped site becomes a local path and is then not found. The failure is the
safe one -- it can never resolve to a different forge's artefact.

### First party distributions carry the `veldris-` prefix

The control plane's distribution is **`veldris-draupnir`**, not `draupnir`.
The import name is unchanged -- `import draupnir` and the `draupnirctl` script
are exactly as before -- and only the name a package index knows differs.

The reason is that `draupnir` on PyPI is an unrelated project, published first
and still maintained. A distribution of ours by that name would let a fresh
virtual environment, a misconfigured runner or a mistyped `pip install` fetch
somebody else's code. That is not hypothetical: it happened here, and
`make audit` spent a minute trying to build a protein dynamics library before
anyone noticed.

> Every first party distribution is named `veldris-…`. A distribution without
> that prefix is not ours, whatever its import name suggests.

`tests/unit/test_distribution.py` enforces it and fails the build if a
distribution named `draupnir` is ever installed. Publishing, and the name
reservation that keeps `veldris-draupnir` ours, are in
[PUBLISHING.md](PUBLISHING.md).

### Plug-ins are signature verified (SAD 9.3)

An unverified plug-in does not load. The verifier arrives in Prompt 6; until
then every plug-in is unverified, and the development concession is exactly one
environment variable wide:

```bash
DRAUPNIR_DEV=1
```

With it set, an unverified plug-in loads and logs a warning naming the
distribution. Without it, the plug-in is refused and the refusal is reported by
`PluginRegistry.failures` -- discovery does not raise, because one bad third
party plug-in must not stop the control plane starting, but asking for that
plug-in afterwards does raise, with the reason.

### Writing a driver

Install `draupnir[testing]` and inherit the published conformance suite:

```python
from draupnir.interfaces.testing import ScheduleDriverConformance


class TestMyDriver(ScheduleDriverConformance):
    @pytest.fixture
    def driver(self):
        return MyDriver()
```

The suite is what enforces Decision S5, which requires `render` to be pure.
"Pure" is not something a code review establishes, so three properties are
checked instead: rendering three times gives byte-identical plans, rendering
opens no socket, and rendering leaves the working directory as it found it.

The third render is taken after a deliberate pause rather than back to back.
The system clock advances in steps -- about 15.6 ms on Windows -- so a driver
stamping the wall clock into its plan returns the *same* value to two
consecutive calls and passes a two-call check. That was found by trying to get
an impure driver past the harness, which is what
`tests/unit/test_conformance_harness.py` does for every rule it enforces.

### Generated clients stay generated (AC-Q2)

`draupnirctl/_generated.py` and `web/packages/api-client/src/generated/` are
written by `make clients` from `docs/api/openapi.json`. The pipeline
regenerates them and fails on any diff. Editing either by hand fails the build,
because a hand edited client is the most common way a generated interface
quietly stops being generated.

If you change an endpoint:

```bash
make openapi     # re-export the contract from the application
make clients     # regenerate both clients
```

and commit all three. `make openapi-diff` then checks the change against
`docs/api/openapi.released.json` and refuses a breaking one: within `/v1`,
changes are additive only.

---

## 3. Testing

| Level | Command | Scope |
|---|---|---|
| Unit | `make test-unit` | Pure domain logic. 90% statement coverage on `core/domain` |
| Property | `make test-property` | Ledger invariants. 500 examples minimum |
| Contract | `make test-contract` | Every driver against the conformance harness |
| Integration | `make test-integration` | Ephemeral PostgreSQL and MinIO, real migrations |
| Frontend | `make test-frontend` | Vitest and Testing Library |
| End to end | `make test-e2e` | Playwright, the four journeys |
| Accessibility | `make test-a11y` | axe on every route and every Storybook story, zero serious or critical |
| Visual | `make test-visual` | Storybook snapshots, diff gate |

Only the integration level needs Docker. Everything else runs on a machine with
nothing but Python and Node.

The accessibility level runs axe twice over: once on the console's routes, and
once on every Storybook story. The second sweep is the one that matters for the
design system, because a component that only ever appears on a route in its
happy path is a component whose denied and partitioned states nothing has run
axe over -- and those are the states most likely to be wrong, since they are the
ones nobody looks at while building.

### The design system's own gates

JARNGREIPR carries three checks beyond the levels above. They live with the
package and are documented in `web/packages/jarngreipr/README.md`.

| Check | Where | What it refuses |
|---|---|---|
| Token linter | `web/scripts/token-lint.mjs`, run by `make lint-web` | A hard-coded colour, spacing or radius anywhere in `web/`, and a `--jg-*` token declared outside the ramp |
| State coverage | `packages/jarngreipr/src/state/stories.test.ts` | A component without a story for each of the seven states, a story file that bypasses the shared factory, and a story file with no component |
| Contrast | `packages/jarngreipr/src/tokens/tokens.test.ts` | A declared colour pairing below 4.5:1 for text or 3:1 for a control boundary, in either ramp, and the two copies of the dark ramp drifting apart |

The token linter is itself tested. `web/tests/token-lint.test.ts` runs it
against fixtures that state a hex colour, a pixel padding, a pixel radius, an
`rgba()` background, a named colour in a border shorthand and a custom property
minted outside the ramp, and requires it to report all six. The first version
of the linter passed the whole workspace and also passed
`.probe { color: #2d6cdf; }`, because its parser read one declaration per line
and the fixture wrote the rule on one. The fixtures exist so that cannot happen
again quietly -- the same reason the gitleaks allowlist is proved with a planted
token rather than trusted.

The unit gate measures `draupnir/core/domain`, which is what SAD 11E.3 scopes
the unit level to ("pure domain logic") and what AC-N8 names ("the core state
machine and ledger"). The infrastructure half of the core is gated by the
integration stage, because a repository cannot be honestly exercised without a
database. AC-N5 -- 100,000 ledger entries verified in under 60 seconds -- is
measured in `tests/integration/test_ledger_performance.py`, and the build log
carries the number.

Every transition in SAD 6.1 has a test, and that claim is maintained by the
build rather than by memory: adding a row to the transition table without a
case in `tests/unit/test_states.py` fails `test_every_transition_has_a_case`.

`make ci` runs every stage in pipeline order. Run it before opening a pull
request; it is the same set of commands the runner executes.

### Visual regression baselines

Screenshots are platform specific, so a baseline recorded on a developer's
machine is not the one the runner will diff against. The visual spec records a
missing baseline rather than failing on it, and annotates the file to commit;
every run after that is a real diff gate. Commit the baselines the runner
records on its first green build.

To check the gate still bites, change a component and run `make test-visual`.

### Dependency audit

`make audit` runs `pip-audit` against the exported lockfile, not the
environment, and `pnpm audit` at `--audit-level high`.

`web/package.json` carries a `pnpm.overrides` block. Those are security fixes
pulled into transitive dependencies that their parents have not yet picked up,
not convenience pins. Each one exists because the audit failed without it;
remove one only when the parent has moved on, and re-run `make audit` to prove
it.

The CycloneDX SBOM for the frontend is produced by `cdxgen`, run through `npx`
at a pinned version rather than installed as a devDependency. Two reasons: it
is a build tool rather than something DRAUPNIR ships, and `cyclonedx-npm`
shells out to `npm ls`, which cannot read a pnpm workspace at all.

### Empty harnesses

Several harnesses exist before the thing they test. The four Playwright journey
specs, the conformance harness and the Storybook visual regression project are
all wired and running against nothing yet. This is deliberate: an empty harness
that runs is worth more than a good harness added at the end.

The journey specs are marked `fixme` rather than left failing. UX Prompt UX-0
asks for specs that fail with a clear "not implemented" message; a permanently
red pipeline teaches people to ignore it, and AC-Q1 requires every stage to run
on main. The specs exist, run, and report by name. Prompts UX-2 to UX-4 replace
the bodies and remove the markers.

---

## 4. Making a change

```bash
git switch -c <topic>
make format          # ruff and prettier
make ci              # everything the runner will run
git commit
```

Conventions worth knowing before the first review comment:

- **Timestamps are offset-aware.** `ruff` rejects `datetime.now()` without a
  timezone. RFC 3339 with an explicit offset, everywhere (SAD 11E.2).
- **Identifiers are UUIDv7.** `draupnir.core.domain.identifiers.new_id()`.
  Use `id_at()` where an identifier must be reproducible.
- **Errors are RFC 9457 problem documents.** Raise `ProblemError` from the edge.
  No bare 500 reaches a client.
- **A forge is a site; an appliance is a node.** They are not interchangeable
  (SAD 11A.2, Decision S12). The eslint configuration refuses the confusion in
  user-facing strings.
- **Migrations are forward only.** The generated `downgrade()` raises. Recovery
  is a restore plus a new forward migration.

### Commits and secrets

`make hooks` installs the pre-commit hooks, including the gitleaks scan
required on every commit by AC-Q3. Install them once, on first clone.

**The one allowlist entry that is not a fixture.** `.gitleaks.toml` excuses
Python type annotations for cryptographic key objects. The `generic-api-key`
rule matches any identifier ending in `key` followed by a value, so
`signing_key: ed25519.Ed25519PrivateKey` reads to it as a credential being
assigned — and it is a type, with no value in the file at all.

Renaming was tried first and does not work: the rule keys on the suffix, so
every accurate name for a signing key trips it. The entry is therefore as
narrow as the tool allows — it matches only a dotted identifier from the
`cryptography` asymmetric modules, and a real credential is not
`ed25519.Ed25519PrivateKey`. The alternative was allowlisting the whole of
`svalinn/pki.py`, which would suppress the detector in the one file most
likely to acquire a real secret.

It is verified rather than assumed. Planting a real-shaped GitHub token in
that same file and re-running the scan still produces a finding; without the
allowlist the scan produces one finding, and with it and no planted token,
none. If you widen this entry, do that check again.

`regexTarget = "match"` on the allowlist is what makes that possible: it
excuses a rule for the *shape of the line* it matched rather than for a value,
which is the distinction between "this is not a secret" and "this secret is
fine".

---

## 5. The pipeline

`.github/workflows/ci.yaml` implements SAD 11H stage by stage, each named for
the figure so that a red build names the stage that failed. No stage carries
`continue-on-error` and none is conditional: AC-Q1 requires that none is
skippable on main.

```
1 STATIC    ruff -> mypy -> eslint + tsc -> import-linter -> gitleaks -> audit -> SBOM
2 TEST      unit -> property -> contract -> integration -> API contract ->
            frontend -> e2e -> a11y -> visual regression
3 BUILD     aarch64 distroless images -> client regeneration -> SBOM -> sign
4 DEPLOY    migrate (dry run first) -> deploy -> smoke -> rollback on failure
```

Stage 4 lives in `.github/workflows/deploy.yaml` and runs only after a green
pipeline on main.

### Images

`make images` builds `linux/arm64` images from a distroless base, running as
uid 65532 (AC-Q7). On an x86-64 development machine this needs QEMU via
`docker buildx`, and is slow; the runner on ALVISS is aarch64 and builds
natively. Nothing in the development stack depends on these images, so a slow
image build never blocks local work.

---

## 6. Layout

```
draupnir/           domain and application, the edge, and the eleven modules
  core/
    domain/         ledger, state machine, projector, sites -- pure, no framework
    application/    the orchestrator: guard, act, write ledger, project
    infrastructure/ models, repositories, configuration -- substitutable
  interfaces/       the seven Protocols and the conformance harness
  api/              FastAPI edge: routers, schemas, guards, error mapping
  hodd/ gleipnir/ motsognir/ hamarr/ brisingamen/ raun/ skidbladnir/
  svalinn/ gullinbursti/ megingjord/
plugins/            first-party drivers, each an installable package
draupnirctl/        the generated CLI
web/                JARNGREIPR design system and the console
skills/             development skills (SAD 11G)
migrations/         forward-only Alembic migrations
scripts/            seed, OpenAPI export and diff, client generation, smoke
deploy/             rollout and rollback
docs/               architecture, this guide, acceptance evidence
tests/              unit, property, contract, integration
```

Each module package carries its responsibility and its "must not" in the
package docstring, taken from SAD 5.2. Read it before adding to one.
