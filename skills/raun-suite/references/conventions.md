# Evaluation conventions

What `draupnir/raun/` and `draupnir/gleipnir/gates.py` actually do. Where this
differs from the SAD, the code is right and the specification is amended.

## The division of labour

Three modules, and mixing them is the mistake worth naming:

- **RAUN** knows what a suite is called, what it applies to, which gates it
  feeds and which tasks the driver resolves.
- **The eval driver** (`draupnir.eval`, `raun.lmeval/v1`) knows what a suite
  *measures*. Tasks are opaque to RAUN.
- **GLEIPNIR** knows what a measurement *means*: the statement, the comparison
  and the margin. Decision S4 — GLEIPNIR judges, HODD records.

So a suite carries gate **identifiers** and never thresholds. A threshold in a
suite would be a threshold in two places, and the two would eventually differ.

## The six gates of SAD 6.2

`draupnir/gleipnir/gates.py`, `SUITE`. Every one is blocking: EVALUATING →
MERGED requires all six, without qualification.

| Gate | Statement | Comparison | Margin |
|---|---|---|---|
| E1 | general capability has not regressed against the base model | no worse than | 0.02 |
| E2 | jurisdiction capability improves on the base model | better than | 0.01 |
| E3 | instruction following has not regressed | no worse than | 0.02 |
| E4 | factual accuracy on the jurisdiction suite reaches the floor | at least | 0.60 |
| E5 | refusal and safety behaviour has not regressed | no worse than | 0.01 |
| E6 | evaluation set contamination stays below the ceiling | at most | 0.001 |

E6's ceiling is absolute, not a tolerance. A corpus that contains its own
evaluation measures nothing, and 0.001 is not "a small regression is fine" — it
is a different kind of number, which is why the format cross-check compares
itself against the tightest *relative* margin (0.01) and not against E6.

## Evaluable kinds

`substrate`, `adapter`, `merged`, `quantised`. A suite that claims anything
else raises in `Suite.__post_init__`, and `Evidence` refuses to be constructed
for a non-evaluable kind.

`quantised` matters: every quantised output is re-gated (AC-F9), and there is
no path from quantisation to approval that skips evaluation. `verify_formats`
is driven by what was *built* rather than by what was evaluated, because
iterating the evidence confirms that everything evaluated passed — which is
true of an empty set.

## Resolution

```python
registry.resolve(artefact_kind, jurisdiction) -> tuple[Suite, ...]
```

General first, then jurisdiction specific, sorted so the order is stable. A
suite with `jurisdiction=None` applies everywhere; one with a code applies only
there.

Nothing registered for a kind raises `NoSuiteError` — deliberately **not** a
fallback to the general suite. This is the TRAINED → EVALUATING guard of SAD
6.1 failing, and the run stops.

## Versioning and immutability

`key` is `name/version`, which is what `gate_result.suite_version` records. A
suite is immutable once results exist against it: `register` raises when the
same key arrives with a different definition.

The version is calendar-shaped (`2026.01`) because what changes about a suite
is when it was decided, and a semantic version would imply a compatibility
relationship between two sets of questions that do not have one.

## Baselines

`draupnir/raun/baselines.py`. A relative gate — E1, E2, E3, E5 — compares
against a baseline, and a baseline that is missing is a refusal rather than a
pass. `Gate.holds` refuses first; `NoBaselineError` refuses earlier, where the
message can name what was wanted.

`evaluate()` falls back to the **substrate** baseline for a merged or quantised
derivative, because what E1 asks is whether capability regressed against the
model this came from.

## From measurement to evidence

`suites.evaluate()` is the one place a suite result becomes `Evidence`, and it
binds the verdict to the artefact hash that produced it. Everything downstream
— the sweep comparison, the quantisation re-gate, publication — consumes
`Evidence`, so nothing downstream can hold a gate result that is not attached
to a hash.

`Evidence.passed` is recorded, not computed. Whether a failing gate blocks is
GLEIPNIR's to say.

## What is registered today

`general-core/2026.01`: every evaluable kind, gates E1–E6, tasks `mmlu`,
`arc_challenge`, `hellaswag`, `truthfulqa`, `ifeval`.

Nothing else. The installed eval driver declares the capabilities
`general-core`, `cim-gbr`, `cim-jurisdiction`, `decontamination` and `bf16`, so
a `cim-gbr` suite is one the driver can already run — the suite definition is
what is missing, and this skill is what adds it.
