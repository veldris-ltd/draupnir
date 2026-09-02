# `veldris-draupnir-mergekit`

The mergekit `MergeDriver` (`brisingamen.mergekit/v1`).

Runs TIES, DARE-TIES, SLERP, task arithmetic and linear merges. It does not
choose a weight, run a sweep, or decide whether a merge is acceptable: the
sweep is BRISINGAMEN's and the verdict is RAUN's (SAD 5.2).

## The one refusal worth knowing

mergekit will happily merge models with mismatched tokenisers and produce a
model that generates plausible nonsense. `validate` refuses when the models
declare different tokeniser hashes, because the alternative is finding out at
evaluation after the compute has been spent.

## Configuration in the environment

`render` is pure (Decision S5), so it cannot write the mergekit YAML. The
configuration travels in `DRAUPNIR_MERGE_CONFIG` and the job writes it before
starting. Emitted as JSON, which `yaml.safe_load` accepts.
