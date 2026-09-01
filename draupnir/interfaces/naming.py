"""Entry point naming and version negotiation.

SAD 10.3 rule 1: "Interfaces are versioned in the entry point name, for example
`hamarr.llamafactory/v1`. A breaking change is a new major version, not an
edit." Rule 2: "The core supports the current and the immediately previous
major version of every interface."

This module is the single place that knows how to read such a name, so the
loader, the drivers and the conformance suite cannot disagree about it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: `namespace.implementation/vMAJOR`. Lowercase, because an entry point name
#: that differs only in case is an entry point name nobody can search for.
PATTERN: Final = re.compile(
    r"^(?P<namespace>[a-z0-9_]+)\.(?P<implementation>[a-z0-9_]+)/v(?P<major>[1-9][0-9]*)$"
)


class InterfaceNameError(ValueError):
    """Raised when an entry point name is not a versioned interface name."""


@dataclass(frozen=True, slots=True, order=True)
class InterfaceName:
    """A parsed entry point name, e.g. `hamarr.llamafactory/v1`."""

    namespace: str
    implementation: str
    major: int

    @classmethod
    def parse(cls, raw: str) -> InterfaceName:
        """Parse `raw`, raising `InterfaceNameError` with the offending text."""
        match = PATTERN.match(raw)
        if match is None:
            msg = (
                f"{raw!r} is not a versioned interface name. "
                "Expected `namespace.implementation/vMAJOR`, "
                "for example `hamarr.llamafactory/v1` (SAD 10.3)."
            )
            raise InterfaceNameError(msg)
        return cls(
            namespace=match["namespace"],
            implementation=match["implementation"],
            major=int(match["major"]),
        )

    def __str__(self) -> str:
        """Render back to the entry point form."""
        return f"{self.namespace}.{self.implementation}/v{self.major}"

    @property
    def unversioned(self) -> str:
        """The name without its version, for grouping implementations."""
        return f"{self.namespace}.{self.implementation}"


def supported_majors(current: int) -> frozenset[int]:
    """Return the major versions the core accepts, given the current one.

    SAD 10.3 rule 2: the current and the immediately previous major version.
    At major 1 there is no previous one, and the set is just {1}.
    """
    if current < 1:
        msg = f"an interface major version starts at 1, not {current}"
        raise ValueError(msg)
    return frozenset({current} if current == 1 else {current, current - 1})
