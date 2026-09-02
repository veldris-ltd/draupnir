# `veldris-draupnir-lmeval`

The lm-evaluation-harness `EvalDriver` (`raun.lmeval/v1`).

## `collect_gates` never sets `passed`

The driver reports the value it measured and leaves `passed` False. Deciding
whether a measurement passes needs the threshold, the baseline and the
comparison, all of which are GLEIPNIR's and reach RAUN through the judge seam.
A driver that filled that field in would be a driver that could pass its own
evaluation.

## The task-to-gate map

`GATE_TASKS` maps a harness metric onto a gate identifier. It is data and it is
overridable per driver instance, because a jurisdiction suite measuring E4 with
its own task set is a configuration change rather than a core change
(SAD 10.2).

## Two refusals

A gate with no task mapped to it is refused: running the suite would produce no
measurement for it, and a gate nobody measured is a gate nobody passed.

A relative gate with no baseline is refused. E1, E2, E3 and E5 all compare
against a baseline, and a relative gate with none is not a pass, it is an
unknown.
