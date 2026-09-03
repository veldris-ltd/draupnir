# Acceptance evidence

Every criterion in SAD 12, one file each, generated from the specification and
from the citations in the repository. The status is the vocabulary AC-D4 asks
for: **IMPLEMENTED**, **DEVIATED** with reasons, or **NOT BUILT**.

90 criteria: 78 implemented, 9 deviated,
3 not built. Of the 68 marked **Must**,
68 have evidence and
0 do not.

Every deviation has one cause, stated on the criterion's own page: the Sindri
estate does not exist yet, so a criterion that asks for a measurement *on that
hardware* is a commissioning measurement rather than one this build can take.
Nothing is marked implemented on the strength of an argument that it ought to
work.

Three documents sit beside the pack rather than inside it, because each is read
end to end rather than looked up:

- [`../runbook.md`](../runbook.md) — the operator runbook, one section per
  degraded mode (AC-D3).
- [`keyboard-pass.md`](keyboard-pass.md) — the manual keyboard pass and its
  findings (AC-U5).
- [`imhotep-reconciliation.md`](imhotep-reconciliation.md) — the SAD reconciled
  against the delivered repository (AC-D4).

```bash
make acceptance     # regenerate this pack
```

| Ref | Criterion | Priority | Status |
|---|---|---|---|
| [AC-F1](AC-F1.md) | A run specification file is submitted through `draupnirctl` and through the web console, and bot… | Must | IMPLEMENTED |
| [AC-F2](AC-F2.md) | Submitting the same specification twice with unchanged inputs is detected and reported as a dupl… | Must | IMPLEMENTED |
| [AC-F3](AC-F3.md) | A corpus is ingested, hashed, licence registered and curated; the raw directory is read only aft… | Must | IMPLEMENTED |
| [AC-F4](AC-F4.md) | A substrate run executes across all three appliances through the `ring` partition, and the run b… | Must | DEVIATED |
| [AC-F5](AC-F5.md) | A fifty six element adapter array is submitted as one action and executes exactly three concurre… | Must | DEVIATED |
| [AC-F6](AC-F6.md) | A failed array element is retried individually without disturbing the other elements | Must | IMPLEMENTED |
| [AC-F7](AC-F7.md) | Gates E1 to E6 execute against an adapter, results are recorded with baseline and margin, and a … | Must | IMPLEMENTED |
| [AC-F8](AC-F8.md) | A merge executes with a weight sweep of at least five points, and each point's gate results are … | Must | IMPLEMENTED |
| [AC-F9](AC-F9.md) | Quantisation to NVFP4, GGUF and MLX executes, and each output is re-gated automatically before i… | Must | DEVIATED |
| [AC-F10](AC-F10.md) | A release produces a model card, a CycloneDX SBOM, a SHA-256 manifest and a lineage attestation,… | Must | IMPLEMENTED |
| [AC-F11](AC-F11.md) | The lineage endpoint for any released artefact returns the complete chain to base model licences… | Must | IMPLEMENTED |
| [AC-F12](AC-F12.md) | The complete Procedure M1 to M10 sequence from VLD-INF-SINDRI-001 executes end to end for one ju… | Must | IMPLEMENTED |
| [AC-F13](AC-F13.md) | Cancelling a run stops the scheduler job and leaves the artefact in a defined state, never in an… | Must | IMPLEMENTED |
| [AC-F14](AC-F14.md) | A dry run renders the exact job plan without consuming an allocation | Should | IMPLEMENTED |
| [AC-F15](AC-F15.md) | Two jurisdictions differing only in corpus produce specifications that differ only in the datase… | Should | IMPLEMENTED |
| [AC-F16](AC-F16.md) | The Tier A list of nine jurisdictions and the Tier B list of forty seven together enumerate all … | Must | IMPLEMENTED |
| [AC-F17](AC-F17.md) | A release package contains the Article 53 training content summary and the copyright policy refe… | Must | IMPLEMENTED |
| [AC-F18](AC-F18.md) | A second site is registered and a run is submitted to it, with site scope resolving correctly in… | Must | IMPLEMENTED |
| [AC-F19](AC-F19.md) | A retention action deletes a raw corpus after 24 months, retains the curated manifests and licen… | Must | IMPLEMENTED |
| [AC-F20](AC-F20.md) | A retention action that would break a lineage chain is refused with the affected release named | Must | IMPLEMENTED |
| [AC-S1](AC-S1.md) | Altering one byte of a base weight file causes the next load to fail with a hash mismatch and a … | Must | IMPLEMENTED |
| [AC-S2](AC-S2.md) | A corpus source with a licence that fails policy cannot reach CURATED. The attempt is refused an… | Must | IMPLEMENTED |
| [AC-S3](AC-S3.md) | The teacher model destination is absent from the allow list and a call to it fails at the egress… | Must | IMPLEMENTED |
| [AC-S4](AC-S4.md) | An unauthenticated request to any `/v1` path returns 401. A `viewer` attempting to submit a run … | Must | IMPLEMENTED |
| [AC-S5](AC-S5.md) | Publishing without a signed approval record returns 409. The same identity submitting and approv… | Must | IMPLEMENTED |
| [AC-S6](AC-S6.md) | A secret injected into the job environment does not appear in any checkpoint, log or artefact. T… | Must | IMPLEMENTED |
| [AC-S7](AC-S7.md) | An unsigned plug-in fails to load. A signed plug-in attempting an undeclared capability is refus… | Must | IMPLEMENTED |
| [AC-S8](AC-S8.md) | Modifying an artefact after its gates pass causes publication to fail on hash re-verification | Must | IMPLEMENTED |
| [AC-S9](AC-S9.md) | Editing a ledger row directly in PostgreSQL is detected by chain verification within one hour, a… | Must | IMPLEMENTED |
| [AC-S10](AC-S10.md) | A run whose projected output exceeds the vault free space is refused at planning rather than fai… | Must | IMPLEMENTED |
| [AC-S11](AC-S11.md) | An executor attempting an outbound connection fails. The attempt appears in the log | Must | DEVIATED |
| [AC-S12](AC-S12.md) | No secret appears in any configuration file in version control. A repository scan in CI returns … | Must | IMPLEMENTED |
| [AC-S13](AC-S13.md) | A forge disconnected from MEGINGJORD continues to train and record, and a release attempt is ref… | Must | IMPLEMENTED |
| [AC-S14](AC-S14.md) | A forge ledger verifies its own chain integrity with MEGINGJORD unreachable. No corpus or weight… | Must | IMPLEMENTED |
| [AC-S15](AC-S15.md) | The approver identity requires hardware backed multi factor authentication. Every release where … | Must | IMPLEMENTED |
| [AC-S16](AC-S16.md) | The cryptographic inventory lists every algorithm, key length and module in use, and each entry … | Must | IMPLEMENTED |
| [AC-S17](AC-S17.md) | The signature envelope carries two signatures of different algorithms in a test case, and verifi… | Must | IMPLEMENTED |
| [AC-S18](AC-S18.md) | No release metadata reaches any external transparency log. Verified by network capture during a … | Must | DEVIATED |
| [AC-B1](AC-B1.md) | Every mutating endpoint honours `Idempotency-Key`. A replayed request returns the original resul… | Must | IMPLEMENTED |
| [AC-B2](AC-B2.md) | Every error response is RFC 9457 problem+json with a stable type URI. No bare 500 reaches a clie… | Must | IMPLEMENTED |
| [AC-B3](AC-B3.md) | Pagination is cursor based throughout. A test inserting rows during pagination shows no skipped … | Must | IMPLEMENTED |
| [AC-B4](AC-B4.md) | A stale conditional write returns 412 rather than overwriting | Must | IMPLEMENTED |
| [AC-B5](AC-B5.md) | The OpenAPI diff gate fails a build on a breaking change within `/v1` | Must | IMPLEMENTED |
| [AC-B6](AC-B6.md) | A route registered without a role declaration prevents application startup | Must | IMPLEMENTED |
| [AC-B7](AC-B7.md) | An import from the edge layer into the domain layer fails the import linter in continuous integr… | Must | IMPLEMENTED |
| [AC-B8](AC-B8.md) | All identifiers are UUIDv7 and sort by creation time | Should | IMPLEMENTED |
| [AC-B9](AC-B9.md) | No endpoint blocks an HTTP request on training work. Long operations return 202 with a run ident… | Must | IMPLEMENTED |
| [AC-B10](AC-B10.md) | Site scope is enforced by row level security, demonstrated by a query with the session variable … | Must | IMPLEMENTED |
| [AC-U1](AC-U1.md) | All four journeys in section 11F.2 complete end to end in Playwright against a seeded stack | Must | IMPLEMENTED |
| [AC-U2](AC-U2.md) | Every JARNGREIPR component has loading, empty, error, denied, read only and partitioned states, … | Must | IMPLEMENTED |
| [AC-U3](AC-U3.md) | No component contains a hard coded colour, spacing or radius value. A token linter enforces this | Must | IMPLEMENTED |
| [AC-U4](AC-U4.md) | The run board reflects a state change within 5 seconds by server sent events, with no manual ref… | Must | IMPLEMENTED |
| [AC-U5](AC-U5.md) | Every function is reachable and operable by keyboard, with a visible focus indicator. Verified b… | Must | IMPLEMENTED |
| [AC-U6](AC-U6.md) | Automated axe scan on every route returns zero serious or critical violations | Must | IMPLEMENTED |
| [AC-U7](AC-U7.md) | Contrast meets 4.5:1 for body text and 3:1 for large text and interface components, in both ligh… | Must | IMPLEMENTED |
| [AC-U8](AC-U8.md) | The console is usable at 200 per cent zoom and at a 320 pixel viewport with no loss of function | Must | IMPLEMENTED |
| [AC-U9](AC-U9.md) | `prefers-reduced-motion` is respected across every animated component | Must | IMPLEMENTED |
| [AC-U10](AC-U10.md) | A log view with 200,000 lines scrolls without degrading the browser, demonstrating virtualisatio… | Must | IMPLEMENTED |
| [AC-U11](AC-U11.md) | Where more than one site is registered, every view states which site it shows. No unscoped aggre… | Must | IMPLEMENTED |
| [AC-U12](AC-U12.md) | A partitioned site is stated plainly in the interface, and the release action is disabled with t… | Must | IMPLEMENTED |
| [AC-U13](AC-U13.md) | The gate queue displays the gate evidence and the sole approver notice before the decision contr… | Must | IMPLEMENTED |
| [AC-U14](AC-U14.md) | Every error surface shows the problem title, the available action and a copyable correlation ide… | Must | IMPLEMENTED |
| [AC-U15](AC-U15.md) | Destructive actions are two step with the consequence stated in words | Must | IMPLEMENTED |
| [AC-U16](AC-U16.md) | First contentful paint under 1.5 s, interaction to next paint under 200 ms at the 75th percentil… | Should | DEVIATED |
| [AC-U17](AC-U17.md) | The command palette covers navigation, run submission and search, and the console is fully opera… | Should | IMPLEMENTED |
| [AC-Q1](AC-Q1.md) | Every pipeline stage in section 11H runs on the main branch with none skippable | Must | IMPLEMENTED |
| [AC-Q2](AC-Q2.md) | Client regeneration fails the build when the CLI or TypeScript client has drifted from the OpenA… | Must | IMPLEMENTED |
| [AC-Q3](AC-Q3.md) | Secret scanning runs on every commit and the repository history is clean | Must | IMPLEMENTED |
| [AC-Q4](AC-Q4.md) | Property tests cover ledger chain invariants, specification hash determinism and projector idemp… | Must | IMPLEMENTED |
| [AC-Q5](AC-Q5.md) | Visual regression snapshots exist for every component in every state and a diff fails the build | Must | IMPLEMENTED |
| [AC-Q6](AC-Q6.md) | Migrations are forward only, run dry first in deployment, and a failed smoke test triggers rollb… | Must | IMPLEMENTED |
| [AC-Q7](AC-Q7.md) | Container images build for aarch64 from a distroless base and run rootless | Must | DEVIATED |
| [AC-Q8](AC-Q8.md) | Each of the six development skills in section 11G produces a conforming artefact in a demonstrat… | Must | IMPLEMENTED |
| [AC-Q9](AC-Q9.md) | A new developer reaches a running stack with seeded data from a clean machine using one document… | Should | IMPLEMENTED |
| [AC-N1](AC-N1.md) | Control plane CPU and memory overhead on ALVISS during a three appliance training run | Target | NOT BUILT |
| [AC-N2](AC-N2.md) | Control plane adds no measurable overhead to training step time | Target | NOT BUILT |
| [AC-N3](AC-N3.md) | Run board reflects a state change | Target | IMPLEMENTED |
| [AC-N4](AC-N4.md) | API responds to a run list of 500 entries | Target | IMPLEMENTED |
| [AC-N5](AC-N5.md) | Ledger chain verification over 100,000 entries | Target | IMPLEMENTED |
| [AC-N6](AC-N6.md) | Control plane restart to full service, with runs in flight preserved | Target | IMPLEMENTED |
| [AC-N7](AC-N7.md) | The application runs on aarch64 Linux and on Apple silicon macOS from the same source | Target | DEVIATED |
| [AC-N8](AC-N8.md) | Test coverage on the core state machine and ledger | Target | IMPLEMENTED |
| [AC-N9](AC-N9.md) | A new `ExportDriver` is added and working | Target | IMPLEMENTED |
| [AC-N10](AC-N10.md) | OpenAPI specification is complete and both clients are generated from it | Target | IMPLEMENTED |
| [AC-N11](AC-N11.md) | Anchor round trip to MEGINGJORD | Target | NOT BUILT |
| [AC-N12](AC-N12.md) | A forge operating with the federation link down | Target | DEVIATED |
| [AC-D1](AC-D1.md) | Every module has a README stating its responsibility and its explicit non responsibilities, matc… | Target | IMPLEMENTED |
| [AC-D2](AC-D2.md) | Every plug-in interface has a reference implementation and a worked example | Target | IMPLEMENTED |
| [AC-D3](AC-D3.md) | An operator runbook covers each degraded mode in section 11.2 | Target | IMPLEMENTED |
| [AC-D4](AC-D4.md) | This document is re-run through Imhotep against the delivered repository, and every SPECIFIED it… | Target | IMPLEMENTED |
