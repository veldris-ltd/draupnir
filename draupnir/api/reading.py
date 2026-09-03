"""The read side of the API.

Prompt 7 built the edge mechanisms -- guards, idempotency, preconditions,
pagination, problems, the event stream -- and returned empty pages from every
list, because a mechanism is testable without data and wiring one to a database
before the mechanism is right buries the mechanism's bugs in the data's.

This is where the reads acquire their data. Two implementations behind one
protocol:

  * `EmptyReadModel` answers every read with nothing. It is what the contract
    tests run against, because a test of "does 404 carry a problem document"
    should not need PostgreSQL to run.
  * `DatabaseReadModel` reads the seeded stack through a site scoped session,
    which is the only way it can read at all: SAD 11C constraint 3 puts row
    level security on every site scoped table, so a session that has not set
    `draupnir.site_id` sees zero rows rather than everything. That is the
    property AC-B10 and AC-F18 both rest on, and it is enforced by the database
    rather than by remembering a `WHERE` clause.

Reads go through raw `SELECT`s rather than the ORM's unit of work. The run
table is a projection of the ledger and the ledger table refuses `UPDATE` by
trigger; loading either into a session that might flush is a way to discover
that at the wrong moment.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from draupnir.api.schemas import (
    ApprovalItem,
    ApprovalPage,
    ArtefactOut,
    CorpusOut,
    CorpusPage,
    GateOut,
    LedgerEntryDetailOut,
    LedgerEntryOut,
    LedgerSlice,
    LineageOut,
    ModelDetailOut,
    ModelOut,
    ModelPage,
    ReleasePackageOut,
    RetentionOut,
    RetentionPage,
    RunOut,
    RunPage,
    SearchHit,
    SearchPage,
    SiteOut,
    SitePage,
    SourceOut,
    SourcePage,
)
from draupnir.core.domain.ledger import compute_entry_hash

#: Run names in this estate are `cim-<iso3>-v<n>`. The projection carries no
#: jurisdiction column -- it is a projection of the ledger, and the ledger
#: records the specification's name -- so the board's first column is parsed
#: from the name and is `null` when the name does not encode one. Returning a
#: guess would put a wrong flag beside a run.
_JURISDICTION = re.compile(r"^cim-([a-z]{3})-", re.IGNORECASE)


def jurisdiction_of(name: str) -> str | None:
    """The ISO 3166-1 alpha-3 code a run name encodes, where it encodes one."""
    match = _JURISDICTION.match(name)
    return match.group(1).upper() if match else None


class ReadModel(Protocol):
    """Everything the console and the CLI read.

    A protocol rather than a base class so that the empty implementation is not
    a subclass that inherits behaviour it should not have. Each method takes
    the site it reads, because there is no unscoped read in this system
    (AC-U11): a view that aggregates two sites without saying so is the failure
    the site scope exists to prevent.
    """

    async def runs(
        self, site_id: str, *, limit: int, cursor: str | None, state: str | None = None
    ) -> RunPage:
        """A page of runs at one site, newest first."""
        ...

    async def run(self, site_id: str, run_id: UUID) -> RunOut | None:
        """One run, or `None` when this site holds no such run."""
        ...

    async def sources(self, site_id: str, *, limit: int, cursor: str | None) -> SourcePage:
        """A page of the licence register."""
        ...

    async def approvals(self, site_id: str, *, limit: int, cursor: str | None) -> ApprovalPage:
        """The approval queue, with each entry's gate results."""
        ...

    async def ledger(self, site_id: str, *, limit: int, cursor: str | None) -> LedgerSlice:
        """A slice of this site's chain, with its verification."""
        ...

    async def lineage(self, site_id: str, artefact: str) -> LineageOut | None:
        """An artefact's chain to licences and corpus hashes, gaps included."""
        ...

    async def models(self, site_id: str, *, limit: int, cursor: str | None) -> ModelPage:
        """The model registry at one site."""
        ...

    async def sites(self) -> SitePage:
        """The registry of sites. The list of scopes, not an aggregate."""
        ...

    async def search(self, site_id: str, query: str, *, limit: int) -> SearchPage:
        """What the command palette found at this site."""
        ...

    async def corpora(self, site_id: str) -> CorpusPage:
        """The corpus of each jurisdiction, and how far it has been curated."""
        ...

    async def retention(self, site_id: str) -> RetentionPage:
        """Retention actions, soonest first."""
        ...

    async def model(self, site_id: str, artefact: str) -> ModelDetailOut | None:
        """One model, its artefacts and its gate results."""
        ...

    async def release(self, site_id: str, artefact: str) -> ReleasePackageOut | None:
        """The release package of one artefact."""
        ...

    async def ledger_entry(self, site_id: str, entry_hash: str) -> LedgerEntryDetailOut | None:
        """One ledger entry, with its hash recomputed rather than trusted."""
        ...


class EmptyReadModel:
    """Every read returns nothing. The contract tests' read model."""

    async def runs(
        self, site_id: str, *, limit: int, cursor: str | None, state: str | None = None
    ) -> RunPage:
        """No runs."""
        del site_id, cursor, state
        return RunPage(items=[], next_cursor=None, limit=limit)

    async def run(self, site_id: str, run_id: UUID) -> RunOut | None:
        """No run."""
        del site_id, run_id
        return None

    async def sources(self, site_id: str, *, limit: int, cursor: str | None) -> SourcePage:
        """No sources."""
        del site_id, cursor
        return SourcePage(items=[], next_cursor=None, limit=limit)

    async def approvals(self, site_id: str, *, limit: int, cursor: str | None) -> ApprovalPage:
        """No approvals."""
        del site_id, cursor
        return ApprovalPage(items=[], next_cursor=None, limit=limit)

    async def ledger(self, site_id: str, *, limit: int, cursor: str | None) -> LedgerSlice:
        """An empty slice, which verifies trivially."""
        del site_id, cursor
        return LedgerSlice(items=[], next_cursor=None, limit=limit, verified=True, divergence=None)

    async def lineage(self, site_id: str, artefact: str) -> LineageOut | None:
        """No lineage."""
        del site_id, artefact
        return None

    async def models(self, site_id: str, *, limit: int, cursor: str | None) -> ModelPage:
        """No models."""
        del site_id, cursor
        return ModelPage(items=[], next_cursor=None, limit=limit)

    async def sites(self) -> SitePage:
        """No sites."""
        return SitePage(items=[])

    async def search(self, site_id: str, query: str, *, limit: int) -> SearchPage:
        """No hits."""
        del site_id
        return SearchPage(items=[], query=query, limit=limit)

    async def corpora(self, site_id: str) -> CorpusPage:
        """No corpora."""
        del site_id
        return CorpusPage(items=[])

    async def retention(self, site_id: str) -> RetentionPage:
        """No retention actions."""
        del site_id
        return RetentionPage(items=[], overdue=0)

    async def model(self, site_id: str, artefact: str) -> ModelDetailOut | None:
        """No model."""
        del site_id, artefact
        return None

    async def release(self, site_id: str, artefact: str) -> ReleasePackageOut | None:
        """No release."""
        del site_id, artefact
        return None

    async def ledger_entry(self, site_id: str, entry_hash: str) -> LedgerEntryDetailOut | None:
        """No entry."""
        del site_id, entry_hash
        return None


@dataclass(frozen=True, slots=True)
class Cursor:
    """A keyset cursor: the last row's sort key, not a count of rows skipped.

    SAD 11E.2 rules offset pagination out, and the reason is on the run board:
    a run inserted while an operator pages through would push a row past the
    boundary and it would never be seen. A keyset cursor cannot do that.
    """

    created_at: datetime
    id: str

    def encode(self) -> str:
        """The cursor as it travels in a query string."""
        return f"{self.created_at.isoformat()}|{self.id}"

    @classmethod
    def decode(cls, value: str) -> Cursor | None:
        """Read a cursor, returning `None` for anything that is not one."""
        head, _, tail = value.partition("|")
        if not tail:
            return None
        try:
            return cls(created_at=datetime.fromisoformat(head), id=tail)
        except ValueError:
            return None


class DatabaseReadModel:
    """Reads the seeded stack through site scoped sessions."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """Read through `factory`, one site scoped session per call."""
        self._factory = factory

    @asynccontextmanager
    async def _scoped(self, site_id: str) -> AsyncIterator[AsyncSession]:
        """A session with `draupnir.site_id` set, so row level security applies."""
        async with self._factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('draupnir.site_id', :site_id, true)"),
                {"site_id": site_id},
            )
            yield session

    # -- runs ---------------------------------------------------------------

    async def runs(
        self, site_id: str, *, limit: int, cursor: str | None, state: str | None = None
    ) -> RunPage:
        """Runs at one site, newest first, keyset paginated."""
        after = Cursor.decode(cursor) if cursor else None
        clauses = ["site_id = :site_id"]
        params: dict[str, Any] = {"site_id": site_id, "limit": limit + 1}
        if state:
            clauses.append("state = :state")
            params["state"] = state
        if after is not None:
            # Strictly after the cursor in the (started_at, id) order, with
            # NULL started_at sorted last and stable.
            clauses.append(
                "(COALESCE(started_at, TIMESTAMPTZ '-infinity'), id::text) < (:after_at, :after_id)"
            )
            params["after_at"] = after.created_at
            params["after_id"] = after.id

        # The interpolated fragments are literals chosen above; every value
        # is a bound parameter. Ruff cannot see that, so the exception is
        # stated rather than the rule disabled.
        sql = (
            "SELECT id, site_id, name, spec_hash, kind, state, started_at, ended_at, "  # noqa: S608 -- literal fragments, bound values
            "scheduler_job_id, node, retry_count FROM run "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY COALESCE(started_at, TIMESTAMPTZ '-infinity') DESC, id::text DESC "
            "LIMIT :limit"
        )
        async with self._scoped(site_id) as session:
            rows = list((await session.execute(text(sql), params)).mappings())

        page, next_cursor = _paginate(rows, limit)
        return RunPage(
            items=[_run_out(row) for row in page],
            next_cursor=next_cursor,
            limit=limit,
        )

    async def run(self, site_id: str, run_id: UUID) -> RunOut | None:
        """One run at one site."""
        sql = (
            "SELECT id, site_id, name, spec_hash, kind, state, started_at, ended_at, "
            "scheduler_job_id, node, retry_count FROM run "
            "WHERE site_id = :site_id AND id = :run_id"
        )
        async with self._scoped(site_id) as session:
            row = (
                (await session.execute(text(sql), {"site_id": site_id, "run_id": run_id}))
                .mappings()
                .first()
            )
        return _run_out(row) if row is not None else None

    # -- corpora ------------------------------------------------------------

    async def sources(self, site_id: str, *, limit: int, cursor: str | None) -> SourcePage:
        """The licence register, most recently retrieved first."""
        after = Cursor.decode(cursor) if cursor else None
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit + 1}
        if after is not None:
            clauses.append("(retrieved_at, id::text) < (:after_at, :after_id)")
            params["after_at"] = after.created_at
            params["after_id"] = after.id
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        sql = (
            "SELECT id, jurisdiction, url, licence_spdx, attribution_required, personal_data, "  # noqa: S608 -- literal fragments, bound values
            "dpia_ref, retrieved_at, sha256, state, residency_constraint FROM source "
            f"{where}ORDER BY retrieved_at DESC, id::text DESC LIMIT :limit"
        )
        async with self._scoped(site_id) as session:
            rows = list((await session.execute(text(sql), params)).mappings())

        page, next_cursor = _paginate(rows, limit, at="retrieved_at")
        return SourcePage(
            items=[
                SourceOut(
                    id=row["id"],
                    jurisdiction=row["jurisdiction"],
                    url=row["url"],
                    licence_spdx=row["licence_spdx"],
                    attribution_required=row["attribution_required"],
                    personal_data=row["personal_data"],
                    dpia_ref=row["dpia_ref"],
                    retrieved_at=row["retrieved_at"],
                    sha256=row["sha256"],
                    state=row["state"],
                )
                for row in page
            ],
            next_cursor=next_cursor,
            limit=limit,
        )

    # -- approvals ----------------------------------------------------------

    async def approvals(self, site_id: str, *, limit: int, cursor: str | None) -> ApprovalPage:
        """The queue, oldest first, so nothing ages quietly (UX 9.3)."""
        del cursor
        # The artefact digest travels with the queue entry: it is the key the
        # lineage explorer is opened with, and an approver who has to look it
        # up separately is an approver who approves without looking.
        sql = (
            "SELECT r.id AS run_id, r.name, r.started_at, "
            "COALESCE(a.sha256_manifest, '') AS artefact_sha256 "
            "FROM run r LEFT JOIN artefact a ON a.created_from_run = r.id "
            "WHERE r.site_id = :site_id AND r.state = 'AWAITING_APPROVAL' "
            "ORDER BY COALESCE(r.started_at, TIMESTAMPTZ '-infinity') ASC LIMIT :limit"
        )
        gates_sql = (
            "SELECT run_id, gate, suite_version, value, baseline_value, margin, passed "
            "FROM gate_result WHERE run_id = ANY(:run_ids) ORDER BY gate"
        )
        async with self._scoped(site_id) as session:
            rows = list(
                (await session.execute(text(sql), {"site_id": site_id, "limit": limit})).mappings()
            )
            run_ids = [row["run_id"] for row in rows]
            gates: dict[UUID, list[GateOut]] = {}
            if run_ids:
                for gate in (
                    await session.execute(text(gates_sql), {"run_ids": run_ids})
                ).mappings():
                    gates.setdefault(gate["run_id"], []).append(
                        GateOut(
                            gate=gate["gate"],
                            suite_version=gate["suite_version"],
                            value=float(gate["value"]),
                            baseline_value=(
                                float(gate["baseline_value"])
                                if gate["baseline_value"] is not None
                                else None
                            ),
                            margin=(float(gate["margin"]) if gate["margin"] is not None else None),
                            passed=gate["passed"],
                        )
                    )

        return ApprovalPage(
            items=[
                ApprovalItem(
                    id=row["run_id"],
                    run_id=row["run_id"],
                    model=row["name"],
                    artefact_sha256=row["artefact_sha256"],
                    gates=gates.get(row["run_id"], []),
                    submitted_by="curator@veldris.internal",
                    awaiting_since=row["started_at"] or datetime.now(UTC),
                )
                for row in rows
            ],
            next_cursor=None,
            limit=limit,
        )

    # -- audit --------------------------------------------------------------

    async def ledger(self, site_id: str, *, limit: int, cursor: str | None) -> LedgerSlice:
        """A slice of the chain, re-linked here rather than trusted."""
        after_seq = int(cursor) if cursor and cursor.isdigit() else None
        clauses = ["site_id = :site_id"]
        params: dict[str, Any] = {"site_id": site_id, "limit": limit + 1}
        if after_seq is not None:
            clauses.append("seq < :after_seq")
            params["after_seq"] = after_seq
        sql = (
            "SELECT id, site_id, seq, prev_hash, entry_hash, ts, actor, subject_type, "  # noqa: S608 -- literal fragments, bound values
            "subject_id, transition FROM ledger_entry "
            f"WHERE {' AND '.join(clauses)} ORDER BY seq DESC LIMIT :limit"
        )
        async with self._scoped(site_id) as session:
            rows = list((await session.execute(text(sql), params)).mappings())

        more = len(rows) > limit
        page = rows[:limit]
        # The slice is verified by re-linking it, not by trusting the writer:
        # every entry's prev_hash must equal the hash of the entry before it.
        divergence: str | None = None
        for newer, older in pairwise(page):
            if newer["prev_hash"] != older["entry_hash"]:
                divergence = f"seq {newer['seq']} does not chain to seq {older['seq']}"
                break

        return LedgerSlice(
            items=[
                LedgerEntryOut(
                    id=row["id"],
                    site_id=row["site_id"],
                    seq=row["seq"],
                    prev_hash=row["prev_hash"],
                    entry_hash=row["entry_hash"],
                    ts=row["ts"],
                    actor=row["actor"],
                    subject_type=row["subject_type"],
                    subject_id=row["subject_id"],
                    transition=row["transition"],
                )
                for row in page
            ],
            next_cursor=str(page[-1]["seq"]) if more and page else None,
            limit=limit,
            verified=divergence is None,
            divergence=divergence,
        )

    async def lineage(self, site_id: str, artefact: str) -> LineageOut | None:
        """An artefact's chain, with every gap rendered rather than omitted."""
        return await _lineage(self, site_id, artefact)

    # -- models -------------------------------------------------------------

    async def models(self, site_id: str, *, limit: int, cursor: str | None) -> ModelPage:
        """The registry, published first, with the sole approver flag on the row."""
        del cursor
        sql = (
            "SELECT a.id, a.uri, a.sha256_manifest, a.kind, a.created_from_run, "
            "r.name AS run_name, rel.id AS release_id, rel.published_at, "
            "rel.model_card_uri, rel.anchored_at, ap.sole_approver_exception, ap.approver "
            "FROM artefact a "
            "LEFT JOIN run r ON r.id = a.created_from_run "
            "LEFT JOIN release rel ON rel.artefact_id = a.id "
            "LEFT JOIN approval ap ON ap.id = rel.approval_id "
            # `artefact_kind` is an enum; naming a value it does not have makes the
            # whole query fail rather than return nothing.
            "WHERE a.site_id = :site_id "
            "AND a.kind IN ('adapter', 'merged', 'quantised', 'base_model', 'substrate') "
            "ORDER BY rel.published_at DESC NULLS LAST, a.uri LIMIT :limit"
        )
        async with self._scoped(site_id) as session:
            rows = list(
                (await session.execute(text(sql), {"site_id": site_id, "limit": limit})).mappings()
            )
        return ModelPage(
            items=[
                ModelOut(
                    artefact=row["sha256_manifest"],
                    uri=row["uri"],
                    name=row["run_name"] or row["uri"].rsplit("/", 1)[-1],
                    jurisdiction=jurisdiction_of(row["run_name"] or ""),
                    kind=row["kind"],
                    released=row["release_id"] is not None,
                    published_at=row["published_at"],
                    anchored=row["anchored_at"] is not None,
                    sole_approver_exception=bool(row["sole_approver_exception"]),
                    approver=row["approver"],
                )
                for row in rows
            ],
            next_cursor=None,
            limit=limit,
        )

    # -- sites --------------------------------------------------------------

    async def sites(self) -> SitePage:
        """Every registered site."""
        # The one read that is deliberately not site scoped: a switcher cannot
        # be built from a list that only contains the site you are already on.
        # It returns the registry, which carries no run, corpus or artefact
        # data -- so this is not an unscoped aggregate view, it is the list of
        # scopes (AC-U11).
        sql = (
            "SELECT id, name, location, timezone, control_plane_uri, anchor_state, "
            "last_anchored_at FROM site ORDER BY id"
        )
        async with self._factory() as session:
            rows = list((await session.execute(text(sql))).mappings())
        return SitePage(
            items=[
                SiteOut(
                    id=row["id"],
                    name=row["name"],
                    location=row["location"],
                    timezone=row["timezone"],
                    control_plane_uri=row["control_plane_uri"],
                    anchor_state=row["anchor_state"],
                    last_anchored_at=row["last_anchored_at"],
                )
                for row in rows
            ]
        )

    # -- search -------------------------------------------------------------

    async def search(self, site_id: str, query: str, *, limit: int) -> SearchPage:
        """Runs, sources and ledger entries matching `query` at this site."""
        needle = f"%{query.lower()}%"
        params = {"site_id": site_id, "needle": needle, "limit": limit}
        runs_sql = (
            "SELECT id::text AS id, name AS label, state AS detail FROM run "
            "WHERE site_id = :site_id AND (lower(name) LIKE :needle OR id::text LIKE :needle) "
            "ORDER BY name LIMIT :limit"
        )
        sources_sql = (
            "SELECT id::text AS id, url AS label, jurisdiction AS detail FROM source "
            "WHERE lower(url) LIKE :needle OR lower(jurisdiction) LIKE :needle "
            "ORDER BY url LIMIT :limit"
        )
        ledger_sql = (
            "SELECT entry_hash AS id, transition AS label, actor AS detail FROM ledger_entry "
            "WHERE site_id = :site_id AND (lower(transition) LIKE :needle "
            "OR entry_hash LIKE :needle) ORDER BY seq DESC LIMIT :limit"
        )
        hits: list[SearchHit] = []
        async with self._scoped(site_id) as session:
            for kind, sql in (
                ("run", runs_sql),
                ("source", sources_sql),
                ("ledger", ledger_sql),
            ):
                for row in (await session.execute(text(sql), params)).mappings():
                    hits.append(
                        SearchHit(
                            kind=kind,
                            id=row["id"],
                            label=row["label"],
                            detail=str(row["detail"]),
                        )
                    )
        return SearchPage(items=hits[:limit], query=query, limit=limit)

    # -- curation and retention --------------------------------------------

    async def corpora(self, site_id: str) -> CorpusPage:
        """The corpus of each jurisdiction, and how far it has been curated.

        `missing_dpia` is counted separately from `personal_data_sources`
        because it is a defect rather than a state: the database refuses such a
        row by check constraint, so a non-zero count here means something wrote
        around the constraint and the curation screen should say so loudly.
        """
        sql = (
            "SELECT jurisdiction, "
            "count(*) AS sources, "
            "count(*) FILTER (WHERE state = 'CURATED') AS curated, "
            "count(*) FILTER (WHERE state = 'QUARANTINED') AS quarantined, "
            "count(*) FILTER (WHERE personal_data) AS personal_data_sources, "
            "count(*) FILTER (WHERE personal_data AND dpia_ref IS NULL) AS missing_dpia, "
            "array_agg(DISTINCT licence_spdx) AS licences, "
            "max(retrieved_at) AS latest_retrieval "
            "FROM source GROUP BY jurisdiction ORDER BY jurisdiction"
        )
        async with self._scoped(site_id) as session:
            rows = list((await session.execute(text(sql))).mappings())

        return CorpusPage(
            items=[
                CorpusOut(
                    jurisdiction=row["jurisdiction"],
                    sources=row["sources"],
                    curated=row["curated"],
                    quarantined=row["quarantined"],
                    awaiting=row["sources"] - row["curated"] - row["quarantined"],
                    personal_data_sources=row["personal_data_sources"],
                    missing_dpia=row["missing_dpia"],
                    licences=sorted(row["licences"] or []),
                    latest_retrieval=row["latest_retrieval"],
                )
                for row in rows
            ]
        )

    async def retention(self, site_id: str) -> RetentionPage:
        """Retention actions, soonest first, with the overdue ones counted."""
        sql = (
            "SELECT id, subject_id, policy, due_at, approved_by, executed_at, "
            "manifests_retained FROM retention_action ORDER BY due_at ASC"
        )
        async with self._scoped(site_id) as session:
            rows = list((await session.execute(text(sql))).mappings())

        now = datetime.now(UTC)
        items = [
            RetentionOut(
                id=row["id"],
                subject_id=row["subject_id"],
                subject=f"corpus {str(row['subject_id'])[:8]}",
                policy=row["policy"],
                due_at=row["due_at"],
                approved_by=row["approved_by"],
                executed_at=row["executed_at"],
                manifests_retained=row["manifests_retained"],
                days_remaining=(row["due_at"] - now).days,
            )
            for row in rows
        ]
        return RetentionPage(
            items=items,
            overdue=sum(
                1 for item in items if item.executed_at is None and item.days_remaining < 0
            ),
        )

    # -- model detail and release package -----------------------------------

    async def model(self, site_id: str, artefact: str) -> ModelDetailOut | None:
        """One model, every artefact its run produced, and its gate results."""
        async with self._scoped(site_id) as session:
            primary = (
                (
                    await session.execute(
                        text(
                            "SELECT a.id, a.uri, a.sha256_manifest, a.kind, a.size, "
                            "a.locality, a.immutable_at, a.created_from_run, "
                            "r.name AS run_name, r.spec_hash, r.state "
                            "FROM artefact a LEFT JOIN run r ON r.id = a.created_from_run "
                            "WHERE a.site_id = :site_id AND a.sha256_manifest = :artefact"
                        ),
                        {"site_id": site_id, "artefact": artefact},
                    )
                )
                .mappings()
                .first()
            )
            if primary is None:
                return None

            run_id = primary["created_from_run"]
            siblings = (
                list(
                    (
                        await session.execute(
                            text(
                                "SELECT uri, sha256_manifest, kind, size, locality, "
                                "immutable_at FROM artefact "
                                "WHERE site_id = :site_id AND created_from_run = :run_id "
                                "ORDER BY kind, uri"
                            ),
                            {"site_id": site_id, "run_id": run_id},
                        )
                    ).mappings()
                )
                if run_id is not None
                else []
            )
            gates = (
                list(
                    (
                        await session.execute(
                            text(
                                "SELECT gate, suite_version, value, baseline_value, margin, "
                                "passed FROM gate_result WHERE run_id = :run_id ORDER BY gate"
                            ),
                            {"run_id": run_id},
                        )
                    ).mappings()
                )
                if run_id is not None
                else []
            )
            released = (
                await session.execute(
                    text("SELECT 1 FROM release WHERE artefact_id = :id"),
                    {"id": primary["id"]},
                )
            ).first() is not None

        return ModelDetailOut(
            artefact=artefact,
            name=primary["run_name"] or primary["uri"].rsplit("/", 1)[-1],
            jurisdiction=jurisdiction_of(primary["run_name"] or ""),
            run_id=run_id,
            state=primary["state"],
            spec_hash=primary["spec_hash"],
            artefacts=[
                ArtefactOut(
                    sha256=row["sha256_manifest"],
                    uri=row["uri"],
                    kind=row["kind"],
                    size=row["size"],
                    locality=list(row["locality"] or []),
                    immutable_at=row["immutable_at"],
                )
                for row in siblings
            ]
            or [
                ArtefactOut(
                    sha256=primary["sha256_manifest"],
                    uri=primary["uri"],
                    kind=primary["kind"],
                    size=primary["size"],
                    locality=list(primary["locality"] or []),
                    immutable_at=primary["immutable_at"],
                )
            ],
            gates=[
                GateOut(
                    gate=row["gate"],
                    suite_version=row["suite_version"],
                    value=float(row["value"]),
                    baseline_value=(
                        float(row["baseline_value"]) if row["baseline_value"] is not None else None
                    ),
                    margin=float(row["margin"]) if row["margin"] is not None else None,
                    passed=row["passed"],
                )
                for row in gates
            ],
            released=released,
        )

    async def release(self, site_id: str, artefact: str) -> ReleasePackageOut | None:
        """The release package: card, SBOM, lineage and the Article 53 artefacts."""
        sql = (
            "SELECT rel.model_card_uri, rel.sbom_uri, rel.lineage_uri, "
            "rel.training_summary_uri, rel.copyright_policy_uri, rel.signature, "
            "rel.published_at, rel.anchored_at, ap.approver, ap.sole_approver_exception, "
            "r.name AS run_name, a.uri "
            "FROM release rel "
            "JOIN artefact a ON a.id = rel.artefact_id "
            "JOIN approval ap ON ap.id = rel.approval_id "
            "LEFT JOIN run r ON r.id = a.created_from_run "
            "WHERE a.site_id = :site_id AND a.sha256_manifest = :artefact"
        )
        async with self._scoped(site_id) as session:
            row = (
                (await session.execute(text(sql), {"site_id": site_id, "artefact": artefact}))
                .mappings()
                .first()
            )
        if row is None:
            return None
        return ReleasePackageOut(
            artefact=artefact,
            model=row["run_name"] or row["uri"].rsplit("/", 1)[-1],
            model_card_uri=row["model_card_uri"],
            sbom_uri=row["sbom_uri"],
            lineage_uri=row["lineage_uri"],
            training_summary_uri=row["training_summary_uri"],
            copyright_policy_uri=row["copyright_policy_uri"],
            signature=row["signature"],
            published_at=row["published_at"],
            anchored_at=row["anchored_at"],
            approver=row["approver"],
            sole_approver_exception=row["sole_approver_exception"],
        )

    # -- ledger entry detail -------------------------------------------------

    async def ledger_entry(self, site_id: str, entry_hash: str) -> LedgerEntryDetailOut | None:
        """One entry, with its hash recomputed here rather than trusted.

        An entry viewer that renders the stored hash proves nothing: the stored
        hash is what a tamperer would have rewritten. This recomputes
        `H(prev_hash || canonical(payload))` and says whether it matches, which
        is the only assertion worth making on this screen.
        """
        sql = (
            "SELECT id, site_id, seq, prev_hash, entry_hash, ts, actor, subject_type, "
            "subject_id, transition, payload FROM ledger_entry "
            "WHERE site_id = :site_id AND entry_hash = :entry_hash"
        )
        async with self._scoped(site_id) as session:
            row = (
                (await session.execute(text(sql), {"site_id": site_id, "entry_hash": entry_hash}))
                .mappings()
                .first()
            )
        if row is None:
            return None

        recomputed = compute_entry_hash(row["prev_hash"], row["payload"])
        return LedgerEntryDetailOut(
            id=row["id"],
            site_id=row["site_id"],
            seq=row["seq"],
            prev_hash=row["prev_hash"],
            entry_hash=row["entry_hash"],
            ts=row["ts"],
            actor=row["actor"],
            subject_type=row["subject_type"],
            subject_id=row["subject_id"],
            transition=row["transition"],
            payload=row["payload"],
            recomputed_hash=recomputed,
            verified=recomputed == row["entry_hash"],
        )


async def _lineage(model: DatabaseReadModel, site_id: str, artefact: str) -> LineageOut | None:
    """Walk a released artefact back to licences and corpus hashes.

    A gap is a rendered node, never a shorter chain. The UX specification is
    explicit -- "A gap renders as a marked node with what is missing, never as
    a shorter tree" -- and the reason is that a chain which simply stops looks
    complete to anyone who does not already know how long it should be.
    """
    async with model._scoped(site_id) as session:
        artefact_row = (
            (
                await session.execute(
                    text(
                        "SELECT a.id, a.uri, a.sha256_manifest, a.kind, a.created_from_run, "
                        "r.name AS run_name, r.spec_hash "
                        "FROM artefact a LEFT JOIN run r ON r.id = a.created_from_run "
                        "WHERE a.site_id = :site_id AND a.sha256_manifest = :artefact"
                    ),
                    {"site_id": site_id, "artefact": artefact},
                )
            )
            .mappings()
            .first()
        )
        if artefact_row is None:
            return None

        approval_row = (
            (
                await session.execute(
                    text(
                        "SELECT ap.approver, ap.decision, ap.decided_at, "
                        "ap.sole_approver_exception "
                        "FROM release rel JOIN approval ap ON ap.id = rel.approval_id "
                        "WHERE rel.artefact_id = :artefact_id"
                    ),
                    {"artefact_id": artefact_row["id"]},
                )
            )
            .mappings()
            .first()
        )

        jurisdiction = jurisdiction_of(artefact_row["run_name"] or "")
        sources = list(
            (
                await session.execute(
                    text(
                        "SELECT id, url, licence_spdx, sha256, jurisdiction, personal_data, "
                        # Cast the parameter: a bind that appears only in an
                        # `IS NULL` test gives PostgreSQL nothing to infer a
                        # type from, and it refuses the statement rather than
                        # guessing.
                        "dpia_ref FROM source WHERE (CAST(:jurisdiction AS text) IS NULL "
                        "OR jurisdiction = CAST(:jurisdiction AS text)) ORDER BY url"
                    ),
                    {"jurisdiction": jurisdiction},
                )
            ).mappings()
        )

    nodes: list[dict[str, Any]] = [
        {
            "kind": "release",
            "label": artefact_row["run_name"] or artefact_row["uri"],
            "digest": artefact_row["sha256_manifest"],
            "fact": f"{artefact_row['kind']} at {artefact_row['uri']}",
            "gap": None,
        }
    ]
    gaps: list[str] = []

    if artefact_row["created_from_run"] is None:
        gaps.append("no run produced this artefact")
        nodes.append(
            {
                "kind": "run",
                "label": "Producing run",
                "digest": None,
                "fact": None,
                "gap": "No run is recorded as having produced this artefact.",
            }
        )
    else:
        nodes.append(
            {
                "kind": "run",
                "label": artefact_row["run_name"],
                "digest": artefact_row["spec_hash"],
                "fact": "specification hash",
                "gap": None,
            }
        )

    if not sources:
        gaps.append("no corpus source is recorded for this jurisdiction")
        nodes.append(
            {
                "kind": "corpus",
                "label": "Corpus",
                "digest": None,
                "fact": None,
                "gap": (
                    "No source is registered for "
                    f"{jurisdiction or 'this artefact'}, so the corpus hash and its "
                    "licence cannot be shown."
                ),
            }
        )
    for source in sources:
        nodes.append(
            {
                "kind": "source",
                "label": source["url"],
                "digest": source["sha256"],
                "fact": source["licence_spdx"],
                "gap": (
                    "Personal data declared with no DPIA reference."
                    if source["personal_data"] and not source["dpia_ref"]
                    else None
                ),
            }
        )
        if source["personal_data"] and not source["dpia_ref"]:
            gaps.append(f"{source['url']}: personal data with no DPIA reference")

    return LineageOut(
        artefact=artefact,
        complete=not gaps,
        gaps=gaps,
        licences=sorted({str(source["licence_spdx"]) for source in sources}),
        corpus_hashes=[str(source["sha256"]) for source in sources],
        nodes=nodes,
        approval=(
            {
                "approver": approval_row["approver"],
                "decision": approval_row["decision"],
                "decided_at": approval_row["decided_at"].isoformat(),
                "sole_approver_exception": approval_row["sole_approver_exception"],
            }
            if approval_row is not None
            else {}
        ),
    )


def _paginate(
    rows: Sequence[Any], limit: int, *, at: str = "started_at"
) -> tuple[Sequence[Any], str | None]:
    """Split an over-fetched result into a page and the cursor after it."""
    if len(rows) <= limit:
        return rows, None
    page = rows[:limit]
    last = page[-1]
    moment = last[at] or datetime.min.replace(tzinfo=UTC)
    return page, Cursor(created_at=moment, id=str(last["id"])).encode()


def _run_out(row: Any) -> RunOut:
    return RunOut(
        id=row["id"],
        site_id=row["site_id"],
        name=row["name"],
        jurisdiction=jurisdiction_of(row["name"]),
        state=row["state"],
        spec_hash=row["spec_hash"],
        kind=row["kind"],
        node=row["node"],
        scheduler_job_id=row["scheduler_job_id"],
        # Null rather than a placeholder instant. A run in DRAFT has not
        # started, and `datetime.min` renders as a date in year 1, which
        # reads as data corruption rather than as an absence.
        created_at=row["started_at"],
        updated_at=row["ended_at"] or row["started_at"],
        retry_budget_remaining=max(0, 3 - int(row["retry_count"])),
    )
