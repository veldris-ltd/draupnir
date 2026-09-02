"""Idempotency keys: a replay returns the original result and does not act twice.

AC-B1, and SAD 11E.2 requires it on *every* mutating endpoint rather than on
the ones somebody remembered.

The interesting cases are not the happy path.

**A replay while the first request is still running.** Two clicks on a submit
button, or a client that retried on a timeout the server had not yet noticed.
Returning "not found" would let the second one act, so a key is reserved before
the work starts and a replay that finds a reservation gets 409 with a problem
document that says to retry, rather than a second run.

**The same key with a different body.** That is a client bug -- a key reused
for a different request -- and returning the *first* response would be worse
than either alternative, because the caller would believe the second request
succeeded. The request body is fingerprinted and a mismatch is 422.

**A key that outlives its usefulness.** Records expire. A key held forever
turns a resubmission a week later into a silent no-op returning a stale run
identifier, which looks exactly like the system ignoring the operator.

Scoped per site and per actor. Two operators using the same obvious key --
`retry-1` -- must not collide, and a key from one site must not resolve at
another.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

#: How long a record is honoured. Long enough to cover a client's retry
#: schedule and a network partition; short enough that a resubmission the next
#: day is a new request rather than a replay of an old one.
TTL: Final = timedelta(hours=24)


class IdempotencyError(Exception):
    """Raised when a request cannot be reconciled with a stored key."""


class InFlightError(IdempotencyError):
    """Raised when a key is reserved and the first request has not finished.

    409 rather than 404. The alternative is letting the second request act,
    which is the exact failure the key exists to prevent.
    """

    def __init__(self, key: str) -> None:
        """Name the key that is in flight."""
        self.key = key
        super().__init__(
            f"a request with Idempotency-Key {key!r} is already in progress. Retry "
            "shortly: this returns 409 rather than starting a second run, which is "
            "what the key exists to prevent."
        )


class KeyReusedError(IdempotencyError):
    """Raised when a key arrives with a different request body.

    422 rather than replaying the first response. Replaying would tell the
    caller their second, different request had succeeded.
    """

    def __init__(self, key: str) -> None:
        """Name the key that was reused."""
        self.key = key
        super().__init__(
            f"Idempotency-Key {key!r} was used for a different request body. The "
            "stored response is not returned, because it would tell you a request "
            "you did not make had succeeded. Use a new key."
        )


def fingerprint(payload: Any) -> str:
    """A stable digest of a request body, for detecting key reuse."""
    import json

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Record:
    """One key, and what became of the request that claimed it."""

    key: str
    site_id: str
    actor: str
    request_fingerprint: str
    created_at: datetime
    #: `None` while the first request is still running.
    status: int | None = None
    body: Mapping[str, Any] | None = None
    #: The resource the request created, echoed on a replay.
    location: str | None = None

    @property
    def in_flight(self) -> bool:
        """Whether the first request has yet to complete."""
        return self.status is None

    def expired(self, now: datetime, ttl: timedelta = TTL) -> bool:
        """Whether this record is old enough to be ignored."""
        return now - self.created_at > ttl


@dataclass
class IdempotencyStore:
    """Reservations and stored responses, keyed per site and actor.

    In memory here. The deployed implementation is a table with the same shape
    and the same three-way outcome; what matters architecturally is that a key
    is *reserved* before the work starts, because a store that only records
    completed responses cannot answer the in-flight case at all.
    """

    records: dict[tuple[str, str, str], Record] = field(default_factory=dict)

    @staticmethod
    def _index(key: str, *, site_id: str, actor: str) -> tuple[str, str, str]:
        """Keys are scoped: two operators may use the same obvious key."""
        return (site_id, actor, key)

    def reserve(
        self,
        key: str,
        *,
        site_id: str,
        actor: str,
        payload: Any,
        now: datetime,
        ttl: timedelta = TTL,
    ) -> Record | None:
        """Claim a key, or return the record a replay should be answered from.

        Returns `None` when the key is fresh and the caller should proceed.
        Returns a completed record when this is a replay. Raises when the first
        request is still running or when the key was reused for a different
        body.
        """
        if not key:
            msg = "an idempotency key is a non-empty string"
            raise IdempotencyError(msg)

        index = self._index(key, site_id=site_id, actor=actor)
        digest = fingerprint(payload)
        existing = self.records.get(index)

        if existing is not None and existing.expired(now, ttl):
            existing = None

        if existing is None:
            self.records[index] = Record(
                key=key,
                site_id=site_id,
                actor=actor,
                request_fingerprint=digest,
                created_at=now,
            )
            return None

        if existing.request_fingerprint != digest:
            raise KeyReusedError(key)
        if existing.in_flight:
            raise InFlightError(key)
        return existing

    def complete(
        self,
        key: str,
        *,
        site_id: str,
        actor: str,
        status: int,
        body: Mapping[str, Any] | None = None,
        location: str | None = None,
    ) -> Record:
        """Record what the first request returned, for a later replay."""
        index = self._index(key, site_id=site_id, actor=actor)
        existing = self.records.get(index)
        if existing is None:
            msg = f"Idempotency-Key {key!r} was never reserved; complete follows reserve"
            raise IdempotencyError(msg)

        from dataclasses import replace

        stored = replace(existing, status=status, body=dict(body or {}), location=location)
        self.records[index] = stored
        return stored

    def release(self, key: str, *, site_id: str, actor: str) -> None:
        """Drop a reservation whose request failed.

        A request that errored has not acted, so its key must not be held: a
        client retrying after a 500 would otherwise be told its request was
        already in flight forever.
        """
        self.records.pop(self._index(key, site_id=site_id, actor=actor), None)

    def purge(self, now: datetime, ttl: timedelta = TTL) -> int:
        """Drop expired records. Returns how many went."""
        stale = [index for index, item in self.records.items() if item.expired(now, ttl)]
        for index in stale:
            del self.records[index]
        return len(stale)

    def __len__(self) -> int:
        """How many records are held."""
        return len(self.records)
