"""A specification a driver can be handed, taken from SAD 6.2.

A conformance suite needs something to render. This is the worked example from
the architecture document rather than an invented one, so that a driver author
reading the SAD and a driver author running the suite are looking at the same
thing.
"""

from __future__ import annotations

from typing import Any

from draupnir.interfaces.types import RunSpec

#: The adapter run of SAD 6.2, verbatim in structure.
SAMPLE_SPEC_MAPPING: dict[str, Any] = {
    "apiVersion": "draupnir/v1",
    "kind": "AdapterRun",
    "metadata": {"name": "cim-gbr-v0.1", "jurisdiction": "GBR", "tier": "A"},
    "spec": {
        "base": {
            "artefact": "hodd://models/core/MIDGARD-CORE-QWEN36-35B-A3B-v1.0",
            "expectSha256": "a" * 64,
        },
        "dataset": {
            "artefact": "hodd://corpora/GBR/curated",
            "expectSha256": "b" * 64,
            "cutoffPercentile": 99,
        },
        "train": {
            "driver": "hamarr.llamafactory/v1",
            "method": "lora",
            "params": {"rank": 64, "alpha": 128, "dropout": 0.05, "epochs": 3, "lr": 1.0e-4},
            "precision": "bf16",
        },
        "placement": {
            "driver": "motsognir.slurm/v1",
            "partition": "adapters",
            "nodes": 1,
            "maxConcurrent": 3,
            "retryBudget": 2,
        },
        "evaluate": {
            "driver": "raun.lmeval/v1",
            "suites": ["general-core", "cim-gbr"],
            "gates": ["E1", "E2", "E3", "E4", "E5", "E6"],
            "baseline": "run://MIDGARD-CORE-QWEN36-35B-A3B-v1.0",
        },
        "release": {
            "route": "B",
            "formats": ["nvfp4", "gguf-q4km", "mlx4"],
            "approval": "required",
        },
    },
}


def sample_spec(**overrides: Any) -> RunSpec:
    """Return the SAD 6.2 specification, with `spec` block overrides applied.

    Overrides are shallow-merged into the `spec` block, which is where a
    driver's own test usually needs to differ: a different method, a different
    format, a different partition.
    """
    data: dict[str, Any] = {
        **SAMPLE_SPEC_MAPPING,
        "spec": {**SAMPLE_SPEC_MAPPING["spec"], **overrides},
    }
    return RunSpec.from_mapping(data)
