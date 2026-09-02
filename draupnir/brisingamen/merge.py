"""Merge planning and adapter-to-dense export.

SAD 5.2 gives BRISINGAMEN "merge method drivers, weight sweep execution,
adapter to dense export" and one prohibition: it must not "decide whether a
merge is acceptable. RAUN decides". Nothing here reads a gate result to form a
view; the only place evidence appears is `sweep.select`, which reads the
verdict rather than reaching one.

A merge configuration is hashed, because SAD 6.1 records `merge_config_hash` on
the MERGED to QUANTISED transition. The hash is over the configuration as it
was submitted to the driver, not over a summary of it: a hash of a summary is a
hash that matches for two configurations that differ in a field the summary
dropped.

Adapter-to-dense export is a merge with one adapter at weight 1.0. Stating it
that way rather than as a separate operation means there is one code path that
produces dense weights, and therefore one thing to get right. A separate
"just apply the adapter" path is where a scaling factor gets silently dropped.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from draupnir.brisingamen.routes import Route, definition


class Method(StrEnum):
    """Merge methods. SAD 8.2: mergekit TIES, DARE-TIES, SLERP, task arithmetic."""

    TIES = "ties"
    DARE_TIES = "dare-ties"
    SLERP = "slerp"
    TASK_ARITHMETIC = "task-arithmetic"
    #: One adapter applied to one base. The adapter-to-dense export of SAD 5.2.
    LINEAR = "linear"


#: Methods that combine more than one adapter. Sweeping a single-adapter merge
#: over its weight is meaningful; sweeping TIES over one adapter is not, and
#: would spend five allocations discovering that.
MULTI_ADAPTER: frozenset[Method] = frozenset(
    {Method.TIES, Method.DARE_TIES, Method.SLERP, Method.TASK_ARITHMETIC}
)


class MergeError(Exception):
    """Raised when a merge cannot be planned."""


@dataclass(frozen=True, slots=True)
class AdapterRef:
    """One adapter going into a merge, by hash."""

    sha256: str
    #: The jurisdiction it was trained for, for the model card's provenance.
    jurisdiction: str
    #: Its coefficient in the merge. Swept, for the point being evaluated.
    weight: float = 1.0

    def as_mapping(self) -> dict[str, Any]:
        """The wire shape."""
        return {"sha256": self.sha256, "jurisdiction": self.jurisdiction, "weight": self.weight}


@dataclass(frozen=True, slots=True)
class MergeConfig:
    """What is merged into what, and how. Hashed into the ledger."""

    method: Method
    base_sha256: str
    adapters: tuple[AdapterRef, ...]
    #: Method-specific settings passed through to the driver untouched.
    parameters: Mapping[str, Any] = field(default_factory=dict)
    dtype: str = "bfloat16"

    def __post_init__(self) -> None:
        """Refuse a merge that cannot be executed or cannot be reproduced."""
        if not self.adapters:
            msg = "a merge with no adapters produces the base model; that is not a merge"
            raise MergeError(msg)
        if self.method in MULTI_ADAPTER and len(self.adapters) < 2:
            msg = (
                f"{self.method} combines several adapters and only "
                f"{len(self.adapters)} was given. Use {Method.LINEAR} to apply one "
                "adapter to a base; a multi-adapter method over a single adapter "
                "spends allocations discovering it is the identity."
            )
            raise MergeError(msg)
        seen = [item.sha256 for item in self.adapters]
        if len(set(seen)) != len(seen):
            msg = (
                "the same adapter appears twice in this merge. Its contribution "
                "would be counted twice at a weight nobody wrote down."
            )
            raise MergeError(msg)

    def as_mapping(self) -> dict[str, Any]:
        """The configuration in the shape the hash is taken over."""
        return {
            "method": str(self.method),
            "baseSha256": self.base_sha256,
            "adapters": [item.as_mapping() for item in self.adapters],
            "parameters": dict(sorted(self.parameters.items())),
            "dtype": self.dtype,
        }

    def canonical(self) -> bytes:
        """Deterministic bytes, for the hash SAD 6.1 records."""
        return json.dumps(
            self.as_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def config_hash(self) -> str:
        """The `merge_config_hash` of the MERGED to QUANTISED transition.

        Over the whole configuration rather than a summary of it. A hash of a
        summary matches for two configurations differing in a field the summary
        dropped, which is the one case where a hash needs to disagree.
        """
        return hashlib.sha256(self.canonical()).hexdigest()

    def at_weight(self, parameters: Mapping[str, float]) -> MergeConfig:
        """This configuration with one sweep point's parameters applied.

        A sweep over `weight` rescales every adapter; a sweep over a named
        adapter's weight rescales that one. Both arrive as a mapping, so the
        sweep does not need to know which shape it is producing.
        """
        from dataclasses import replace

        if "weight" in parameters and len(self.adapters) == 1:
            only = self.adapters[0]
            return replace(self, adapters=(replace(only, weight=parameters["weight"]),))

        adjusted = tuple(
            replace(item, weight=parameters.get(item.jurisdiction, item.weight))
            for item in self.adapters
        )
        extra = {key: value for key, value in parameters.items() if key == "weight"}
        return replace(self, adapters=adjusted, parameters={**dict(self.parameters), **extra})


def linear(
    *, base_sha256: str, adapter_sha256: str, jurisdiction: str, weight: float = 1.0
) -> MergeConfig:
    """One adapter applied to one base. The adapter-to-dense export.

    Route B needs dense weights, and this is how they are produced. Deliberately
    the same object a swept merge uses, so there is one path to dense weights
    and therefore one place a scaling factor can be dropped.
    """
    return MergeConfig(
        method=Method.LINEAR,
        base_sha256=base_sha256,
        adapters=(AdapterRef(sha256=adapter_sha256, jurisdiction=jurisdiction, weight=weight),),
    )


def plan_for_route(
    route: Route | str, *, base_sha256: str, adapter_sha256: str, jurisdiction: str
) -> tuple[MergeConfig, str]:
    """The merge a route requires, and the artefact kind it will publish.

    Both routes merge, and they merge identically. Route A publishes the
    adapter but is still evaluated on the merged model, because an adapter's
    capability is a claim about base plus adapter and there is nothing else to
    measure. What differs between the routes is which artefact reaches the
    customer, which is the second value returned.
    """
    chosen = definition(route)
    config = linear(
        base_sha256=base_sha256, adapter_sha256=adapter_sha256, jurisdiction=jurisdiction
    )
    return config, chosen.publishes
