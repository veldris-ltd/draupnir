# Imhotep reconciliation

VLD-SAD-DRAUPNIR-001 against the delivered repository.

Evidence for **AC-D4**: "This document is re-run through Imhotep against the
delivered repository, and every SPECIFIED item is marked IMPLEMENTED, DEVIATED
with reasons, or NOT BUILT."

## What this is

SAD 1.2 records that the document adopts the Imhotep **structure** while
replacing the citation column with a specification status column, because no
repository existed when it was written: "Every component, interface, schema and
control described here is **SPECIFIED**, meaning it is a requirement on the
implementation, and none is **OBSERVED**, meaning read from a repository." It
then says what to do at the first release, which is this.

**On the tool.** Imhotep proper is a Veldris skill, and it is not in this
repository — `skills/` holds the six development skills of SAD 11G and no
seventh. So this reconciliation was performed by reading the document section by
section against the code, rather than by running the tool over it. That is a
deviation from the letter of AC-D4 and it is recorded here rather than glossed;
what the criterion asks for substantively — every SPECIFIED item marked, with
reasons for every deviation — is below.

**Vocabulary.**

| Mark | Means |
|---|---|
| **IMPLEMENTED** | Built, and exercised by something that runs in the pipeline |
| **DEVIATED** | Built differently from the specification, or built and not exercisable here. The reason is stated. |
| **NOT BUILT** | Absent. What is missing is stated. |

**The one recurring reason.** The Sindri estate does not exist. SAD 1.3 puts
the hardware build in VLD-INF-SINDRI-001 and out of scope. So there is no
three-appliance ring, no Slurm controller on REGIN, no NFS vault, no GPU, no
uninterruptible supply, and no WireGuard link to a federation registry.
Anything whose specification is a *measurement on that hardware* is marked
DEVIATED or NOT BUILT and says so. Nothing is marked IMPLEMENTED because it
ought to work.

## Summary

| Section | Items | Implemented | Deviated | Not built |
|---|---:|---:|---:|---:|
| 5.1 Deployable units | 6 | 5 | 1 | 0 |
| 5.2 Module responsibilities | 11 | 11 | 0 | 0 |
| 6.1 Lifecycle | 14 states, 16 transitions | all | 0 | 0 |
| 6.2 Run specification | 1 | 1 | 0 | 0 |
| 7.1–7.4 Data | 11 entities, 4 topics | 15 | 0 | 0 |
| 8.1 API surface | 34 operations | 34 | 0 | 0 |
| 8.2 Plug-in interfaces | 7 | 7 | 0 | 0 |
| 9.1–9.5 Security | 5 topics, 14 threats | 18 | 1 | 0 |
| 9A Article 53 | 4 | 4 | 0 | 0 |
| 10 Extensibility | 3 | 3 | 0 | 0 |
| 11.1–11.4 Operations | 4 | 2 | 1 | 1 |
| 11A Federation | 6 | 6 | 0 | 0 |
| 11B–11E Engineering | 8 | 8 | 0 | 0 |
| 11F Frontend | 4 | 4 | 0 | 0 |
| 11G Skills | 6 | 6 | 0 | 0 |
| 11H Pipeline | 5 stages | 5 | 0 | 0 |
| 15 Decisions | 14 | 13 | 1 | 0 |
| 16A Custody | 1 | 1 | 0 | 0 |

Three items are **NOT BUILT**; all three are named in the sections below and
repeated at the end.

---

## 5.1 Deployable units

| Unit | Mark | Where |
|---|---|---|
| Control plane API | IMPLEMENTED | `draupnir/api/`, `make api` |
| Worker / orchestrator | DEVIATED | `draupnir/core/application/orchestrator.py` and `draupnir/procedures/`. The state machine, the ledger write and the projection are one transaction; the API and the M1–M10 procedure both drive them. There is **no long-running worker process** polling the scheduler and advancing runs on its own: dispatch and collection are driven by the procedure runner, not by a daemon. See NOT BUILT 1 below. |
| Web console | IMPLEMENTED | `web/apps/console/`, 31 screens |
| CLI | IMPLEMENTED | `draupnirctl/`, generated from the OpenAPI document |
| Executor shims on the appliances | IMPLEMENTED | `plugins/hamarr_llamafactory/`, `plugins/motsognir_slurm/` — the drivers that render and submit. They run where the tools are. |
| CON-A local view | IMPLEMENTED | `tools/stedi-view/`, no dependencies, works with the API unreachable |

## 5.2 Module responsibilities and boundaries

All eleven modules exist with the responsibilities and the "must not" of the
table, and each carries both in its package docstring, from which its README is
generated (AC-D1).

`HODD` `GLEIPNIR` `MOTSOGNIR` `HAMARR` `BRISINGAMEN` `RAUN` `SKIDBLADNIR`
`SVALINN` `GULLINBURSTI` `MEGINGJORD` `Core` — **IMPLEMENTED**.

The "must not" clauses are enforced rather than documented where they can be:
`.importlinter` holds seven contracts, including "the core names no driver
implementation" and "a driver sees the interfaces, never the core", and
AC-B7's test plants a violation and watches the linter catch it.

**One addition to the layering.** `draupnir/procedures/` is a package the SAD's
5.1 table does not name. It sits above the modules, composes them, and holds no
domain logic — the same position `draupnir.api` holds, and it is in the layers
contract at that height. It exists because SAD 1.1's purpose sentence needed to
become a program.

## 6.1 Lifecycle and workflow

**IMPLEMENTED.** Fourteen states, sixteen transitions, each with its guard and
its required ledger fields, in `draupnir/core/domain/states.py`. The table is
the only definition: `ALLOWED_TRANSITIONS` and `TERMINAL_STATES` are derived
from it, so the two cannot disagree. Every transition is exercised by name
(AC-N8), and the M1–M10 procedure walks eleven of them in one run.

`missing_records` refuses a transition whose payload omits a field the table
requires, which makes SAD 6.1 the schema for its own audit record rather than a
description of it.

## 6.2 Run specification

**IMPLEMENTED.** `draupnir/interfaces/types.py` parses the worked example
structurally, and `spec_hash()` is the SHA-256 of its canonical bytes. The run
*identity* of AC-F1 is a second hash over the specification hash and the sorted
resolved input hashes (`draupnir/core/domain/identity.py`), which is the thing
two clients agree on.

## 7.1–7.4 Data architecture

**IMPLEMENTED.** Eleven entities in `migrations/versions/0001_initial_schema.py`
plus `projection_checkpoint` in `0002`. Storage placement, retention and
`hodd://` addressing are in `draupnir/hodd/`.

Two things are stronger than the specification asked for, and one weaker:

- `run` is a **projection** of the ledger rather than a table written directly.
  SAD 7.1 lists it as an entity; it is one, and it is derived. The table
  comment says so, because an operator with `psql` who assumes otherwise will
  eventually write to it.
- The three constraints of SAD 11C are enforced by the **database**: an
  append-only trigger, a foreign key and NOT NULL on `release.approval_id`, and
  row level security with `FORCE` on every scoped table.
- Retention runs when it is asked to. There is no scheduler firing it at
  24 months; see NOT BUILT 1.

## 8.1 API surface

**IMPLEMENTED.** Thirty-four operations, every one with a role declaration, an
`operationId` and a problem-document error path, and both clients generated
from the exported document. The nine conventions of SAD 11E.2 are attached to
routes rather than described, and `tests/contract/test_api_surface.py` checks
each against a real request.

**Every mutating endpoint writes.** Each records through the orchestrator, in
one transaction, or refuses and says why. The two shapes differ, and the
difference is the design rather than an implementation detail:

| Endpoint | Records | Shape |
|---|---|---|
| `POST /v1/runs` | a run at DRAFT, with its identity | transition (registration) |
| `POST /v1/sources` | the facts HODD holds, with the DPIA determination | `source` entry |
| `POST /v1/corpora/{iso3}/ingest` | the ingest | `corpus` entry |
| `POST /v1/corpora/{iso3}/curate` | the curation | `corpus` entry |
| `POST /v1/gates/{id}/decide` | AWAITING_APPROVAL → RELEASED or QUARANTINED | transition |
| `POST /v1/runs/{id}/cancel` | TRAINING → FAILED | transition |
| `POST /v1/runs/{id}/retry` | EVALUATING → QUEUED | transition |
| `POST /v1/releases/{artefact}/publish` | the publication, under its approval | `release` entry |

A source, a corpus and a release are not runs — SAD 7.1 gives each its own
entity — so those entries are folded by nothing and passed through by the
projector. A decision, a cancellation and a requeue are lifecycle transitions,
so they go through the state machine, which checks them against SAD 6.1.

`Orchestrator.record` refuses a `run` subject outright. The projector folds
every run entry and raises on a transition string it cannot parse, so one
free-form entry about a run would stop the registry rebuilding — and stop it
for every run at the site, not only that one.

**Three refusals came out of this rather than three features.** Cancelling a
run that is not `TRAINING` is refused 409, because cancelling stops a scheduler
job and a queued run holds no allocation — and SAD 6.1 has no transition out of
`QUEUED` except to `TRAINING`, so there is nowhere to put a withdrawn one.
Requeueing a run with no recorded gate failure is refused, because the requeue
of SAD 6.1 is for a run that failed one within its budget. Deciding a run that
is not `AWAITING_APPROVAL` is refused, naming the state it is in. Each refusal
names the row of the table it could not find, so an operator is not left
guessing whether the handler or the lifecycle said no.

**One defect fixed on the way.** `decideGate` hard-coded
`sole_approver_exception=False`. AC-S15 requires every release where the
approver also submitted to carry the exception, and constraint C-11 requires it
to be computed rather than supplied. It is now read from the run's registration
entry — the submitter is whoever appended it — so an approver cannot suppress
it by describing themselves differently. `publishRelease` was likewise
unconditional: it refused every publication with "no signed approval". It now
looks for the approval in the chain and refuses only when there is none.

## 8.2 Plug-in interfaces

**IMPLEMENTED**, all seven, and every one now has an installed reference
driver:

| Group | Reference driver |
|---|---|
| `draupnir.train` | `hamarr.llamafactory/v1` |
| `draupnir.merge` | `brisingamen.mergekit/v1` |
| `draupnir.eval` | `raun.lmeval/v1` |
| `draupnir.export` | `skidbladnir.quantise/v1`, `skidbladnir.targz/v1` |
| `draupnir.schedule` | `motsognir.slurm/v1`, `motsognir.local_subprocess/v1` |
| `draupnir.store` | `hodd.posix_reference/v1` — **written for AC-D2 in this prompt** |
| `draupnir.policy` | `gleipnir.spdx/v1` — **written for AC-D2 in this prompt** |

The store and policy points had no implementation until now, because HODD
addresses its own vault and GLEIPNIR decides its own policy directly. An
extension point nobody has extended is an extension point whose Protocol nobody
has read from the outside, and writing the two found nothing wrong with either
Protocol — which is worth recording as a result rather than assumed.

Every driver passes the published conformance harness, which checks that
`render` is pure by rendering three times, rendering with the network removed,
and diffing the working directory.

## 9.1–9.5 Security architecture

**IMPLEMENTED**: trust boundaries, the fourteen-threat register with its
control mapping, five roles with a permission table the API publishes from the
same attribute the guard reads, and the cryptographic standards with a
generated inventory.

**DEVIATED**: the executor sandbox (`draupnir/svalinn/sandbox.py`). The profile
is generated and its content asserted — no outbound network, read-only mounts,
no privilege escalation — but nothing here applies it to a process, because
applying it needs the appliance's kernel. AC-S11's "an executor attempting an
outbound connection fails" is demonstrated against the profile, not against a
running executor.

Threats T1 to T14 each have their control and their test. T3's teacher-model
destination is absent from the allow list, and distillation stays out of scope
for Release 1 as the SAD says.

## 9A EU AI Act compliance

**IMPLEMENTED.** The Article 53 training content summary and the copyright
policy reference are generated from the licence register and neither is hand
authored (Decision S11). `article53.summarise` takes the same mappings a policy
driver is handed, so the published document and the decision that permitted the
release are rendered from one set of facts.

The template version in force at a release's date is recorded with the release,
so a summary rendered under one template stays explicable under the next.

## 10 Extensibility

**IMPLEMENTED.** Seven extension points, two worked scenarios (a new export
format, added in under two hundred lines with no core file changed; a
jurisdiction policy as a driver rather than a core change), and the three
compatibility rules, of which rule 1 — the version in the entry point name — is
parsed by one module the loader, the drivers and the harness all share.

## 11.1–11.4 Operational architecture

| Topic | Mark | Note |
|---|---|---|
| 11.1 Deployment | IMPLEMENTED | Compose for development, distroless aarch64 images, migrate-dry then migrate, smoke test, rollback |
| 11.2 Degraded modes | IMPLEMENTED | All nine rows, each with the fault injected for real. `docs/runbook.md` and `tests/integration/test_degraded_modes.py` |
| 11.3 Observability | DEVIATED | Every signal has a source and a surface. The **fabric bandwidth probe** — an hourly `nccl-tests` job dispatched by MOTSOGNIR — is not dispatched by anything: there is no scheduler loop and no fabric. See NOT BUILT 1. |
| 11.4 Technology selection | IMPLEMENTED | Every selected technology is the one in use |

The uninterruptible supply is worth its own line. SAD 11.3 lists the mains and
battery signal as arriving over USB "once fitted", and it is not fitted.
`draupnir/motsognir/supply.py` reads the status file the supply's daemon
publishes and decides what to do about it — forced checkpoint on transfer, then
drain, then halt at the low-battery threshold — and that decision is exercised
by writing the file. The USB link is the estate's and is absent; the decision is
DRAUPNIR's and is built.

## 11A Federation architecture

**IMPLEMENTED.** The Forge Matrix, the two tiers, the naming rules, anchoring,
partition behaviour and the "what federation does not do" prohibition. The last
is enforced by construction: every federation payload is built through
`core.domain.federation.sealed`, which walks the finished structure and refuses
corpus or weight content, so T13's mitigation is a property of the code path
rather than of review.

A partitioned forge trains and does not release (Decision S8). A forge that
finds a divergence goes read-only, and restoring the link does not lift it.

## 11B–11E Engineering standards

**IMPLEMENTED.** The layering, the data model constraints, the end-to-end
sequence, the repository layout, the nine API conventions, the five test levels
and the observability instrumentation.

One correction to 11E.1's layout: the tree now also holds `draupnir/procedures/`
and `skills/`, and `docs/` holds `runbook.md` and `acceptance/` as 11E.1 says it
should.

## 11F Frontend and experience

**IMPLEMENTED.** JARNGREIPR with twenty-four components at seven states each,
the four primary journeys as Playwright acceptance evidence, the interaction
requirements, and accessibility as an acceptance criterion rather than a review
comment (Decision S13). 175 Storybook stories at zero serious or critical axe
violations, and 23 routes likewise.

The manual keyboard pass of AC-U5 is in `keyboard-pass.md`, with three findings,
one of which is open with a recommendation.

## 11G Development skills

**IMPLEMENTED.** All six, each shipping an executable scaffold rather than a
description of one, and each demonstrated in `tests/contract/test_skills.py` by
running the scaffold and putting its output through the real gate.

The seventh skill the table names — `imhotep`, marked "Existing" — is not in
this repository. This document is what it would have produced.

## 11H Continuous integration and delivery

**IMPLEMENTED.** Five stages, none skippable, on the main branch. The
acceptance pack and the skills demonstration are both inside the test stage
rather than beside it.

## 15 Resolved decisions

| Decision | Mark |
|---|---|
| S1–S3, S6, S7, S9–S14 | IMPLEMENTED |
| S4 GLEIPNIR judges, HODD records | IMPLEMENTED — `Evidence.passed` is recorded, never computed by an eval driver |
| S5 `render` must be pure | IMPLEMENTED — checked by three renders, a network block and a directory diff |
| S8 Training continues through a partition, release does not | IMPLEMENTED |
| S12 A site is not a node | DEVIATED in one place only: `Appliance` and `Site` are separate types and never conflated, but there is one estate and one site in any running configuration here, so the distinction is exercised by tests rather than by deployment |

## 16A Site 0 standing risk acceptance

**IMPLEMENTED** as far as software can be. The custody concentration is a risk
the programme accepts; what DRAUPNIR contributes is that every artefact is
hashed, every transition is chained, and the chain is anchored off-site, so the
concentration is auditable even though it is not reduced.

---

## The four that are not built

### NOT BUILT 1 — There is no worker process

SAD 5.1 lists a worker as a deployable unit. Nothing polls the scheduler,
advances a run from `TRAINING` to `TRAINED`, dispatches the hourly fabric probe
of SAD 11.3, or fires a retention action at 24 months. The M1–M10 procedure does
all of that inline, in one call, which is what makes it a demonstration rather
than an operating system.

**Consequence.** A run submitted through the console reaches `DRAFT` and stays
there: the endpoints that would move it exist and are wired, but nothing calls
them on the run's behalf. An operator can drive every transition by hand
through the API, and `make procedure` drives them all in one call. What is
missing is the thing that does it without being asked.

### NOT BUILT 2 — Vault reconciliation

SAD 11.2 row 4's recovery is "restore NFS, run reconciliation". There is no
reconciliation command: after a vault outage, staging what running jobs wrote
to local scratch is manual. The runbook says so where an operator will read it
rather than leaving them to discover it.

### NOT BUILT 3 — Three non-functional targets are unmeasured

AC-N1 (control plane overhead on ALVISS), AC-N2 (step time within one per cent)
and AC-N11 (anchor round trip over WireGuard) are measurements on hardware that
does not exist. They are commissioning measurements. Nothing here estimates
them, because an estimate recorded in an acceptance pack is read as a
measurement.

---

## What this reconciliation changed

Five items moved from NOT BUILT to IMPLEMENTED while it was being written,
because reading the specification against the code is what found them:

1. **The mutating endpoints did not write.** Recorded here as a NOT BUILT and
   then built: every one now records through the orchestrator or refuses and
   names the row of SAD 6.1 it could not find. Two defects came with it — the
   sole approver exception was hard-coded to false, and publication refused
   every artefact unconditionally.
2. **AC-F2, duplicate detection.** Nothing detected a resubmitted
   specification. `Orchestrator.register` now looks the identity up in the
   chain, and the API returns 409 naming the run that already carries it.
3. **AC-D2, the last two extension points.** `draupnir.store` and
   `draupnir.policy` had no reference implementation.
4. **AC-B7, the import contract.** The contract existed and nothing had ever
   watched it fail. A test now plants a violation.
5. **AC-D1, module READMEs.** None existed. They are generated from the package
   docstrings, so they cannot drift from the responsibilities they state.

And one defect was found by injecting a failure rather than by reading:
**an unmounted HODD vault was silently recreated on the control plane's local
disk** by `put`, because it creates the artefact's parent directories. A run
would have trained and staged its weights somewhere nobody backs up, and the
vault returning later would have hidden it. Every store operation now checks
that the root is present first.
