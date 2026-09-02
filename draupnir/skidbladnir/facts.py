"""A recorded fact, and the difference between "no" and "not recorded".

The requirement: "The model card is rendered from recorded facts only. If a
fact is absent, the card says so; it never omits the field silently."

That is harder than it sounds, and the reason is `None`. A renderer handed a
mapping cannot tell the difference between a field that was measured as absent,
a field nobody recorded, and a field whose key was misspelled upstream. All
three arrive as a missing key, and the natural rendering of a missing key is to
skip the row -- which is precisely the silent omission the requirement forbids.

So a fact is a `Fact`, not a value. It is either `known`, and carries a value
and where the value came from, or it is `absent`, and carries why. Rendering an
absent fact produces a visible row saying it is not recorded. There is no
overload of `None` anywhere in the card.

The provenance matters as much as the value. Decision S11 requires Article 53
artefacts to be "generated from the pipeline record, never authored separately";
a card that states a fact without recording where it came from is a card that
cannot be checked against the record it claims to be generated from.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Self

#: What is rendered where a fact was never recorded. Deliberately not blank,
#: not "N/A" and not "-": a reader must be able to tell a missing measurement
#: from a measurement of zero, and a dash does not do that.
NOT_RECORDED = "not recorded"


class FactError(Exception):
    """Raised when a fact is built inconsistently."""


@dataclass(frozen=True, slots=True)
class Fact:
    """One value, or a stated reason there is no value."""

    name: str
    value: Any = None
    #: Where the value came from: `ledger`, `licence-register`, `gate-result`.
    #: A fact with no source cannot be checked against the record.
    source: str = ""
    #: Why there is no value. Set only when the fact is absent.
    reason: str = ""
    known: bool = True

    def __post_init__(self) -> None:
        """Refuse a fact that claims to be both known and unrecorded."""
        if self.known and not self.source:
            msg = (
                f"fact {self.name!r} states a value but records no source. Decision "
                "S11 requires release documentation to be generated from the pipeline "
                "record; a value with no provenance cannot be checked against it."
            )
            raise FactError(msg)
        if not self.known and not self.reason:
            msg = (
                f"fact {self.name!r} is absent and says nothing about why. The card "
                "must say what it does not know, and 'unknown' is not an explanation."
            )
            raise FactError(msg)

    @classmethod
    def recorded(cls, name: str, value: Any, source: str) -> Self:
        """A fact read from the record."""
        return cls(name=name, value=value, source=source, known=True)

    @classmethod
    def absent(cls, name: str, reason: str) -> Self:
        """A fact nobody recorded, and why."""
        return cls(name=name, value=None, reason=reason, known=False)

    @classmethod
    def optional(cls, name: str, value: Any, source: str, reason: str) -> Self:
        """A fact that may or may not have been recorded.

        The one constructor that takes a possibly-`None` value, and it converts
        it into an explicit absence immediately, so `None` never travels
        further than this line.
        """
        if value is None or value == "":
            return cls.absent(name, reason)
        return cls.recorded(name, value, source)

    def render(self) -> str:
        """The value as it appears on the card, or a statement that it is absent."""
        if not self.known:
            return f"{NOT_RECORDED}: {self.reason}"
        if isinstance(self.value, bool):
            return "yes" if self.value else "no"
        if isinstance(self.value, (list, tuple)):
            return ", ".join(str(item) for item in self.value) if self.value else "none"
        return str(self.value)

    def as_payload(self) -> dict[str, Any]:
        """The machine-readable shape, keeping the absence explicit."""
        return {
            "name": self.name,
            "known": self.known,
            "value": self.value if self.known else None,
            "source": self.source or None,
            "reason": self.reason or None,
        }


@dataclass(frozen=True, slots=True)
class FactSet:
    """The facts of one section of a card, in the order they are rendered."""

    section: str
    facts: tuple[Fact, ...] = ()

    def __iter__(self) -> Iterable[Fact]:
        """Every fact, in render order."""
        return iter(self.facts)

    def __len__(self) -> int:
        """How many facts the section holds."""
        return len(self.facts)

    @property
    def absent(self) -> tuple[Fact, ...]:
        """Every fact this section could not record."""
        return tuple(item for item in self.facts if not item.known)

    @property
    def complete(self) -> bool:
        """Whether every fact in the section was recorded."""
        return not self.absent

    def get(self, name: str) -> Fact:
        """One fact by name, or an absence saying it was never assembled."""
        for item in self.facts:
            if item.name == name:
                return item
        return Fact.absent(name, f"no {name!r} fact was assembled for this section")

    def as_payload(self) -> dict[str, Any]:
        """The machine-readable shape."""
        return {
            "section": self.section,
            "complete": self.complete,
            "facts": [item.as_payload() for item in self.facts],
        }

    def as_rows(self) -> tuple[tuple[str, str], ...]:
        """Label and rendered value, for a table. Absences included."""
        return tuple((item.name, item.render()) for item in self.facts)


def from_mapping(
    section: str, values: Mapping[str, Any], *, source: str, expected: Iterable[str]
) -> FactSet:
    """Build a section from a mapping, stating every expected key that is missing.

    `expected` is what makes this safe. Iterating the mapping's own keys would
    produce a card describing whatever happened to be there, which is exactly
    the failure mode of a card assembled from a dictionary.
    """
    return FactSet(
        section=section,
        facts=tuple(
            Fact.optional(
                name,
                values.get(name),
                source,
                f"the {source} holds no {name!r} for this release",
            )
            for name in expected
        ),
    )


def absences(sections: Iterable[FactSet]) -> tuple[str, ...]:
    """Every unrecorded fact across a card, as `section.fact`.

    Rendered at the top of the card. A reader deciding whether to trust a
    release should not have to find the gaps by reading every section.
    """
    return tuple(
        f"{section.section}.{fact.name}" for section in sections for fact in section.absent
    )
