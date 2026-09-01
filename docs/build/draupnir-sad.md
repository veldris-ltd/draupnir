<!--
  VLD-SAD-DRAUPNIR-001 Rev 1.4
  CONFIDENTIAL - RECIPIENT EYES ONLY
  Veldris Ltd, company no. 17366869
-->

# DRAUPNIR

## Solutions Architecture Document
### CIM-56 Model Factory Control Plane

> **CONFIDENTIAL — RECIPIENT EYES ONLY**

One application governing corpus, training, reweighting, evaluation, quantisation and release for the fifty six Midgard Commonwealth Intelligence Models.

| Field | Value |
|---|---|
| Document reference | VLD-SAD-DRAUPNIR-001 |
| Revision | 1.4 |
| Status | Issued for build. Forward specification, not a repository survey |
| Date of issue | 28 August 2026 |
| Author and owner | JB Benjamin, Chief Executive Officer |
| Asset owner | Veldris Ltd, company no. 17366869, 128 City Road, London EC1V 2NX |
| Programme | Midgard Suite, CIM-56 |
| Companion documents | VLD-INF-SINDRI-001 Rev 3.3; VLD-UX-DRAUPNIR-001 Rev 1.0 |
| Structure | arc42 and TOGAF ordering, C4 decomposition, STRIDE threat model |
| Supersedes | VLD-SAD-DRAUPNIR-001 Rev 1.0 through Rev 1.3 |

### Revision history

| Rev | Date | Author | Summary of change |
|---|---|---|---|
| 1.0 | 2026-08-28 | JB Benjamin | Initial issue. Architecture, security, extension model, acceptance criteria and build prompts |
| 1.1 | 2026-08-28 | JB Benjamin | All open questions closed. Multi-forge federation added (GULLINBURSTI, MEGINGJORD). EU AI Act Article 53 compliance section added. Cryptography moved from FIPS to ISO/IEC and NCSC, dedicated control host withdrawn, public Sigstore replaced by internal PKI. Tier A jurisdictions fixed. Retention set at 24 months. Distillation removed from Release 1 scope |
| 1.2 | 2026-08-28 | JB Benjamin | Forge Matrix terminology introduced with Sindri as Site 0. Site, node and rank separated as terms (Decision S12). Q9 closed: control concentrations accepted for Site 0 under standing acceptance VLD-RA-SINDRI-001 with a defined review trigger |
| 1.3 | 2026-08-28 | JB Benjamin | Full stack engineering specification added as Part 3A. Acceptance criteria extended with backend, experience and quality sets. Prompts restructured to twelve. Q10 closed |
| 1.4 | 2026-08-28 | JB Benjamin | Frontend scope devolved to VLD-UX-DRAUPNIR-001, which supersedes Prompts 8 and 9 in detail. Effort revised accordingly |

### Distribution and handling

This document is confidential to Veldris Ltd and is issued to named recipients only. It specifies software that governs the production of commercially released artefacts and describes the security controls protecting them. It is not to be reproduced, forwarded or disclosed in whole or in part without the written authority of the author.

---

## Contents

- **[Part 1  Introduction and constraints](#part-1-introduction-and-constraints)**
  - [1  Purpose, scope and status](#1-purpose-scope-and-status)
  - [2  Stakeholders and drivers](#2-stakeholders-and-drivers)
  - [3  Solution strategy](#3-solution-strategy)
- **[Part 2  Architecture](#part-2-architecture)**
  - [4  System context, C4 level 1](#4-system-context-c4-level-1)
  - [5  Container and module view, C4 level 2](#5-container-and-module-view-c4-level-2)
  - [6  Lifecycle and workflow](#6-lifecycle-and-workflow)
  - [7  Data architecture](#7-data-architecture)
  - [8  Interface architecture](#8-interface-architecture)
  - [11A  Federation architecture](#11a-federation-architecture)
- **[Part 3  Security, extensibility and operations](#part-3-security-extensibility-and-operations)**
  - [9  Security architecture](#9-security-architecture)
  - [9A  EU AI Act compliance](#9a-eu-ai-act-compliance)
  - [10  Extensibility](#10-extensibility)
  - [11  Operational architecture](#11-operational-architecture)
- **[Part 3A  Full stack engineering specification](#part-3a-full-stack-engineering-specification)**
  - [11B  Component decomposition of the core, C4 level 3](#11b-component-decomposition-of-the-core-c4-level-3)
  - [11C  Data model](#11c-data-model)
  - [11D  End to end sequence](#11d-end-to-end-sequence)
  - [11E  Backend engineering standards](#11e-backend-engineering-standards)
  - [11F  Frontend and experience specification](#11f-frontend-and-experience-specification)
  - [11G  Development skills](#11g-development-skills)
  - [11H  Continuous integration and delivery](#11h-continuous-integration-and-delivery)
- **[Part 4  Build specification](#part-4-build-specification)**
  - [12  Acceptance criteria](#12-acceptance-criteria)
  - [13  Build prompts](#13-build-prompts)
  - [14  Delivery sequence and effort](#14-delivery-sequence-and-effort)
  - [15  Resolved decisions](#15-resolved-decisions)
  - [16  Questions arising from those answers](#16-questions-arising-from-those-answers)
  - [16A  Site 0 standing risk acceptance](#16a-site-0-standing-risk-acceptance)

---
---

# Part 1  Introduction and constraints

## 1  Purpose, scope and status

### 1.1  Purpose

This document specifies the architecture of DRAUPNIR, a control application that governs the production of the fifty six Midgard Commonwealth Intelligence Models (CIM-56) from permissively licensed open weight base models, through a single repeatable and auditable pipeline.

DRAUPNIR automates the procedures currently described as manual sequences in VLD-INF-SINDRI-001 Parts 4 and 5. Those procedures are correct but are executed by hand, which means the audit record depends on operator discipline. DRAUPNIR makes the audit record a property of the system rather than of the operator.

### 1.2  Status of this document

> **This is a forward specification, not a survey of existing code.** No DRAUPNIR repository exists at the time of writing. Every component, interface, schema and control described here is **SPECIFIED**, meaning it is a requirement on the implementation, and none is **OBSERVED**, meaning read from a repository.
>
> The Veldris standard for a Solutions Architecture Document is the Imhotep structure, whose governing rule is that every architectural claim must be traceable to a real artefact in a repository. That rule cannot be satisfied before the code exists, and inventing citations to satisfy the form would defeat its purpose. This document therefore adopts the Imhotep **structure** (arc42 and TOGAF section ordering, C4 decomposition, dual format diagrams, DOCX with a Markdown companion) while replacing the citation column with a specification status column.
>
> When the first release of DRAUPNIR is cut, this document is to be re-run through Imhotep proper against the repository, and each **SPECIFIED** item confirmed as **IMPLEMENTED**, **DEVIATED** with reasons, or **NOT BUILT**. That comparison is the acceptance evidence for the build.

### 1.3  Scope

In scope: the control plane application, its modules, its data model, its interfaces to the Sindri Forge infrastructure, its security architecture, its extension model, and the acceptance criteria and build prompts by which it is to be produced.

Out of scope: the Sindri Forge hardware and operating system build, which is VLD-INF-SINDRI-001; the Midgard Suite applications that consume released models; and corpus acquisition contracts.

### 1.4  Naming

DRAUPNIR is the ring that, in the Norse sources, drips eight further rings of equal weight every ninth night. The name is taken for a system whose function is to turn one shared substrate into fifty six derived models. It is a dwarf made work and therefore available under the Veldris naming standard recorded in VLD-INF-SINDRI-001 section 1.5, which reserves gods and realms to the Midgard product line.

| Module | Name source | Responsibility |
|---|---|---|
| **DRAUPNIR Core** | The ring | Workflow state machine, run registry, audit ledger, event bus, plug-in loader, API |
| **HODD** | The hoard | Corpus and artefact store. Immutable ingest, hashing, licence register, retention |
| **GLEIPNIR** | The fetter that bound the wolf | Policy and assurance gates. Licence, data protection, approval, release sign off |
| **MOTSOGNIR** | The first and greatest of dwarves, who shaped the rest | Job dispatch and placement. Scheduler drivers, array concurrency, retry policy |
| **HAMARR** | The hammer | Training executors. Framework drivers for substrate and adapter runs |
| **BRISINGAMEN** | The necklace made by four dwarves working together | Reweighting and merge. Merge method drivers, weight sweeps, release route selection |
| **RAUN** | Trial, ordeal | Evaluation and assurance. Gate suite, baselines, regression, quantisation checks |
| **SKIDBLADNIR** | The ship that folds into a pouch | Quantisation, packaging and release. Format conversion, SBOM, model card, lineage |
| **SVALINN** | The shield that stands before the sun | Cross cutting security. Identity, authorisation, secrets, signing, sandboxing |
| **GULLINBURSTI** | The boar that ran over sky and sea | Per forge site agent. Anchors the local ledger head, pulls policy, pushes release metadata, reports capacity |
| **MEGINGJORD** | The belt of strength that binds and augments | Federation registry. Global model registry, cross forge ledger anchors, policy distribution, identity issuer, signing trust root |

---

## 2  Stakeholders and drivers

### 2.1  Stakeholders

| Stakeholder | Concern | What the architecture must give them |
|---|---|---|
| Forge operator | Submit, monitor and recover runs without memorising a procedure manual | One application, one run board, safe retry, clear failure diagnosis |
| Corpus curator | Register sources with their licences and prove provenance | Immutable ingest, per source licence register, hash manifests |
| Release approver | Sign off only what has passed every gate | Gate queue with the evidence attached, no path to release without sign off |
| Auditor and counsel | Reconstruct how any released model was made | Append only ledger, lineage attestation, licence chain, read only access |
| Veldris as vendor | Sell models whose provenance withstands due diligence | Provenance as a build time artefact rather than a reconstruction exercise |
| Customer under regulation | Evidence of data protection and licence compliance | Model card, SBOM, DPIA reference, corpus licence list per release |

### 2.2  Architectural drivers

| Ref | Driver | Origin |
|---|---|---|
| D-1 | Repeatability. The same inputs must produce the same pipeline behaviour, and every run must be re-derivable from recorded state | Programme requirement |
| D-2 | Auditability. Every state transition must be attributable to an actor and an input hash, and the record must be tamper evident | Commercial positioning on sovereignty |
| D-3 | Extensibility. New base model families, training frameworks, merge methods, evaluation suites and export formats must be addable without modifying the core | The open weight landscape changed substantially between Rev 1.0 and Rev 3.0 of the infrastructure manual |
| D-4 | Security. The system handles unlicensed third party corpora, personal data and commercially valuable weights on a node with no redundancy | Risk register R5, R6, R10 in VLD-INF-SINDRI-001 |
| D-5 | Sovereignty. No training telemetry, corpus content or artefact may leave the installation other than through an explicit, logged, approved egress | Veldris market position |
| D-6 | Operability at small scale. One or two people run this. Ceremony that does not earn its keep will be bypassed | Team size |
| D-7 | Graceful degradation. Loss of one appliance, the scheduler, or the network must not lose recorded state or corrupt an in flight run | Infrastructure has no compute redundancy |
| D-8 | Regulatory evidence. Article 53 documentation must be a by product of the pipeline, not a document written afterwards from memory | Constraint C-10 |
| D-9 | Federation. A forge must operate through a wide area network outage and reconcile on reconnect | Constraint C-8 |

### 2.3  Constraints

| Ref | Constraint | Consequence for the architecture |
|---|---|---|
| C-1 | Executes on Sindri Forge: three GB10 appliances (aarch64, DGX OS), two Apple silicon hosts, one Raspberry Pi | The control plane must run on aarch64 and on macOS. No x86 assumption |
| C-2 | Slurm is the scheduler, with the controller on REGIN | MOTSOGNIR drives Slurm first. Ray is a second driver, not a replacement |
| C-3 | 273 GB/s memory bandwidth per appliance is the binding performance constraint | The control plane must not add measurable overhead to a training step. All orchestration is out of band |
| C-4 | Egress is restricted to an allow list at the site boundary | Every outbound call must be declared, and the system must function when the allow list denies an unexpected destination |
| C-5 | No baseboard management controller on the appliances | DRAUPNIR cannot power cycle a node. Recovery is drain, diagnose, resume |
| C-6 | Single operator team | Approval workflow must support a documented single approver with a recorded exception, rather than mandating two person control it cannot staff |
| C-7 | The vault is a single 4 TB volume until gap G2 is closed. An offline backup target and an uninterruptible power supply are on order | HODD must enforce retention and quota, and must fail a run cleanly rather than filling the volume. When the supply arrives, its signalling drives a forced checkpoint on mains loss |
| C-8 | The design must support several forges at one location and several locations. Sindri is the first of an intended estate | Site scope is a property of every entity from the first schema, not a later migration. Federation is specified in section 11A |
| C-9 | Cryptography follows ISO/IEC and NCSC guidance. FIPS validation is not required | ISO/IEC 19790 conformant modules where available, NCSC recommended algorithms, crypto agility for the post quantum migration. The control plane therefore remains on ALVISS |
| C-10 | Veldris places the CIM-56 models on the EU market and is a provider of general purpose AI models under Regulation (EU) 2024/1689 | Article 53 obligations apply to every model in the set from Release 1. Specified in section 9A |
| C-11 | Release approval is held by a single named individual, the creator, codename Akuma | Separation of duties is unavailable. The single approver exception is recorded on every release and is visible in lineage. Compensating controls carry the assurance weight |
| C-12 | Sindri is Site 0 of the Forge Matrix, the intended estate of linked forges. Control concentrations acceptable at Site 0 are accepted under a dated standing acceptance with a defined review trigger, recorded in section 16A | The architecture does not design the concentration away. It records it, and defines the conditions under which it must be revisited |

---

## 3  Solution strategy

| Driver | Strategy |
|---|---|
| D-1 Repeatability | Every run is defined by a declarative specification file under version control. The specification, not the operator's shell history, is the unit of reproduction. Runs are identified by a content hash of the specification plus the hashes of every input artefact |
| D-2 Auditability | A single append only, hash chained ledger records every state transition. Nothing mutates state except through a ledger writing transition. The ledger is the system of record; the relational tables are a materialised view of it and can be rebuilt from it |
| D-3 Extensibility | Every pipeline stage is a plug-in behind a versioned Python interface, discovered through entry points. The core knows the interface, never the implementation. A new base family or merge method is a new plug-in package, not a core change |
| D-4 Security | SVALINN is a cross cutting layer rather than a module in the flow: identity at the edge, authorisation at every API call, secrets brokered as short lived tokens, plug-ins signed and capability scoped, artefacts signed at release |
| D-5 Sovereignty | All state, tracking and artefacts are local. Every outbound call passes through one declared egress broker that logs destination, purpose, run and approver |
| D-6 Operability | Two interfaces over one API: a web console for the run board and gate queue, and `draupnirctl` for scripting. Gates that would be bypassed are made cheap rather than made optional |
| D-8 Regulatory evidence | The training content summary, copyright policy and technical documentation required by Article 53 are rendered by SKIDBLADNIR from the licence register and lineage that the pipeline already maintains. No separate compliance authoring step exists, because a step that is separate is a step that drifts |
| D-9 Federation | Each forge holds its own hash chained ledger segment and anchors its chain head to MEGINGJORD. A forge that loses the network continues to train, evaluate and record. Release is blocked until the head re anchors, because release depends on the federation trust root |
| D-7 Degradation | The core is stateless between requests. A run in flight survives a control plane restart because its state is in the ledger, not in memory. Scheduler loss suspends dispatch without failing runs |

**DESIGN DECISION S1. The ledger is the system of record and the relational schema is a projection of it.**

Rationale. Driver D-2 requires that the audit record be tamper evident. If the relational tables were authoritative, an operator with database access could alter history without trace. Making the hash chained ledger authoritative means any divergence between the ledger and the projection is detectable by replay.

Alternative rejected. A conventional relational schema with an audit table alongside it, which is simpler to implement but produces an audit trail that can be edited independently of the data it describes.

**DESIGN DECISION S2. DRAUPNIR orchestrates the existing tools rather than reimplementing them.**

Rationale. LLaMA-Factory, mergekit, lm-evaluation-harness, TensorRT Model Optimizer and llama.cpp are each maintained by their own communities and move faster than a single company can track. Wrapping them behind a driver interface keeps DRAUPNIR small and lets the training stack be upgraded independently.

Alternative rejected. A monolithic trainer owning its own training loop, which would give tighter control and better error messages at the cost of permanently trailing the ecosystem.

**DESIGN DECISION S3. The control plane runs on ALVISS, not on an appliance and not on a dedicated host.**

An earlier draft moved the control plane to a dedicated Linux host to satisfy a FIPS 140-3 requirement. Constraint C-9 removed that requirement, so the dedicated host is withdrawn rather than retained for its own sake.

Rationale. The appliances are the scarce resource. Placing a long lived web service, database and object store on a training node consumes memory that the training workload needs and makes the control plane a casualty of an out of memory kill. ALVISS is already the evaluation and control host and is idle during ring training.

Alternative rejected. Running the control plane on REGIN alongside Slurm, which concentrates too much on a Raspberry Pi and couples the scheduler's availability to the control plane's. Also rejected: a dedicated control host, which is justified only by a validated cryptography requirement that no longer applies. The federation tier described in section 11A is a separate concern and lives outside every forge.

**DESIGN DECISION S7. Site scope is present in the data model from the first migration.**

Rationale. Constraint C-8 makes a multi forge estate a stated intention rather than a hypothetical. Adding a site dimension to a populated ledger and artefact store later is a data migration across every table and every historical run specification. Adding it at the start costs one column and one resolution rule.

Alternative rejected. Single installation now, federate later, which is cheaper this month and expensive in the month it is needed.

---

# Part 2  Architecture

## 4  System context, C4 level 1

![Figure 1. DRAUPNIR system context](diagrams/07_draupnir_c4_context.png)

| External element | Direction | Purpose | Trust |
|---|---|---|---|
| Forge operator | Inbound | Submit, monitor, cancel and retry runs | Authenticated, role scoped |
| Corpus curator | Inbound | Register sources, declare licences, trigger curation | Authenticated, role scoped |
| Release approver | Inbound | Approve or reject GLEIPNIR gates | Authenticated, elevated role |
| Auditor or counsel | Outbound | Read the ledger, lineage and model cards | Authenticated, read only |
| Slurm on REGIN | Outbound | Job submission, status polling, cancellation | Internal, trusted |
| DVALIN, DURIN, DAIN | Indirect | Execute training, merge, evaluation and quantisation jobs | Internal, semi trusted execution |
| HODD vault on ANDVARI | Bidirectional | Corpora, weights, adapters, checkpoints, releases | Internal, encrypted at rest |
| MLflow and PostgreSQL | Bidirectional | Run metrics and metadata | Internal |
| Prometheus, Grafana, Loki | Inbound | Infrastructure telemetry surfaced on the run board | Internal, read only |
| Hugging Face Hub | Outbound | One shot base weight acquisition, hashed and archived | External, allow listed |
| PyPI, GitHub, NGC | Outbound | Dependency and container image acquisition | External, allow listed |
| MEGINGJORD federation registry | Bidirectional | Ledger anchoring, policy distribution, identity, signing trust root | Veldris operated, mTLS over WireGuard |
| Teacher model API | Outbound | Distillation data generation. **Out of scope for Release 1** and not allow listed | External, not enabled |

## 5  Container and module view, C4 level 2

![Figure 2. DRAUPNIR containers and modules](diagrams/08_draupnir_c4_container.png)

### 5.1  Deployable units

| Unit | Technology | Host | Scaling |
|---|---|---|---|
| `draupnir-api` | Python 3.12, FastAPI, uvicorn | ALVISS | Single instance. Stateless between requests |
| `draupnir-worker` | Python 3.12, async task runner | ALVISS | Two to four processes. Poll the ledger for actionable transitions |
| `draupnir-web` | React and TypeScript, served static | ALVISS | Static assets behind the API |
| `draupnirctl` | Python CLI, single binary via `uv tool` | Any host | Per invocation |
| PostgreSQL 16 | Existing instance | ANDVARI | Single instance. Ledger and projection |
| MinIO | Existing instance | ANDVARI | Single instance. Artefact object store |
| Executor shims | Rootless containers | DVALIN, DURIN, DAIN | One per job, scheduled by Slurm |

### 5.2  Module responsibilities and boundaries

| Module | Owns | Must not |
|---|---|---|
| **Core** | Workflow state machine, run registry, ledger, event bus, plug-in loader, API surface | Know any framework, format or merge method by name |
| **HODD** | Ingest, hashing, immutability, licence register, retention, quota, object and file layout | Interpret licence terms. It records them; GLEIPNIR judges them |
| **GLEIPNIR** | Gate definitions, policy evaluation, approval workflow, release sign off, exception recording | Execute pipeline work. It permits or refuses only |
| **MOTSOGNIR** | Scheduler drivers, placement policy, array concurrency, retry and backoff | Know what a job computes |
| **HAMARR** | Framework drivers, run configuration rendering, checkpoint policy, log capture | Choose which model to train, or judge the result |
| **BRISINGAMEN** | Merge method drivers, weight sweep execution, adapter to dense export | Decide whether a merge is acceptable. RAUN decides |
| **RAUN** | Gate suite execution, baseline management, comparison, regression detection | Change any artefact |
| **SKIDBLADNIR** | Format conversion, SBOM generation, model card rendering, lineage attestation, registry publish | Publish without a GLEIPNIR release approval |
| **SVALINN** | Identity, authorisation decisions, secret brokerage, plug-in signature verification, artefact signing, egress brokerage | Contain pipeline logic |

**DESIGN DECISION S4. GLEIPNIR judges and never executes; HODD records and never judges.**

Rationale. Separating the recording of a fact from the judgement about that fact means a licence policy change is a GLEIPNIR configuration change and never requires re-ingesting a corpus. It also means the provenance record stays neutral and remains valid if the policy is later found to have been wrong.

Alternative rejected. Licence evaluation inside the ingest path, which is fewer moving parts but bakes a policy decision into an immutable record.

## 6  Lifecycle and workflow

![Figure 3. Model lifecycle state machine](diagrams/09_draupnir_state.png)

### 6.1  Transition rules

| From | To | Guard | Ledger entry |
|---|---|---|---|
| DRAFT | CORPUS_REGISTERED | Every source has a licence declaration and a personal data determination | Source list, per source SHA-256, curator identity |
| CORPUS_REGISTERED | LICENCE_CLEARED | GLEIPNIR licence policy passes for every source and for the base model | Policy version, evaluation result, approver where manual |
| CORPUS_REGISTERED | QUARANTINED | Any source fails licence policy | Failing source, rule, actor |
| LICENCE_CLEARED | CURATED | Curation pipeline completes; decontamination confirmed against the evaluation sets | Stage retention rates, output hash, token count |
| CURATED | QUEUED | A run specification exists and validates | Specification hash, input artefact hashes |
| QUEUED | TRAINING | MOTSOGNIR obtains an allocation | Scheduler job id, node, placement decision |
| TRAINING | TRAINED | Executor exits zero and the final checkpoint hashes | Checkpoint hash, step count, final loss |
| TRAINING | FAILED | Executor exits non zero, or the watchdog fires | Exit code, last log lines, resource state |
| TRAINED | EVALUATING | RAUN suite resolves for the artefact type | Suite version, baseline reference |
| EVALUATING | MERGED | Gates E1 to E6 pass | Per gate result and margin |
| EVALUATING | QUEUED | Any gate fails and the retry budget is not exhausted | Failing gate, requeue reason |
| MERGED | QUANTISED | Re-gate of the merged artefact passes | Merge configuration hash, sweep result |
| QUANTISED | AWAITING_APPROVAL | Re-gate of every quantised build passes | Per format gate results |
| AWAITING_APPROVAL | RELEASED | A human with the approver role signs off | Approver identity, signature, timestamp |
| AWAITING_APPROVAL | QUARANTINED | Approver rejects | Rejection reason |

> Artefacts are never deleted by a transition. QUARANTINED means withdrawn from release and retained with its ledger history. Deletion is a separate, explicitly approved retention action recorded in the ledger.

### 6.2  Run specification

The run specification is the unit of reproduction. It is version controlled, validated against a JSON Schema, and hashed into the run identity.

```
apiVersion: draupnir/v1
kind: AdapterRun
metadata:
  name: cim-gbr-v0.1
  jurisdiction: GBR
  tier: A
spec:
  base:
    artefact: hodd://models/core/MIDGARD-CORE-QWEN36-35B-A3B-v1.0
    expectSha256: "…"
  dataset:
    artefact: hodd://corpora/GBR/curated
    expectSha256: "…"
    cutoffPercentile: 99
  train:
    driver: hamarr.llamafactory/v1
    method: lora
    params: { rank: 64, alpha: 128, dropout: 0.05, epochs: 3, lr: 1.0e-4 }
    precision: bf16
  placement:
    driver: motsognir.slurm/v1
    partition: adapters
    nodes: 1
    maxConcurrent: 3
    retryBudget: 2
  evaluate:
    driver: raun.lmeval/v1
    suites: [general-core, cim-gbr]
    gates: [E1, E2, E3, E4, E5, E6]
    baseline: run://MIDGARD-CORE-QWEN36-35B-A3B-v1.0
  release:
    route: B
    formats: [nvfp4, gguf-q4km, mlx4]
    approval: required
```

## 7  Data architecture

### 7.1  Core entities

| Entity | Key attributes | Notes |
|---|---|---|
| `ledger_entry` | id, **site_id**, seq, prev_hash, entry_hash, ts, actor, subject_type, subject_id, transition, payload | Append only, one chain per site. `entry_hash = H(prev_hash ‖ canonical(payload))` |
| `artefact` | id, **site_id**, **locality**, kind, uri, sha256_manifest, size, created_from_run, immutable_at | Kinds: corpus_raw, corpus_curated, base_model, substrate, adapter, merged, quantised, report. Locality records which forges hold a copy |
| `source` | id, jurisdiction, url, licence_spdx, attribution_required, retrieved_at, sha256, personal_data, dpia_ref | The licence register |
| `run` | id, **site_id**, spec_hash, kind, state, started_at, ended_at, scheduler_job_id, node, retry_count | State mirrors the machine in section 6 |
| `gate_result` | id, run_id, gate, suite_version, value, baseline_value, margin, passed, evaluated_at | One row per gate per artefact |
| `approval` | id, subject_id, approver, decision, reason, signature, **sole_approver_exception**, decided_at | Signed with the approver's key. The exception flag is set whenever the approver also submitted the run, and is surfaced in lineage |
| `plugin` | name, version, interface, signature_verified, capabilities, enabled | The extension registry |
| `release` | id, artefact_id, model_card_uri, sbom_uri, lineage_uri, **training_summary_uri**, **copyright_policy_uri**, signature, anchored_at, published_at | The release record. The two additional URIs are the Article 53 artefacts |
| `site` | id, name, location, timezone, control_plane_uri, anchor_state, last_anchored_at | The forge registry. Sindri is site 1 |
| `retention_action` | id, subject_id, policy, due_at, approved_by, executed_at, manifests_retained | Deletion is an approved, ledgered action |

### 7.2  Storage placement

| Data | Store | Rationale |
|---|---|---|
| Ledger, registers, run state, RBAC | PostgreSQL on ANDVARI | Transactional integrity, replayable |
| Weights, adapters, merged and quantised artefacts, reports | MinIO on ANDVARI | Content addressed, S3 interface, versioned |
| Corpora and in flight checkpoints | HODD vault over NFS | Large sequential access from the appliances |
| Training metrics and curves | MLflow with PostgreSQL backend | Existing tooling, avoids reimplementation |
| Logs | Loki on REGIN | Existing tooling |

### 7.3  Retention

Raw corpora are retained for **24 months** from the release of the last model derived from them, then deleted under an approved and ledgered retention action rather than by an unattended job.

> Deletion of a raw corpus means the derived model can afterwards be **verified** but no longer **re-derived**. HODD therefore retains the curated manifests, per source hashes and the licence register entries indefinitely, so that lineage and the Article 53 training content summary survive the deletion of the underlying text. A retention action that would break a lineage chain is refused.

### 7.4  Addressing

Artefacts are addressed by a `hodd://` URI resolved by HODD to a physical location. Under federation the authority component carries the site, as `hodd://sindri/models/core/...`, and an omitted authority resolves to the local site. This keeps run specifications portable across a storage change, which matters because gap G2 and the Phase 2 NVMe over Fabrics option in VLD-INF-SINDRI-001 section 11.3 both alter physical placement.

## 8  Interface architecture

### 8.1  API surface

| Method and path | Purpose | Role |
|---|---|---|
| `POST /v1/sources` | Register a corpus source with its licence | curator |
| `POST /v1/corpora/{iso3}/ingest` | Ingest and hash, transition to CORPUS_REGISTERED | curator |
| `POST /v1/corpora/{iso3}/curate` | Run the curation pipeline | curator |
| `POST /v1/runs` | Submit a run specification | operator |
| `GET /v1/runs`, `GET /v1/runs/{id}` | List and inspect runs | operator, auditor |
| `POST /v1/runs/{id}/cancel`, `/retry` | Control a run | operator |
| `GET /v1/gates?state=pending` | The approval queue | approver |
| `POST /v1/gates/{id}/decide` | Approve or reject, signed | approver |
| `POST /v1/releases/{artefact}/publish` | Publish, requires an approval record | approver |
| `GET /v1/lineage/{artefact}` | Full lineage attestation | auditor |
| `GET /v1/ledger?from=&to=` | Ledger slice with chain verification | auditor |
| `GET /v1/plugins` | Installed plug-ins, versions, signature status | operator |
| `GET /healthz`, `/readyz`, `/metrics` | Operational endpoints | unauthenticated on loopback only |

The API is OpenAPI 3.1 described. `draupnirctl` and the web console are both generated clients over the same specification; neither has a private path.

### 8.2  Plug-in interfaces

Extension is by entry point group. Each interface is versioned and the core refuses to load a plug-in declaring an unknown major version.

| Entry point group | Interface | Example implementations |
|---|---|---|
| `draupnir.train` | `TrainDriver` | LLaMA-Factory, Axolotl, NeMo, TRL |
| `draupnir.merge` | `MergeDriver` | mergekit TIES, DARE-TIES, SLERP, task arithmetic |
| `draupnir.eval` | `EvalDriver` | lm-evaluation-harness, lighteval, custom jurisdiction suites |
| `draupnir.export` | `ExportDriver` | TensorRT Model Optimizer NVFP4, llama.cpp GGUF, MLX |
| `draupnir.schedule` | `ScheduleDriver` | Slurm, Ray, local subprocess for development |
| `draupnir.store` | `StoreDriver` | POSIX and NFS, S3 and MinIO, future NVMe over Fabrics |
| `draupnir.policy` | `PolicyDriver` | Licence policy, data protection policy, export control policy |

```
class TrainDriver(Protocol):
    name: str                      # e.g. "hamarr.llamafactory/v1"
    capabilities: frozenset[str]   # {"lora","qlora","full","moe","multinode"}

    def validate(self, spec: RunSpec) -> list[ValidationError]:
        """Reject an unrunnable spec before an allocation is consumed."""

    def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
        """Produce the concrete command, environment and resource request.
        Must be pure: no side effects, no network, deterministic given spec."""

    def parse_progress(self, line: str) -> ProgressEvent | None:
        """Translate one line of executor output into a structured event."""

    def collect(self, workdir: Path) -> RunArtefacts:
        """Return produced artefacts with their hashes. Must not mutate them."""
```

**DESIGN DECISION S5. `render` is required to be pure and side effect free.**

Rationale. Driver D-1 requires reproducibility. If a driver may perform network calls or mutate state while planning a job, the plan is no longer a function of the specification and the run cannot be reproduced from recorded inputs. Purity also makes a dry run trivially safe, which matters when an allocation on this node is expensive.

Alternative rejected. Allowing drivers to resolve dependencies during planning, which is more convenient for the driver author and destroys the reproducibility guarantee for everyone.

---

## 11A  Federation architecture

![Figure 5. Multi-forge federation](diagrams/11_federation.png)

### 11A.0  The Forge Matrix

The estate of linked forges is the **Forge Matrix**. Sindri is Site 0, the first and currently only member. The Forge Matrix is a descriptive collective term rather than a component, so it takes no name from the Veldris naming standard; the standard governs components, and the components here are the forges, which are smiths, and the federation tier, which is MEGINGJORD.

> The obvious Norse name for a collective of dwarf forges is the dwarven realm itself, which is reserved to the Midgard product line and therefore unavailable. The descriptive term is used instead.

**DESIGN DECISION S12. "Site" identifies a forge. "Node" identifies an appliance within a forge. The two words are not interchangeable.**

Rationale. Sindri already uses "node" in two senses that resolve to the same three machines: BAUGR ring nodes 1 to 3 and NCCL ranks 0 to 2. Introducing a third sense, in which a whole forge is a node of the Forge Matrix, makes a runbook line reading "node 0 is down" ambiguous between an appliance and an installation. Site ordinals and site names are used for forges throughout.

The same reasoning previously kept NORDRI out of the host namespace because NORI is a switch. Ambiguity in a naming scheme is paid for at three in the morning, not at design time.

| Term | Refers to | Example |
|---|---|---|
| Forge Matrix | The estate of linked forges | Not a component |
| Site | One forge, with its own control plane, ledger segment and scheduler | Site 0, Sindri |
| Node | One appliance within a forge | BAUGR node 1, DVALIN |
| Rank | NCCL rank within a training job | Rank 0, DVALIN |

### 11A.1  Tiers

| Tier | Component | Runs on | Responsibility |
|---|---|---|---|
| Matrix | **MEGINGJORD** | Veldris_NXT VPS, United Kingdom | Global CIM-56 model registry, cross forge ledger anchors, policy and gate distribution, OIDC issuer and RBAC source of truth, plug-in signature trust root, internal PKI and self hosted transparency log |
| Site | **GULLINBURSTI** | Alongside DRAUPNIR Core at each forge | Anchors the local chain head, pulls policy, pushes release metadata, reports capacity and health |
| Forge | DRAUPNIR Core and modules | ALVISS at Sindri | Everything in sections 5 to 8, scoped to one site |

### 11A.2  Naming

Forges are named for smiths and hosts for dwarves, so a fully qualified name states both. Sindri is Site 0. Brokkr is the planned second at the same premises. Eitri is a planned second location.

```
<host>.<forge>.veldris.internal

dvalin.sindri.veldris.internal        Site 0, Nuneaton
dvalin.brokkr.veldris.internal        Site 1, Nuneaton, same premises
dvalin.eitri.veldris.internal         Site 2, second location
```

Several forges at one location share premises, power and network, but not a control plane, a ledger segment or a scheduler. The unit of federation is the forge, not the building.

### 11A.3  Anchoring

Each forge maintains its own hash chained ledger segment. At an interval, and on every release transition, GULLINBURSTI submits the current chain head, being the sequence number and entry hash, to MEGINGJORD. MEGINGJORD countersigns and records it.

Anchoring gives three properties. A forge cannot rewrite history after an anchor without the divergence being visible centrally. The federation holds no forge content, only hashes, so corpora and weights never leave their site. And a forge that has never anchored a given entry can still prove its own internal chain integrity locally.

### 11A.4  Partition behaviour

| Condition | Behaviour |
|---|---|
| Wide area network available | Normal. Anchoring on interval and on release |
| Wide area network lost | The forge continues to ingest, curate, train, merge, evaluate and quantise. All transitions record to the local segment |
| Wide area network lost, release attempted | **Release is blocked.** Publication requires the federation signing trust root and a countersigned anchor. The artefact waits in AWAITING_APPROVAL |
| Network restored | GULLINBURSTI submits every unanchored head in order. MEGINGJORD verifies chain continuity before countersigning. Blocked releases become available |
| Divergence detected at anchor | Both sides alarm. The forge enters read only mode. No release from that forge until investigated |

**DESIGN DECISION S8. Training continues through a partition; release does not.**

Rationale. Training is the expensive activity and stopping it on a network fault wastes the scarcest resource in the estate. Release is the moment a commercial artefact and its regulatory documentation leave Veldris control, and that moment should depend on the trust root rather than on a local decision.

Alternative rejected. Full local autonomy including release, which removes the outage cost entirely and also removes the only structural check on a compromised or isolated forge.

### 11A.5  What federation does not do

The federation tier is deliberately thin. It does not schedule work across forges, replicate artefacts, or hold corpora. Cross forge job placement was considered and rejected: the artefacts are large, wide area links are slow relative to BAUGR, and a job that spans sites would be bounded by the worst link in the estate. Work is assigned to a forge, and stays there.

---

# Part 3  Security, extensibility and operations

## 9  Security architecture

![Figure 4. Threat model dataflow with trust boundaries](diagrams/10_draupnir_threat.png)

### 9.1  Trust boundaries

| Boundary | Contains | Assumption |
|---|---|---|
| TB1 Internet | Hugging Face, corpus source sites, teacher model API | Hostile. Everything crossing inward is untrusted and hashed |
| TB2 Operator edge | Humans over WireGuard or the CON-A local console | Authenticated but fallible. Authorisation enforced per call |
| TB3 Control plane | Core API, ledger, SVALINN secrets broker | Trusted and fully audited |
| TB4 Execution plane | Training jobs, third party plug-ins | Semi trusted. Sandboxed, no outbound network, capability scoped |
| TB5 Data at rest | HODD vault, PostgreSQL | Encrypted. Self encrypting drive, FileVault, APFS encryption |

### 9.2  Threat register

STRIDE aligned. Each threat maps to a control and to an acceptance criterion in Part 4.

| Ref | Threat | STRIDE | Control | Acceptance |
|---|---|---|---|---|
| T1 | Poisoned or substituted base weights from the Hub | Tampering | Pin by revision, verify SHA-256 manifest at every load, archive the licence text at acquisition, refuse a load on hash mismatch | AC-S1 |
| T2 | Corpus poisoning or unlicensed material entering training | Tampering, Information disclosure | Immutable ingest with per source hashing, GLEIPNIR licence gate before curation, decontamination stage, quarantine on failure | AC-S2 |
| T3 | Exfiltration of corpus content through the teacher model API | Information disclosure | **Out of scope for Release 1.** Distillation is not built and the teacher destination is not allow listed. The egress broker is built and refuses the call. Reinstate this threat when distillation enters scope | AC-S3 |
| T4 | Spoofed operator identity or privilege escalation | Spoofing, Elevation | OIDC at the edge, role based authorisation on every call, no shared accounts, short lived sessions, mTLS between control plane components | AC-S4 |
| T5 | Release approval bypassed or later denied | Repudiation, Elevation | Publish endpoint requires a signed approval record; approvals are signed with the approver's key and chained into the ledger; single approver permitted under constraint C-6 but recorded as an exception | AC-S5 |
| T6 | Credentials leaked into a checkpoint or log | Information disclosure | Secrets brokered as short lived tokens, never written to the job environment file; checkpoint and log scanning for secret patterns before an artefact is registered | AC-S6 |
| T7 | Malicious or compromised plug-in executing arbitrary code | Tampering, Elevation | Plug-ins signed and signature verified at load; capability declaration enforced at runtime; execution in a rootless container with no outbound network and a read only artefact mount | AC-S7 |
| T8 | Artefact tampered between passing a gate and being released | Tampering | Gate results bind to the artefact hash, not its path. Publish re-verifies the hash and refuses on mismatch | AC-S8 |
| T9 | Ledger altered to conceal a transition | Repudiation | Hash chained entries, periodic chain verification, projection rebuildable from the ledger, divergence alarms | AC-S9 |
| T10 | Denial of service by exhausting the vault | Denial of service | HODD quota and retention enforcement, pre flight capacity check, run refused rather than volume filled | AC-S10 |
| T11 | Unapproved egress by a dependency or framework | Information disclosure | Site allow list plus an in application egress broker. Executors run with no outbound network at all | AC-S11 |
| T12 | A compromised or isolated forge publishes an unauthorised release | Tampering, Elevation | Release requires a countersigned anchor from MEGINGJORD. A forge in partition cannot publish. Chain divergence at anchor puts the forge read only | AC-S13 |
| T13 | The federation registry is compromised, affecting every forge | Elevation | MEGINGJORD holds hashes and policy, never corpora or weights. Forge ledgers are independently verifiable without it. Its signing key is held offline with a hardware backed operational key. Custody concentration accepted at Site 0 under section 16A | AC-S14 |
| T14 | Sole approver account compromised, giving one credential full release authority | Spoofing, Elevation | Constraint C-11 removes separation of duties, so the compensating controls are hardware backed multi factor authentication on the approver identity, signed approvals chained into the ledger, and the sole approver exception recorded on every release. Accepted at Site 0 under section 16A | AC-S15 |

### 9.3  Control mapping

| Family | ISO/IEC 27001 Annex A | Other standard | OWASP ASVS | Implementation |
|---|---|---|---|---|
| Identity and access | A.5.15, A.5.16, A.5.18 | NCSC access control guidance | V2, V4 | OIDC, RBAC, least privilege roles, no shared accounts |
| Cryptography | A.8.24 | ISO/IEC 19790, ISO/IEC 18033 | V6 | See section 9.5. TLS 1.3 and mTLS in transit, AES-256 at rest, internal PKI artefact signing, crypto agile for post quantum migration |
| Logging and monitoring | A.8.15, A.8.16 | NCSC logging and protective monitoring | V7 | Hash chained ledger, Loki log aggregation, Prometheus alerting |
| Supply chain | A.5.21, A.8.30 | NCSC supply chain security | V14 | Pinned dependencies, CycloneDX SBOM per release, plug-in signature verification |
| Secure development | A.8.25, A.8.28 | NCSC secure development and deployment | V1, V5 | Schema validated input, no dynamic code execution from user input, dependency scanning in CI |
| Data protection | A.5.34, A.8.11 | UK GDPR, Regulation (EU) 2016/679 | V8 | Personal data determination per source, DPIA gate, redaction stage, retention policy |
| Resilience | A.5.29, A.8.13 | ISO/IEC 27031 | V1.11 | Ledger replay, run state survives restart, documented degraded modes |

### 9.4  Roles

| Role | Permissions |
|---|---|
| `viewer` | Read runs, gates, lineage, ledger |
| `curator` | Register sources, ingest, curate. Cannot submit training runs |
| `operator` | Submit, cancel, retry runs. Cannot approve or publish |
| `approver` | Decide gates and publish releases. Cannot alter run specifications |
| `admin` | Manage users, plug-ins and policy. Cannot decide gates, and cannot delete ledger entries |

**DESIGN DECISION S6. No role may both submit a run and approve its release.**

Rationale. Threat T5 is the commercially serious one, because a released model whose approval cannot be evidenced is worth nothing at due diligence. Separating submission from approval is the minimum meaningful control.

Alternative rejected. A single privileged role for a small team, which is operationally simpler and removes the only structural check on release.

**Position for Release 1.** Constraint C-11 records that release approval is held by one named individual, the creator, codename Akuma. Separation of duties is therefore unavailable and the control is not met. The system does not pretend otherwise. Every release where the approver also submitted the run carries `sole_approver_exception = true`, which appears in the lineage attestation and in the model card provenance section.

Keeping that flag visible is deliberate. A purchaser's technical diligence will establish the approval arrangement either way, and finding it disclosed in the artefact is a materially different conversation from finding it absent. The compensating controls listed against threat T14 are what carry the assurance weight in its place, and they are Must criteria rather than aspirations.

### 9.5  Cryptographic standards

Constraint C-9 sets ISO/IEC and NCSC guidance rather than FIPS 140-3. That distinction matters operationally.

> **NCSC operates guidance, not a validation scheme comparable to CMVP.** There is no certificate to obtain. The claim Veldris can make and evidence is conformance to NCSC recommended algorithms and configurations, and use of ISO/IEC 19790 conformant modules where a conformant build is available, demonstrated by configuration evidence and a documented cryptographic inventory. This is auditable and defensible, and it is a weaker assertion than "FIPS validated". It should be presented as what it is.

| Purpose | Release 1 | Post quantum migration |
|---|---|---|
| Transport | TLS 1.3 only. mTLS between control plane components and between GULLINBURSTI and MEGINGJORD | Hybrid X25519 with ML-KEM once the library estate supports it |
| Hashing | SHA-256 for artefact manifests and ledger chaining. SHA-384 where a longer digest is warranted | Unchanged. SHA-2 at these lengths remains suitable |
| Artefact and approval signing | Ed25519 or ECDSA P-384 against an internal Veldris signing CA | Hybrid classical with ML-DSA. The signature envelope is versioned from Release 1 so that adding an algorithm is not a format change |
| Data at rest | AES-256. Self encrypting drive on the appliances, FileVault on Apple silicon, APFS encryption on the HODD vault | Unchanged |
| Key custody | Federation root held offline. Operational keys in a hardware backed store. Short lived tokens brokered by SVALINN | Unchanged |

**DESIGN DECISION S9. Artefact signing uses an internal Veldris PKI with a self hosted transparency log, not public Sigstore.**

An earlier draft selected Sigstore for its transparency log. Reconsidered against driver D-5.

Rationale. Sigstore's public transparency log publishes release metadata, including artefact identifiers and timing, to infrastructure outside Veldris control. For a company whose commercial position rests on sovereignty, publishing the shape of a customer's model release schedule to a public log is a poor fit. A self hosted Rekor instance behind an internal signing CA provides the same tamper evidence and append only guarantee without the external disclosure.

Alternative rejected. Public Sigstore, which offers a stronger third party trust story to an external verifier and discloses more than the positioning permits.

**DESIGN DECISION S10. The signature envelope is crypto agile from Release 1.**

Rationale. NCSC's published migration timeline expects organisations to have discovered and planned their post quantum migration well before the end of the decade, with high priority systems migrated ahead of full completion. Signed release artefacts are long lived: a model signed in 2026 may need to be verifiable in 2036. Designing the envelope to carry more than one signature now costs a schema field. Retrofitting it later means re-signing every historical release.

This also aligns the forge with the Kryotech post quantum work already inside the group, so the algorithm selection and the library estate are a shared problem rather than two.

*Timelines and algorithm recommendations should be confirmed against current NCSC publications at implementation, since guidance in this area is being revised actively.*

## 9A  EU AI Act compliance

Constraint C-10 records that Veldris is a provider of general purpose AI models placing them on the EU market, so Regulation (EU) 2024/1689 applies.

### 9A.1  Position

| Point | Position |
|---|---|
| Obligations in force | GPAI model obligations have applied since 2 August 2025 and were not deferred. Transparency duties and the AI Office's enforcement powers over GPAI providers took effect on 2 August 2026 |
| Effect of the Digital Omnibus | Regulation (EU) 2026/1744 entered into force on 27 July 2026 and deferred only the high risk tier, to 2 December 2027 for Annex III and 2 August 2028 for Annex I. None of that relief applies here |
| Scope across CIM-56 | All fifty six models, not only those concerning EU or UK jurisdictions. The Act attaches to placing the model on the EU market, not to the subject matter of the model |
| Systemic risk tier | **Not applicable.** CIM-56 training compute is orders of magnitude below the systemic risk threshold. Article 55 obligations do not attach |
| Article 50 boundary | Article 50 attaches to systems that generate synthetic content and to their providers and deployers. That is the Midgard Suite, not the forge. DRAUPNIR discharges Article 53 and must not absorb Article 50 duties belonging to the Suite |

### 9A.2  Article 53 obligations and where they are discharged

| Obligation | DRAUPNIR module | Artefact |
|---|---|---|
| Technical documentation of the model | SKIDBLADNIR | Model card plus the lineage attestation, rendered from recorded facts |
| Information and documentation for downstream providers | SKIDBLADNIR | Downstream integration annex in the release package |
| Policy to comply with Union copyright law, including the text and data mining reservation | GLEIPNIR | Machine readable copyright policy, versioned, referenced by every release |
| Sufficiently detailed public summary of training content | SKIDBLADNIR, from the HODD licence register | Training content summary on the AI Office template, generated rather than authored |
| Cooperation with the AI Office | Core | Ledger and lineage export on request |

**DESIGN DECISION S11. Article 53 artefacts are generated from the pipeline record, never authored separately.**

Rationale. Driver D-8. A compliance document written by hand after the fact describes what the author remembers, and drifts from the system on the first revision that nobody propagates. The licence register, the corpus manifests and the lineage chain already hold everything the training content summary requires. Rendering the summary from that record makes the document correct by construction and makes it impossible to release a model whose summary is stale.

Alternative rejected. A compliance authoring workflow producing documents alongside the pipeline, which is how most organisations do it and is the reason most such documents are wrong within two revisions.

*The Act and its implementing guidance are being revised actively. The AI Office template for the training content summary, and the current text of the Regulation as amended, must be checked at implementation and at each release.*

## 10  Extensibility

### 10.1  Extension points

| Point | What it enables | Adding one requires |
|---|---|---|
| `draupnir.train` | A new training framework or method | Implement `TrainDriver`, sign, install, declare capabilities |
| `draupnir.merge` | A new reweighting method | Implement `MergeDriver` |
| `draupnir.eval` | A new gate, suite or jurisdiction eval | Implement `EvalDriver` and register the gate in policy |
| `draupnir.export` | A new quantisation or packaging format | Implement `ExportDriver` |
| `draupnir.schedule` | A different scheduler or a cloud burst target | Implement `ScheduleDriver` |
| `draupnir.store` | A different storage backend | Implement `StoreDriver` |
| `draupnir.policy` | A new compliance regime | Implement `PolicyDriver` and version the policy |

### 10.2  Worked extension scenarios

These are the changes most likely to be needed, given that the open weight landscape shifted substantially within a single revision cycle of the infrastructure manual.

| Scenario | Change required | Core change |
|---|---|---|
| A new Qwen or GLM generation is released | Add the chat template identifier to the LLaMA-Factory driver's template map. Register the base model and its licence | None |
| A new base family with a different architecture | New `TrainDriver` if the framework differs; otherwise configuration only | None |
| A jurisdiction requires a bespoke evaluation | New `EvalDriver` plus a gate registration in GLEIPNIR policy | None |
| A customer requires a new export format | New `ExportDriver` | None |
| A fourth appliance is added | MOTSOGNIR placement policy configuration; Slurm node list | None |
| A second forge is commissioned at the same premises | Register the site in MEGINGJORD, deploy Core and GULLINBURSTI, issue certificates | None |
| A forge is commissioned at a second location | As above. Site scope already exists in the schema per Decision S7 | None |
| A post quantum signature algorithm is adopted | Add the algorithm to the versioned signature envelope. Historical signatures remain verifiable | None |
| A QSFP switch is added and NVMe over Fabrics adopted | New `StoreDriver`; `hodd://` URIs are unchanged in every run specification | None |
| Article 53 guidance or the AI Office template changes | New `PolicyDriver` version and an updated template in SKIDBLADNIR. Existing releases keep the template version in force at their release date | None |

### 10.3  Compatibility rules

1. Interfaces are versioned in the entry point name, for example `hamarr.llamafactory/v1`. A breaking change is a new major version, not an edit.
2. The core supports the current and the immediately previous major version of every interface.
3. A run specification records the driver version used. Replaying a historical run resolves that version, and fails loudly rather than silently substituting a newer one.
4. A plug-in declares its capabilities. The core refuses to plan a job whose specification requires a capability the driver has not declared.

## 11  Operational architecture

### 11.1  Deployment

| Step | Action |
|---|---|
| 1 | `draupnir-api`, `draupnir-worker` and `draupnir-web` deploy to ALVISS as launchd managed services or rootless containers |
| 2 | PostgreSQL schema migrations run under Alembic, forward only, with the ledger table append constrained at the database level |
| 3 | Executor shim images build for aarch64 and publish to a local registry on ANDVARI |
| 4 | Plug-ins install into the API virtual environment and are signature verified at first load |
| 5 | Configuration is file based and version controlled. Secrets are never in configuration; they are brokered by SVALINN |
| 6 | GULLINBURSTI deploys alongside Core at each forge and is issued a site certificate from the MEGINGJORD internal PKI |
| 7 | MEGINGJORD deploys to the Veldris_NXT VPS. Its signing root is held offline; the operational key is hardware backed |

### 11.2  Degraded modes

| Failure | Behaviour | Recovery |
|---|---|---|
| Control plane restarts | Runs in flight continue on the appliances. State is reconstructed from the ledger on start | Automatic |
| Slurm controller on REGIN unavailable | Dispatch suspends. Queued runs stay QUEUED. Running jobs are unaffected | Restore REGIN, dispatch resumes |
| One appliance lost | MOTSOGNIR reduces array concurrency from three to two automatically. Ring partition runs refuse to plan | Follow VLD-INF-SINDRI-001 section 49.1 |
| HODD vault unavailable | New runs refuse to plan. Running jobs writing to local scratch continue and stage on recovery | Restore NFS, run reconciliation |
| PostgreSQL unavailable | API returns 503. No state is lost. Executors continue | Restore, replay ledger, verify chain |
| Ledger chain verification fails | System enters read only mode and alarms. No release is possible | Investigate before any further transition |
| Wide area network to MEGINGJORD lost | Training, evaluation and merge continue. Anchoring queues. Release is blocked | Restore link, anchor queued heads in order, releases unblock |
| Chain divergence detected at anchor | Forge enters read only mode. Both forge and federation alarm | Investigate before any further transition from that forge |
| Mains loss, uninterruptible supply on battery | The supply signals over USB. DRAUPNIR forces an immediate checkpoint on every running job, then drains the queue and halts cleanly at the low battery threshold | Restore mains, resume from the forced checkpoint |

### 11.3  Observability

| Signal | Source | Surfaced on |
|---|---|---|
| Run state and queue depth | Core | Web console run board, CON-B dashboard 3 |
| Gate pass rates and margins | RAUN | Web console, per jurisdiction trend |
| Appliance thermal and throttle | DCGM exporter | CON-B dashboard 1, and CON-A locally on DVALIN |
| Fabric bandwidth probe | Hourly `nccl-tests` job dispatched by MOTSOGNIR | CON-B dashboard 2, alarms below 80 per cent of the commissioned baseline |
| Vault capacity | HODD | Web console, alarm at 85 per cent |
| Ledger chain integrity | Core, verified hourly | Alarm on any divergence |
| Anchor freshness | GULLINBURSTI | Alarm when the last successful anchor exceeds the configured interval |
| Mains and battery state | Uninterruptible supply over USB, once fitted | Alarm on transfer to battery, forced checkpoint on transfer |

### 11.4  Technology selection

| Choice | Selected | Alternative rejected | Reason |
|---|---|---|---|
| API framework | FastAPI | Django REST | OpenAPI generation, async support, lighter for a single service |
| Language | Python 3.12 | Go or Rust | The entire training ecosystem is Python. A different control language would mean a subprocess boundary at every driver |
| Federation transport | WireGuard with mTLS | Public API over TLS | Already deployed for operator access. Keeps the federation link off the public internet |
| Database | PostgreSQL 16 | SQLite | Already deployed for MLflow. Concurrent workers need real transactions |
| Object store | MinIO | Filesystem only | Already deployed. Content addressing and an S3 interface for future portability |
| Queue | PostgreSQL advisory locks and polling | Redis or RabbitMQ | Job rate is tens per day. An additional broker is unjustified operational surface |
| Front end | React and TypeScript | Server rendered templates | The run board is a live view; a typed client generated from OpenAPI removes a class of drift |
| Signing | Internal Veldris PKI with self hosted Rekor transparency log | Public Sigstore | Same tamper evidence without publishing release metadata externally. See Decision S9 |
| Sandboxing | Rootless containers with no network namespace egress | gVisor or a virtual machine | Proportionate to the threat, and available on DGX OS without extra components |

---

# Part 3A  Full stack engineering specification

## 11B  Component decomposition of the core, C4 level 3

![Figure 6. DRAUPNIR Core internal components](diagrams/13_c4_l3_core.png)

| Layer | Components | Rule |
|---|---|---|
| Edge | Router, authentication middleware, authorisation guard, request validation, event stream, error mapper | Knows HTTP. Contains no domain logic. A route without a role declaration must fail at registration rather than at runtime |
| Application | Specification compiler, state machine, transition orchestrator, event bus, worker pool | Knows the workflow. Every state change passes through the orchestrator in one transaction: guard, act, write ledger, project |
| Domain | Ledger writer, projector, site resolver, run registry | Knows the invariants. Pure where possible, no framework imports |
| Ports | Entry point loader, conformance harness | Knows interfaces, never implementations |
| Infrastructure | Repositories, object store client, telemetry | Knows the technology. Substitutable |

Dependencies point inward only. A domain module importing from the edge layer fails review, and an import linter enforces it in continuous integration rather than by convention.

## 11C  Data model

![Figure 7. Entity relationships](diagrams/12_er_model.png)

Section 7.1 lists the attributes. Three constraints are enforced at the database rather than in application code, because a constraint that lives only in the application is a constraint that a migration script can bypass.

| Constraint | Enforcement |
|---|---|
| `ledger_entry` accepts INSERT only | Trigger rejecting UPDATE and DELETE |
| `release` requires an `approval_id` | Foreign key, NOT NULL |
| Site scope on every scoped query | Row level security policy plus a session variable set by the site resolver |

The `source` table carries `residency_constraint`, a list of site identifiers permitted to hold that corpus. It is populated when a jurisdiction imposes one. Where a corpus is residency constrained, work on it is planned only at a permitted site, and the constraint is checked at planning rather than at execution.

## 11D  End to end sequence

![Figure 8. Adapter run from submission to release](diagrams/16_sequence.png)

Two properties of this flow are worth naming. Policy evaluation happens before an allocation is consumed, at step 5, because scheduler time on this estate is the scarce resource. And release at step 21 requires two independent things: a signed approval and a countersigned federation anchor. Neither alone is sufficient.

## 11E  Backend engineering standards

### 11E.1  Repository layout

```
draupnir/
  core/           domain and application: ledger, state, sites, registry, plugins
  interfaces/     the seven Protocols, plus the conformance harness
  api/            FastAPI edge: routers, schemas, guards, error mapping
  hodd/  gleipnir/  motsognir/  hamarr/
  brisingamen/  raun/  skidbladnir/  svalinn/  gullinbursti/
  megingjord/     federation registry, deployed separately
plugins/          first-party drivers, each an installable package
draupnirctl/      generated CLI
web/              JARNGREIPR design system and the console
skills/           development skills, section 11G
docs/             architecture, runbook, acceptance evidence
tests/            unit, property, contract, integration, e2e
```

### 11E.2  API conventions

| Concern | Rule |
|---|---|
| Errors | RFC 9457 `application/problem+json`. Every problem type has a stable URI, a human readable title and a machine readable code. No bare 500 reaches a client |
| Idempotency | Every mutating endpoint accepts an `Idempotency-Key`. A repeat with the same key returns the original result rather than acting twice |
| Pagination | Cursor based, never offset. Offset pagination over a growing ledger silently skips rows |
| Concurrency | `ETag` and `If-Match` on mutable resources. A stale write returns 412 |
| Versioning | Path versioned at `/v1`. Additive changes only within a version. The OpenAPI diff gate fails a build on a breaking change |
| Long operations | Return 202 with a run identifier. Nothing blocks an HTTP request on training |
| Time | RFC 3339 with an explicit offset. No naive timestamps anywhere |
| Identifiers | UUIDv7, so identifiers sort by creation time without exposing a sequence |

### 11E.3  Testing strategy

| Level | Scope | Target |
|---|---|---|
| Unit | Pure domain logic, every state transition | 90 per cent statement coverage on `core/` |
| Property | Ledger chain invariants, specification hashing determinism, projector idempotence | Hypothesis, minimum 500 examples per property |
| Contract | Every driver against the conformance harness | Every first party driver, and the harness published for third parties |
| Integration | Ephemeral PostgreSQL and MinIO, real migrations | Every repository and every module boundary |
| API contract | OpenAPI diff against the previous release | Breaking change fails the build |
| Frontend unit | Component behaviour, not implementation | Vitest and Testing Library |
| End to end | The four journeys in section 11F.2 | Playwright, against a seeded stack |
| Accessibility | Automated axe scan on every route, plus a manual keyboard pass | Zero serious or critical violations |
| Visual regression | Storybook snapshots of every design system component in every state | Snapshot diff gate |

### 11E.4  Observability instrumentation

OpenTelemetry traces from the API edge through the orchestrator to the driver boundary. Structured logging with `structlog`, one event per line, with the run identifier, site identifier and actor on every record. Prometheus metrics for queue depth, transition latency, gate pass rate, anchor age and driver failure rate. No log line may contain a secret, a corpus excerpt or a token, and the pre registration scan in SVALINN covers logs as well as checkpoints.

## 11F  Frontend and experience specification

![Figure 9. Console information architecture and user journeys](diagrams/14_ui_ia.png)

> **The frontend is specified in full in VLD-UX-DRAUPNIR-001**, the Experience and Interface Design Specification. That document carries the token values, the thirty one screen inventory, wireframes for the key screens, the four journey flows, sixty two frontend acceptance criteria and five build prompts, and it replaces Prompts 8 and 9 of section 13 for frontend work. This section states the architectural position only.

### 11F.1  JARNGREIPR, the design system

The console is built on **JARNGREIPR**, the iron gloves with which a smith handles work too hot to touch by hand. It is specified as a separate package from the console so that the Midgard Suite can adopt it rather than diverging into a second visual language.

| Layer | Contents |
|---|---|
| Tokens | Colour with light and dark ramps, typographic scale, spacing scale, radius, elevation, motion durations and easings. Tokens are the only source of visual values; a hard coded hex in a component fails review |
| Primitives | Button, input, select, checkbox, radio, toggle, table, badge, tag, tooltip, dialog, drawer, toast, tabs, breadcrumb, pagination |
| Composites | Run card, gate card with evidence, lineage tree, sweep comparison matrix, log viewer with virtualised scrolling, capacity gauge, ledger entry viewer, diff viewer |
| States | Every component ships loading, empty, error, denied, read only and partitioned states. A component with only a happy path is incomplete |
| Documentation | Storybook, one story per component per state, used as both documentation and the visual regression target |

### 11F.2  Primary journeys

| Journey | Actor | Path | Success measure |
|---|---|---|---|
| J1 Curate | Curator | Register source, declare licence and personal data, ingest, curate, review retention | A source is registered and curated without leaving the console |
| J2 Operate | Operator | Compose specification, dry run, submit, watch the board, diagnose a failure, retry | A failed run is diagnosed from the console without reading a raw scheduler log |
| J3 Approve | Approver | Open the gate queue, read the evidence, see the sole approver notice, sign, publish | Approval requires seeing the gate results, not only a button |
| J4 Audit | Auditor | Select a release, walk the lineage to base model licence and corpus hash, verify the chain, export | A complete lineage is reached in three interactions or fewer |

### 11F.3  Interaction requirements

| Requirement | Specification |
|---|---|
| Live updates | Server sent events. The run board reflects a state change within 5 seconds without a manual refresh |
| Command palette | Keyboard invoked, covering navigation, run submission and search. The console is operable without a mouse |
| Site context | Where more than one site is registered, every view states which site it shows. No unscoped aggregate that could be mistaken for one forge |
| Destructive actions | Two step, with the consequence stated in words rather than a generic confirmation |
| Long output | Log and ledger views virtualise. A run with 200,000 log lines must not degrade the browser |
| Offline and partition | The console states plainly when the site is partitioned from the federation and that release is unavailable, rather than failing an action with a generic error |
| Error presentation | Every error renders the problem type title, what the user can do, and a copyable correlation identifier |

### 11F.4  Accessibility and performance

| Requirement | Target |
|---|---|
| Conformance | WCAG 2.2 level AA |
| Keyboard | Every function reachable and operable by keyboard, with a visible focus indicator throughout |
| Contrast | 4.5:1 for body text and 3:1 for large text and interface components, in both themes |
| Zoom and reflow | Usable at 200 per cent zoom and at a 320 pixel viewport width without loss of function |
| Motion | Respects `prefers-reduced-motion` |
| Screen reader | Live regions announce run state changes. Tables have proper header association |
| First contentful paint | Under 1.5 seconds on the local network |
| Interaction to next paint | Under 200 milliseconds at the 75th percentile |
| Bundle | Under 300 KB gzipped for the initial route, code split thereafter |

**DESIGN DECISION S13. Accessibility is an acceptance criterion, not a later remediation.**

Rationale. The console is an internal tool with a small user base, which is the usual argument for deferring accessibility. It is also the prototype for the Midgard Suite interface, which will be sold into Commonwealth public sector buyers who apply accessibility procurement requirements as a matter of course. Building the design system to WCAG 2.2 AA once is cheaper than retrofitting the component library after it has been adopted by a second product.

Alternative rejected. Internal tool exemption, which saves effort now and produces a component library that cannot be reused where it matters.

## 11G  Development skills

A set of skills is authored alongside the code so that repetitive extension work is fast and conforms by construction. Each is a folder with instructions, references and scripts, matching the Veldris skill convention.

| Skill | Purpose | Triggered by |
|---|---|---|
| `draupnir-driver` | Scaffold a conforming plug-in driver: package layout, Protocol implementation, capability declaration, conformance test wiring, signing manifest | "add a training driver", "new export format", "support framework X" |
| `draupnir-endpoint` | Add an API endpoint: router, schema, role declaration, problem types, OpenAPI entry, client regeneration, tests | "add an endpoint", "expose X through the API" |
| `draupnir-migration` | Schema change with the site scope and ledger implications handled: forward only migration, row level security policy, projector update, rebuild test | "add a column", "change the schema" |
| `jarngreipr-component` | Build a design system component: tokens only, all six states, Storybook stories, accessibility test, visual regression snapshot | "add a component", "build the sweep matrix view" |
| `raun-suite` | Author a jurisdiction evaluation suite: task definitions, baseline capture, gate registration, decontamination check | "add an evaluation for X", "new jurisdiction suite" |
| `cim-release` | Render and validate a release package: model card, SBOM, lineage, Article 53 training summary, manifest, signature | "prepare a release", "check the release package" |
| `imhotep` | Existing. Reconcile this document against the delivered repository at acceptance | "document the codebase" |

**DESIGN DECISION S14. Skills are a deliverable of the build, not a by-product of it.**

Rationale. Fifty six models, seven extension points and a component library mean the same shapes of work recur many times. A skill that scaffolds a conforming driver removes both the effort and the class of error where a driver looks correct and violates the purity or capability contract. Authoring them during the build, while the conventions are being decided, produces skills that match the code. Authoring them afterwards produces documentation of what someone remembers.

Alternative rejected. Conventional documentation, which describes the pattern and leaves each application of it to be done by hand and reviewed.

## 11H  Continuous integration and delivery

![Figure 10. Continuous integration and delivery pipeline](diagrams/15_cicd.png)

The pipeline runs on a self hosted runner on ALVISS. No stage is skippable on the main branch. The client regeneration stage exists specifically to fail the build when the CLI or the TypeScript client has drifted from the OpenAPI specification, since a hand edited client is the most common way a generated interface quietly stops being generated.

---

# Part 4  Build specification

## 12  Acceptance criteria

Acceptance is by demonstration against these criteria. Each is written so that it either passes or fails on inspection, with no room for interpretation. The build is not accepted until every criterion in the Must column passes.

### 12.1  Functional

| Ref | Criterion | Priority |
|---|---|---|
| AC-F1 | A run specification file is submitted through `draupnirctl` and through the web console, and both produce an identical run identity, being the hash of the specification plus its input artefact hashes | Must |
| AC-F2 | Submitting the same specification twice with unchanged inputs is detected and reported as a duplicate rather than silently re-running | Must |
| AC-F3 | A corpus is ingested, hashed, licence registered and curated; the raw directory is read only afterwards and a write attempt is refused | Must |
| AC-F4 | A substrate run executes across all three appliances through the `ring` partition, and the run board shows live step progress | Must |
| AC-F5 | A fifty six element adapter array is submitted as one action and executes exactly three concurrently, one per appliance | Must |
| AC-F6 | A failed array element is retried individually without disturbing the other elements | Must |
| AC-F7 | Gates E1 to E6 execute against an adapter, results are recorded with baseline and margin, and a failure requeues the run automatically within its retry budget | Must |
| AC-F8 | A merge executes with a weight sweep of at least five points, and each point's gate results are comparable side by side in the console | Must |
| AC-F9 | Quantisation to NVFP4, GGUF and MLX executes, and each output is re-gated automatically before it can reach AWAITING_APPROVAL | Must |
| AC-F10 | A release produces a model card, a CycloneDX SBOM, a SHA-256 manifest and a lineage attestation, all four present and internally consistent | Must |
| AC-F11 | The lineage endpoint for any released artefact returns the complete chain to base model licences and corpus hashes with no gaps | Must |
| AC-F12 | The complete Procedure M1 to M10 sequence from VLD-INF-SINDRI-001 executes end to end for one jurisdiction with no manual shell step | Must |
| AC-F13 | Cancelling a run stops the scheduler job and leaves the artefact in a defined state, never in an ambiguous one | Must |
| AC-F14 | A dry run renders the exact job plan without consuming an allocation | Should |
| AC-F15 | Two jurisdictions differing only in corpus produce specifications that differ only in the dataset block | Should |
| AC-F16 | The Tier A list of nine jurisdictions and the Tier B list of forty seven together enumerate all fifty six, with no duplicate and no omission, validated at submission | Must |
| AC-F17 | A release package contains the Article 53 training content summary and the copyright policy reference, both generated from the licence register and neither hand authored | Must |
| AC-F18 | A second site is registered and a run is submitted to it, with site scope resolving correctly in every artefact URI and ledger entry | Must |
| AC-F19 | A retention action deletes a raw corpus after 24 months, retains the curated manifests and licence entries, and the lineage for every derived release remains complete afterwards | Must |
| AC-F20 | A retention action that would break a lineage chain is refused with the affected release named | Must |

### 12.2  Security

| Ref | Criterion | Threat | Priority |
|---|---|---|---|
| AC-S1 | Altering one byte of a base weight file causes the next load to fail with a hash mismatch and a ledger entry, and the run does not start | T1 | Must |
| AC-S2 | A corpus source with a licence that fails policy cannot reach CURATED. The attempt is refused and quarantined with the failing rule named | T2 | Must |
| AC-S3 | The teacher model destination is absent from the allow list and a call to it fails at the egress broker with a logged refusal. Distillation is out of scope for Release 1 | T3 | Must |
| AC-S4 | An unauthenticated request to any `/v1` path returns 401. A `viewer` attempting to submit a run returns 403. Both are logged | T4 | Must |
| AC-S5 | Publishing without a signed approval record returns 409. The same identity submitting and approving is permitted only where a recorded single approver exception exists, and the exception is visible in the lineage | T5 | Must |
| AC-S6 | A secret injected into the job environment does not appear in any checkpoint, log or artefact. The pre registration scan detects a planted test secret and blocks registration | T6 | Must |
| AC-S7 | An unsigned plug-in fails to load. A signed plug-in attempting an undeclared capability is refused at runtime, not at review time | T7 | Must |
| AC-S8 | Modifying an artefact after its gates pass causes publication to fail on hash re-verification | T8 | Must |
| AC-S9 | Editing a ledger row directly in PostgreSQL is detected by chain verification within one hour, and the system enters read only mode | T9 | Must |
| AC-S10 | A run whose projected output exceeds the vault free space is refused at planning rather than failing partway | T10 | Must |
| AC-S11 | An executor attempting an outbound connection fails. The attempt appears in the log | T11 | Must |
| AC-S12 | No secret appears in any configuration file in version control. A repository scan in CI returns clean | | Must |
| AC-S13 | A forge disconnected from MEGINGJORD continues to train and record, and a release attempt is refused with a clear reason. On reconnect the queued anchors submit in order and the release becomes available | T12 | Must |
| AC-S14 | A forge ledger verifies its own chain integrity with MEGINGJORD unreachable. No corpus or weight content is present in any federation payload, verified by inspection of the wire format | T13 | Must |
| AC-S15 | The approver identity requires hardware backed multi factor authentication. Every release where the approver also submitted the run carries the sole approver exception, and it is visible in the lineage output and the model card | T14, C-11 | Must |
| AC-S16 | The cryptographic inventory lists every algorithm, key length and module in use, and each entry maps to NCSC guidance or an ISO/IEC standard | C-9 | Must |
| AC-S17 | The signature envelope carries two signatures of different algorithms in a test case, and verification succeeds against either | C-9 | Must |
| AC-S18 | No release metadata reaches any external transparency log. Verified by network capture during a release | D-5 | Must |

### 12.2a  Backend and interface

| Ref | Criterion | Priority |
|---|---|---|
| AC-B1 | Every mutating endpoint honours `Idempotency-Key`. A replayed request returns the original result and does not act twice | Must |
| AC-B2 | Every error response is RFC 9457 problem+json with a stable type URI. No bare 500 reaches a client under any tested failure | Must |
| AC-B3 | Pagination is cursor based throughout. A test inserting rows during pagination shows no skipped or duplicated record | Must |
| AC-B4 | A stale conditional write returns 412 rather than overwriting | Must |
| AC-B5 | The OpenAPI diff gate fails a build on a breaking change within `/v1` | Must |
| AC-B6 | A route registered without a role declaration prevents application startup | Must |
| AC-B7 | An import from the edge layer into the domain layer fails the import linter in continuous integration | Must |
| AC-B8 | All identifiers are UUIDv7 and sort by creation time | Should |
| AC-B9 | No endpoint blocks an HTTP request on training work. Long operations return 202 with a run identifier | Must |
| AC-B10 | Site scope is enforced by row level security, demonstrated by a query with the session variable unset returning no rows rather than all rows | Must |

### 12.2b  Frontend and experience

| Ref | Criterion | Priority |
|---|---|---|
| AC-U1 | All four journeys in section 11F.2 complete end to end in Playwright against a seeded stack | Must |
| AC-U2 | Every JARNGREIPR component has loading, empty, error, denied, read only and partitioned states, each with a Storybook story | Must |
| AC-U3 | No component contains a hard coded colour, spacing or radius value. A token linter enforces this | Must |
| AC-U4 | The run board reflects a state change within 5 seconds by server sent events, with no manual refresh and no full list poll | Must |
| AC-U5 | Every function is reachable and operable by keyboard, with a visible focus indicator. Verified by a manual keyboard pass recorded in the evidence pack | Must |
| AC-U6 | Automated axe scan on every route returns zero serious or critical violations | Must |
| AC-U7 | Contrast meets 4.5:1 for body text and 3:1 for large text and interface components, in both light and dark themes | Must |
| AC-U8 | The console is usable at 200 per cent zoom and at a 320 pixel viewport with no loss of function | Must |
| AC-U9 | `prefers-reduced-motion` is respected across every animated component | Must |
| AC-U10 | A log view with 200,000 lines scrolls without degrading the browser, demonstrating virtualisation | Must |
| AC-U11 | Where more than one site is registered, every view states which site it shows. No unscoped aggregate view exists | Must |
| AC-U12 | A partitioned site is stated plainly in the interface, and the release action is disabled with the reason given, rather than failing with a generic error | Must |
| AC-U13 | The gate queue displays the gate evidence and the sole approver notice before the decision control, not after | Must |
| AC-U14 | Every error surface shows the problem title, the available action and a copyable correlation identifier | Must |
| AC-U15 | Destructive actions are two step with the consequence stated in words | Must |
| AC-U16 | First contentful paint under 1.5 s, interaction to next paint under 200 ms at the 75th percentile, initial bundle under 300 KB gzipped | Should |
| AC-U17 | The command palette covers navigation, run submission and search, and the console is fully operable without a mouse | Should |

### 12.2c  Quality and delivery

| Ref | Criterion | Priority |
|---|---|---|
| AC-Q1 | Every pipeline stage in section 11H runs on the main branch with none skippable | Must |
| AC-Q2 | Client regeneration fails the build when the CLI or TypeScript client has drifted from the OpenAPI specification | Must |
| AC-Q3 | Secret scanning runs on every commit and the repository history is clean | Must |
| AC-Q4 | Property tests cover ledger chain invariants, specification hash determinism and projector idempotence, with at least 500 examples each | Must |
| AC-Q5 | Visual regression snapshots exist for every component in every state and a diff fails the build | Must |
| AC-Q6 | Migrations are forward only, run dry first in deployment, and a failed smoke test triggers rollback | Must |
| AC-Q7 | Container images build for aarch64 from a distroless base and run rootless | Must |
| AC-Q8 | Each of the six development skills in section 11G produces a conforming artefact in a demonstration, and the driver skill output passes the conformance harness unmodified | Must |
| AC-Q9 | A new developer reaches a running stack with seeded data from a clean machine using one documented command | Should |

### 12.3  Non functional

| Ref | Criterion | Target |
|---|---|---|
| AC-N1 | Control plane CPU and memory overhead on ALVISS during a three appliance training run | Under 5 per cent of one core at idle, under 1 GB resident |
| AC-N2 | Control plane adds no measurable overhead to training step time | Step time within 1 per cent of the same job run by hand |
| AC-N3 | Run board reflects a state change | Within 5 seconds |
| AC-N4 | API responds to a run list of 500 entries | Under 300 ms at the 95th percentile |
| AC-N5 | Ledger chain verification over 100,000 entries | Under 60 seconds |
| AC-N6 | Control plane restart to full service, with runs in flight preserved | Under 30 seconds |
| AC-N7 | The application runs on aarch64 Linux and on Apple silicon macOS from the same source | Both, no conditional code paths in the core |
| AC-N8 | Test coverage on the core state machine and ledger | 90 per cent statement coverage, and every transition in section 6.1 exercised |
| AC-N9 | A new `ExportDriver` is added and working | Under 200 lines, no core file modified |
| AC-N10 | OpenAPI specification is complete and both clients are generated from it | No hand written client path |
| AC-N11 | Anchor round trip to MEGINGJORD | Under 2 seconds at the 95th percentile over WireGuard |
| AC-N12 | A forge operating with the federation link down | 72 hours of continuous training with no degradation other than blocked release |

### 12.4  Documentation

| Ref | Criterion |
|---|---|
| AC-D1 | Every module has a README stating its responsibility and its explicit non responsibilities, matching section 5.2 |
| AC-D2 | Every plug-in interface has a reference implementation and a worked example |
| AC-D3 | An operator runbook covers each degraded mode in section 11.2 |
| AC-D4 | This document is re-run through Imhotep against the delivered repository, and every SPECIFIED item is marked IMPLEMENTED, DEVIATED with reasons, or NOT BUILT |

---

## 13  Build prompts

The following prompts are the specification handed to whoever or whatever writes the code, in sequence. Each prompt states its inputs, its deliverable and its exit condition. They are written to be executed one at a time, with review between, rather than issued as a single request.

> **Standing instruction to prepend to every prompt in this section.**
>
> You are building DRAUPNIR, the CIM-56 model factory control plane for Veldris Ltd, specified in VLD-SAD-DRAUPNIR-001. Read that document before writing code and treat it as the requirement, not as a suggestion.
>
> Rules that apply to every task:
> 1. Python 3.12. Must run on aarch64 Linux and Apple silicon macOS from one source tree with no conditional core paths.
> 2. The core knows interfaces, never implementations. If you find yourself importing a framework name into `draupnir/core`, stop and add a driver instead.
> 3. Every state transition writes a ledger entry. There is no other way to change state.
> 4. No secret in configuration, in code, or in a log line.
> 5. Type annotate everything. `mypy --strict` must pass.
> 6. Write the test with the code, not after it. Every transition in the state machine needs a test.
> 7. If the specification is ambiguous, stop and state the ambiguity. Do not guess and proceed.
> 8. British English in all user facing strings and documentation.
> 9. No em dashes in documentation or comments.
> 10. Every entity carries a site scope from the first migration. This is a multi-forge system from day one, not a single installation that federates later.
> 11. Cryptography follows ISO/IEC and NCSC guidance, not FIPS. No public transparency log. Signature envelopes are versioned and carry more than one algorithm.
> 12. Article 53 artefacts are generated from recorded facts. If you find yourself writing a compliance document template with blanks for a human to fill, stop; the fact is somewhere in the ledger and should be read from there.

### 13.1  Prompt 0, repository, toolchain and pipeline

```
Establish the DRAUPNIR repository and delivery pipeline before any feature code.

Deliver:
  repository layout per SAD 11E.1
  pyproject.toml     uv-managed, Python 3.12, aarch64 + Apple silicon
  web/               Vite + React + TypeScript workspace
  .github/ or CI     the full pipeline of SAD 11H
  docker/            aarch64 distroless images, rootless
  Makefile or task runner: one command from clean machine to running stack
  docs/CONTRIBUTING.md

Requirements:
- Every pipeline stage in SAD 11H exists and runs. None skippable on main.
- ruff, mypy --strict, eslint, tsc --noEmit, gitleaks, dependency audit,
  CycloneDX SBOM generation, OpenAPI diff gate, import linter.
- Import linter configured now, enforcing the inward-only dependency rule of
  SAD 11B, so the rule is enforced from the first module rather than asserted.
- Ephemeral PostgreSQL and MinIO for integration tests via containers.
- Playwright, axe and Storybook wired even though there is nothing to test yet;
  an empty harness that runs is worth more than a good harness added at the end.
- Seed script producing a realistic dataset: 2 sites, 6 sources, 12 runs across
  every state, 3 releases, 400 ledger entries.

Exit condition:
- AC-Q1, AC-Q2, AC-Q3, AC-Q6, AC-Q7, AC-Q9 pass.
- `make dev` on a clean machine yields a running stack with seeded data.
```

### 13.2  Prompt 1, foundation and ledger

```
Build the DRAUPNIR core foundation.

Deliver:
  draupnir/core/ledger.py     hash-chained append-only ledger
  draupnir/core/models.py     SQLAlchemy models for the entities in SAD 7.1
  draupnir/core/state.py      the state machine of SAD 6.1 as declarative transitions
  draupnir/core/sites.py      site registry, site scope resolution, anchor state
  alembic/                    forward-only migrations, ledger table append-constrained
  tests/                      unit tests

Requirements:
- entry_hash = sha256(prev_hash || canonical_json(payload)). Canonical JSON means
  sorted keys, no whitespace, UTF-8.
- The ledger table must reject UPDATE and DELETE at the database level via a
  trigger, not only in application code.
- verify_chain(from_seq, to_seq) returns the first divergent sequence or None.
- The state machine is data, not control flow: transitions are declared with
  their guards, and adding a state must not require editing an if-chain.
- Projection tables are rebuildable: rebuild_projection() replays the ledger
  from zero and must produce byte-identical table contents.
- One chain per site. site_id is on ledger_entry, artefact and run from the
  first migration. A query without a site scope must fail, not silently
  default to site 1.
- Chain head export: (site_id, seq, entry_hash) with a detached signature,
  ready for anchoring in Prompt 6.

Exit condition:
- AC-N5 passes: chain verification over 100,000 synthetic entries under 60 s.
- AC-N8 passes on this module: every transition in SAD 6.1 has a test.
- A test proves that a direct UPDATE on the ledger table is refused.
- A test proves rebuild_projection() is idempotent.
- A test proves an unscoped query raises rather than defaulting.
```

### 13.3  Prompt 2, plug-in loader and interfaces

```
Build the plug-in system.

Deliver:
  draupnir/core/plugins.py    entry-point discovery, version negotiation, loading
  draupnir/interfaces/        the seven Protocol definitions from SAD 8.2
  draupnir/interfaces/testing/  a conformance test suite any driver can run
  plugins/local_subprocess/   a reference ScheduleDriver for development

Requirements:
- Interfaces are versioned in the entry-point name, e.g. hamarr.llamafactory/v1.
- The loader supports the current and previous major version only, and refuses
  anything else with a clear error naming the version it found and expected.
- Drivers declare capabilities as a frozenset. The planner refuses a spec
  requiring an undeclared capability, before any allocation.
- TrainDriver.render must be pure. Enforce this in the conformance suite:
  call render twice with the same spec and assert byte-identical JobPlan, and
  assert no network syscall occurs (monkeypatch socket).
- Signature verification is a hook here, implemented in Prompt 6. For now,
  unsigned plug-ins load only when DRAUPNIR_DEV=1 and log a warning.

Exit condition:
- AC-N9 demonstrated: a trivial ExportDriver is added in under 200 lines
  with no file under draupnir/core/ modified.
- The conformance suite passes for the reference ScheduleDriver.
```

### 13.4  Prompt 3, HODD and GLEIPNIR

```
Build the artefact store and the policy gate.

Deliver:
  draupnir/hodd/     ingest, hashing, immutability, licence register, retention,
                     quota, hodd:// URI resolution, StoreDriver implementations
                     for POSIX/NFS and S3/MinIO
  draupnir/gleipnir/ policy engine, gate definitions, approval workflow,
                     PolicyDriver interface and a licence policy implementation

Requirements:
- Ingest is atomic: hash, write manifest, set read-only, then register. A crash
  at any point must not leave a half-registered corpus.
- HODD records licence facts. It must not interpret them. GLEIPNIR judges.
  A code review that finds licence logic in hodd/ fails this task.
- hodd:// URIs resolve through StoreDriver so that physical relocation does not
  invalidate historical run specifications.
- Quota check is pre-flight: estimate output size from the spec and refuse the
  run if it would breach the reserve threshold.
- Approval records are signed. Store the signature, the approver identity and
  the policy version in force at decision time.
- Single-approver exception per SAD 9.4 and constraint C-11: permitted,
  recorded as sole_approver_exception on the approval, surfaced in lineage
  and in the model card. Never silent, never suppressible by configuration.
- Retention: 24 months from the release of the last derived model. Deletion is
  an approved, ledgered retention_action, never a cron job. Curated manifests,
  per-source hashes and licence register entries are retained indefinitely.
  Refuse any retention action that would break a lineage chain, naming the
  affected release.
- Copyright policy is a versioned, machine-readable GLEIPNIR artefact that
  every release references by version.

Exit condition:
- AC-F3, AC-S2, AC-S5, AC-S10 pass.
- A test proves a licence-policy change re-evaluates existing sources without
  re-ingesting them.
- AC-F19 and AC-F20 pass.
```

### 13.5  Prompt 4, MOTSOGNIR and HAMARR

```
Build job dispatch and training execution.

Deliver:
  draupnir/motsognir/  ScheduleDriver for Slurm, array management, concurrency
                       control, retry with backoff, placement policy
  draupnir/hamarr/     TrainDriver for LLaMA-Factory, config rendering,
                       checkpoint policy, structured progress parsing
  plugins/hamarr_llamafactory/

Requirements:
- Array concurrency implements SAD placement: N jobs, exactly M concurrent,
  one per appliance. Map to Slurm --array=0-(N-1)%M.
- On loss of an appliance, concurrency reduces automatically. Ring-partition
  jobs refuse to plan rather than running degraded.
- Checkpoint policy derives save_steps from observed step time so that no more
  than 30 minutes of work is ever unwritten. Recompute after the first 50 steps.
- Progress parsing produces structured events, never regex-scraped strings in
  the UI layer.
- Tier assignment drives base selection: Tier A (GBR, CYP, MLT, IND, CAN, AUS,
  NGA, ZAF, SGP) uses the 27B dense base; Tier B (the remaining 47) uses the
  35B-A3B MoE base. Validate at submission that the two lists enumerate all 56
  with no duplicate and no omission.
- The LLaMA-Factory driver resolves the chat template from a versioned map and
  FAILS LOUDLY on an unknown base model. A silently wrong template produces a
  model trained against the wrong chat format, which is the single most
  expensive failure mode in this pipeline.

Exit condition:
- AC-F4, AC-F5, AC-F6, AC-F13, AC-N2 pass.
- A test proves that an unknown base model raises rather than defaulting.
```

### 13.6  Prompt 5, BRISINGAMEN, RAUN and SKIDBLADNIR

```
Build reweighting, evaluation and release.

Deliver:
  draupnir/brisingamen/  MergeDriver for mergekit, weight sweep orchestration,
                         adapter-to-dense export, route A/B handling
  draupnir/raun/         EvalDriver for lm-evaluation-harness, gate suite E1-E6,
                         baseline management, regression detection
  draupnir/skidbladnir/  ExportDriver for NVFP4, GGUF and MLX; SBOM generation;
                         model card rendering; lineage attestation; publish

Requirements:
- Gate results bind to the artefact SHA-256, never to a path. Publish
  re-verifies the hash and refuses on mismatch.
- Every quantised output is automatically re-gated. There is no path from
  quantisation to approval that skips evaluation.
- The weight sweep is a first-class object: N merge points, each with its gate
  results, comparable in one view, with the selected point recorded in the
  model card.
- The model card is rendered from recorded facts only. If a fact is absent,
  the card says so; it never omits the field silently.
- Article 53 artefacts are generated here, not authored: the training content
  summary on the AI Office template, rendered from the HODD licence register;
  the downstream provider annex; the copyright policy reference by version.
  The template version in force at release date is recorded with the release.
- Article 50 obligations belong to the Midgard Suite, not to DRAUPNIR. Do not
  implement watermarking or synthetic-content marking here.
- MLX export runs on ALVISS and its results are compared against the NVFP4
  results as a cross-platform quantisation check. A divergence beyond
  threshold raises rather than passing.

Exit condition:
- AC-F7, AC-F8, AC-F9, AC-F10, AC-F11, AC-F17, AC-S8 pass.
```

### 13.7  Prompt 6, SVALINN, GULLINBURSTI and MEGINGJORD

```
Build the cross-cutting security layer.

Deliver:
  draupnir/svalinn/  OIDC authentication, RBAC enforcement, secrets broker,
                     plug-in signature verification, artefact signing against
                     the internal PKI, egress broker, executor sandbox profile
  draupnir/gullinbursti/  per-site agent: chain-head anchoring, policy pull,
                     release metadata push, capacity reporting
  megingjord/        federation registry: site registry, anchor store and
                     countersigning, policy distribution, OIDC issuer,
                     signing trust root, self-hosted Rekor

Requirements:
- Authorisation is enforced at the API boundary by a decorator that fails
  closed. A route without an explicit role declaration must fail to register
  at startup, not fail open at runtime.
- Secrets are brokered as short-lived tokens fetched at job start. They are
  never written into a job environment file or a rendered config.
- Pre-registration artefact scan detects secret patterns in checkpoints and
  logs and blocks registration.
- Executors run rootless with no outbound network namespace and a read-only
  artefact mount.
- Egress broker: every outbound call declares destination, purpose, run id and
  approving policy, and is logged. Calls to undeclared destinations fail. The
  teacher-model destination is NOT allow-listed in Release 1.
- Cryptography follows SAD 9.5. TLS 1.3 only. Internal PKI, no public
  transparency log. Signature envelope is versioned and carries multiple
  algorithms so that a post-quantum algorithm can be added without a format
  change. Produce a cryptographic inventory as a build artefact.
- Approver identity requires hardware-backed multi-factor authentication.
- Anchoring: GULLINBURSTI submits (site_id, seq, entry_hash) signed; MEGINGJORD
  verifies chain continuity before countersigning. A forge in partition keeps
  working; release is blocked pending a countersigned anchor.
- MEGINGJORD holds hashes and policy only. Assert in a test that no corpus or
  weight bytes appear in any federation payload.

Exit condition:
- AC-S1, AC-S3, AC-S4, AC-S6, AC-S7, AC-S11 through AC-S18, AC-N11, AC-N12 pass.
- A test proves a route without a role declaration prevents startup.
- A planted test secret in a checkpoint blocks registration.
```

### 13.8  Prompt 7, backend API surface

```
Build the HTTP interface.

Deliver:
  draupnir/api/   FastAPI application implementing SAD 8.1
                  routers, Pydantic schemas, authz guards, problem mapping,
                  idempotency store, cursor pagination, ETag handling,
                  server-sent event stream
  openapi.json    OpenAPI 3.1, complete, the single source for both clients

Requirements:
- Conventions of SAD 11E.2 are not optional: RFC 9457 problem+json,
  Idempotency-Key on every mutating endpoint, cursor pagination, ETag and
  If-Match, UUIDv7, RFC 3339 with offset, 202 for long operations.
- A route registered without an explicit role declaration must prevent
  application startup. Not a warning. Startup failure.
- Site scope enforced by PostgreSQL row level security with a session variable
  set by the site resolver. A query with the variable unset returns no rows.
- Server-sent events carry state deltas, not full list refreshes.
- OpenTelemetry spans from edge through orchestrator to driver boundary.
  Every log line carries run id, site id and actor. No secret, token or
  corpus excerpt in any log line.

Exit condition:
- AC-B1 through AC-B10, AC-N4, AC-N10 pass.
- A test proves an undeclared-role route prevents startup.
- A test proves an unscoped query returns zero rows rather than all rows.
```

### 13.9  Prompt 8, JARNGREIPR design system

```
Build the design system as a standalone package before building screens.

Deliver:
  web/packages/jarngreipr/   tokens, primitives, composites, Storybook
  web/packages/jarngreipr/README.md  usage and contribution rules

Requirements:
- Tokens are the only source of visual values. A hard-coded colour, spacing or
  radius in any component fails the token linter. Light and dark ramps.
- Primitives: button, input, select, checkbox, radio, toggle, table, badge,
  tag, tooltip, dialog, drawer, toast, tabs, breadcrumb, pagination.
- Composites: run card, gate card with evidence, lineage tree, sweep
  comparison matrix, virtualised log viewer, capacity gauge, ledger entry
  viewer, diff viewer.
- EVERY component ships six states: loading, empty, error, denied, read-only,
  partitioned. A component with only a happy path is not done.
- One Storybook story per component per state. Stories are both the
  documentation and the visual regression target.
- WCAG 2.2 AA at the component level: contrast, focus indicator, keyboard
  operation, correct roles and labels, live regions where state changes.
- prefers-reduced-motion respected in every animated component.

Exit condition:
- AC-U2, AC-U3, AC-U6, AC-U7, AC-U9, AC-Q5 pass at component level.
- Storybook builds and every story renders.
```

### 13.10  Prompt 9, console, CLI and local view

```
Build the operator-facing interfaces on top of Prompts 7 and 8.

Deliver:
  web/apps/console/   the DRAUPNIR console, IA per SAD 14 (Figure 9)
  draupnirctl/        Python CLI generated from openapi.json
  tools/stedi-view/   CON-A local status view on DVALIN

Requirements:
- One API. Console and CLI are both generated clients. A hand-written client
  method fails review.
- Implement the four journeys of SAD 11F.2 end to end. Each gets a Playwright
  test that is the acceptance evidence, not an afterthought.
- Run board: server-sent events, state change visible within 5 seconds.
- Lineage explorer: full chain from a released artefact to base model licence
  and corpus hashes, with any gap marked explicitly rather than omitted.
- Gate queue: evidence above the decision control. The sole-approver notice
  is shown before signing, not in a confirmation afterwards.
- Site switcher where more than one site is registered. Every view states its
  site. No unscoped aggregate view.
- Partition state is stated plainly and the release action is disabled with a
  reason, not left enabled to fail.
- Command palette covering navigation, submission and search. Fully operable
  without a mouse.
- CON-A view is read-only, reads the local appliance only, and works when the
  API is unreachable. That is its entire purpose.

Exit condition:
- AC-F1, AC-F14, AC-F18, AC-U1, AC-U4, AC-U5, AC-U8, AC-U10 through AC-U17,
  AC-N3 pass.
```

### 13.11  Prompt 10, development skills

```
Author the six development skills of SAD 11G.

Deliver:
  skills/draupnir-driver/       skills/draupnir-endpoint/
  skills/draupnir-migration/    skills/jarngreipr-component/
  skills/raun-suite/            skills/cim-release/

Requirements:
- Each is a folder with SKILL.md, references and any scripts, matching the
  Veldris skill convention.
- Each is written from the conventions actually established in this build, not
  from the specification. Where the two differ, the code is right and the
  specification is amended.
- draupnir-driver output must pass the conformance harness with no manual
  edit. That is the test of whether the skill is real.
- jarngreipr-component output must include all six states and pass the token
  linter and the axe check.
- Each skill carries a worked example that is exercised in CI, so a skill
  cannot silently rot as the conventions move.

Exit condition:
- AC-Q8 passes: each skill produces a conforming artefact in a demonstration.
```

### 13.12  Prompt 11, integration and acceptance

```
Integrate and demonstrate.

Deliver:
  A full end-to-end run of Procedures M1 through M10 from VLD-INF-SINDRI-001
  for one jurisdiction, executed entirely through DRAUPNIR with no manual
  shell step.

  docs/runbook.md    operator runbook covering every degraded mode in SAD 11.2
  docs/acceptance/   evidence for every AC in SAD 12, one file per criterion

Requirements:
- Run the degraded-mode tests for real: kill the API mid-run, stop Slurm,
  unmount the vault, corrupt a ledger row, sever the federation link, pull
  mains with the UPS fitted. Each must behave as SAD 11.2 states.
- Manual keyboard accessibility pass, recorded with findings, not only the
  automated axe result.
- Produce the acceptance evidence pack as the deliverable, not as an
  afterthought.

Exit condition:
- AC-F12, AC-D1 through AC-D4 pass.
- Every Must criterion in SAD 12 has evidence.
- This SAD is re-run through Imhotep against the repository and each SPECIFIED
  item marked IMPLEMENTED, DEVIATED with reasons, or NOT BUILT.
```

## 14  Delivery sequence and effort

| Prompt | Deliverable | Depends on | Indicative effort |
|---|---|---|---|
| 0 | Repository, toolchain, pipeline | none | 4 to 6 days |
| 1 | Foundation and ledger | 0 | 5 to 8 days |
| 2 | Plug-in system | 1 | 3 to 5 days |
| 3 | HODD and GLEIPNIR | 1, 2 | 6 to 9 days |
| 4 | MOTSOGNIR and HAMARR | 2, 3 | 7 to 10 days |
| 5 | BRISINGAMEN, RAUN, SKIDBLADNIR | 3, 4 | 8 to 12 days |
| 6 | SVALINN, GULLINBURSTI, MEGINGJORD | 2, 3 | 9 to 13 days |
| 7 | Backend API surface | 1 to 6 | 5 to 8 days |
| 8 | JARNGREIPR design system (see VLD-UX-DRAUPNIR-001 UX-0 to UX-3) | 0 | 18 to 26 days |
| 9 | Console, CLI, local view (see VLD-UX-DRAUPNIR-001 UX-4) | 7, 8 | 12 to 18 days |
| 10 | Development skills | 2, 5, 7, 8 | 3 to 5 days |
| 11 | Integration and acceptance | all | 5 to 8 days |
| | **Total** | | **85 to 128 working days** |

Three parallelisation opportunities. Prompt 8 depends only on Prompt 0, so the design system can be built alongside the entire backend. Prompts 4 and 6 can run together once 3 completes. Prompt 10 can begin as soon as the conventions it documents are settled, which is after 7 and 8 rather than at the end.

With one developer the total is the sequence. With a backend and a frontend developer working in parallel the critical path is approximately 60 to 88 working days, because Prompts UX-0 through UX-3 depend only on Prompt 0 and run alongside the entire backend.

> The estimate grew from 51 to 77 days at Revision 1.1 because the frontend, the design system, the delivery pipeline and the development skills were previously implicit. They were always going to be built. Naming them moves the effort from a schedule overrun into the plan.

## 15  Resolved decisions

The open questions carried in Revision 1.0 are closed. The answers are recorded here with their effect on the specification.

| Ref | Question | Answer | Effect |
|---|---|---|---|
| Q1 | Single approver, or must a second be staffed? | Single approver: the creator, codename Akuma | Constraint C-11. Separation of duties unavailable and stated as unmet. Sole approver exception on every affected release, visible in lineage and model card. Compensating controls under T14 and AC-S15 |
| Q2 | Which jurisdictions are Tier A? | Nine: United Kingdom, Cyprus, Malta, India, Canada, Australia, Nigeria, South Africa, Singapore | Tier A on the 27B dense base, three waves, approximately 9 days. Tier B is the remaining forty seven on the 35B-A3B base, sixteen waves, approximately 9 days. AC-F16 validates the split |
| Q2a | What did EU regioning mean? | Veldris falls within EU AI Act scope as a provider | Constraint C-10. Article 53 applies to all fifty six models from Release 1, not to the three EU and UK jurisdictions only. Section 9A |
| Q3 | Is teacher model distillation in scope for Release 1? | No | Threat T3 out of scope. The egress broker is built; the destination is not allow listed. AC-S3 tests the refusal |
| Q4 | Retention period for raw corpora? | 24 months | Section 7.3. Deletion is an approved, ledgered action. Curated manifests, hashes and licence entries retained indefinitely so lineage survives. AC-F19 and AC-F20 |
| Q5 | Is FIPS validated cryptography required? | No. ISO/IEC and NCSC guidance | Constraint C-9. The dedicated Linux control host proposed in an earlier draft is withdrawn and Decision S3 stands: the control plane remains on ALVISS. Section 9.5, Decisions S9 and S10 |
| Q6 | Will DRAUPNIR manage more than one forge? | Yes. Several forges at one location and across locations | Constraint C-8, Decision S7, section 11A. Site scope in the schema from the first migration. GULLINBURSTI and MEGINGJORD added to Prompt 6 |
| Q10 | Data residency in Tier A jurisdictions? | Residency is satisfied by deploying a forge in the jurisdiction, not by constraining processing at a United Kingdom site | The `source.residency_constraint` field becomes load bearing. A residency constrained corpus is planned only at a permitted site, checked at planning. Section 11C |
| Q7 | Uninterruptible supply and offline backup? | Both on order | Gaps G1 and G2 move to mitigated pending delivery. The supply's USB signalling drives a forced checkpoint on mains loss |

## 16  Questions arising from those answers

| Ref | Question | Owner | Needed by |
|---|---|---|---|
| Q8 | Is the cryptographic requirement genuinely ISO/IEC 19790 module conformance, or a customer expressing "approved cryptography" in the vocabulary they know? The architecture is unchanged either way, but the answer determines what has to be procured and what can be claimed in a tender response | Veldris commercial | Prompt 6 |
| Q11 | Should the Article 53 training content summary be published per model, or once for the CIM-56 set as a family? The Regulation permits either reading and the choice affects the release package structure | Counsel | Prompt 5 |
| Q12 | What is the intended second forge, Brokkr, and when? The federation code is built in Prompt 6 either way, but a known date changes how much of it is exercised before Release 1 | Veldris | Prompt 8 |

## 16A  Site 0 standing risk acceptance

Two control concentrations exist and are accepted for Site 0 of the Forge Matrix. They are recorded here rather than designed away, because at a single site operated by its sole principal the alternatives cost more than they return.

| Ref | Concentration | Accepted position |
|---|---|---|
| SA-1 | Release approval and run submission are held by the same individual, so separation of duties is unavailable | Accepted. The control is stated as unmet rather than satisfied differently. Every affected release carries the sole approver exception in lineage and model card |
| SA-2 | The MEGINGJORD offline signing root and the release approver identity are held by the same individual, so the federation trust root and the release authority are not independent | Accepted at Site 0, where the root governs one forge whose sole principal is that individual. The concentration acquires meaning only when the root governs assets or parties beyond that person |

**Review trigger.** This acceptance is reviewed, and expected to be withdrawn, on the first of the following:

1. A second forge anchors to MEGINGJORD, because the root then governs assets beyond Site 0.
2. A model is released to an external customer under contract, because an external party then relies on the approval.
3. Any employee, contractor or co-director acquires release-adjacent access, because separation of duties becomes staffable.
4. Twenty four months from the date of acceptance, whichever is sooner.

The acceptance is dated and signed by the approver, held in the registry, and referenced by identifier from every release package. An undated acceptance is indistinguishable from an oversight at diligence; a dated one with a trigger is a decision.

| Field | Entry |
|---|---|
| Reference | VLD-RA-SINDRI-001 |
| Scope | SA-1 and SA-2, Site 0 only |
| Accepted by | JB Benjamin, Chief Executive Officer |
| Date | |
| Review trigger | As above |
| Signature | |