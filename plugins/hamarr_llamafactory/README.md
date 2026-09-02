# `veldris-draupnir-llamafactory`

The LLaMA-Factory `TrainDriver` (`hamarr.llamafactory/v1`).

## The chat template map

The one thing to read before changing anything here is
[`templates.py`](draupnir_hamarr_llamafactory/templates.py).

A base model that is not in the map raises `UnknownBaseModelError`. There is no
default and there must not be one. Applying the wrong chat template does not
crash: training proceeds, the loss curve looks ordinary, and the model learns
to answer a conversation format nobody will send it. It surfaces at evaluation,
after days of compute, looking like a data problem.

Adding a base model is a line in the map under a new map version. The map is
versioned because LLaMA-Factory renames templates between releases, and a run
replayed two years on must resolve the template the original run used, not the
one the current release calls by that name (SAD 10.1).

## Why the configuration travels in the environment

`render` is pure (Decision S5): no filesystem, no network, no clock. So it
cannot write a configuration file. It puts the configuration in
`DRAUPNIR_TRAIN_CONFIG` and the job's command writes it out before starting the
trainer. Two renders of one specification produce identical bytes, which is what
the conformance suite checks.

The configuration is JSON. LLaMA-Factory reads its configuration with
`yaml.safe_load`, and JSON is valid YAML, so this costs nothing and avoids
depending on a YAML library's formatting choices for determinism.

## The regular expressions

The patterns in `__init__.py` are the only place in the system that reads
executor output as text. Everything downstream receives `ProgressEvent`s, so an
upgrade that changes LLaMA-Factory's log format is a version bump of this
plug-in and touches no core file.
