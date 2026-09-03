---
name: raun-suite
description: >
  Add a RAUN evaluation suite — a jurisdiction's gates, versioned, registered
  and resolvable alongside the general suite. Use when a jurisdiction needs its
  own evaluation: "add an evaluation suite", "new jurisdiction", "cim-XXX
  needs its own gates", "register a suite".
---

# raun-suite

Add a suite that resolves for the artefact kinds it claims, arrives alongside
`general-core` rather than instead of it, and cannot be redefined under the
same version.

## Why a scaffold rather than a description

A suite is the smallest thing in this system whose mistakes are invisible.

- A suite that **replaces** the general one rather than joining it lets a model
  that regressed on general capability release, because nobody measured it.
  `resolve()` returns a tuple for exactly this reason, and SAD 6.2's example
  names `general-core` and `cim-gbr` together.
- A suite **edited in place** rewrites the meaning of every historical
  `gate_result` without changing a single stored number. `gate_result` records
  `suite_version` so a result stays explicable; a suite is immutable once
  results exist against it, and the registry refuses a redefinition.
- A suite that **claims a kind it cannot judge** stops the run at the
  TRAINED → EVALUATING guard. That is correct — an artefact evaluated by the
  wrong suite produces numbers that look like gate results and mean something
  else — but it is a slow way to find out, so the scaffold refuses first.

## Use it

```bash
python skills/raun-suite/scripts/new_suite.py --name cim-gbr --version 2026.01 --jurisdiction GBR --applies-to adapter merged quantised --task uk-legislation --task hansard-qa --rationale "The GBR jurisdiction suite of SAD 6.2: E2 and E4 are measured on UK material, and the general suite cannot measure them."
```

It writes:

```
draupnir/raun/suites.py      the CIM_GBR constant, and its line in default_registry()
tests/unit/test_cim_gbr_suite.py   resolution, specificity, version, immutability
```

SAD 10.2 calls a jurisdiction suite a configuration change. In this build it is
a small reviewed change to one file, and that is deliberate: a suite loaded
from a file on the appliance is a suite that can differ between sites, and a
gate result whose suite definition is not in the repository is a gate result
nobody can explain two years later.

## What the generated suite already gets right

| Rule | How |
|---|---|
| It joins rather than replaces | `resolve()` returns general-first, then jurisdiction. The generated test asserts the order. |
| It is versioned | `YYYY.MM`, and `key` is `name/version` — which is what every `gate_result` records. |
| It is immutable | A second registration with a different definition raises. The test proves it. |
| It feeds gates, not thresholds | `gates=("E1", …)` — identifiers only. A threshold here would be a threshold in two places, and GLEIPNIR owns the one that decides (Decision S4). |
| It claims only evaluable kinds | `substrate`, `adapter`, `merged`, `quantised`. Anything else raises in `__post_init__`. |
| Its tasks are opaque | What a suite *measures* is the eval driver's business; what a measurement *means* is GLEIPNIR's. |

## What you then write

A **baseline**, and the tasks themselves.

A relative gate with no baseline is refused, not passed — `Gate.holds` will not
compare against nothing, and `NoBaselineError` says so where it can name what
was missing. Capture one with `draupnir.raun.baselines` before the first run
of the new suite, or every E1, E2, E3 and E5 stops.

The tasks named in `tasks=` are resolved by the evaluation driver
(`raun.lmeval/v1`). They are opaque here on purpose. If the driver does not
know them, that is a driver change — see `skills/draupnir-driver`.

## Refusals

**Do not edit a registered suite.** Not the tasks, not the gates, not the
applicability. Issue a new version. Every stored result would keep its numbers
and change its meaning, and nothing would look wrong.

**Do not add a gate here.** `gates=` names identifiers GLEIPNIR defines. A new
gate is a change to `draupnir/gleipnir/gates.py`, with a statement, a
comparison and a margin, and it is a policy decision rather than a
configuration one.

**Do not fall back to the general suite when a specific one is missing.**
`NoSuiteError` exists precisely to stop that. The run stops.

**Do not make a jurisdiction suite unscoped.** Leaving `jurisdiction` as `None`
makes it apply everywhere, which is a general suite wearing a jurisdiction's
name.

## References

- `references/conventions.md` — the six gates, the evaluable kinds, baselines,
  and how a suite becomes evidence.

## Verified

`tests/contract/test_skills.py` runs this scaffold against a copy of
`draupnir/raun/suites.py`, imports the result, and resolves the new suite from
the registry it produces — asserting both that it arrives alongside
`general-core` for its own jurisdiction and that it does not arrive for
another. No edit in between.
