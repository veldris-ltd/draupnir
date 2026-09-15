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
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from draupnir.core.domain import gate_results, releases, sources
from draupnir.core.domain.federation import ANCHOR_SUBMITTED
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

    def serialise(self) -> None:
        """Take this site's advisory write lock until the transaction ends.

        A chain is serial by construction: the next entry is at seq N+1. Two
        writers that both read N both compute N+1, and the unique constraint on
        `(site_id, seq)` refuses the second -- which is the right backstop and
        the wrong answer to two operators submitting at once. So contending
        writers queue here instead of racing there.

        `pg_advisory_xact_lock` rather than a table lock: it is released when
        the transaction ends, however it ends, and it blocks nothing except
        another writer to the same site. Readers are untouched.
        """
        self._connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:site_id))"),
            {"site_id": self.site_id},
        )

    def entries_matching(self, probe: Mapping[str, Any]) -> tuple[LedgerEntry, ...]:
        """Entries whose payload contains `probe`, oldest first.

        A JSONB containment match rather than a scan: `payload @> {...}` uses
        the default GIN operator class, so this stays affordable on a chain
        that grows without bound. Scoped like everything else -- a run at
        another site is not a duplicate of one here, because a site is a
        separate chain and a separate estate.
        """
        if not probe:
            msg = "an empty probe matches every entry; ask for something"
            raise UnscopedQueryError(msg)
        rows = self._connection.execute(
            text(
                f"SELECT {_LEDGER_COLUMNS} FROM ledger_entry "  # noqa: S608 -- literal columns
                "WHERE site_id = :site_id AND payload @> :probe ORDER BY seq"
            ),
            {"site_id": self.site_id, "probe": json.dumps(dict(probe), sort_keys=True)},
        ).all()
        return tuple(_entry(row) for row in rows)

    def entries_of_type(self, subject_type: str) -> tuple[LedgerEntry, ...]:
        """Every entry about subjects of one kind, oldest first. RF-10.

        By subject type rather than by transition, because `(subject_type,
        subject_id)` is the index this table carries and a transition predicate
        would be a sequential scan of a chain AC-N5 sizes at a hundred
        thousand entries. Scoped like everything else.
        """
        if not subject_type:
            msg = "an empty subject type matches every entry; ask for something"
            raise UnscopedQueryError(msg)
        rows = self._connection.execute(
            text(
                f"SELECT {_LEDGER_COLUMNS} FROM ledger_entry "  # noqa: S608 -- literal columns
                "WHERE site_id = :site_id AND subject_type = :subject_type ORDER BY seq"
            ),
            {"site_id": self.site_id, "subject_type": subject_type},
        ).all()
        return tuple(_entry(row) for row in rows)

    def entries_for_subject(self, subject_id: str) -> tuple[LedgerEntry, ...]:
        """Every entry about one subject, oldest first.

        The subject's whole history, which is what an audit view of one run
        shows and what the sole approver check reads to find who submitted it.
        """
        rows = self._connection.execute(
            text(
                f"SELECT {_LEDGER_COLUMNS} FROM ledger_entry "  # noqa: S608 -- literal columns
                "WHERE site_id = :site_id AND subject_id = :subject_id ORDER BY seq"
            ),
            {"site_id": self.site_id, "subject_id": subject_id},
        ).all()
        return tuple(_entry(row) for row in rows)

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


def _concerns_releases(entry: LedgerEntry) -> bool:
    """Whether an entry can change an artefact, approval or release row."""
    payload = entry.payload if isinstance(entry.payload, Mapping) else {}
    return (
        isinstance(payload.get("artefacts"), list)
        or entry.transition in releases.DECISIONS
        or (
            entry.subject_type == releases.RELEASE_SUBJECT
            and entry.transition == releases.PUBLISHED
        )
        or entry.transition == ANCHOR_SUBMITTED
    )


class ReleaseProjection(ScopedRepository):
    """Maintains `artefact`, `approval` and `release` from the chain. RF-33.

    The seed wrote these three tables and nothing else did, so a publication on
    an estate left no release to read, no approval on its lineage and no
    document to download. They are projections now, exactly as `run` is: folded
    by `releases.fold`, which never reads them, and advanced on every append.

    `approval` and `release` carry no site of their own, so a rebuild clears
    them through what they belong to -- the site's runs and the site's
    artefacts -- rather than through a column they do not have.
    """

    NAME = "release"

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
        """Discard the three tables for this site and fold the chain from sequence 1."""
        entries = list(self._ledger().stream(1))
        self._connection.execute(
            text(
                "DELETE FROM release WHERE artefact_id IN "
                "(SELECT id FROM artefact WHERE site_id = :site_id)"
            ),
            {"site_id": self.site_id},
        )
        self._connection.execute(
            text(
                "DELETE FROM approval WHERE subject_id IN "
                "(SELECT id FROM run WHERE site_id = :site_id)"
            ),
            {"site_id": self.site_id},
        )
        self._connection.execute(
            text("DELETE FROM artefact WHERE site_id = :site_id"), {"site_id": self.site_id}
        )
        written = self._write(releases.fold(entries))
        last_seq = entries[-1].seq if entries else 0
        self._record_checkpoint(last_seq, rebuilt=True)
        return ProjectionReport(self.NAME, self.site_id, len(entries), written, last_seq, True)

    def catch_up(self) -> ProjectionReport:
        """Fold the chain again when an entry since the checkpoint could change a row.

        The whole chain, as `RunProjection.catch_up` folds it, because a
        publication binds to an approval and an artefact recorded long before it.
        Most appends concern none of the three tables, and those only move the
        checkpoint.
        """
        checkpoint = self.checkpoint()
        pending = list(self._ledger().stream(checkpoint + 1))
        if not pending:
            return ProjectionReport(self.NAME, self.site_id, 0, 0, checkpoint, False)
        last_seq = pending[-1].seq
        written = 0
        if any(_concerns_releases(entry) for entry in pending):
            written = self._write(releases.fold(self._ledger().stream(1)))
        self._record_checkpoint(last_seq, rebuilt=False)
        return ProjectionReport(self.NAME, self.site_id, len(pending), written, last_seq, False)

    def _ledger(self) -> LedgerRepository:
        return LedgerRepository(self._connection, self._scope)

    def _write(self, projected: releases.Projected) -> int:
        """Upsert every folded row, artefacts first because releases reference them."""
        stored: dict[str, UUID] = {}
        for artefact in projected.artefacts:
            stored[artefact.uri] = self._connection.execute(
                text(
                    "INSERT INTO artefact (id, site_id, locality, kind, uri, sha256_manifest, "
                    " size, created_from_run, immutable_at) "
                    "VALUES (:id, :site_id, :locality, CAST(:kind AS artefact_kind), :uri, "
                    " :sha256, :size, (SELECT id FROM run WHERE id = :run), :immutable_at) "
                    # An address is unique, and the identifier is derived from it,
                    # so a second record of one artefact updates the same row.
                    "ON CONFLICT (uri) DO UPDATE SET kind = EXCLUDED.kind, "
                    "sha256_manifest = EXCLUDED.sha256_manifest, size = EXCLUDED.size, "
                    "created_from_run = EXCLUDED.created_from_run, "
                    "immutable_at = EXCLUDED.immutable_at "
                    "RETURNING id"
                ),
                {
                    "id": artefact.id,
                    "site_id": artefact.site_id,
                    "locality": [artefact.site_id],
                    "kind": artefact.kind,
                    "uri": artefact.uri,
                    "sha256": artefact.sha256,
                    "size": artefact.size,
                    "run": artefact.created_from_run,
                    "immutable_at": artefact.immutable_at,
                },
            ).scalar_one()

        if projected.approvals:
            self._connection.execute(
                text(
                    "INSERT INTO approval (id, subject_id, approver, decision, reason, signature, "
                    " sole_approver_exception, decided_at) "
                    "VALUES (:id, :subject_id, :approver, :decision, :reason, :signature, "
                    " :sole_approver_exception, :decided_at) "
                    "ON CONFLICT (id) DO UPDATE SET approver = EXCLUDED.approver, "
                    "decision = EXCLUDED.decision, reason = EXCLUDED.reason, "
                    "signature = EXCLUDED.signature, "
                    "sole_approver_exception = EXCLUDED.sole_approver_exception, "
                    "decided_at = EXCLUDED.decided_at"
                ),
                [
                    {
                        "id": approval.id,
                        "subject_id": approval.subject_id,
                        "approver": approval.approver,
                        "decision": approval.decision,
                        "reason": approval.reason,
                        "signature": approval.signature,
                        "sole_approver_exception": approval.sole_approver_exception,
                        "decided_at": approval.decided_at,
                    }
                    for approval in projected.approvals
                ],
            )

        bound = [release for release in projected.releases if release.artefact_uri in stored]
        if bound:
            self._connection.execute(
                text(
                    "INSERT INTO release (id, artefact_id, approval_id, model_card_uri, sbom_uri, "
                    " lineage_uri, training_summary_uri, copyright_policy_uri, signature, "
                    " anchored_at, published_at, licence_policy_version) "
                    "VALUES (:id, :artefact_id, :approval_id, :model_card, :sbom, :lineage, "
                    " :training_summary, :copyright_policy, :signature, :anchored_at, "
                    " :published_at, :licence_policy_version) "
                    "ON CONFLICT (id) DO UPDATE SET artefact_id = EXCLUDED.artefact_id, "
                    "approval_id = EXCLUDED.approval_id, signature = EXCLUDED.signature, "
                    "anchored_at = EXCLUDED.anchored_at, published_at = EXCLUDED.published_at, "
                    "licence_policy_version = EXCLUDED.licence_policy_version"
                ),
                [
                    {
                        "id": release.id,
                        "artefact_id": stored[release.artefact_uri],
                        "approval_id": release.approval_id,
                        "model_card": release.documents["model-card"],
                        "sbom": release.documents["sbom"],
                        "lineage": release.documents["lineage"],
                        "training_summary": release.documents["training-summary"],
                        "copyright_policy": release.documents["copyright-policy"],
                        "signature": release.signature,
                        "anchored_at": release.anchored_at,
                        "published_at": release.published_at,
                        "licence_policy_version": release.licence_policy_version,
                    }
                    for release in bound
                ],
            )
        return len(stored) + len(projected.approvals) + len(bound)


class GateResultProjection(ScopedRepository):
    """Maintains `gate_result` from the chain. RF-41.

    The seed wrote this table and nothing else did, so an estate's approval
    queue, model detail and `/metrics` read an empty one while the worker
    recorded every outcome in the chain. Folded by `gate_results.fold`, which
    never reads it, and advanced on every append beside the releases.

    `gate_result` carries no site of its own -- it belongs to a run -- so a
    rebuild clears it through the site's runs.
    """

    NAME = "gate_result"

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
        """Discard this site's gate results and fold the chain from sequence 1."""
        entries = list(self._ledger().stream(1))
        self._connection.execute(
            text(
                "DELETE FROM gate_result WHERE run_id IN "
                "(SELECT id FROM run WHERE site_id = :site_id)"
            ),
            {"site_id": self.site_id},
        )
        written = self._write(gate_results.fold(entries))
        last_seq = entries[-1].seq if entries else 0
        self._record_checkpoint(last_seq, rebuilt=True)
        return ProjectionReport(self.NAME, self.site_id, len(entries), written, last_seq, True)

    def catch_up(self) -> ProjectionReport:
        """Fold the chain again when an entry since the checkpoint recorded a gate outcome."""
        checkpoint = self.checkpoint()
        pending = list(self._ledger().stream(checkpoint + 1))
        if not pending:
            return ProjectionReport(self.NAME, self.site_id, 0, 0, checkpoint, False)
        last_seq = pending[-1].seq
        written = 0
        if any(gate_results.concerns(entry) for entry in pending):
            written = self._write(gate_results.fold(self._ledger().stream(1)))
        self._record_checkpoint(last_seq, rebuilt=False)
        return ProjectionReport(self.NAME, self.site_id, len(pending), written, last_seq, False)

    def _ledger(self) -> LedgerRepository:
        return LedgerRepository(self._connection, self._scope)

    def _write(self, rows: Sequence[gate_results.ProjectedGateResult]) -> int:
        """Upsert every folded row whose run is projected, keyed as the table is."""
        if not rows:
            return 0
        self._connection.execute(
            text(
                "INSERT INTO gate_result (id, run_id, gate, suite_version, value, "
                " baseline_value, margin, passed, evaluated_at) "
                "SELECT :id, :run_id, :gate, :suite_version, :value, :baseline_value, "
                " :margin, :passed, :evaluated_at "
                # A row names the run it measured, and the run registry is
                # advanced first; an outcome for a run it does not hold is not
                # written rather than refused, so one entry cannot stop the rest.
                "WHERE EXISTS (SELECT 1 FROM run WHERE id = :run_id) "
                "ON CONFLICT (run_id, gate, suite_version) DO UPDATE SET "
                "value = EXCLUDED.value, baseline_value = EXCLUDED.baseline_value, "
                "margin = EXCLUDED.margin, passed = EXCLUDED.passed, "
                "evaluated_at = EXCLUDED.evaluated_at"
            ),
            [
                {
                    "id": row.id,
                    "run_id": row.run_id,
                    "gate": row.gate,
                    "suite_version": row.suite_version,
                    "value": row.value,
                    "baseline_value": row.baseline_value,
                    "margin": row.margin,
                    "passed": row.passed,
                    "evaluated_at": row.evaluated_at,
                }
                for row in rows
            ],
        )
        return len(rows)


class SourceProjection(ScopedRepository):
    """Maintains the licence register, `source`, from the chain. RF-42.

    `registerSource` recorded a `source` entry and wrote no row, and the seed
    was the only writer, so a source registered on an estate appeared in
    neither the register nor a release's lineage. Folded by `sources.fold`,
    which never reads the table, and advanced on every append beside the other
    projections.

    `source` has no row level security, so a rebuild names its site in every
    statement: migration 0007 records which site's chain registered each row.
    """

    NAME = "source"

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
        """Discard this site's register and fold the chain from sequence 1."""
        entries = list(self._ledger().stream(1))
        self._connection.execute(
            text("DELETE FROM source WHERE site_id = :site_id"), {"site_id": self.site_id}
        )
        written = self._write(sources.fold(entries))
        last_seq = entries[-1].seq if entries else 0
        self._record_checkpoint(last_seq, rebuilt=True)
        return ProjectionReport(self.NAME, self.site_id, len(entries), written, last_seq, True)

    def catch_up(self) -> ProjectionReport:
        """Fold the chain again when an entry since the checkpoint moved the register."""
        checkpoint = self.checkpoint()
        pending = list(self._ledger().stream(checkpoint + 1))
        if not pending:
            return ProjectionReport(self.NAME, self.site_id, 0, 0, checkpoint, False)
        last_seq = pending[-1].seq
        written = 0
        if any(sources.concerns(entry) for entry in pending):
            written = self._write(sources.fold(self._ledger().stream(1)))
        self._record_checkpoint(last_seq, rebuilt=False)
        return ProjectionReport(self.NAME, self.site_id, len(pending), written, last_seq, False)

    def _ledger(self) -> LedgerRepository:
        return LedgerRepository(self._connection, self._scope)

    def _write(self, rows: Sequence[sources.ProjectedSource]) -> int:
        """Upsert every folded source, keyed by its identifier."""
        if not rows:
            return 0
        self._connection.execute(
            text(
                "INSERT INTO source (id, site_id, jurisdiction, url, licence_spdx, "
                " attribution_required, retrieved_at, sha256, personal_data, dpia_ref, "
                " residency_constraint, state) "
                "VALUES (:id, :site_id, :jurisdiction, :url, :licence_spdx, "
                " :attribution_required, :retrieved_at, :sha256, :personal_data, :dpia_ref, "
                " :residency_constraint, :state) "
                "ON CONFLICT (id) DO UPDATE SET "
                "site_id = EXCLUDED.site_id, jurisdiction = EXCLUDED.jurisdiction, "
                "url = EXCLUDED.url, licence_spdx = EXCLUDED.licence_spdx, "
                "attribution_required = EXCLUDED.attribution_required, "
                "retrieved_at = EXCLUDED.retrieved_at, sha256 = EXCLUDED.sha256, "
                "personal_data = EXCLUDED.personal_data, dpia_ref = EXCLUDED.dpia_ref, "
                "residency_constraint = EXCLUDED.residency_constraint, state = EXCLUDED.state"
            ),
            [
                {
                    "id": row.id,
                    "site_id": row.site_id,
                    "jurisdiction": row.jurisdiction,
                    "url": row.url,
                    "licence_spdx": row.licence_spdx,
                    "attribution_required": row.attribution_required,
                    "retrieved_at": row.retrieved_at,
                    "sha256": row.sha256,
                    "personal_data": row.personal_data,
                    "dpia_ref": row.dpia_ref,
                    "residency_constraint": list(row.residency_constraint),
                    "state": str(row.state),
                }
                for row in rows
            ],
        )
        return len(rows)


class ChainProjections:
    """Every projection an append advances, behind the one port the orchestrator knows.

    The orchestrator asks one projection to catch up and reads runs from it.
    This keeps that shape while adding the release projection (RF-33), the gate
    results (RF-41) and the licence register (RF-42): runs first, because an
    artefact row and a gate result both name the run they belong to.
    """

    def __init__(
        self,
        runs: RunProjection,
        release_rows: ReleaseProjection,
        gate_rows: GateResultProjection,
        source_rows: SourceProjection,
    ) -> None:
        """Hold the projections, in the order they are advanced."""
        self._runs = runs
        self._releases = release_rows
        self._gates = gate_rows
        self._sources = source_rows

    def catch_up(
        self,
    ) -> tuple[ProjectionReport, ProjectionReport, ProjectionReport, ProjectionReport]:
        """Advance the run registry, the releases, the gate results, then the register."""
        return (
            self._runs.catch_up(),
            self._releases.catch_up(),
            self._gates.catch_up(),
            self._sources.catch_up(),
        )

    def read(self) -> tuple[ProjectedRun, ...]:
        """The projected runs, which is all the orchestrator reads."""
        return self._runs.read()


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
        because the fold is pure and every folded run is written in full.

        A run the chain still holds is rewritten in place rather than deleted
        and inserted: its identity is its specification, so the row is the same
        row, and the gate results that name it (RF-41) stay valid. Only a run
        the chain no longer holds is removed, with the gate results that named it.
        """
        entries = list(self._stream_all())
        runs = project(entries)
        kept = [_as_uuid(run_id) for run_id in runs]
        stale = (
            "SELECT id FROM run WHERE site_id = :s AND NOT (id = ANY(:kept))"
            if kept
            else "SELECT id FROM run WHERE site_id = :s"
        )
        parameters: dict[str, Any] = {"s": self.site_id, "kept": kept}
        self._connection.execute(
            text(f"DELETE FROM gate_result WHERE run_id IN ({stale})"),  # noqa: S608 -- fixed text
            parameters,
        )
        self._connection.execute(
            text(f"DELETE FROM run WHERE id IN ({stale})"),  # noqa: S608 -- fixed text
            parameters,
        )
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
