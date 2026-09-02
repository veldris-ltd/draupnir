"""ETag and If-Match. A stale conditional write returns 412 rather than winning.

SAD 11E.2 and AC-B4. The failure this prevents is the ordinary one: two
operators open a run, one cancels it, the other retries it against the state
they loaded a minute ago, and the retry silently undoes the cancellation.

Three decisions.

**The ETag is derived from the resource, not stored beside it.** A stored
version column has to be incremented by every writer, and the one that forgets
produces an ETag that says nothing changed when something did. A digest over
the fields that matter cannot be forgotten, because there is nothing to
remember.

**`If-Match` is required on a mutating request against a mutable resource,
not optional.** A missing header is 428 Precondition Required, not a
free-for-all. Optional concurrency control is concurrency control that the one
client which needed it did not use.

**The wildcard is honoured, and it means what the RFC says.** `If-Match: *`
asserts the resource exists, which is a useful thing for a client that wants
to cancel whatever is current without caring what it is. It is not a way to
opt out of the check.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class ConcurrencyError(Exception):
    """Raised when a conditional request cannot proceed."""


class PreconditionRequiredError(ConcurrencyError):
    """Raised when a mutating request omits `If-Match`. 428.

    Not a permissive default. The whole value of the control is that it applies
    to the request that would otherwise have overwritten something.
    """

    def __init__(self, resource: str) -> None:
        """Name the resource that requires a precondition."""
        self.resource = resource
        super().__init__(
            f"this request modifies {resource} and carries no If-Match. Read the "
            "resource, take its ETag, and send it back: a write with no precondition "
            "is a write that silently overwrites whatever changed in between "
            "(SAD 11E.2)."
        )


class PreconditionFailedError(ConcurrencyError):
    """Raised when `If-Match` does not match the current state. 412.

    AC-B4. The response says what the current ETag is, so a client can re-read,
    reconcile and retry without a second round trip to discover it.
    """

    def __init__(self, resource: str, expected: str, current: str) -> None:
        """Name the resource and both tags."""
        self.resource = resource
        self.expected = expected
        self.current = current
        super().__init__(
            f"{resource} has changed since it was read. If-Match sent {expected}, and "
            f"it is now {current}. The write is refused rather than applied over "
            "somebody else's change (AC-B4). Re-read, reconcile, and retry."
        )


def etag(state: Mapping[str, Any]) -> str:
    """Derive an entity tag from the state that matters.

    Strong rather than weak: the tag changes when any byte of the represented
    state changes, which is what a conditional *write* needs. A weak tag says
    two representations are semantically equivalent, and equivalence is not the
    question when deciding whether somebody else has written.
    """
    canonical = json.dumps(dict(state), sort_keys=True, separators=(",", ":"), default=str)
    return f'"{hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]}"'


def matches(current: str, header: str | None) -> bool:
    """Whether an `If-Match` header value permits the write.

    Handles the list form: RFC 9110 allows `If-Match: "a", "b"` and any member
    matching is a match.
    """
    if header is None:
        return False
    candidate = header.strip()
    if candidate == "*":
        return True
    return any(item.strip() == current for item in candidate.split(","))


@dataclass(frozen=True, slots=True)
class Precondition:
    """The outcome of checking a conditional request."""

    resource: str
    current: str
    header: str | None

    @property
    def supplied(self) -> bool:
        """Whether the client sent a precondition at all."""
        return self.header is not None

    @property
    def satisfied(self) -> bool:
        """Whether the write may proceed."""
        return matches(self.current, self.header)

    def enforce(self) -> None:
        """Raise unless the write may proceed. AC-B4."""
        if not self.supplied:
            raise PreconditionRequiredError(self.resource)
        if not self.satisfied:
            raise PreconditionFailedError(self.resource, str(self.header), self.current)


def require(resource: str, state: Mapping[str, Any], header: str | None) -> str:
    """Check a conditional write and return the current tag.

    Returns the tag so a handler can set it on the response it is about to
    produce -- a mutation returns the new representation, and the client's next
    conditional write should use the tag of what it just received rather than
    re-reading.
    """
    precondition = Precondition(resource=resource, current=etag(state), header=header)
    precondition.enforce()
    return precondition.current


def unchanged(state: Mapping[str, Any], header: str | None) -> bool:
    """Whether a read may be answered 304, per `If-None-Match`."""
    return matches(etag(state), header)
