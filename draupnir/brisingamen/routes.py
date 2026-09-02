"""Release routes: what a CIM ships as.

**This is an interpretation, and it needs confirming.** SAD 5.2 gives
BRISINGAMEN "release route selection" and SAD 6.2's specification carries
`release.route: B`, but the document never says what A and B are. The reading
below is the one the rest of the SAD supports:

* **Route A** ships the adapter. The customer holds the base model and applies
  a LoRA adapter to it. Small to distribute, and the base stays the vendor's
  problem rather than ours.
* **Route B** ships merged dense weights, quantised. BRISINGAMEN's other
  responsibility is "adapter to dense export" (SAD 5.2), and SAD 6.2's example
  pairs `route: B` with `formats: [nvfp4, gguf-q4km, mlx4]`, which are dense
  quantisation formats. An adapter is not quantised to NVFP4; the model it is
  merged into is.

Both routes pass through MERGED, because the state machine of SAD 6.1 has no
path around it: EVALUATING to MERGED is the only successful exit from
EVALUATING. That is coherent rather than awkward. The merged artefact is what
capability is measured on either way -- an adapter's quality is a claim about
base plus adapter, and there is nothing else to evaluate -- so route A merges
to evaluate and publishes the adapter, while route B merges to evaluate and
publishes what it evaluated.

If the programme means something else by A and B, the change is this table and
the two functions under it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Route(StrEnum):
    """How a released model is delivered."""

    #: The adapter, with a reference to the base it applies to.
    A = "A"
    #: Merged dense weights, quantised to the named formats.
    B = "B"


class RouteError(Exception):
    """Raised when a route cannot be satisfied by what a run produced."""


#: Formats that describe a dense model. An adapter cannot be built in any of
#: them, which is what makes route A with a quantisation format a contradiction
#: rather than a preference.
DENSE_FORMATS: frozenset[str] = frozenset({"nvfp4", "gguf-q4km", "gguf-q8", "mlx4", "mlx8"})

#: Formats an adapter ships in.
ADAPTER_FORMATS: frozenset[str] = frozenset({"safetensors", "peft"})


@dataclass(frozen=True, slots=True)
class RouteDefinition:
    """What a route publishes, and what has to exist before it can."""

    route: Route
    #: The artefact kind that is published to the customer.
    publishes: str
    #: Formats this route may be asked for.
    permitted_formats: frozenset[str]
    #: Whether the published artefact needs the base model to be usable.
    requires_base: bool
    statement: str

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, for the model card and the console."""
        return {
            "route": str(self.route),
            "publishes": self.publishes,
            "permittedFormats": sorted(self.permitted_formats),
            "requiresBase": self.requires_base,
            "statement": self.statement,
        }


DEFINITIONS: dict[Route, RouteDefinition] = {
    Route.A: RouteDefinition(
        route=Route.A,
        publishes="adapter",
        permitted_formats=ADAPTER_FORMATS,
        requires_base=True,
        statement=(
            "The adapter is published with a reference to the base model it applies "
            "to. The customer holds the base. Distribution is small and the base "
            "model's licence obligations travel with the base rather than with us."
        ),
    ),
    Route.B: RouteDefinition(
        route=Route.B,
        publishes="quantised",
        permitted_formats=DENSE_FORMATS,
        requires_base=False,
        statement=(
            "The adapter is merged into the base and the dense result is quantised "
            "to the named formats. The customer needs nothing else, and every "
            "obligation attaching to the base model's weights travels with the "
            "release."
        ),
    ),
}


def definition(route: Route | str) -> RouteDefinition:
    """The definition for a route, or raise naming what exists."""
    try:
        return DEFINITIONS[Route(route)]
    except (KeyError, ValueError) as error:
        known = ", ".join(str(item) for item in Route)
        msg = f"{route!r} is not a release route; the routes are {known}"
        raise RouteError(msg) from error


def validate(route: Route | str, formats: Iterable[str]) -> RouteDefinition:
    """Raise unless these formats can be produced on this route.

    Checked at submission rather than at export. A route A specification asking
    for NVFP4 is not a preference to be honoured by quietly merging; it is two
    incompatible statements about what is being shipped, and the person who
    wrote it should say which they meant.
    """
    chosen = definition(route)
    requested = tuple(formats)
    if not requested:
        msg = (
            f"route {chosen.route} publishes a {chosen.publishes} and no format was "
            f"named. Permitted: {', '.join(sorted(chosen.permitted_formats))}"
        )
        raise RouteError(msg)

    impermissible = tuple(
        sorted(name for name in requested if name not in chosen.permitted_formats)
    )
    if impermissible:
        other = _route_permitting(impermissible)
        suggestion = f" Route {other} publishes those." if other is not None else ""
        msg = (
            f"route {chosen.route} publishes a {chosen.publishes} and cannot produce "
            f"{', '.join(impermissible)}. Permitted on this route: "
            f"{', '.join(sorted(chosen.permitted_formats))}.{suggestion} The route and "
            "the formats are two statements about what is shipped, and they disagree."
        )
        raise RouteError(msg)
    return chosen


def _route_permitting(formats: Sequence[str]) -> Route | None:
    """The route that permits all of these formats, if exactly one does."""
    candidates = [
        item.route
        for item in DEFINITIONS.values()
        if all(name in item.permitted_formats for name in formats)
    ]
    return candidates[0] if len(candidates) == 1 else None


def published_kind(route: Route | str) -> str:
    """The artefact kind a route delivers. What publication re-verifies."""
    return definition(route).publishes


def requires_merge_to_dense(route: Route | str) -> bool:
    """Whether this route needs the adapter exported into dense weights."""
    return definition(route).publishes != "adapter"
