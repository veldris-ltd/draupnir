# The release procedure

What `draupnir/skidbladnir/` and `draupnir/gleipnir/` actually do. Where this
differs from the SAD, the code is right and the specification is amended.

## The order of operations

`publish()` applies its refusals in this order, and the order is the design:

1. **Re-hash the artefact** and require passing evidence for those bytes.
   Everything after this point is an assertion about a particular set of bytes,
   so the bytes are established first.
2. **Verify the formats.** Every built format has passing evidence.
3. **Verify the approval.** Present, signed, and recording `approved`, and
   naming the same artefact.
4. **Verify the package.** The four artefacts agree with each other.

The distinction inside step 1 is worth knowing. If the evidence log holds
evidence *of this kind* for different bytes, something changed —
`ArtefactMismatchError`. If it holds none, evaluation was bypassed —
`UngatedArtefactError`. Two different failures, two different responses.

## What each artefact contains

**Model card** (`modelcard.render`). Four sections — identity, provenance,
evaluation, compliance — and every field is either recorded or recorded as
`NOT_RECORDED`. A `Fact` that is unknown says so; it does not render as an
empty string, because an empty string in a published card reads as "none"
rather than "we did not capture this".

**SBOM** (`sbom.from_lineage`). CycloneDX, built from the lineage nodes. One
source for the SBOM and the attestation, so they cannot disagree.

**Lineage attestation** (`lineage.attest`). The complete chain from the release
artefact back to base-model licences and corpus hashes (AC-F11). `attest`
raises `IncompleteLineageError` on a chain with gaps rather than signing
around them.

**Manifest** (`ReleasePackage.manifest`). SHA-256 of each of the four, plus the
model, the artefact hash, the release timestamp and the Article 53 versions.

**Training content summary** (`article53.summarise`). Generated from
`LicenceRegister.facts_for_policy()` — the same mappings a licence policy
driver is handed. The consequence worth stating: the summary is generated from
exactly the facts the decision was made on, so the document and the decision
cannot disagree.

**Downstream annex** (`article53.annex`). Attribution taken from the summary,
not restated.

## What `consistency_problems` compares

- The SBOM's subject hash against the release hash.
- The attestation's lineage root against the release hash.
- The lineage's completeness.
- The model card's `artefactSha256` against the release hash — only when the
  card records one, since `NOT_RECORDED` is a different claim from a wrong one.
- The lineage's **corpus** licences against the summary's licences. Corpus
  only: the base model's licence is in the chain and is not training content —
  it is a component, documented in the SBOM and the card. Comparing against
  every licence in the chain would report a correct summary as stale on every
  release.
- The template version the release records against the one the summary was
  rendered on.

## Anchor states

`ANCHORED`, `DEGRADED`, `PARTITIONED`, `UNANCHORED`. Release is permitted from
the first two. Degraded is slow; partitioned is cut off, and that difference is
the whole of Decision S8: training continues through a partition, release does
not.

## The format cross-check

`formats.check_mlx_against_nvfp4` compares the two quantised builds against
each other, with a threshold **tighter** than the tightest relative gate margin
(0.01). The gates ask whether each build is good enough; this asks whether they
are the same model, and a divergence means one of the two conversion pipelines
is wrong rather than that one build is weaker.

Only relative gates are compared. E6 is an absolute ceiling on contamination,
which is a different kind of number that only looks similar because both are
small.

`BUILD_HOST` records that mlx4 is built on `alviss` and nvfp4 on `sindri`.

## What an operator sees

Every refusal carries the reason and the next action. Two examples, verbatim
from the code:

> the quantised being published is not the one the gates passed. Gated: … .
> Present: … . Publication is refused. Gate results bind to the bytes, not to
> where they are kept (AC-S8), so this artefact has either been modified since
> evaluation or is a different build that has never been evaluated. Re-gate it.

> quantised build(s) may not be published — never evaluated: mlx4. Every
> quantised output is re-gated, and there is no path from quantisation to
> approval that skips evaluation (AC-F9).

The console renders these as problem documents. The attestation viewer
recomputes `H(prev || canonical(payload))` and shows it beside the stored hash,
because rendering the stored one proves nothing — it is exactly what a tamperer
would have rewritten.

## Article 53

`TEMPLATE_VERSION` is `ai-office/training-content-summary/2025-07`, and a
published release keeps the version in force at its date (SAD 10.2). The three
template sections — general information, data sources, data processing — are
named in `SECTIONS` so that a section the register cannot fill is rendered as
an absence rather than dropped.
