"""Persistence for the ledger and the run projection.

Two rules shape every method here.

A repository cannot be constructed without a `SiteScope`. Not "defaults to the
local site", not "None means all sites" -- there is no way to spell an
unscoped query, so there is no way to write one by accident. The row level
security policy of SAD 11C is the second line of that defence; this is the
first, and it is the one that produces a stack trace naming the caller.

Nothing writes `run` except the projector. The table is derived, and the
repository exposes rebuild and incremental projection rather than an update.

These are synchronous. The worker polls, the CLI is a one-shot process, and
migrations and the seed are synchronous; the async edge of SAD 5.1 reaches
them through a thread, which is the right trade while a request does no more
than one of these per call.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from draupnir.core.domain.ledger import (
    GENESIS_HASH,
    ChainHead,
    LedgerEntry,
    first_divergence,
)
from draupnir.core.domain.projector import ProjectedRun, as_rows, project
from draupnir.core.domain.sites import (
    AnchorState,
    Site,
    SiteRegistry,
    SiteScope,
    UnscopedQueryError,
)
from draupnir.core.domain.states import RunState

#: How many entries a streaming read pulls at a time. Large enough that the
#: 100,000 entry verification of AC-N5 is a handful of round trips, small
#: enough that memory stays flat however long a chain grows.
BATCH = 5_000

_LEDGER_COLUMNS = (
    "id, site_id, seq, prev_hash, entry_hash, ts, actor, "
    "subject_type, subject_id, transition, payload"
)


def set_site_scope(connection: Connection, scope: SiteScope, *, local: bool = True) -> None:
    """Set the row level security variable the policies of SAD 11C read.

    `local=True` scopes it to the transaction, which is what production wants:
    the variable dies with the unit of work rather than leaking into whatever
    the pooled connection does next.
    """
    connection.execute(
        text("SELECT set_config('draupnir.site_id', :site_id, :local)"),
        {"site_id": scope.site_id, "local": local},
    )


def clear_site_scope(connection: Connection) -> None:
    """Clear the scope variable, so a subsequent unscoped query is refused.

    Cleared transaction-locally, matching how it is set. Clearing it at session
    level would leave a `SET LOCAL` from the current transaction still in
    force, so the scope would appear cleared and not be.
    """
    connection.execute(text("SELECT set_config('draupnir.site_id', '', true)"))


def current_site_scope(connection: Connection) -> str:
    """Return the scope variable as the database currently sees it."""
    return str(
        connection.execute(
            text("SELECT coalesce(current_setting('draupnir.site_id', true), '')")
        ).scalar_one()
    )


class ScopedRepository:
    """Base class carrying the scope every query is made under."""

    def __init__(self, connection: Connection, scope: SiteScope | None) -> None:
        """Bind to a connection and a scope, refusing to exist without one."""
        if scope is None:
            raise UnscopedQueryError(type(self).__name__)
        self._connection = connection
        self._scope = scope
        set_site_scope(connection, scope)

    @property
    def scope(self) -> SiteScope:
        """The site this repository reads and writes."""
        return self._scope

    @property
    def site_id(self) -> str:
        """The scoped site identifier."""
        return self._scope.site_id


class SiteRepository:
    """The forge registry. Unscoped by design: `site` has no site_id.

    Reading the list of forges is how a scope is chosen in the first place, so
    this is the one repository that cannot require one.
    """

    def __init__(self, connection: Connection) -> None:
        """Bind to a connection."""
        self._connection = connection

    def all(self) -> tuple[Site, ...]:
        """Return every registered forge."""
        rows = self._connection.execute(
            text(
                "SELECT id, name, location, timezone, control_plane_uri, "
                "anchor_state, last_anchored_at FROM site ORDER BY id"
            )
        ).all()
        return tuple(
            Site(
                id=row.id,
                name=row.name,
                location=row.location,
                timezone=row.timezone,
                control_plane_uri=row.control_plane_uri,
                anchor_state=AnchorState(row.anchor_state),
                last_anchored_at=row.last_anchored_at,
            )
            for row in rows
        )

    def registry(self, local: str) -> SiteRegistry:
        """Load the registry, naming which forge this control plane serves."""
        return SiteRegistry(self.all(), local=local)

    def set_anchor_state(
        self, site_id: str, state: AnchorState, *, anchored_at: datetime | None = None
    ) -> None:
        """Record a site's anchor state. SAD 11A.3 and 11A.4."""
        self._connection.execute(
            text(
                "UPDATE site SET anchor_state = :state, "
                "last_anchored_at = COALESCE(:anchored_at, last_anchored_at) WHERE id = :id"
            ),
            {"id": site_id, "state": str(state), "anchored_at": anchored_at},
        )


class LedgerRepository(ScopedRepository):
    """The append-only chain for one site."""

    def head(self) -> LedgerEntry | None:
        """Return the highest-sequence entry, or None for an empty chain."""
        row = self._connection.execute(
            text(
                f"SELECT {_LEDGER_COLUMNS} FROM ledger_entry "  # noqa: S608
                "WHERE site_id = :site_id ORDER BY seq DESC LIMIT 1"
            ),
            {"site_id": self.site_id},
        ).one_or_none()
        return None if row is None else _entry(row)

    def length(self) -> int:
        """Return how many entries the chain holds."""
        return int(
            self._connection.execute(
                text("SELECT count(*) FROM ledger_entry WHERE site_id = :site_id"),
                {"site_id": self.site_id},
            ).scalar_one()
        )

    def entry_hash_at(self, seq: int) -> str | None:
        """Return the `entry_hash` at `seq`, or None if the chain is shorter."""
        return self._connection.execute(
            text("SELECT entry_hash FROM ledger_entry WHERE site_id = :site_id AND seq = :seq"),
            {"site_id": self.site_id, "seq": seq},
        ).scalar_one_or_none()

    def append(self, entry: LedgerEntry) -> None:
        """Insert one entry. The table refuses anything but INSERT."""
        self.append_many((entry,))

    def append_many(self, entries: Sequence[LedgerEntry]) -> None:
        """Insert a run of entries in one statement."""
        if not entries:
            return
        foreign = {entry.site_id for entry in entries} - {self.site_id}
        if foreign:
            msg = f"{type(self).__name__} is scoped to {self.site_id}; refused {sorted(foreign)}"
            raise UnscopedQueryError(msg)

        self._connection.execute(
            text(
                f"INSERT INTO ledger_entry ({_LEDGER_COLUMNS}) VALUES "  # noqa: S608
                "(:id, :site_id, :seq, :prev_hash, :entry_hash, :ts, :actor, "
                ":subject_type, :subject_id, :transition, :payload)"
            ),
            [
                {
                    "id": entry.id,
                    "site_id": entry.site_id,
                    "seq": entry.seq,
                    "prev_hash": entry.prev_hash,
                    "entry_hash": entry.entry_hash,
                    "ts": entry.ts,
                    "actor": entry.actor,
                    "subject_type": entry.subject_type,
                    "subject_id": entry.subject_id,
                    "transition": entry.transition,
                    "payload": json.dumps(entry.payload, sort_keys=True),
                }
                for entry in entries
            ],
        )

    def stream(self, from_seq: int = 1, to_seq: int | None = None) -> Iterator[LedgerEntry]:
        """Yield entries in sequence order, a batch at a time.

        Streaming rather than loading: a chain grows without bound, and the
        one operation that must stay affordable forever is reading all of it.
        """
        cursor = max(from_seq, 1)
        while to_seq is None or cursor <= to_seq:
            upper = cursor + BATCH - 1
            if to_seq is not None:
                upper = min(upper, to_seq)
            rows = self._connection.execute(
                text(
                    f"SELECT {_LEDGER_COLUMNS} FROM ledger_entry "  # noqa: S608
                    "WHERE site_id = :site_id AND seq BETWEEN :low AND :high ORDER BY seq"
                ),
                {"site_id": self.site_id, "low": cursor, "high": upper},
            ).all()
            if not rows:
                return
            for row in rows:
                yield _entry(row)
            cursor = upper + 1

    def verify_chain(self, from_seq: int = 1, to_seq: int | None = None) -> int | None:
        """Return the first divergent sequence number, or None if intact.

        The window is verified against the entry before it, so verifying a
        slice is exactly as strong as verifying the whole chain up to that
        slice's end: a rewritten entry at seq 40 is caught by a check of
        41 to 50, because 41's prev_hash no longer matches.
        """
        start = max(from_seq, 1)
        if start == 1:
            expected_prev = GENESIS_HASH
        else:
            preceding = self.entry_hash_at(start - 1)
            if preceding is None:
                return start
            expected_prev = preceding

        cursor = start
        while to_seq is None or cursor <= to_seq:
            upper = cursor + BATCH - 1
            if to_seq is not None:
                upper = min(upper, to_seq)
            batch = list(self.stream(cursor, upper))
            if not batch:
                return None
            divergence = first_divergence(batch, expected_prev=expected_prev, start_seq=cursor)
            if divergence is not None:
                return divergence.seq
            expected_prev = batch[-1].entry_hash
            cursor = batch[-1].seq + 1
        return None

    def export_head(self) -> ChainHead | None:
        """Return the chain head in the form GULLINBURSTI anchors. SAD 11A.3."""
        latest = self.head()
        if latest is None:
            return None
        return ChainHead(site_id=latest.site_id, seq=latest.seq, entry_hash=latest.entry_hash)


def _as_uuid(value: Any) -> UUID:
    return UUID(value) if isinstance(value, str) else value


def _entry(row: Any) -> LedgerEntry:
    payload = row.payload if isinstance(row.payload, dict | list) else json.loads(row.payload)
    return LedgerEntry(
        id=row.id,
        site_id=row.site_id,
        seq=row.seq,
        prev_hash=row.prev_hash,
        entry_hash=row.entry_hash,
        ts=row.ts,
        actor=row.actor,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        transition=row.transition,
        payload=payload,
    )


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """What a projection run did."""

    projection: str
    site_id: str
    entries_read: int
    rows_written: int
    last_seq: int
    rebuilt: bool


class RunProjection(ScopedRepository):
    """Maintains the `run` table from the chain."""

    NAME = "run"

    def checkpoint(self) -> int:
        """Return the last sequence number this projection consumed."""
        value = self._connection.execute(
            text(
                "SELECT last_seq FROM projection_checkpoint "
                "WHERE site_id = :site_id AND projection = :projection"
            ),
            {"site_id": self.site_id, "projection": self.NAME},
        ).scalar_one_or_none()
        return int(value or 0)

    def _record_checkpoint(self, last_seq: int, *, rebuilt: bool) -> None:
        self._connection.execute(
            text(
                "INSERT INTO projection_checkpoint (site_id, projection, last_seq, rebuilt_at) "
                "VALUES (:site_id, :projection, :last_seq, :rebuilt_at) "
                "ON CONFLICT (site_id, projection) DO UPDATE SET "
                "last_seq = EXCLUDED.last_seq, "
                "rebuilt_at = COALESCE(EXCLUDED.rebuilt_at, projection_checkpoint.rebuilt_at)"
            ),
            {
                "site_id": self.site_id,
                "projection": self.NAME,
                "last_seq": last_seq,
                "rebuilt_at": datetime.now(tz=UTC) if rebuilt else None,
            },
        )

    def rebuild(self) -> ProjectionReport:
        """Discard the projection and replay the chain from sequence 1.

        Idempotent: running it twice produces identical table contents,
        because the fold is pure and the write is a full replacement rather
        than a merge.
        """
        entries = list(self._stream_all())
        runs = project(entries)
        self._connection.execute(text("DELETE FROM run WHERE site_id = :s"), {"s": self.site_id})
        written = self._write(runs.values())
        last_seq = entries[-1].seq if entries else 0
        self._record_checkpoint(last_seq, rebuilt=True)
        return ProjectionReport(
            projection=self.NAME,
            site_id=self.site_id,
            entries_read=len(entries),
            rows_written=written,
            last_seq=last_seq,
            rebuilt=True,
        )

    def catch_up(self) -> ProjectionReport:
        """Project entries the checkpoint has not yet consumed.

        The fold needs the runs it is about to advance, and it reconstructs
        them from the chain rather than from the table: reading back a
        projection to extend it would make the projection an input to itself,
        and a single bad row would then propagate forever.
        """
        checkpoint = self.checkpoint()
        pending = list(self._ledger().stream(checkpoint + 1))
        if not pending:
            return ProjectionReport(self.NAME, self.site_id, 0, 0, checkpoint, rebuilt=False)

        entries = list(self._stream_all())
        runs = project(entries)
        touched = {entry.subject_id for entry in pending if entry.subject_type == "run"}
        written = self._write(run for run in runs.values() if run.id in touched)
        last_seq = entries[-1].seq
        self._record_checkpoint(last_seq, rebuilt=False)
        return ProjectionReport(
            projection=self.NAME,
            site_id=self.site_id,
            entries_read=len(pending),
            rows_written=written,
            last_seq=last_seq,
            rebuilt=False,
        )

    def _ledger(self) -> LedgerRepository:
        return LedgerRepository(self._connection, self._scope)

    def _stream_all(self) -> Iterator[LedgerEntry]:
        return self._ledger().stream(1)

    def _write(self, runs: Any) -> int:
        rows = as_rows(runs)
        if not rows:
            return 0
        self._connection.execute(
            text(
                "INSERT INTO run (id, site_id, name, spec_hash, kind, state, started_at, "
                " ended_at, scheduler_job_id, node, retry_count) "
                "VALUES (:id, :site_id, :name, :spec_hash, :kind, :state, :started_at, "
                " :ended_at, :scheduler_job_id, :node, :retry_count) "
                "ON CONFLICT (id) DO UPDATE SET "
                "state = EXCLUDED.state, started_at = EXCLUDED.started_at, "
                "ended_at = EXCLUDED.ended_at, scheduler_job_id = EXCLUDED.scheduler_job_id, "
                "node = EXCLUDED.node, retry_count = EXCLUDED.retry_count"
            ),
            [{**row, "id": _as_uuid(row["id"])} for row in rows],
        )
        return len(rows)

    def read(self) -> tuple[ProjectedRun, ...]:
        """Return the projected runs as the table currently holds them."""
        rows = self._connection.execute(
            text(
                "SELECT id, site_id, name, spec_hash, kind, state, started_at, ended_at, "
                "scheduler_job_id, node, retry_count FROM run "
                "WHERE site_id = :site_id ORDER BY id"
            ),
            {"site_id": self.site_id},
        ).all()
        return tuple(
            ProjectedRun(
                id=str(row.id),
                site_id=row.site_id,
                name=row.name,
                spec_hash=row.spec_hash,
                kind=row.kind,
                state=RunState(row.state),
                started_at=row.started_at,
                ended_at=row.ended_at,
                scheduler_job_id=row.scheduler_job_id,
                node=row.node,
                retry_count=row.retry_count,
            )
            for row in rows
        )
