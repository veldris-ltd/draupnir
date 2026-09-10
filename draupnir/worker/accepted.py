"""The chain as a queue: what was accepted and what closed it.

An API handler that returns 202 has accepted work, not done it (AC-B9: no
endpoint blocks an HTTP request on minutes of hashing or hours of training). The
worker does it. What joins the two is an entry in the chain, and the property
that makes the join reliable is that the queue *is* the chain -- a worker holds
nothing between ticks (SAD 11.2 row 1), so a restarted worker reads the same
queue and two workers reach the same answer.

RF-12 found the corpus half of that missing entirely: `ingest-accepted` was
recorded by the API and consumed by nothing, so a curator pressed Ingest, got a
run identifier, and nothing ever happened. RF-13 found the same shape around
arrays. This is the one implementation both use, because two would agree on the
day they were written.

**Keyed by the accepted entry's sequence number.** Not by the subject: a corpus
is re-ingested deliberately and an element is requeued more than once, and
keying on the subject would make the second request a silent no-op that had
already returned 202.

**Both outcomes close a request.** A failure is an entry. A chain holding only
successes cannot tell "not reached yet" from "tried and failed" -- the two
states an operator most needs told apart -- and an accepted entry nobody closed
is retried on every tick for ever while telling them nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from draupnir.core.domain.ledger import LedgerEntry

#: The payload field carrying the sequence number of the request an outcome
#: closes. One name across every queue, so a reader of the chain can join the
#: two without knowing which pair of transitions they are looking at.
ANSWERS = "answers_seq"


@dataclass(frozen=True, slots=True)
class Request:
    """One accepted piece of work, waiting to be done."""

    seq: int
    subject: str
    transition: str
    payload: Mapping[str, Any]

    def answering(self, **extra: Any) -> dict[str, Any]:
        """The common half of an outcome entry: what it closes, and about what."""
        return {"subject": self.subject, ANSWERS: self.seq, **extra}


def outstanding(
    entries: Iterable[LedgerEntry], *, accepted: str, closed_by: Sequence[str]
) -> tuple[Request, ...]:
    """Every accepted request no outcome has closed, in acceptance order.

    One pass over the entries rather than a query per request. The number of
    entries about any one subject type is small -- fifty-six jurisdictions and
    a handful of requests each -- and a query per request would be a query per
    request per tick for ever.
    """
    closing = set(closed_by)
    answered: set[int] = set()
    requests: list[Request] = []

    for entry in entries:
        payload = entry.payload if isinstance(entry.payload, Mapping) else {}
        if entry.transition in closing:
            recorded = payload.get(ANSWERS)
            if isinstance(recorded, int):
                answered.add(recorded)
            continue
        if entry.transition != accepted:
            continue
        requests.append(
            Request(
                seq=entry.seq,
                subject=entry.subject_id,
                transition=accepted,
                payload=dict(payload),
            )
        )

    return tuple(item for item in requests if item.seq not in answered)


__all__ = ["ANSWERS", "Request", "outstanding"]
