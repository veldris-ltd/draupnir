"""The chat template map, and the refusal when a base model is not in it.

A chat template is how a base model was taught to see a conversation: which
tokens open a turn, which close it, where the system prompt goes. Applying the
wrong one is not a crash. Training proceeds, the loss curve looks ordinary,
and what comes out is a model that has learned to answer a format nobody will
ever send it. It is discovered at evaluation, after the compute is spent, and
the symptom -- a model that is subtly worse -- looks like a data problem.

So there is no default. A base model that is not in this map raises, and the
message names what is known so that the fix is a line in a versioned map
rather than a guess.

The map is versioned because the templates themselves change: LLaMA-Factory
renames them between releases, and a run reproduced two years on must resolve
the template the original run used, not the one the current release calls by
that name (SAD 10.1).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

#: The current map version. Recorded with every rendered configuration, so a
#: replay resolves the template the run actually used.
CURRENT_VERSION: Final = "hamarr-templates/2026.01"

#: The previous map, kept because SAD 10.3 rule 2 supports the current and the
#: immediately previous version, and a run recorded under it must still replay.
PREVIOUS_VERSION: Final = "hamarr-templates/2025.11"


class UnknownBaseModelError(ValueError):
    """Raised when a base model has no chat template in the map.

    The whole point of the class. Defaulting to LLaMA-Factory's `default`
    template would train against the wrong conversation format and produce a
    model that fails evaluation for reasons no one would connect to this.
    """

    def __init__(self, base: str, known: Mapping[str, str], version: str) -> None:
        """Name the base, the map it was looked up in, and what is in it."""
        self.base = base
        self.known = tuple(sorted(known))
        self.version = version
        super().__init__(
            f"no chat template is registered for base model {base!r} in {version}. "
            f"Known: {', '.join(self.known)}. There is deliberately no default: "
            "applying the wrong chat template trains a model against a conversation "
            "format it will never be sent, and the damage is only visible at "
            "evaluation, after the allocation is spent. Add the base to the template "
            "map under a new map version."
        )


@dataclass(frozen=True, slots=True)
class TemplateMap:
    """One version of the base-model to chat-template mapping."""

    version: str
    templates: Mapping[str, str]

    def resolve(self, base: str) -> str:
        """Return the chat template for `base`, or raise. Never defaults."""
        try:
            return self.templates[base]
        except KeyError as missing:
            raise UnknownBaseModelError(base, self.templates, self.version) from missing

    def knows(self, base: str) -> bool:
        """Whether this map can resolve `base`."""
        return base in self.templates


#: 2026.01. The two CIM-56 substrates, plus the upstream bases they derive
#: from, because a substrate run trains the substrate itself.
_CURRENT: Final[Mapping[str, str]] = {
    # Tier A substrate and its upstream.
    "MIDGARD-CORE-GEMMA3-27B-v1.0": "gemma3",
    "google/gemma-3-27b-pt": "gemma3",
    # Tier B substrate and its upstream.
    "MIDGARD-CORE-QWEN36-35B-A3B-v1.0": "qwen3_nothink",
    "Qwen/Qwen3-30B-A3B-Base": "qwen3_nothink",
}

#: 2025.11. Kept for replay. Qwen was on the thinking template before the
#: substrate was rebuilt; a run recorded under this version replays with it.
_PREVIOUS: Final[Mapping[str, str]] = {
    "MIDGARD-CORE-GEMMA3-27B-v1.0": "gemma",
    "google/gemma-3-27b-pt": "gemma",
    "MIDGARD-CORE-QWEN36-35B-A3B-v1.0": "qwen3",
    "Qwen/Qwen3-30B-A3B-Base": "qwen3",
}

CURRENT = TemplateMap(version=CURRENT_VERSION, templates=_CURRENT)
PREVIOUS = TemplateMap(version=PREVIOUS_VERSION, templates=_PREVIOUS)

_MAPS: Final[Mapping[str, TemplateMap]] = {
    CURRENT_VERSION: CURRENT,
    PREVIOUS_VERSION: PREVIOUS,
}


def by_version(version: str | None = None) -> TemplateMap:
    """Return a template map by version, defaulting to the current one."""
    if version is None:
        return CURRENT
    try:
        return _MAPS[version]
    except KeyError as missing:
        msg = (
            f"template map {version!r} is not available. This build carries "
            f"{', '.join(sorted(_MAPS))}. SAD 10.3 keeps the current and the "
            "immediately previous version, and no more."
        )
        raise ValueError(msg) from missing


def resolve(base: str, *, version: str | None = None) -> str:
    """Resolve a base model's chat template, or raise. Never defaults."""
    return by_version(version).resolve(base)


__all__ = [
    "CURRENT",
    "CURRENT_VERSION",
    "PREVIOUS",
    "PREVIOUS_VERSION",
    "TemplateMap",
    "UnknownBaseModelError",
    "by_version",
    "resolve",
]
