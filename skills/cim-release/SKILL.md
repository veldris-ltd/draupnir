---
name: cim-release
description: >
  Take a CIM-56 model through release — the four artefacts of AC-F10, the
  Article 53 pair, and every refusal that stands between a gated artefact and
  a published one. Use when releasing or preparing to release: "release
  cim-XXX", "publish a model", "why was the release refused", "prepare the
  release package", "what does the release package contain".
---

# cim-release

Establish whether an artefact may be released, and say exactly why not when it
may not.

## Why a script rather than a checklist

A release is the one operation in DRAUPNIR that cannot be undone by a later
run. Everything else — a bad adapter, a wrong merge, a failed evaluation — is
recoverable by doing it again. A published model with an inconsistent Article
53 summary is a regulatory fact.

So the refusals are not advice. `skidbladnir.publish` raises on each of them,
and the preflight here **calls that same function** and reports what it raised,
rather than keeping its own copy of the rules. A preflight that agreed with its
own copy would pass a release the release path then refuses, which is the worst
possible moment to find out.

## Use it

```bash
python skills/cim-release/scripts/preflight.py --demo
```

The demonstration builds a complete release from the real modules — real bytes
on disk, a real SHA-256, a real lineage, an Article 53 summary generated from a
licence register — reports zero refusals, then breaks it five ways and reports
each refusal in full.

In the control plane, the package, the evidence log and the approval already
exist, and `refusals()` is the function to call:

```python
from preflight import Release, refusals

problems = refusals(
    Release(
        package=package,  # the four artefacts plus the Article 53 pair
        artefact=path,  # re-hashed, not looked up
        evidence=evidence_log,
        approval=approval_record,
        built_formats=("nvfp4", "gguf-q4km", "mlx4"),
        submitter="brokkr-operator",
        anchor_state=site.anchor_state,
    )
)
```

Empty means it would publish.

## What stands between a gated artefact and a published one

| Refusal | Where | Why |
|---|---|---|
| The site is partitioned | preflight | Training continues through a partition and release does not (Decision S8). A release from a site that cannot reach the federation cannot be anchored, and an unanchorable release is one nobody can prove the date of. |
| Approver is the submitter | preflight | No role both submits and approves (Decision S6). The exception is computed by GLEIPNIR from the two identities and never accepted from a request. |
| The bytes are not the gated bytes | `verify_artefact` | AC-S8. The hash is **computed**, not looked up: a lookup answers "what did we record about this artefact", and the question at publication is "what do we know about these bytes". |
| A built format was never evaluated | `verify_formats` | AC-F9. Driven by what was built, because iterating the evidence confirms that everything evaluated passed — which is true of an empty set. |
| No approval, no signature, or a refusal | `publish` | SAD 5.2: SKIDBLADNIR must not publish without a GLEIPNIR release approval. |
| The four artefacts disagree | `consistency_problems` | AC-F10. The SBOM, the lineage, the model card and the summary must name the same artefact and the same licences. |
| The lineage has gaps | `lineage.attest` | An attestation over an incomplete chain would certify the gap. It exports unsigned and says so. |

## The release package

Four artefacts of AC-F10, plus the Article 53 pair of AC-F17:

```
model card                  every fact recorded, or recorded as absent
CycloneDX SBOM              built from the same lineage the attestation is
lineage attestation         the complete chain to base-model licences and corpus hashes
SHA-256 manifest            every artefact and its digest
training content summary    Article 53, generated from the licence register
downstream annex            attribution taken from the summary
```

The SBOM and the attestation are built from **one** lineage, so "internally
consistent" is a property of how the package is assembled rather than something
the consistency check has to discover afterwards.

## What you then do

Nothing by hand. Article 53 artefacts are generated, not authored
(Decision S11, AC-F17): the summary is rendered from exactly the facts the
licence policy was evaluated against, so the document and the decision cannot
disagree. If a summary is wrong, the register is wrong.

## Refusals

**Do not hand-edit an Article 53 summary.** It is generated from the licence
register. Editing it makes the published document disagree with the decision
that permitted the release, and only the register is evidence.

**Do not sign an attestation over an incomplete lineage.** It exports unsigned
and says so, which is the correct outcome: a signature over a chain with gaps
certifies the gaps.

**Do not release from a partitioned site**, and do not work around it by
anchoring later. Decision S8 is a decision, not a limitation.

**Do not publish a format that was not re-gated.** Quantisation changes the
model. Every quantised output is evaluated again, and the cross-platform check
between the nvfp4 and mlx4 builds is tighter than the tightest gate margin
because it asks a different question: not "is this good enough" but "are these
two the same model".

**Do not compare a hash you were given.** Re-hash the bytes.

## References

- `references/procedure.md` — the order of operations, what each artefact
  contains, and what an operator sees at each refusal.

## Verified

`tests/contract/test_skills.py` runs `--demo`: it builds the complete release,
asserts zero refusals, then asserts that each of the five deliberate breakages
— partitioned site, sole approver, no approval, an unevaluated format, and
tampered bytes — is refused and that the refusal names the cause. The
demonstration uses the real `publish()`, so the skill and the release path
cannot drift apart.
