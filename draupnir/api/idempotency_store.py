"""The idempotency store, in PostgreSQL. RF-14.

`IdempotencyStore` keeps its records in a `dict`, which is correct for one
process and wrong for the deployment: SAD 5.1 specifies two to four API
processes, so a key reserved in process A was unknown to process B. The
documented behaviour -- "a second click while the first request is still
running is refused rather than acting twice" -- failed precisely under the
concurrency the control exists for, and every reservation was lost on restart.

The in-memory store stays. It is what the contract tests run against, because a
test of "does 409 carry a problem document" should not need PostgreSQL, and
because the three-way outcome is a property of the *rule* rather than of the
storage. This implements the same three refusals against a table.

**The reservation is one statement.** `INSERT ... ON CONFLICT ... DO UPDATE ...
WHERE created_at <= cutoff` does three things at once and does them atomically:
it claims a fresh key, it takes over an expired one, and it declines to touch a
live one. Whichever transaction commits first owns the key and the other is
told so by the database. A read-then-write would be a race between the read and
the write, which is the failure the key exists to prevent, reintroduced by the
fix for it.

**Synchronous, and it blocks the loop for the length of one indexed
statement.** The handlers call `reserve` and `complete` synchronously from
async endpoints, and keeping the interface identical was the point -- a store
that had to be awaited would change every call site and every test. Each call
is a single primary-key lookup against a table with one row per outstanding
request; the write path already goes through a thread for anything larger.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text

from draupnir.api.idempotency import (
    TTL,
    IdempotencyError,
    InFlightError,
    KeyReusedError,
    Record,
    fingerprint,
)

#: Claim the key, take over an expired one, or touch nothing. The `WHERE` on
#: the conflict branch is what makes "expired" and "live" different outcomes:
#: an expired row is overwritten and returned, a live one leaves the statement
#: affecting no rows, and the caller then reads it to decide which refusal.
_RESERVE = text(
    """
    INSERT INTO idempotency_key
        (site_id, actor, key, request_fingerprint, created_at)
    VALUES (:site_id, :actor, :key, :fingerprint, :now)
    ON CONFLICT (site_id, actor, key) DO UPDATE
       SET request_fingerprint = EXCLUDED.request_fingerprint,
           created_at = EXCLUDED.created_at,
           status = NULL,
           body = NULL,
           location = NULL
     WHERE idempotency_key.created_at <= :cutoff
    RETURNING key
    """
)

_READ = text(
    """
    SELECT key, site_id, actor, request_fingerprint, created_at, status, body, location
      FROM idempotency_key
     WHERE site_id = :site_id AND actor = :actor AND key = :key
    """
)

_COMPLETE = text(
    """
    UPDATE idempotency_key
       SET status = :status, body = CAST(:body AS JSONB), location = :location
     WHERE site_id = :site_id AND actor = :actor AND key = :key
    RETURNING key, site_id, actor, request_fingerprint, created_at, status, body, location
    """
)

_RELEASE = text(
    "DELETE FROM idempotency_key WHERE site_id = :site_id AND actor = :actor AND key = :key"
)

_PURGE = text("DELETE FROM idempotency_key WHERE created_at <= :cutoff")


@dataclass
class DatabaseIdempotencyStore:
    """The same three-way outcome, against a table shared by every process.

    Site scoped like every other read and write: the session variable is set
    per transaction and the row level security policy reads it, so a key from
    one forge cannot resolve at another even if a caller names it.
    """

    engine: Engine

    @contextmanager
    def _scoped(self, site_id: str) -> Iterator[Any]:
        """A transaction with the site scope set, committed on the way out.

        `SET LOCAL` rather than `SET`: the scope lasts for the transaction and
        not for whatever the pool hands this connection to next, which is how a
        scoped read becomes an unscoped one three requests later.
        """
        with self.engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('draupnir.site_id', :site_id, true)"),
                {"site_id": site_id},
            )
            yield connection

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

        `None` when the key is fresh and the caller should proceed; a completed
        record when this is a replay; a refusal when the first request is still
        running or the key was reused for a different body.
        """
        if not key:
            msg = "an idempotency key is a non-empty string"
            raise IdempotencyError(msg)

        digest = fingerprint(payload)
        arguments = {
            "site_id": site_id,
            "actor": actor,
            "key": key,
            "fingerprint": digest,
            "now": now,
            "cutoff": now - ttl,
        }

        with self._scoped(site_id) as connection:
            claimed = connection.execute(_RESERVE, arguments).first()
            if claimed is not None:
                return None

            row = (
                connection.execute(_READ, {"site_id": site_id, "actor": actor, "key": key})
                .mappings()
                .first()
            )

        if row is None:
            # The row was there for the insert and gone for the read, which
            # means the sweep removed it between the two. Treat it as fresh:
            # the alternative is refusing a request that has every right to
            # proceed, and the reservation it would have taken is available.
            return None

        existing = _record(row)
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
        with self._scoped(site_id) as connection:
            row = (
                connection.execute(
                    _COMPLETE,
                    {
                        "site_id": site_id,
                        "actor": actor,
                        "key": key,
                        "status": status,
                        "body": json.dumps(dict(body or {}), default=str),
                        "location": location,
                    },
                )
                .mappings()
                .first()
            )

        if row is None:
            msg = f"Idempotency-Key {key!r} was never reserved; complete follows reserve"
            raise IdempotencyError(msg)
        return _record(row)

    def release(self, key: str, *, site_id: str, actor: str) -> None:
        """Drop a reservation whose request failed and therefore did not act.

        A client retrying after a 500 would otherwise be told its request was
        already in flight for the whole of the twenty-four hour window.
        """
        with self._scoped(site_id) as connection:
            connection.execute(_RELEASE, {"site_id": site_id, "actor": actor, "key": key})

    def purge(self, now: datetime, ttl: timedelta = TTL, *, site_id: str = "") -> int:
        """Drop expired records. Returns how many went.

        Called from the worker's timetable rather than from a request path: a
        sweep on the request path makes one unlucky caller pay for everybody
        else's expired keys, and does nothing at all on a quiet estate --
        which is exactly when the table grows without anybody looking.
        """
        with self._scoped(site_id) as connection:
            result = connection.execute(_PURGE, {"cutoff": now - ttl})
        return int(result.rowcount or 0)


def _record(row: Mapping[str, Any]) -> Record:
    """One row, as the store's vocabulary."""
    return Record(
        key=str(row["key"]),
        site_id=str(row["site_id"]),
        actor=str(row["actor"]),
        request_fingerprint=str(row["request_fingerprint"]),
        created_at=row["created_at"],
        status=row["status"],
        body=row["body"],
        location=row["location"],
    )


__all__ = ["DatabaseIdempotencyStore"]
