<!--
  VLD-UX-DRAUPNIR-001 Rev 1.0
  CONFIDENTIAL - RECIPIENT EYES ONLY
  Veldris Ltd, company no. 17366869
-->

# DRAUPNIR

## Experience and Interface Design Specification
### Console and JARNGREIPR Design System

> **CONFIDENTIAL — RECIPIENT EYES ONLY**

Visual language, information architecture, user flows, accessibility and the build specification for every screen of the DRAUPNIR console.

| Field | Value |
|---|---|
| Document reference | VLD-UX-DRAUPNIR-001 |
| Revision | 1.0 |
| Status | Issued for build. Forward specification, not a repository survey |
| Date of issue | 28 August 2026 |
| Author and owner | JB Benjamin, Chief Executive Officer |
| Asset owner | Veldris Ltd, company no. 17366869, 128 City Road, London EC1V 2NX |
| Programme | Midgard Suite, CIM-56 |
| Governs | DRAUPNIR console and the JARNGREIPR design system |
| Companion documents | VLD-SAD-DRAUPNIR-001 Rev 1.4; VLD-INF-SINDRI-001 Rev 3.3 |
| Precedence | Governs frontend matters. The SAD governs architectural matters |

### Revision history

| Rev | Date | Author | Summary of change |
|---|---|---|---|
| 1.0 | 2026-08-28 | JB Benjamin | Initial issue. Design principles, users and contexts, JARNGREIPR token system and component library, thirty one screen inventory, four user journeys, interaction and accessibility specification, sixty two acceptance criteria and five build prompts |

### Distribution and handling

This document is confidential to Veldris Ltd and is issued to named recipients only. It specifies the interface through which commercially released artefacts are approved and published. It is not to be reproduced, forwarded or disclosed in whole or in part without the written authority of the author.

---

## Contents

- **[Part 1  Foundations](#part-1-foundations)**
  - [1  Purpose, scope and status](#1-purpose-scope-and-status)
  - [2  Design principles](#2-design-principles)
  - [3  Users and contexts of use](#3-users-and-contexts-of-use)
- **[Part 2  JARNGREIPR visual language](#part-2-jarngreipr-visual-language)**
  - [4  Tokens](#4-tokens)
  - [5  Component library](#5-component-library)
  - [6  Content and microcopy](#6-content-and-microcopy)
- **[Part 3  Architecture, screens and flows](#part-3-architecture-screens-and-flows)**
  - [7  Information architecture](#7-information-architecture)
  - [8  Screen inventory](#8-screen-inventory)
  - [9  Key screens in detail](#9-key-screens-in-detail)
  - [10  User journeys](#10-user-journeys)
  - [11  Interaction model](#11-interaction-model)
  - [12  Accessibility](#12-accessibility)
- **[Part 4  Build specification](#part-4-build-specification)**
  - [13  Acceptance criteria](#13-acceptance-criteria)
  - [14  Build prompts](#14-build-prompts)
  - [15  Delivery sequence and effort](#15-delivery-sequence-and-effort)
  - [16  Design operations](#16-design-operations)
  - [17  Open questions](#17-open-questions)

---
---

# Part 1  Foundations

## 1  Purpose, scope and status

### 1.1  Purpose

This document specifies the visual design, interaction model, information architecture and user flows for the DRAUPNIR console, and for JARNGREIPR, the design system it is built on. It is the design authority for the frontend work described in Prompts 8 and 9 of VLD-SAD-DRAUPNIR-001.

The console is the surface through which the fifty six Midgard Commonwealth Intelligence Models are curated, trained, reweighted, evaluated, approved and released. Everything the pipeline records is only as useful as the interface that surfaces it, and an approval control that can be pressed without reading the evidence produces a signature that means nothing.

### 1.2  Status

> **This is a forward specification.** No DRAUPNIR repository exists at the time of writing. Every screen, component, token and flow described here is **SPECIFIED**, meaning it is a requirement on the implementation, and none is **OBSERVED**. Wireframes are low fidelity: they fix layout, hierarchy, content and state, not final visual treatment. Values shown in them are illustrative and are not real data.
>
> Where this document and VLD-SAD-DRAUPNIR-001 disagree on a frontend matter, this document governs. Where they disagree on an architectural matter, the SAD governs.

### 1.3  Scope

In scope: design principles, users and contexts of use, the visual language and its tokens, the component library, the screen inventory, the navigation model, the four primary journeys, interaction and accessibility requirements, content and microcopy standards, design operations, and the prompts and acceptance criteria by which the frontend is to be produced.

Out of scope: the backend API, which is SAD section 8; the Midgard Suite customer facing interfaces, which will adopt JARNGREIPR but are specified separately; and the Veldris marketing identity, which is a separate brand asset.

### 1.4  Naming

**JARNGREIPR**, the iron gloves with which a smith handles work too hot to touch by hand, is the design system. The name follows the Veldris standard recorded in VLD-INF-SINDRI-001 section 1.5, which reserves gods and realms to the Midgard product line and draws infrastructure naming from smiths, dwarves and their works.

The choice is not decorative. The console exists so that an operator can handle a process otherwise too large, too slow and too consequential to hold directly.

## 2  Design principles

Six principles in priority order. Where two conflict, the lower ranked yields.

| # | Principle | What it means in practice |
|---|---|---|
| 1 | **Evidence before decision** | Any control that commits an irreversible act sits below the evidence for it. The approve button is beneath the gate results, never beside a summary badge. A decision made without seeing the basis is a decision the audit record cannot defend |
| 2 | **State the truth about state** | The interface says what is happening, including when what is happening is bad. A partitioned site says so. A degraded run says so. Nothing hides behind a spinner that resolves into a generic failure |
| 3 | **Disabled with a reason, never enabled to fail** | An action that cannot succeed is disabled and the reason is stated in place. The alternative teaches operators that errors are random |
| 4 | **The record is the product** | Lineage, ledger and provenance are primary navigation, not an export. An auditor reaches a complete chain in three interactions or fewer |
| 5 | **Fast to read, slow to destroy** | Reading is optimised for scanning: dense tables, live updates, keyboard navigation. Destruction is deliberately slow: two steps, the consequence in words, the artefact name typed for a release withdrawal |
| 6 | **One visual language across the estate** | JARNGREIPR is a separate package because the Midgard Suite will adopt it. A component built only for this console is a component built twice |

**DESIGN DECISION U1. The approve control sits below the evidence, not in a sticky action bar.**

Rationale. A persistent action bar is the conventional pattern and is better for speed. It also lets an approver sign without passing the gate results, which defeats the only structural control available at Site 0, where separation of duties does not exist. Speed is the wrong optimisation for the one irreversible action in the system.

Alternative rejected. A sticky footer with the approve control always visible, which is faster and removes the reason the control exists.

## 3  Users and contexts of use

Four roles, defined in SAD section 9.4. At Site 0 one individual holds several of them, which the interface must handle without pretending otherwise.

| Role | What they are doing | Frequency | Pressure |
|---|---|---|---|
| Curator | Registering sources, declaring licences and personal data, running curation | Bursts of several sources, then nothing for days | Low. Accuracy matters more than speed |
| Operator | Composing specifications, submitting runs, watching progress, diagnosing failures | Daily, with longer sessions when something fails | Moderate. A failure that idles an appliance costs hours |
| Approver | Reading gate evidence, signing, publishing | Weekly to a few times a week | High consequence, low frequency. Rare enough to be unfamiliar, serious enough to matter |
| Auditor | Reconstructing how a released model was made | Rare, usually under external scrutiny | High. Often performed with a third party watching |

### 3.1  Contexts

| Context | Device | Density | Purpose |
|---|---|---|---|
| Workstation | Desktop browser, 1440 px and above | Comfortable | All four journeys. The primary context |
| CON-A, STEDI U10 to U12 | 9 inch 1280 x 720 touch panel driven directly by DVALIN | Compact | Local appliance status. Read only. Works when the API and the network do not |
| CON-B, TANGIR | 9 inch 1280 x 720 touch panel driven by REGIN | Compact | Thermal, fabric and queue dashboards in kiosk mode. Read only |
| Remote | Desktop browser over WireGuard | Comfortable | As workstation, over a slower link |

**DESIGN DECISION U2. CON-A is not a small version of the console.**

Rationale. CON-A exists because the DGX Spark has no baseboard management controller, so it is the only console that survives a total network failure. Its entire value is that it depends on nothing beyond the appliance it is attached to. Rendering the web console on it would make it depend on the API, the network and the scheduler, which is exactly what it is there to outlive.

Alternative rejected. A responsive console layout at 1280 x 720, which is less code and forfeits the only console that works during an outage.

### 3.2  Anti-goals

The console is not a chat interface, not a notebook, and not a general purpose model playground. It offers no inference against released models; that is the Midgard Suite. Every feature request beginning "while we are in here" is assessed against that boundary.

---

# Part 2  JARNGREIPR visual language

![Figure 1. JARNGREIPR design tokens](diagrams/20_tokens.png)

## 4  Tokens

Tokens are the only source of visual values. A hard coded colour, spacing, radius or duration in any component fails the token linter and fails acceptance criterion AC-V3.

### 4.1  Colour

| Ramp | Purpose | Values |
|---|---|---|
| ink | Text, surfaces, chrome, borders | 900 `#0E1A2B`, 800 `#1F3350`, 700 `#2A4A6B`, 600 `#3E5C82`, 400 `#7E8FA3`, 300 `#B9C6D4`, 100 `#E9F0F7`, 50 `#F5F8FB` |
| forge | Accent, primary action, active navigation, training state | 800 `#8A5A08`, 700 `#C6851C`, 500 `#E0A030`, 300 `#EFC272`, 100 `#FBEFD6`, 50 `#FDF7EC` |
| success | Passed gates, released, healthy | 800 `#1B5E3A`, 500 `#2E8B57`, 300 `#7CC49B`, 50 `#EAF4EE` |
| warning | Margin breach, sole approver notice, attention | 800 `#8A5A08`, 500 `#D98E04`, 300 `#F0C15C`, 50 `#FBF3E0` |
| danger | Failure, rejection, destructive action | 800 `#7A2A1F`, 500 `#B3402F`, 300 `#DC8C80`, 50 `#FBF2F0` |
| info | Network, fabric, informational | 800 `#0F5760`, 500 `#177E89`, 300 `#7DBFC6`, 50 `#F2FAFB` |
| merge | Reweighting, merged artefacts, partition state | 500 `#6C5B9E`, 100 `#E8E2F5`, 50 `#F4F1FA` |

Dark theme surfaces: base `#0B1420`, raised `#14202F`, overlay `#1D2C3E`, primary text `#E9F0F7`, secondary text `#7E8FA3`. Accent and semantic ramps shift one step lighter in dark theme to hold contrast.

### 4.2  Run state semantics

State colour is a token, not a per component choice, so a state reads the same on a table row, a card, a badge and a timeline.

| State | Token | Rationale |
|---|---|---|
| QUEUED | ink 600 | Inert. Nothing is happening yet |
| TRAINING | forge 500 | The active state, and the accent of the whole system |
| TRAINED | forge 300 | Active work finished, not yet judged |
| EVALUATING | ink 800 | Under judgement |
| MERGED | merge 500 | Reweighting is a distinct kind of act and reads distinctly |
| QUANTISED | ink 900 | Near release, deliberately sober |
| AWAITING_APPROVAL | warning 500 | Requires a human. Warning, not danger: nothing is wrong |
| RELEASED | success 500 | Terminal and good |
| FAILED | danger 500 | Terminal and bad, recoverable by retry |
| QUARANTINED | danger 800 | Withdrawn and retained. Darker than failed, because it is a decision rather than an accident |

Colour never carries meaning alone. Every state renders as a text label inside its pill, and every gate result renders a value and a margin beside its colour.

### 4.3  Typography

Inter for interface text. JetBrains Mono for hashes, identifiers, log output, specifications and any value the user may need to compare character by character.

| Role | Size / line height | Weight | Use |
|---|---|---|---|
| display | 32 / 40 | 600 | Page title on a detail screen |
| h1 | 24 / 32 | 600 | Screen title |
| h2 | 20 / 28 | 600 | Section |
| h3 | 16 / 24 | 600 | Card and panel title |
| body | 14 / 22 | 400 | Default |
| small | 13 / 20 | 400 | Table cells at comfortable density, secondary text |
| caption | 12 / 16 | 400 | Column headers, metadata, timestamps |
| mono | 13 / 20 | 400 | Hashes, identifiers, logs, YAML |

Hashes are truncated to eight characters followed by an ellipsis, with the full value on hover and a copy control. A truncated hash is never presented without a way to obtain the whole of it.

### 4.4  Space, radius, elevation, motion

Space scale on a 4 px base: 4, 8, 12, 16, 24, 32, 48, 64. Radius: 2 for inputs, 4 for cards, 8 for panels, 12 for dialogs, pill for badges. Elevation in three levels only: flat, card, overlay. Motion: 120 ms micro, 200 ms standard, 320 ms large, easing `cubic-bezier(0.2, 0, 0, 1)`, all suppressed under `prefers-reduced-motion`.

### 4.5  Density

| Mode | Row height | Body | Gutter | Context |
|---|---|---|---|---|
| Comfortable | 44 px | 14 px | 24 px | Desktop browser. Default |
| Compact | 32 px | 13 px | 12 px | CON-A and CON-B at 1280 x 720. Touch targets remain 44 px regardless |

Compact reduces chrome and spacing. It never reduces touch target size, and it never introduces a composition task. Both consoles are read only.

### 4.6  Iconography and data visualisation

Icons are a single stroke weight from one set, used only where a label alone is ambiguous. No icon carries meaning without a text label beside it or an accessible name.

Charts use the ink and forge ramps, never a categorical rainbow. A loss curve is forge; a baseline comparison is ink 400. Gate margins render as a signed number with a colour, never as a bare colour. Any chart conveying a pass or fail also states the value.

---

## 5  Component library

![Figure 2. Universal component states and content rules](diagrams/21_states.png)

### 5.1  Primitives

| Component | Notes beyond the obvious |
|---|---|
| Button | Four intents: primary, secondary, danger, ghost. Loading state disables and announces. No icon only button without an accessible name |
| Input, textarea | Label always visible. Placeholder never substitutes for a label. Error text below, associated by `aria-describedby` |
| Select, combobox | Native select where the option set is short. Combobox with filtering above twelve options |
| Checkbox, radio, toggle | Toggle is for an immediate effect, checkbox for a value submitted later. The two are not interchangeable |
| Table | Sortable, column visibility, sticky header, row density from the density token. Header cells are `th` with scope |
| Badge and pill | Carries the state token. Text always present |
| Tooltip | Supplementary only. Never the sole carrier of information, never on touch only |
| Dialog | Focus trapped, restores focus on close, escape closes unless destructive |
| Drawer | For detail beside a list without losing list context |
| Toast | Transient confirmations only. Never an error that requires action |
| Tabs, breadcrumb, pagination | Cursor based pagination. No page numbers, because the underlying data grows during reading |

### 5.2  Composites

| Component | Purpose | Critical detail |
|---|---|---|
| Run card | A run on the board | State pill, progress, node, elapsed, and estimated remaining. Estimate is omitted rather than guessed when step time is not yet stable |
| Gate card | One gate result | Value, baseline, margin and pass or fail. Never a bare tick |
| Evidence panel | The full E1 to E6 set | Sits above any decision control. Sortable by margin so the tightest result is findable |
| Lineage tree | Release back to base licence and corpus | Renders gaps explicitly as a marked node, never by omission |
| Sweep matrix | Merge points against gate results | Highlights the selected point and states the trade in words beneath |
| Log viewer | Run output | Virtualised. Follows tail by default, releases follow on scroll up, and says so |
| Ledger entry viewer | One transition | Actor, timestamp, transition, payload, entry hash and previous hash |
| Capacity gauge | Appliance and vault utilisation | Thresholds from policy, not hard coded |
| Diff viewer | Specification and policy versions | Used when comparing a retried run against its predecessor |
| Spec editor | Compose a run | Schema validation inline, dry run before submit, never submits on enter |

### 5.3  The six universal states

Every component ships all six. A component with only a happy path is incomplete and fails AC-V2.

| State | Rule |
|---|---|
| Loading | Skeleton for content. A spinner only for an action expected to complete within a second |
| Empty | Says what would be here and offers the single action that creates it |
| Error | Problem title, the action available, and a copyable correlation identifier |
| Denied | Names the role required and the role held. "Forbidden" is not an explanation |
| Read only | States why: ledger divergence, auditor role, or an archived run |
| Partitioned | The site is cut off from MEGINGJORD. Training continues, release is unavailable, and both facts are stated |

## 6  Content and microcopy

British English throughout. Sentence case for labels and headings. No title case. Capitals reserved for state pills and acronyms.

| Rule | Example |
|---|---|
| Errors name the thing that failed and the action available | "Out of memory during the backward pass. A sequence of 14,208 tokens exceeded the cutoff of 8,192." Not "Something went wrong" |
| Empty states describe what belongs there | "No runs yet" with a submit control, not "No data" |
| Denied states name roles | "Requires role: approver. You hold: operator" |
| Numbers carry units, durations are human | "11 h 04 m", "62.4 GB", "+1.2 pp" |
| Destructive confirmations state the consequence | "This withdraws MIDGARD-CIM-GBR-v1.0 from the registry. Existing downloads are unaffected. Type the artefact name to confirm." Not "Are you sure?" |
| Terminology is fixed | Site is a forge. Node is an appliance. Rank is a position in a job. Never interchanged |
| The sole approver notice is a statement of record | "You submitted this run and you are approving it." Not a warning, not an error. A disclosed fact |

**DESIGN DECISION U3. Terminology is enforced by a lint rule over user facing strings, not by review.**

Rationale. VLD-INF-SINDRI-001 Decision S12 separates site, node and rank because the estate already used "node" in two senses. A convention held only by reviewer memory decays. A rule that fails a build does not.

Alternative rejected. A glossary in the contributor guide, which is what every project has and which is why every project drifts.

---

# Part 3  Architecture, screens and flows

## 7  Information architecture

![Figure 3. Console information architecture](diagrams/14_ui_ia.png)

Seven primary sections, chosen to match the shape of the work rather than the shape of the data model. Corpora, Runs, Models and Gates correspond to the four journeys. Overview, Admin and Audit are supporting.

The shell carries the site switcher, global search, the command palette and the alert tray. Site context is always visible, because a Forge Matrix with more than one site makes an unlabelled view dangerous rather than merely unclear.

## 8  Screen inventory

![Figure 4. Screen inventory and navigation model](diagrams/25_screens.png)

Thirty one screens. Every screen has a purpose, a primary action, and a defined behaviour in all six universal states.

| Ref | Screen | Purpose | Primary action | Role |
|---|---|---|---|---|
| S01 | Overview | Health of the site at a glance | Navigate to the thing that needs attention | viewer |
| S02 | Corpus list | Corpora by jurisdiction with status and token count | Open a corpus | viewer |
| S03 | Source register | Every source with licence, attribution, personal data, residency, hash | Register a source | curator |
| S04 | Register source wizard | Four steps ending at the data protection gate | Continue, or resolve the gate | curator |
| S05 | Curation run | Stage by stage retention and decontamination result | Re-run a stage | curator |
| S06 | Retention schedule | Corpora approaching the 24 month deletion point | Approve a retention action | approver |
| S07 | Run board | All runs, filtered, live | Submit a run | operator |
| S08 | Run detail | One run across six tabs | Cancel, or retry | operator |
| S09 | Compose run | Specification editor with inline validation | Dry run | operator |
| S10 | Dry run result | The rendered job plan, no allocation consumed | Submit | operator |
| S11 | Failure diagnosis | Cause, suggested action, supporting evidence | Retry with the correction applied | operator |
| S12 | Array monitor | The fifty six element adapter array | Requeue a single element | operator |
| S13 | Model registry | All jurisdictions with tier and version | Open a model | viewer |
| S14 | Model detail | Card, artefacts and quantised builds | Open lineage | viewer |
| S15 | Sweep comparison | Merge points against gate results | Select a merge point | operator |
| S16 | Lineage explorer | Release back to base licence and corpus hashes | Export attestation | viewer |
| S17 | Release package | Card, SBOM, manifest, Article 53 artefacts | Download the package | viewer |
| S18 | Approval queue | Artefacts awaiting decision, by age | Review | approver |
| S19 | Approval detail | Full evidence, then the decision | Sign and approve | approver |
| S20 | Publish | Pre-flight checklist and anchor state | Publish to registry | approver |
| S21 | Reject | Reason required, artefact quarantined | Reject | approver |
| S22 | Sites | The Forge Matrix, anchor state per site | Register a site | admin |
| S23 | Plug-ins | Version, capabilities, signature status | Enable or disable | admin |
| S24 | Policy | Licence, copyright and retention policy versions | Publish a policy version | admin |
| S25 | Users and roles | Named accounts and their roles | Assign a role | admin |
| S26 | Ledger explorer | Filterable ledger with chain verification | Verify chain | viewer |
| S27 | Ledger entry detail | Payload, actor, entry and previous hash | Copy entry | viewer |
| S28 | Attestation export | Signed lineage bundle | Export | viewer |
| S29 | Sign in | OIDC, hardware backed second factor for approver | Sign in | none |
| S30 | CON-A local view | Local appliance state, no API dependency | None. Read only | none |
| S31 | CON-B ops dashboard | Thermal, fabric and queue in kiosk mode | None. Read only | none |

## 9  Key screens in detail

![Figure 5. Run board and run detail](diagrams/22_wire_runs.png)

### 9.1  S07 Run board and S08 Run detail

The board is the operator's home. Six columns at comfortable density: jurisdiction, base, state, progress, node, elapsed. Live by server sent events, with the freshness stated in words beneath the table rather than implied by an animation.

Run detail carries six tabs, in this order because it is the order of enquiry when something is wrong: Overview, Specification, Logs, Gates, Lineage, Ledger. The loss curve and the run facts sit side by side above a virtualised log tail, so the three questions an operator asks first are answered without a tab change.

### 9.2  S11 Failure diagnosis

A failure screen that shows only a stack trace transfers the diagnostic work back to the operator. This screen states the cause in a sentence, the suggested correction as a concrete parameter change, and the evidence for both.

The wireframe shows the out of memory case, which is the most common failure in this pipeline and is almost always a long sequence in the data rather than a configuration error. The screen therefore renders the token length distribution from the dataset that produced the failure, with the 99th percentile marked, because that is the number the operator needs in order to act.

![Figure 6. Gate queue, approval, publish and partitioned state](diagrams/23_wire_gates.png)

### 9.3  S18 Approval queue and S19 Approval detail

The queue orders by waiting time, not by jurisdiction, so nothing ages quietly. Each entry shows the gate summary and the age.

Approval detail is the screen this whole document exists for. The evidence table renders all six gates with value, baseline, margin and result. Below it, and only below it, sits the sole approver notice and then the two decision controls. The notice is styled as warning rather than danger because nothing is wrong: it is a disclosed fact about how this release was approved, and it will appear in the lineage and the model card whatever the approver does next.

### 9.4  S20 Publish and the partitioned variant

Publish is a pre-flight checklist of nine conditions, all of which must be green. The ninth is the federation anchor. When the site is partitioned, the checklist cannot complete, and the screen states why in a panel above a disabled control rather than allowing the attempt and returning an error.

![Figure 7. Lineage, sweep comparison and corpus registration](diagrams/24_wire_lineage.png)

### 9.5  S16 Lineage explorer

A vertical chain from release to corpus, each node carrying its identifying hash and its key fact. The banner beneath states chain completeness and the ledger sequence verified. A gap renders as a marked node with what is missing, never as a shorter tree.

### 9.6  S15 Sweep comparison

The reweighting decision is a trade, and the screen presents it as one. Five merge points against four gates, with values that breach a floor shown in danger and the selected point outlined. The sentence beneath states the trade in words, because a matrix of twenty numbers does not by itself tell an operator that the higher scoring points fail a different gate.

### 9.7  S04 Register source wizard

Four steps, with the data protection gate at step two. When personal data is declared, the DPIA reference field becomes required and a panel explains why, including the observation that legal corpora are dense with named individuals so the gate applies to most jurisdictions rather than a minority. The residency constraint field records the sites permitted to hold the corpus.

### 9.8  S30 CON-A local view

Rendered by DVALIN, not by the console. Eight lines of local state: GPU, throttle, fabric, ring, current run, vault, scheduler and API. The last two are expected to read unreachable during an outage, and the view continues to function, which is its purpose.

## 10  User journeys

![Figure 8. The four primary journeys](diagrams/26_journeys.png)

Each journey is an end to end Playwright test and constitutes the acceptance evidence for AC-J1 to AC-J4.

### 10.1  J1 Curate

Corpora, register source, declare licence and attribution, answer the personal data question, supply a DPIA reference where required, set any residency constraint, ingest and hash, curate. Error path: a licence that fails policy quarantines the source and names the failing rule.

Target: a new source registered and curating within six minutes.

### 10.2  J2 Operate

Runs, compose specification, dry run, submit, watch the board, and on failure diagnose and retry. The dry run step exists so that a specification error costs nothing; an allocation on this estate is the scarce resource.

Target: three minutes to submit, two minutes from failure to a corrected retry.

### 10.3  J3 Approve

Gate queue, open artefact, read all six gate results, see the sole approver notice, decide. On approval the anchor state is checked; where the site is partitioned the artefact is held and the reason stated. On rejection a reason is required and the artefact is quarantined rather than deleted.

Target: no time target. This journey is deliberately not optimised for speed.

### 10.4  J4 Audit

Models, select release, lineage explorer, walk to base licence and corpus hashes, verify the ledger chain, export the attestation. A gap in the chain is rendered explicitly.

Target: a complete lineage reached in three interactions or fewer.

## 11  Interaction model

| Concern | Specification |
|---|---|
| Live updates | Server sent events carrying state deltas. The board reflects a change within five seconds. No full list polling |
| Command palette | `⌘K` or `Ctrl+K`. Covers navigation, run submission, search across runs, models, corpora and ledger entries |
| Keyboard | Every function reachable and operable by keyboard. Visible focus indicator throughout. Tables support arrow navigation and type ahead |
| Focus management | A dialog traps focus and restores it on close. A navigation change moves focus to the new page heading |
| Optimistic updates | Not used. A state change is shown when the ledger has recorded it. An optimistic interface that later reverts is worse than one that waits |
| Destructive actions | Two step, consequence in words. Release withdrawal requires typing the artefact name |
| Long output | Log and ledger views virtualise. Two hundred thousand lines must not degrade the browser |
| Deep links | Every screen has a URL that restores its state, including filters and the selected site |
| Session expiry | Warned before it happens, with work preserved. A specification in progress is never lost to a token refresh |

## 12  Accessibility

Conformance target: WCAG 2.2 level AA, verified by automated scan on every route and by a recorded manual keyboard pass.

| Requirement | Specification |
|---|---|
| Contrast | 4.5:1 body text, 3:1 large text and interface components, in both themes |
| Keyboard | Complete operation without a mouse. No keyboard trap. Skip link to main content |
| Focus | Visible indicator meeting the 2.2 focus appearance criterion. Never removed for aesthetics |
| Target size | 24 by 24 CSS pixels minimum, 44 by 44 on touch contexts |
| Zoom and reflow | Usable at 200 per cent and at 320 px width without loss of function |
| Motion | `prefers-reduced-motion` respected across every animated component |
| Screen reader | Live regions announce run state changes and gate results. Tables use proper header association. Every icon has an accessible name |
| Forms | Labels visible and associated. Errors announced and linked to the field. No error conveyed by colour alone |
| Language | `lang` set. Jurisdiction content in a non-English language carries its own `lang` |

**DESIGN DECISION U4. Accessibility is verified per route in continuous integration, not audited once before release.**

Rationale. A single pre-release audit finds the violations present on that day and does nothing about the ones added the following week. Per route automated scanning plus a component level check in the design system catches regressions where they are cheap.

Alternative rejected. An external audit before release, which produces a certificate and a list, and no mechanism to keep either true.

---

# Part 4  Build specification

## 13  Acceptance criteria

Sixty two criteria across five sets. Every Must passes before the frontend is accepted. These extend, and do not replace, the AC-U set in VLD-SAD-DRAUPNIR-001 section 12.2b.

### 13.1  Visual system

| Ref | Criterion | Priority |
|---|---|---|
| AC-V1 | Every token in section 4 exists in the token package and is consumed by name. Light and dark ramps both present | Must |
| AC-V2 | Every component ships all six universal states with a Storybook story for each. A component missing a state fails the build | Must |
| AC-V3 | No component contains a hard coded colour, spacing, radius or duration. A token linter enforces this in continuous integration | Must |
| AC-V4 | Run state colour comes from the state token in every context: table row, card, badge, timeline. No component defines its own | Must |
| AC-V5 | No information is conveyed by colour alone. Every state renders a text label, every gate result renders a value | Must |
| AC-V6 | Dark theme meets the same contrast thresholds as light. Verified by automated contrast check on every token pair in use | Must |
| AC-V7 | Compact density reduces row height and gutter but never reduces touch target below 44 px | Must |
| AC-V8 | Hashes render truncated to eight characters with the full value available and a copy control. No truncated hash without a route to the whole | Must |
| AC-V9 | Charts use the ink and forge ramps. No categorical rainbow palette appears anywhere | Should |
| AC-V10 | Icons carry an accessible name and never appear without a label or an equivalent | Must |
| AC-V11 | JARNGREIPR builds and publishes as a package independently of the console application | Must |
| AC-V12 | Visual regression snapshots exist for every component in every state and a diff fails the build | Must |

### 13.2  Screens

| Ref | Criterion | Priority |
|---|---|---|
| AC-S1 | All thirty one screens in section 8 exist and are reachable by their documented route | Must |
| AC-S2 | Every screen has a defined and implemented behaviour in all six universal states | Must |
| AC-S3 | Every screen has a URL that restores its full state including filters and selected site | Must |
| AC-S4 | The run board renders six columns at comfortable density and states data freshness in words | Must |
| AC-S5 | Run detail presents its six tabs in the documented order, with the loss curve, run facts and log tail visible without a tab change | Must |
| AC-S6 | Failure diagnosis states a cause in a sentence and a suggested correction as a concrete parameter change | Must |
| AC-S7 | For the out of memory case, failure diagnosis renders the token length distribution with the 99th percentile marked | Must |
| AC-S8 | Approval detail places the full six gate evidence table above the decision controls. No decision control is reachable without the evidence in the viewport first | Must |
| AC-S9 | The sole approver notice renders in warning style, states the fact plainly, and appears before the decision controls, not in a confirmation after | Must |
| AC-S10 | Publish renders a nine item pre-flight checklist including anchor state | Must |
| AC-S11 | Lineage renders a gap as a marked node stating what is missing, never as a shorter tree | Must |
| AC-S12 | Sweep comparison marks the selected point and states the trade in a sentence beneath the matrix | Must |
| AC-S13 | The register source wizard makes the DPIA reference required when personal data is declared, and explains why | Must |
| AC-S14 | The residency constraint field is present and records permitted sites | Must |
| AC-S15 | CON-A renders from DVALIN, depends on no API call, and continues to display local state with the network disconnected | Must |
| AC-S16 | CON-B runs in kiosk mode and recovers automatically after an appliance restart | Should |

### 13.3  Journeys

| Ref | Criterion | Target | Priority |
|---|---|---|---|
| AC-J1 | J1 Curate completes end to end in Playwright, including the licence failure path to quarantine | 6 min unassisted | Must |
| AC-J2 | J2 Operate completes end to end including dry run, submission, failure and corrected retry | 3 min submit, 2 min diagnose | Must |
| AC-J3 | J3 Approve completes end to end including the partitioned hold path and the rejection path | No time target | Must |
| AC-J4 | J4 Audit reaches a complete lineage and exports an attestation | 3 interactions or fewer | Must |
| AC-J5 | Each journey test is the acceptance evidence, stored in `docs/acceptance/` with its trace | Must |
| AC-J6 | An unassisted first time user completes J1 and J2 within target in a recorded session with two participants | Should |

### 13.4  Interaction

| Ref | Criterion | Priority |
|---|---|---|
| AC-X1 | The run board reflects a state change within 5 s by server sent events. No full list polling appears in the network trace | Must |
| AC-X2 | The command palette opens on the platform shortcut and covers navigation, submission and search across all four entity types | Must |
| AC-X3 | Every function is reachable and operable by keyboard alone, verified by a recorded manual pass | Must |
| AC-X4 | Dialogs trap focus and restore it on close. Navigation moves focus to the new page heading | Must |
| AC-X5 | No optimistic update appears anywhere. A state change renders only once the ledger has recorded it | Must |
| AC-X6 | Destructive actions are two step with the consequence in words. Release withdrawal requires typing the artefact name | Must |
| AC-X7 | A log view with 200,000 lines scrolls smoothly, demonstrating virtualisation | Must |
| AC-X8 | Log tail follow is on by default, releases on scroll up, and the state is stated | Should |
| AC-X9 | Session expiry is warned before it occurs and an in progress specification survives a token refresh | Must |
| AC-X10 | Where more than one site is registered, every view states its site. No unscoped aggregate view exists | Must |
| AC-X11 | A partitioned site states the condition and disables release with the reason in place | Must |
| AC-X12 | Every error surface renders the problem title, the available action and a copyable correlation identifier | Must |

### 13.5  Accessibility and content

| Ref | Criterion | Priority |
|---|---|---|
| AC-A1 | Automated axe scan on every one of the thirty one routes returns zero serious or critical violations | Must |
| AC-A2 | Contrast meets 4.5:1 body and 3:1 large and interface, in both themes, verified by automated token pair check | Must |
| AC-A3 | Manual keyboard pass recorded with findings, covering all four journeys | Must |
| AC-A4 | Focus indicator visible throughout and meeting the WCAG 2.2 focus appearance criterion | Must |
| AC-A5 | Targets meet 24 by 24 CSS px minimum and 44 by 44 in touch contexts | Must |
| AC-A6 | Console usable at 200 per cent zoom and 320 px width with no loss of function | Must |
| AC-A7 | `prefers-reduced-motion` respected in every animated component | Must |
| AC-A8 | Live regions announce run state changes and gate results to a screen reader | Must |
| AC-A9 | Form errors are announced, linked to their field, and never conveyed by colour alone | Must |
| AC-A10 | Non-English jurisdiction content carries its own `lang` attribute | Must |
| AC-A11 | All user facing strings are British English, sentence case, and pass the terminology lint rule for site, node and rank | Must |
| AC-A12 | No error string reads "Something went wrong" or equivalent. Every error names what failed | Must |
| AC-A13 | Every empty state names what belongs there and offers the action that creates it | Must |
| AC-A14 | Every denied state names the role required and the role held | Must |
| AC-A15 | A screen reader user completes J3 Approve unassisted in a recorded session | Should |

---

## 14  Build prompts

Five prompts, executed in order, with review between. They expand Prompts 8 and 9 of VLD-SAD-DRAUPNIR-001 and replace them for frontend work.

> **Standing instruction to prepend to every prompt in this section.**
>
> You are building the DRAUPNIR console and the JARNGREIPR design system for Veldris Ltd, specified in VLD-UX-DRAUPNIR-001. Read that document and VLD-SAD-DRAUPNIR-001 before writing code. Treat both as requirements.
>
> Rules for every task:
> 1. React 18 with TypeScript, strict mode. Vite. `tsc --noEmit` and eslint must pass.
> 2. Tokens are the only source of visual values. If you type a hex, a pixel value or a duration into a component, you have made an error.
> 3. Every component ships six states: loading, empty, error, denied, read only, partitioned.
> 4. The API client is generated from `openapi.json`. A hand-written client method fails review.
> 5. No optimistic updates. Render state when the ledger has recorded it.
> 6. WCAG 2.2 AA is a build gate, not a later pass.
> 7. British English, sentence case, terminology lint for site, node and rank.
> 8. No em dashes in code, comments, documentation or user facing strings.
> 9. If this document and the SAD conflict on a frontend matter, this document wins. On an architectural matter, the SAD wins.
> 10. If the specification is ambiguous, stop and state the ambiguity. Do not guess.

### 14.1  Prompt UX-0, foundation and tooling

```
Establish the frontend workspace and its quality gates before any component.

Deliver:
  web/                      pnpm workspace, Vite, React 18, TypeScript strict
  web/packages/jarngreipr/  empty package with build and publish configured
  web/apps/console/         empty application shell
  Storybook configured against jarngreipr
  vitest + Testing Library
  Playwright with four empty journey specs, named J1 to J4
  axe-core wired into both Storybook and Playwright
  token linter: fails on any literal colour, spacing, radius or duration
  terminology linter: fails on misuse of site, node, rank in user facing strings
  visual regression harness against Storybook stories

Requirements:
- Every gate runs in CI and none is skippable on main.
- The four Playwright specs exist and fail with a clear "not implemented"
  message. An empty harness that runs is worth more than a good one added late.
- The token linter must fail the build on a planted violation. Prove it.

Exit condition:
- AC-V3, AC-V12 harnesses operational.
- A planted hard-coded hex fails CI.
- A planted use of "node" meaning a forge fails CI.
```

### 14.2  Prompt UX-1, tokens and theming

```
Build the JARNGREIPR token layer.

Deliver:
  tokens for colour (7 ramps, light and dark), typography, space, radius,
  elevation, motion, density
  CSS custom property output and a typed TypeScript export
  theme provider with light, dark and system, persisted per user
  density provider with comfortable and compact
  automated contrast checker over every token pair in use

Requirements:
- Values are exactly those in VLD-UX-DRAUPNIR-001 section 4. Do not improve
  them. If a value is wrong, raise it rather than silently changing it.
- Run state tokens per section 4.2. The mapping lives in the token layer, not
  in any component.
- Compact density reduces row height, body size and gutter. It must NOT reduce
  touch target size below 44 px. Enforce with a test.
- The contrast checker runs in CI and fails on any pair below threshold.

Exit condition:
- AC-V1, AC-V4, AC-V6, AC-V7 pass.
- Switching theme and density changes no component code.
```

### 14.3  Prompt UX-2, primitives

```
Build the JARNGREIPR primitive components.

Deliver:
  button, input, textarea, select, combobox, checkbox, radio, toggle, table,
  badge, pill, tooltip, dialog, drawer, toast, tabs, breadcrumb, pagination
  one Storybook story per component per state (six states each)
  axe check on every story
  visual regression snapshot on every story

Requirements:
- Section 5.1 notes are requirements, not suggestions. In particular:
  labels always visible; placeholder never substitutes for a label; tooltips
  never carry sole information; toasts never carry errors requiring action;
  pagination is cursor based with no page numbers.
- Dialogs trap focus and restore it on close.
- Table renders th with scope, supports arrow navigation and type ahead.
- Every icon-bearing control has an accessible name.
- No component reads a raw token value; all consume the typed export.

Exit condition:
- AC-V2, AC-V5, AC-V10, AC-A4, AC-A5 pass at component level.
- Every story passes axe with zero serious or critical violations.
```

### 14.4  Prompt UX-3, composites

```
Build the JARNGREIPR composite components.

Deliver:
  run card, gate card, evidence panel, lineage tree, sweep matrix,
  log viewer, ledger entry viewer, capacity gauge, diff viewer, spec editor

Requirements per section 5.2:
- Run card omits the remaining-time estimate rather than guessing when step
  time is not yet stable.
- Gate card always renders value, baseline, margin and result. Never a bare tick.
- Evidence panel is sortable by margin so the tightest result is findable.
- Lineage tree renders a gap as a MARKED NODE stating what is missing. It must
  be impossible to render a gap by omission; write a test that proves it.
- Sweep matrix highlights the selected point and renders a trade sentence
  beneath, generated from the data, not hard coded.
- Log viewer virtualises; follow-tail on by default, releases on scroll up,
  and the state is stated in words.
- Spec editor validates against the JSON Schema inline and never submits on
  Enter. Dry run is the primary action; submit is secondary.

Exit condition:
- AC-V8, AC-S11, AC-S12, AC-X7, AC-X8 pass at component level.
- A test proves the lineage tree cannot omit a gap.
```

### 14.5  Prompt UX-4, screens and journeys

```
Build the console application: all thirty one screens and the four journeys.

Deliver:
  web/apps/console/   shell, routing, all screens S01 to S29
  tools/stedi-view/   S30 CON-A local view
  kiosk config        S31 CON-B dashboard
  four Playwright journey specs, now implemented

Requirements:
- Screen inventory of section 8 in full. Each screen: purpose, primary action,
  six states, deep-linkable URL restoring filters and site.
- S19 Approval detail: the evidence table is above the decision controls and
  the decision controls must not be reachable without the evidence entering
  the viewport first. Write a Playwright assertion for this.
- S09 Compose run: dry run is primary. Submitting without a dry run requires an
  extra confirmation.
- S11 Failure diagnosis: cause sentence, concrete suggested correction, and for
  the OOM case the token length distribution with p99 marked.
- S30 CON-A: rendered by DVALIN, NO API dependency, no shared code path with
  the web console that could introduce one. Test it with the network down.
- Shell: site switcher always visible, command palette on the platform
  shortcut, alert tray, global search.
- No optimistic updates anywhere.

Exit condition:
- AC-S1 through AC-S16, AC-J1 through AC-J5, AC-X1 through AC-X12,
  AC-A1 through AC-A14 pass.
- Recorded manual keyboard pass covering all four journeys, with findings.
```

## 15  Delivery sequence and effort

| Prompt | Deliverable | Depends on | Indicative effort |
|---|---|---|---|
| UX-0 | Workspace, gates, harnesses | SAD Prompt 0 | 3 to 4 days |
| UX-1 | Tokens and theming | UX-0 | 2 to 3 days |
| UX-2 | Primitives | UX-1 | 6 to 9 days |
| UX-3 | Composites | UX-2 | 7 to 10 days |
| UX-4 | Screens and journeys | UX-3, SAD Prompt 7 | 12 to 18 days |
| | **Total** | | **30 to 44 working days** |

UX-0 through UX-3 depend only on SAD Prompt 0 and can run in parallel with the entire backend. Only UX-4 requires the API from SAD Prompt 7. This is the reason the design system is specified as a separate package: it removes the frontend from the backend critical path entirely.

The SAD Rev 1.3 estimate allocated 15 to 22 days across its Prompts 8 and 9. This document's 30 to 44 days is the same work specified properly. The difference is thirty one screens enumerated rather than implied, six states per component rather than a happy path, and accessibility as a gate rather than a later pass.

## 16  Design operations

| Concern | Practice |
|---|---|
| Source of truth | Tokens live in code. Any design tool file is downstream of the token package, never upstream of it |
| Component documentation | Storybook is the documentation and the visual regression target. There is no separate component wiki to fall out of date |
| Handoff | There is no handoff. This document plus Storybook is the specification; a screen is done when its stories and journey test pass |
| Review gates | A component enters the library only with six states, an axe pass, a visual snapshot and a story. A screen enters the console only with a deep-linkable URL and defined behaviour in all six states |
| Change control | A token change is a versioned release of the JARNGREIPR package. The console upgrades deliberately, so a visual change is never accidental |
| Skill | `jarngreipr-component`, specified in SAD section 11G, scaffolds a conforming component with all six states, stories, axe test and snapshot. Its output must pass the gates unmodified |

## 17  Open questions

| Ref | Question | Owner | Needed by |
|---|---|---|---|
| UX-Q1 | Is Inter acceptable as the interface typeface, or does Veldris have a licensed brand face that the Midgard Suite will also use? Changing it later is a token change, but the metrics differ enough to affect table density | Veldris | UX-1 |
| UX-Q2 | Should the console default to dark theme? The two rack consoles sit in a room with 1 kW of equipment and are read at a glance; the workstation context may differ | Veldris | UX-1 |
| UX-Q3 | Does any Commonwealth public sector buyer impose an accessibility standard beyond WCAG 2.2 AA, such as a national profile? The build targets AA regardless, but a known requirement would be evidenced differently | Veldris commercial | UX-4 |
| UX-Q4 | Is a recorded usability session with two participants achievable, given the team size? AC-J6 and AC-A15 are marked Should for that reason and can be withdrawn if not | Veldris | UX-4 |
