"""Cursor pagination. Never offset.

SAD 11E.2 states the rule and the reason in one line: "Cursor based, never
offset. Offset pagination over a growing ledger silently skips rows."

AC-B3 is the test of it -- "a test inserting rows during pagination shows no
skipped or duplicated record" -- and that test is the whole justification. With
`LIMIT 50 OFFSET 50`, a row inserted before the second page shifts every later
row down by one, and the row that was at index 50 is now at 51 and is never
returned. Nobody notices, because the page after it is full.

A cursor is a position in a total order rather than a count of rows skipped, so
an insertion anywhere changes what comes next but never removes something that
was going to come.

Two things make that true rather than approximately true.

**The sort key is unique.** UUIDv7 identifiers sort by creation time
(SAD 11E.2, AC-B8), so `(created_at, id)` is a total order and a cursor names
exactly one row. Ordering by a timestamp alone would leave ties, and a tie
straddling a page boundary is a row returned twice or not at all.

**The cursor is opaque and validated.** It is base64 of the sort key, not
because that hides anything -- it plainly does not -- but because a client that
can read a cursor will construct one, and then the page boundary becomes an
input rather than a position we handed out.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

#: The default page size, and the ceiling. A client asking for everything gets
#: a page; AC-N4's 500-run list is four pages rather than one query that holds
#: half a megabyte of JSON in memory per concurrent request.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class PaginationError(Exception):
    """Raised when a cursor or a page size cannot be used."""


@dataclass(frozen=True, slots=True)
class Cursor:
    """A position in the `(created_at, id)` order."""

    created_at: datetime
    id: UUID

    def encode(self) -> str:
        """The opaque string a client passes back."""
        payload = json.dumps(
            {"t": self.created_at.isoformat(), "i": str(self.id)}, separators=(",", ":")
        )
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, raw: str) -> Cursor:
        """Read a cursor, refusing anything that is not one we issued.

        A malformed cursor is a client error rather than a silent reset to the
        first page. Resetting would loop a paginating client forever, which is
        a worse failure than an error it can see.
        """
        try:
            padded = raw + "=" * (-len(raw) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            return cls(created_at=datetime.fromisoformat(data["t"]), id=UUID(data["i"]))
        except (binascii.Error, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            msg = (
                f"{raw!r} is not a cursor this API issued. Cursors are opaque: pass "
                "back the `nextCursor` from the previous page rather than "
                "constructing one."
            )
            raise PaginationError(msg) from error


@dataclass(frozen=True, slots=True)
class Page[Item]:
    """One page of results, and where the next one starts."""

    items: tuple[Item, ...]
    next_cursor: str | None
    limit: int

    @property
    def has_more(self) -> bool:
        """Whether another page exists."""
        return self.next_cursor is not None

    def as_payload(self, render: Callable[[Item], Any]) -> dict[str, Any]:
        """The wire shape every collection endpoint returns.

        `nextCursor` is null on the last page rather than absent, so a client
        can branch on its value without checking whether the field is there.
        """
        return {
            "items": [render(item) for item in self.items],
            "nextCursor": self.next_cursor,
            "limit": self.limit,
        }


def clamp(limit: int | None) -> int:
    """Return a usable page size, refusing a nonsensical one.

    A negative or zero limit is refused rather than silently defaulted: it is
    a client bug, and returning a full page for `limit=0` would hide it.
    """
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1:
        msg = f"a page size is at least 1; {limit} was asked for"
        raise PaginationError(msg)
    return min(limit, MAX_LIMIT)


def paginate[Item](
    rows: Sequence[Item],
    *,
    limit: int,
    key: Callable[[Item], tuple[datetime, UUID]],
) -> Page[Item]:
    """Turn one over-fetched batch into a page and its next cursor.

    The caller fetches `limit + 1` rows. The extra one is not returned; its
    existence is what says there is another page, which avoids the count query
    that would otherwise be needed and would be wrong the moment it returned.
    """
    if limit < 1:
        msg = f"a page size is at least 1; {limit} was given"
        raise PaginationError(msg)

    page = tuple(rows[:limit])
    if len(rows) <= limit:
        return Page(items=page, next_cursor=None, limit=limit)

    created_at, identifier = key(page[-1])
    return Page(
        items=page,
        next_cursor=Cursor(created_at=created_at, id=identifier).encode(),
        limit=limit,
    )


def after(cursor: str | None) -> Cursor | None:
    """Decode a cursor parameter, or `None` for the first page."""
    return Cursor.decode(cursor) if cursor else None


def predicate(cursor: Cursor | None) -> tuple[str, dict[str, Any]]:
    """The SQL fragment and parameters that continue from a cursor.

    A row tuple comparison rather than `created_at > :t OR (created_at = :t AND
    id > :i)`. The two are equivalent and the tuple form is the one PostgreSQL
    can satisfy from the `(created_at, id)` index without re-reading, which is
    what keeps AC-N4 in budget on a growing table.
    """
    if cursor is None:
        return "", {}
    return "AND (created_at, id) > (:cursor_created_at, :cursor_id)", {
        "cursor_created_at": cursor.created_at,
        "cursor_id": str(cursor.id),
    }


def order_by() -> str:
    """The ordering every paginated query uses.

    One function, so a query cannot page over one order and cursor on another
    -- which produces a page sequence that skips and repeats while looking
    entirely correct.
    """
    return "ORDER BY created_at ASC, id ASC"
