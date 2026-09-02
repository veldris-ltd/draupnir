# `veldris-draupnir-quantise`

The `ExportDriver` for NVFP4, GGUF and MLX (`skidbladnir.quantise/v1`).

One driver for three formats, because they differ only in the command they
invoke and three packages would be three places to change when the collection
step changes.

## MLX runs on ALVISS

`BUILDS` records the host each format is built on, and `hosts_for` returns the
mapping so a scheduler can split one export across two machines without this
driver knowing how a scheduler works. ALVISS is the only Apple silicon in the
estate; an MLX build scheduled onto the forge would silently not happen.

## MLX without NVFP4 is refused

The two are compared as a cross-platform quantisation check
(`draupnir.skidbladnir.formats`). A quantisation defect looks like a healthy
model — it loads, it generates fluent text, it scores within the loss a gate
tolerates. What catches it is two builds of the same weights disagreeing. An
MLX build with no NVFP4 to compare against is the one case where such a defect
passes unnoticed, so `validate` refuses that combination.
