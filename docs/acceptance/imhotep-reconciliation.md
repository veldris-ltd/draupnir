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
| **REACHABLE** | Implemented, and on a path a running deployment takes. Derived, not judged — see [Reachability](#reachability) |
| **DEVIATED** | Built differently from the specification, or built and not exercisable here. The reason is stated. |
| **NOT BUILT** | Absent. What is missing is stated. |

**Why a fourth mark.** IMPLEMENTED is satisfied by a unit test, so a module can
carry it and sit on no path a request, a worker tick or a deployed command ever
takes. The remedial register found the platform's security, federation,
publication and array controls in exactly that state while this document marked
them IMPLEMENTED (RF-30). REACHABLE is the third of the three things the
register asks of a control — built, tested, and reachable — and it is applied
to modules rather than to sections, because reachability is a property of
code, not of a specification heading.

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
| 5.1 Deployable units | 6 | 6 | 0 | 0 |
| 5.2 Module responsibilities | 11 | 11 | 0 | 0 |
| 6.1 Lifecycle | 14 states, 16 transitions | all | 0 | 0 |
| 6.2 Run specification | 1 | 1 | 0 | 0 |
| 7.1–7.4 Data | 11 entities, 4 topics | 15 | 0 | 0 |
| 8.1 API surface | 43 operations | 43 | 0 | 0 |
| 8.2 Plug-in interfaces | 7 | 7 | 0 | 0 |
| 9.1–9.5 Security | 5 topics, 14 threats | 18 | 1 | 0 |
| 9A Article 53 | 4 | 4 | 0 | 0 |
| 10 Extensibility | 3 | 3 | 0 | 0 |
| 11.1–11.4 Operations | 4 | 3 | 1 | 0 |
| 11A Federation | 6 | 6 | 0 | 0 |
| 11B–11E Engineering | 8 | 8 | 0 | 0 |
| 11F Frontend | 4 | 4 | 0 | 0 |
| 11G Skills | 6 | 6 | 0 | 0 |
| 11H Pipeline | 5 stages | 5 | 0 | 0 |
| 15 Decisions | 14 | 13 | 1 | 0 |
| 16A Custody | 1 | 1 | 0 | 0 |

One item is **NOT BUILT**; it is named in the sections below and repeated at
the end.

---

## 5.1 Deployable units

| Unit | Mark | Where |
|---|---|---|
| Control plane API | IMPLEMENTED | `draupnir/api/`, `make api` |
| Worker / orchestrator | IMPLEMENTED | `draupnir/core/application/orchestrator.py` and `draupnir/worker/`, `make worker` or `python -m draupnir.worker`. The orchestrator makes the state machine, the ledger write and the projection one transaction; the worker ticks, and each tick drives every run one step and performs whatever periodic duty of SAD 11.3 has come due. It holds nothing between ticks — SAD 11.2 row 1 — and it is safe to run the "two to four processes" SAD 5.1 asks for, because the chain serialises on the site's advisory lock and the guards refuse the loser of a race. `tests/integration/test_worker_loop.py` drives a curated run from QUEUED to AWAITING_APPROVAL with nobody asking. Since RF-43 it also takes the corpus half of SAD 6.1 for a run submitted through the API: it registers the run's corpus once a source is registered for its jurisdiction, and takes GLEIPNIR's licence decision through the installed `draupnir.policy` driver (`tests/integration/test_worker_licence_decision.py`). |
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

What is stronger than the specification asked for:

- `run` is a **projection** of the ledger rather than a table written directly.
  SAD 7.1 lists it as an entity; it is one, and it is derived. The table
  comment says so, because an operator with `psql` who assumes otherwise will
  eventually write to it.
- So are `artefact`, `approval` and `release`, since RF-33. Until then only
  the seed wrote them. A publication recorded a `published` entry and no row,
  so on an estate a release had no package to read or download and its lineage
  named no approval. They are folded from the chain on every append,
  beside `run`.
- So are `gate_result`, since RF-41, and the licence register, `source`, since
  RF-42. Until then only the seed wrote either: the approval queue's evidence,
  a model's gates and `/metrics` read a table an estate never filled, and a
  source registered through the API appeared in neither the register nor a
  release's lineage. Migration `0007` records the site whose chain registered
  each source, so a rebuild clears only its own site's rows.
- The three constraints of SAD 11C are enforced by the **database**: an
  append-only trigger, a foreign key and NOT NULL on `release.approval_id`, and
  row level security with `FORCE` on every scoped table.
- Vault reconciliation is `scripts/vault_admin.py reconcile`. It stages what a
  running job wrote to scratch only when those bytes hash to the digest the
  chain recorded, reports everything else, and deletes nothing but an abandoned
  staging tree.
- Retention is swept daily by the worker, which reads the 24 month rule out of
  the chain — a corpus, the runs that consumed it, and the last release derived
  from it — and records a proposal against any corpus past it. It never
  deletes: SAD 7.3 gives the deletion an approver and `hodd.retention` refuses
  an unapproved action, so a worker that deleted on a timer would be exactly
  the cron job that rule forbids.

## 8.1 API surface

**IMPLEMENTED.** 43 operations, every one with a role declaration, an
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
| `POST /v1/sources` | the facts HODD holds, with the DPIA determination and the residency constraint (RF-42) | `source` entry |
| `POST /v1/corpora/{iso3}/ingest` | the ingest | `corpus` entry |
| `POST /v1/corpora/{iso3}/curate` | the curation | `corpus` entry |
| `POST /v1/gates/{id}/decide` | AWAITING_APPROVAL → RELEASED or QUARANTINED | transition |
| `POST /v1/runs/{id}/cancel` | TRAINING → FAILED | transition |
| `POST /v1/runs/{id}/retry` | EVALUATING → QUEUED | transition |
| `POST /v1/releases/{artefact}/publish` | the publication, under its approval | `release` entry |
| `POST /v1/arrays` | the array and its elements | `array` entry |
| `POST /v1/arrays/{name}/elements/{index}/requeue` | the requeue of an element that stopped without completing | `array` entry |
| `POST /v1/retention/{action_id}/approve` | the approval; the retention duty carries it out | `corpus` entry |
| `POST /v1/sweeps/{run_id}/select` | the chosen merge point, among those RAUN passed | `sweep` entry |

`POST /v1/runs/dry-run` is the one mutating method that records nothing, by
design: it validates a specification and returns what submitting it would do.

A source, a corpus and a release are not runs — SAD 7.1 gives each its own
entity — so the run projector passes those entries through. They are not
passed over: since RF-33 a release, and since RF-42 a source, is folded by a
projection of its own on the same append. A decision, a cancellation and a
requeue are lifecycle transitions, so they go through the state machine, which
checks them against SAD 6.1.

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

**Publication's controls are on the request path.** The table above says what
`publishRelease` records. For a time that was all it did: the controls AC-S8,
AC-F9 and AC-S13 describe lived in `skidbladnir.publish`, and no request path
called them. Since RF-05 the endpoint calls it before anything is recorded:
- the artefact bytes are re-hashed;
- the gates and the approval are checked;
- the anchor must be current.

Each refusal is a 409 naming the control that refused — `artefact-mismatch`,
`artefact-ungated`, `release-unapproved`, `anchor-behind` — and a store outage
is a 503, because a refusal is final and an outage is a retry.

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
| `draupnir.policy` | `gleipnir.spdx/v1` — **written for AC-D2 in this prompt**; and `gleipnir.licence/v1`, GLEIPNIR's policy in force, installed by this distribution since RF-43 |

The store and policy points had no implementation until now, because HODD
addresses its own vault and GLEIPNIR decides its own policy directly. An
extension point nobody has extended is an extension point whose Protocol nobody
has read from the outside, and writing the two found nothing wrong with either
Protocol — which is worth recording as a result rather than assumed.

Since RF-43 GLEIPNIR's own policy is installed as a driver too,
`gleipnir.licence/v1`. The worker takes a run's licence decision through the
driver `DRAUPNIR_POLICY_DRIVER` names, and a release it clears renders its
copyright policy under the version the decision recorded, which has to be one
`licence.by_version` holds; the reference driver's is not.

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

**IMPLEMENTED**: transport security, SAD 9.5's "TLS 1.3 only, mTLS between
control plane components" (RF-37). This section was marked IMPLEMENTED while
nothing terminated TLS, which RF-30 corrected to NOT BUILT. It is now built:
- the console proxy (`docker/nginx.conf`) terminates TLS 1.3 only, and has no
  plain HTTP listener to fall back to;
- the proxy passes requests to the API over TLS 1.3, verifies the API's
  certificate against the internal CA, and presents its own;
- the API (`draupnir.api.serve`, the image's command) serves TLS 1.3 only, and
  refuses a connection with no client certificate from the internal CA;
- GULLINBURSTI's client presents its site certificate to MEGINGJORD and
  verifies MEGINGJORD's against the same CA.

The evidence:
- `tests/contract/test_transport.py` starts the proxy in its base image and
  has it refuse a TLS 1.2 handshake. It also starts the API and has it refuse
  a connection with no client certificate, one from another CA, and TLS 1.2.
- `tests/integration/test_transport.py` passes a request through the proxy to
  the API over mTLS. The API refuses the same proxy without its certificate.
  It reaches the API through the test's host, not the address a deployment
  uses.
- `python tasks.py images-transport`, stage 3.1a, starts both built images the
  way `deploy/units/draupnir-run.sh` starts them — one network, the addresses
  `deploy/lib.sh` gives them — and proxies a request through to the API over
  mTLS (RF-44). Until RF-44 the proxy's upstream was its own container's
  loopback, so on a commissioned host every proxied request answered 502; and
  until RF-45 the console image could not be built at all.
- The cryptographic inventory's TLS row is derived from what
  `docker/nginx.conf` declares, not from whether a certificate path is set.
- `install.sh --check` loads every certificate in the image that will read it.

What the estate supplies is the certificates, from the internal CA of Decision
S9. `docs/runbook.md`, Certificates, says which, what each must carry, and who
reads each file.

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
| 11.3 Observability | DEVIATED | Every signal has a source and a surface, and the four with a clock now have something running them: the worker verifies the chain hourly, dispatches the **fabric bandwidth probe** hourly, reads vault capacity, and checks anchor freshness, alarming at the thresholds the table states. Two deviations, both on CON-B. **Appliance thermal and throttle** are read from the DCGM exporter through the site's Prometheus (`GET /v1/estate/telemetry`) and render as a temperature and a throttle state; they depend on that Prometheus being reachable, and read *unmeasured*, with the reason, where it is not — which is every development machine and every CI runner. **The fabric probe** runs only where `all_reduce_perf` is configured, which no control plane is; its reading and the commissioned baseline are exposed on `/metrics` and rendered against each other on dashboard 2 (RF-28), and until the probe runs on the estate and ALVISS is a scrape target the dashboard says unmeasured, and why. |
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

**IMPLEMENTED.** JARNGREIPR with 30 components (19 primitives and 11 composites) at seven states each,
the four primary journeys as Playwright acceptance evidence, the interaction
requirements, and accessibility as an acceptance criterion rather than a review
comment (Decision S13). 220 Storybook stories at zero serious or critical axe
violations, and 23 routes likewise.

The manual keyboard pass of AC-U5 is in `keyboard-pass.md`, with three findings,
none of them open. K-1, unavailable controls leaving the tab ring, was closed
by RF-29.

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

## Reachability

REACHABLE is derived by `scripts/reachability.py`, and
`tests/unit/test_reachability.py` fails when a mark below disagrees with it, or
when a platform module nothing reaches is missing from this table.

A module is REACHABLE when it is imported, directly or through other modules,
from something a deployment runs:
- the API application, `draupnir.api.app`, and `draupnir.api.serve`, which
  serves it over mTLS and is the API image's command (RF-37);
- the worker, `draupnir.worker.__main__`;
- `draupnirctl`, through `draupnirctl.__main__`;
- the approver's signing agent, `draupnir.gleipnir.signing_agent`, which runs
  on the approver's own machine rather than on the forge (RF-40);
- an installed plug-in's entry point;
- a migration.

The analysis is static and conservative in one direction. An import inside a
function counts whether or not the function is called, so REACHABLE is a
necessary condition, not proof of use. NOT REACHABLE is a finding. To see the
path by which a module is reached:

```bash
python -m scripts.reachability draupnir.svalinn.pki
```

**The remedial register's orphans, re-marked.** Section 2 of
`docs/fixes/remedial-fixes.md` lists 24 modules no importer outside `tests/`
reached. The register says twenty-three of them, and nineteen once it sets
aside four as correct, but its own list has 24 names and 20 after the four. Of
those 20, 13 are now reachable and 7 are not:

| Module | Mark | Note |
|---|---|---|
| `draupnir.api.assurance` | **REACHABLE** | |
| `draupnir.gleipnir.copyright` | **REACHABLE** | |
| `draupnir.hamarr.config` | **REACHABLE** | |
| `draupnir.hodd.quota` | **REACHABLE** | |
| `draupnir.motsognir.arrays` | **REACHABLE** | |
| `draupnir.skidbladnir.publish` | **REACHABLE** | on the publish request path since RF-05 |
| `draupnir.svalinn.egress` | **REACHABLE** | |
| `draupnir.svalinn.integrity` | **REACHABLE** | |
| `draupnir.svalinn.pki` | **REACHABLE** | |
| `draupnir.svalinn.sandbox` | **REACHABLE** | reached; the profile is still not applied to a process, as 9.1–9.5 says |
| `draupnir.svalinn.scanning` | **REACHABLE** | |
| `draupnir.svalinn.secrets` | **REACHABLE** | |
| `draupnir.svalinn.signing` | **REACHABLE** | |
| `draupnir.brisingamen.merge` | **NOT REACHABLE** | merge planning and adapter-to-dense export; the worker merges through the `draupnir.merge` plug-in instead |
| `draupnir.megingjord.anchors` | **NOT REACHABLE** | chain continuity before a head is countersigned |
| `draupnir.megingjord.registry` | **NOT REACHABLE** | the site registry, policy distribution and the signing trust root |
| `draupnir.motsognir.retry` | **NOT REACHABLE** | retry and backoff |
| `draupnir.raun.regression` | **NOT REACHABLE** | noticing that a release is worse than the last |
| `draupnir.raun.transitions` | **NOT REACHABLE** | evidence into the facts the state machine's guards ask for |
| `draupnir.skidbladnir.formats` | **NOT REACHABLE** | export formats and the cross-platform quantisation check |

The four the register set aside:

| Module | Mark | Note |
|---|---|---|
| `draupnir.worker.__main__` | **REACHABLE** | an entry point itself |
| `draupnir.interfaces.testing.suite` | **NOT REACHABLE** | by design: the conformance harness a driver package's own tests inherit |
| `draupnir.core.infrastructure.models` | **NOT REACHABLE** | nothing a deployment runs imports it; the schema is carried by the migrations, and only tests read these models. The register's "reached through SQLAlchemy metadata" does not hold |
| `draupnir.svalinn.inventory` | **NOT REACHABLE** | by design: a build artefact, produced by `tasks.py crypto-inventory` |

**Unreachable modules the register did not list.** Its analysis stopped at "has
an importer outside `tests/`". Being imported only by another unreachable
module, or only by a script, is the same state:

| Module | Mark | Note |
|---|---|---|
| `draupnir.megingjord` | **NOT REACHABLE** | the federation registry's package: nothing a deployment runs imports any of MEGINGJORD |
| `draupnir.brisingamen.routes` | **NOT REACHABLE** | imported only by `brisingamen.merge`, which is itself unreachable |
| `draupnir.gullinbursti.roster` | **NOT REACHABLE** | what each machine at the forge is for (RF-E20) |
| `draupnir.motsognir.supply_adapters` | **NOT REACHABLE** | a supply daemon's output rendered into the status contract (RF-E18) |
| `draupnir.svalinn.containment` | **NOT REACHABLE** | how a training job is contained at a forge |
| `draupnir.svalinn.site_egress` | **NOT REACHABLE** | by design: commissioning, through `scripts/egress_policy.py` |
| `draupnir.procedures` | **NOT REACHABLE** | by design: the acceptance procedures, through `scripts/procedure.py` |
| `draupnir.procedures.sindri` | **NOT REACHABLE** | by design, as above |
| `draupnir.interfaces.testing` | **NOT REACHABLE** | by design: the conformance harness |
| `draupnir.interfaces.testing.fixtures` | **NOT REACHABLE** | by design, as above |
| `draupnir.interfaces.testing.harness` | **NOT REACHABLE** | by design, as above |

Of the 21 modules marked NOT REACHABLE, 8 are not deployment code by design and
13 are platform code a running deployment never loads. Seven of those thirteen
are the federation, evaluation, export, merge and retry controls this document
otherwise marks IMPLEMENTED — which is what IMPLEMENTED alone could not say.

**Re-run after RF-45.** The analysis was run again on 17 September 2026, once
RF-32 to RF-45 had landed. No mark changed: the same modules are unreachable,
and every module those findings added is reachable — among them
`gleipnir.clearance`, the projections of RF-41 and RF-42, and the signing agent.
What had gone stale was the prose beside the tables. The roots above named
neither `draupnir.api.serve` nor the signing agent, both of which the analysis
already counted, and the summary's counts added up to 26 against 21 marks.
`tests/unit/test_reachability.py` now derives both, so neither can go stale by
hand again.

---

## The one that is not built

### NOT BUILT 1 — Three non-functional targets are unmeasured

AC-N1 (control plane overhead on ALVISS), AC-N2 (step time within one per cent)
and AC-N11 (anchor round trip over WireGuard) are measurements on hardware that
does not exist. They are commissioning measurements. Nothing here estimates
them, because an estimate recorded in an acceptance pack is read as a
measurement.

Transport security was NOT BUILT 2 until RF-37 built it: TLS 1.3 only at the
console proxy, and mTLS from the proxy to the API and from GULLINBURSTI to
MEGINGJORD. The evidence is under 9.1–9.5 above.

---

## What this reconciliation changed

Seven items moved from NOT BUILT to IMPLEMENTED, because reading the
specification against the code is what found them:

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
6. **The worker.** SAD 5.1 named a deployable unit that did not exist, so a
   curated run stopped at `QUEUED` until somebody ran `make procedure`. It is
   `draupnir/worker/`: a tick that reads the chain, does one thing to each run,
   and performs the periodic duties of SAD 11.3. Building it closed the
   retention sweep and the fabric probe with it, because those were missing for
   the same reason — nothing owned a clock.
7. **Vault reconciliation.** SAD 11.2 row 4's recovery column reads "restore
   NFS, run reconciliation" and there was no reconciliation, so staging what
   running jobs wrote to scratch was a manual copy. It is
   `draupnir/hodd/reconcile.py` and `scripts/vault_admin.py`. Building it found
   a second instance of the defect that row already carried: `total_bytes`
   created the vault root in order to measure it, so an unmounted vault
   reported the control plane's own disk capacity and every quota check passed
   (AC-S10). It also produced the `.hodd-vault` marker, which tells a dropped
   mount apart from a directory somebody created on the mount point — the state
   the runbook warns against and nothing could previously detect.

And one defect was found by injecting a failure rather than by reading:
**an unmounted HODD vault was silently recreated on the control plane's local
disk** by `put`, because it creates the artefact's parent directories. A run
would have trained and staged its weights somewhere nobody backs up, and the
vault returning later would have hidden it. Every store operation now checks
that the root is present first.
