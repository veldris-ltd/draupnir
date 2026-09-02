"""RAUN, trial by ordeal: evaluation and assurance.

Gate suite, baselines, regression, quantisation checks. SAD 5.2.

Owns: Gate suite execution, baseline management, comparison, regression detection.
Must not: Change any artefact.

| Module | What it decides |
|---|---|
| `suites` | Which evaluation runs against which artefact, at what version |
| `baselines` | What a relative gate is measured against |
| `regression` | Whether this is worse than the release it would replace |
| `judging` | The seam to GLEIPNIR, who decides what a number means |
| `transitions` | Evidence rendered as the facts the state machine's guards read |

GLEIPNIR defines a gate and RAUN executes it, and the two modules cannot import
each other -- so the seam is a `Judge` protocol and the wiring lives in
`draupnir.api.assurance`. RAUN never holds a threshold, which is what makes
"the module that runs the evaluation does not decide what it means" structural
rather than a convention (Decision S4).

A regression is reported, never blocked on. RAUN is not the module that judges.
"""
