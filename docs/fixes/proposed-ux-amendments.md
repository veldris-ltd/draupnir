# Change proposal: VLD-UX-DRAUPNIR-001 amendments

**For** VLD-UX-DRAUPNIR-001 Rev 1.0 → Rev 1.1
**Raised by** [remedial-fixes.md](remedial-fixes.md), RF-27
**Status** Proposed. Not applied — the UX specification is issued for build
under its owner's name, and amending it bumps a revision, which is the owner's
to do.

Companion to [proposed-sad-amendments.md](proposed-sad-amendments.md), which
carries the architectural half of the same finding: the permissions these
actions need, and the operations they add to §8.1.

---

## What was wrong

Section 8 gives each of the thirty one screens a primary action. Nine of them
named an action no API operation could perform, and the console offered none
of those controls either — so the screens agreed with the API and disagreed
with the specification, and nothing compared the two.

| Screen | Primary action in §8 | Outcome of RF-27 |
|---|---|---|
| S05 Curation run | Re-run a stage | Read only; amendment 2 |
| S06 Retention schedule | Approve a retention action | Built: `approveRetention` |
| S12 Array monitor | Requeue a single element | Built: `requeueArrayElement` (RF-13), with its console control |
| S15 Sweep comparison | Select a merge point | Built: `selectMergePoint` |
| S17 Release package | Download the package | Built: `downloadReleaseDocument` |
| S22 Sites | Register a site | Read only; amendment 3 |
| S23 Plug-ins | Enable or disable | Read only; amendment 3 |
| S24 Policy | Publish a policy version | Read only; amendment 3 |
| S25 Users and roles | Assign a role | Read only; amendment 3 |

The two documents are now compared by a test.
[`docs/api/screen-actions.json`](../api/screen-actions.json) records what
performs every row of section 8 — an operation, the read a navigation action
opens, or a stated reason the screen is read only — and
`tests/unit/test_screen_inventory.py` fails if a screen, its action text or an
operation drifts from this document or from `docs/api/openapi.json`.

---

## Amendment 1 — S06, S12, S15 and S17: built; rows unchanged

These four rows stand as written. What changed is that each now has an
operation and a console control, so no amendment to their text is proposed.
Recorded here so a reader of Rev 1.1 knows the actions were built rather than
assumed.

| Screen | Operation | What it does, and what it deliberately does not |
|---|---|---|
| S06 | `POST /v1/retention/{action_id}/approve` | Records an approver's decision and deletes nothing. The daily retention duty carries it out and records the outcome, and refuses a deletion that would leave no curated manifest, naming the releases (AC-F20). Needs a hardware authenticator: a deleted corpus cannot be re-read. |
| S12 | `POST /v1/arrays/{name}/elements/{index}/requeue` | Offered only on an element that stopped without completing. A completed element requeued is an adapter trained twice. |
| S15 | `POST /v1/sweeps/{run_id}/select` | Chooses among the points RAUN passed. The worker merges and re-gates every point first and waits at MERGED for the choice; the whole comparison goes on the model card. |
| S17 | `GET /v1/releases/{artefact}/documents/{document}` | Downloads one document of the package, generated from the release record and dated by the release, so the same download is the same bytes. |

### A proposed sentence for §9, under S15

> The run waits at MERGED once its sweep has been merged and re-gated, and is
> quantised from the point an operator chooses. Only a point that clears every
> blocking gate can be chosen; the others stay on screen and on the model card,
> because a comparison that dropped them would be a comparison of survivors.

---

## Amendment 2 — S05 is read only in this release

### What was wrong

"Re-run a stage" cannot be performed, and not for want of an operation. A
curated corpus is ingested into HODD and sealed at its address, so curating the
same jurisdiction again is refused by the store rather than overwriting what a
run was trained on. And a single stage is not something to re-run on its own:
decontamination against the evaluation sets cannot be skipped, so "re-run
dedupe" is re-running curation.

A different curation is a new corpus, with its own address, hash and lineage.
Building that is a change to corpus addressing and to how a specification names
its dataset, and is not proposed here.

### Proposed row

| Ref | Screen | Purpose | Primary action | Role |
|---|---|---|---|---|
| S05 | Curation run | Stage by stage retention and decontamination result | None. Read only: a curated corpus is sealed, and a different curation is a new corpus | curator |

---

## Amendment 3 — S22 to S25 are read only at the forge

### What was wrong

Each of these names an administrative action that the architecture places
somewhere other than a forge's console. Building any of them would create a
second authority for something another component owns.

| Screen | Why the forge does not perform it |
|---|---|
| S22 Sites | A site is registered in MEGINGJORD when it is commissioned, with its core deployed and its certificates issued (SAD 10.2, 11A.1). A forge that registered sites would be a second site registry the federation does not hold. |
| S23 Plug-ins | Plug-ins install into the API environment as signed distributions and are verified at first load (SAD 11.1); the installed set cannot change without a restart. Enabling one is a signed, deploy-time step. |
| S24 Policy | A policy change is a GLEIPNIR configuration change, file based and version controlled (SAD 5.2, 11.1), distributed by MEGINGJORD and pulled by each forge (SAD 11A.1). A forge that published versions would diverge from what the federation distributes. |
| S25 Users and roles | Roles are assigned in the identity provider and arrive in the token, with MEGINGJORD the RBAC source of truth (SAD 11A.1). A role assigned in the console would be a grant the identity provider knows nothing about. |

The screens stay: each shows what is in force at this forge — the registry of
sites, the installed plug-ins and their signature status, the policy rules the
licence gate decides with, and the role table generated from the enforced route
declarations.

### Proposed rows

| Ref | Screen | Purpose | Primary action | Role |
|---|---|---|---|---|
| S22 | Sites | The Forge Matrix, anchor state per site | None. Read only: sites are registered in MEGINGJORD at commissioning | admin |
| S23 | Plug-ins | Version, capabilities, signature status | None. Read only: plug-ins are installed signed, at deployment | admin |
| S24 | Policy | Licence, copyright and retention policy versions | None. Read only: policy versions are published in MEGINGJORD | admin |
| S25 | Users and roles | Named accounts and their roles | None. Read only: roles are assigned in the identity provider | admin |

### A consequence worth stating

SAD 9.4 gives `admin` "Manage users, plug-ins and policy", and the code grants
`MANAGE_USERS`, `MANAGE_PLUGINS` and `MANAGE_POLICY` to admin — and no route
requires any of them. Amendment 3 makes that correct at the forge rather than
a gap. The SAD proposal records the question of what admin manages, and where.

---

## When these are applied

The drift test quotes section 8 verbatim. Applying amendments 2 and 3 changes
five rows' action text, so the test fails until
`docs/api/screen-actions.json` quotes the new text and drops those rows'
`amendment` citations. That is intended: the register follows the
specification, never the other way round, and a citation to a proposal that has
been applied would be a reference to nothing.

### Proposed revision history row

| Rev | Date | Author | Summary of change |
|---|---|---|---|
| 1.1 | — | — | Section 8: S05 and S22 to S25 stated read only, with reasons. S06, S12, S15 and S17 primary actions backed by operations (RF-27). §9: S15 waits for an operator's choice of merge point. |
