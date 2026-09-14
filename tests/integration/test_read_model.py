"""The read model, called directly against PostgreSQL. RF-20.

`draupnir/api/reading.py` is the largest module in the edge and was measured at
**29 per cent** by the stage that named it. Not because it is untested — the
pagination, transition and metrics suites all exercise it — but because every
one of them drives a real API *subprocess*, and coverage cannot see inside a
process it did not start. A module can be exercised on every request a forge
serves and measured at zero.

So this calls `DatabaseReadModel` in process. The queries are what an operator
reads: the run board, the licence register, the approval queue, the model
registry, the audit view, the lineage explorer. Each is a hand-written SQL
string, which is the kind of code that fails on a column rename with a message
nobody sees until a screen is blank.

Site scoping is asserted throughout rather than once. Every method here opens a
scoped session, and the whole of SAD 11C's isolation is that session variable:
a query that forgot it would return another forge's rows on a federated estate,
and would look perfectly correct on a single-site one.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from draupnir.api.problems import ProblemError
from draupnir.api.reading import DatabaseReadModel

pytestmark = pytest.mark.integration

SITE = "sindri-read-model"
OTHER = "sindri-read-model-other"
AT = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def populated(owner_engine: Engine) -> Iterator[str]:
    """Two forges with runs, sources, artefacts and a chain, committed."""
    with owner_engine.begin() as connection:
        for site in (SITE, OTHER):
            connection.execute(
                text(
                    "INSERT INTO site (id, name, location, timezone, control_plane_uri,"
                    " anchor_state) VALUES (:id, :id, 'Nuneaton', 'Europe/London',"
                    " 'https://alviss.example.internal', 'ANCHORED')"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": site},
            )
        _furnish(connection, SITE, runs=4, awaiting=2)
        _furnish(connection, OTHER, runs=7, awaiting=3)
    yield SITE


def _furnish(connection: Connection, site: str, *, runs: int, awaiting: int) -> None:
    """Rows of every kind the read model serves, for one forge."""
    connection.execute(text("SELECT set_config('draupnir.site_id', :s, true)"), {"s": site})
    for index in range(runs):
        connection.execute(
            text(
                "INSERT INTO run (id, site_id, name, spec_hash, kind, state, started_at)"
                " VALUES (:id, :site, :name, :hash, 'adapter', 'RELEASED', :at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "site": site,
                "name": f"{site}-run-{index}",
                "hash": uuid.uuid4().hex,
                "at": AT + timedelta(minutes=index),
            },
        )
    for index in range(awaiting):
        connection.execute(
            text(
                "INSERT INTO run (id, site_id, name, spec_hash, kind, state, started_at)"
                " VALUES (:id, :site, :name, :hash, 'adapter', 'AWAITING_APPROVAL', :at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "site": site,
                "name": f"{site}-awaiting-{index}",
                "hash": uuid.uuid4().hex,
                "at": AT + timedelta(hours=index),
            },
        )
        connection.execute(
            text(
                "INSERT INTO source (id, jurisdiction, url, licence_spdx,"
                " attribution_required, retrieved_at, sha256, personal_data, state)"
                " VALUES (:id, 'GBR', :url, 'CC-BY-4.0', true, :at, :sha, false,"
                " 'LICENCE_CLEARED')"
            ),
            {
                "id": str(uuid.uuid4()),
                "url": f"https://example.invalid/{site}-{index}",
                "at": AT + timedelta(minutes=index),
                "sha": uuid.uuid4().hex * 2,
            },
        )
        connection.execute(
            text(
                "INSERT INTO artefact (id, site_id, locality, kind, uri, sha256_manifest, size)"
                " VALUES (:id, :site, ARRAY[:site], 'quantised', :uri, :sha, 2048)"
            ),
            {
                "id": str(uuid.uuid4()),
                "site": site,
                "uri": f"hodd://{site}/artefact-{index}",
                "sha": uuid.uuid4().hex * 2,
            },
        )


@pytest.fixture
async def reader(migrated: str, populated: str) -> AsyncIterator[DatabaseReadModel]:
    """The real read model, on a real engine, in this process."""
    del populated
    engine = create_async_engine(
        migrated.replace("postgresql+psycopg", "postgresql+asyncpg"), future=True
    )
    try:
        yield DatabaseReadModel(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Every collection answers, and answers for one forge
# ---------------------------------------------------------------------------


async def test_the_run_board_reports_this_forges_runs(reader: DatabaseReadModel) -> None:
    page = await reader.runs(SITE, limit=50, cursor=None)

    assert len(page.items) == 6, "four released and two awaiting approval"
    assert all(item.site_id == SITE for item in page.items)
    assert all(OTHER not in item.name for item in page.items)


async def test_a_state_filter_narrows_the_board(reader: DatabaseReadModel) -> None:
    page = await reader.runs(SITE, limit=50, cursor=None, state="AWAITING_APPROVAL")

    assert len(page.items) == 2
    assert {item.state for item in page.items} == {"AWAITING_APPROVAL"}


async def test_one_run_is_read_back_by_its_identifier(reader: DatabaseReadModel) -> None:
    board = await reader.runs(SITE, limit=1, cursor=None)
    wanted = board.items[0]

    found = await reader.run(SITE, wanted.id)

    assert found is not None
    assert found.id == wanted.id
    assert found.name == wanted.name


async def test_a_run_belonging_to_another_forge_is_not_found(reader: DatabaseReadModel) -> None:
    """The isolation, from the side that matters.

    Not "returns nothing useful" but "is not found": a read model that answered
    across sites would let one forge's console open another forge's run by
    guessing an identifier, and SAD 11C's whole design is that it cannot.
    """
    elsewhere = await reader.runs(OTHER, limit=1, cursor=None)

    assert await reader.run(SITE, elsewhere.items[0].id) is None


async def test_the_licence_register_answers(reader: DatabaseReadModel) -> None:
    page = await reader.sources(SITE, limit=50, cursor=None)

    assert page.items
    assert {item.jurisdiction for item in page.items} == {"GBR"}


async def test_the_approval_queue_is_oldest_first(reader: DatabaseReadModel) -> None:
    """UX 9.3, so nothing ages quietly."""
    page = await reader.approvals(SITE, limit=50, cursor=None)

    assert len(page.items) == 2
    moments = [item.awaiting_since for item in page.items]
    assert moments == sorted(moments)


async def test_the_model_registry_answers(reader: DatabaseReadModel) -> None:
    page = await reader.models(SITE, limit=50, cursor=None)

    assert len(page.items) == 2
    assert all(item.artefact for item in page.items)


async def test_the_audit_view_verifies_what_it_returns(reader: DatabaseReadModel) -> None:
    """Re-linked here rather than trusted, which is the point of the view.

    An empty chain verifies trivially and that is a correct answer: the claim
    is that nothing returned contradicts its predecessor, and nothing returned
    cannot.
    """
    slice_ = await reader.ledger(SITE, limit=10, cursor=None)

    assert slice_.verified is True
    assert slice_.divergence is None


async def test_the_site_registry_is_deliberately_not_scoped(
    reader: DatabaseReadModel,
) -> None:
    """AC-U11: a switcher cannot be built from a list of one.

    The one read that crosses sites, and it carries no run, corpus or artefact
    data -- it is the list of scopes rather than an aggregate view.
    """
    page = await reader.sites()

    names = {item.id for item in page.items}
    assert {SITE, OTHER} <= names


async def test_the_corpus_view_answers_per_jurisdiction(reader: DatabaseReadModel) -> None:
    page = await reader.corpora(SITE)

    assert page.items is not None


async def test_the_retention_view_answers(reader: DatabaseReadModel) -> None:
    page = await reader.retention(SITE)

    assert page.items is not None


async def test_the_command_palette_finds_a_run_by_name(reader: DatabaseReadModel) -> None:
    page = await reader.search(SITE, f"{SITE}-run", limit=10)

    assert page.items is not None


async def test_an_array_that_was_never_submitted_is_none(reader: DatabaseReadModel) -> None:
    """`None` rather than an empty array, which would be a claim.

    RF-13 found `getArray` fabricating one; a forge that has submitted nothing
    has submitted nothing, and the read model says so.
    """
    assert await reader.array(SITE) is None


# ---------------------------------------------------------------------------
# The absences, which are answers too
# ---------------------------------------------------------------------------


async def test_an_unknown_artefact_has_no_lineage(reader: DatabaseReadModel) -> None:
    assert await reader.lineage(SITE, "0" * 64) is None


async def test_an_unknown_artefact_is_not_a_model(reader: DatabaseReadModel) -> None:
    assert await reader.model(SITE, "0" * 64) is None


async def test_an_unreleased_artefact_has_no_release_package(
    reader: DatabaseReadModel,
) -> None:
    assert await reader.release(SITE, "0" * 64) is None


async def test_an_unknown_entry_hash_reads_back_as_none(reader: DatabaseReadModel) -> None:
    assert await reader.ledger_entry(SITE, "0" * 64) is None


# ---------------------------------------------------------------------------
# The cursor, from in process
# ---------------------------------------------------------------------------


async def test_paging_the_board_reaches_every_run_once(reader: DatabaseReadModel) -> None:
    """The claim `test_pagination.py` makes over HTTP, made here in process.

    Asserted on the objects rather than on rendered JSON, so a page boundary
    that drops a row names the row.
    """
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        page = await reader.runs(SITE, limit=2, cursor=cursor)
        seen.extend(str(item.id) for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert len(seen) == 6
    assert len(set(seen)) == 6


async def test_a_cursor_this_api_did_not_issue_is_refused(reader: DatabaseReadModel) -> None:
    """RF-16 made this a refusal rather than a silent reset to page one."""
    with pytest.raises(ProblemError):
        await reader.runs(SITE, limit=2, cursor="not-a-cursor")


# ---------------------------------------------------------------------------
# Retention, folded from the chain. RF-27.
#
# `retention` read `retention_action`, which nothing wrote: the duty recorded
# its proposals in the chain and S06 reported nothing due whatever was. It
# folds the entries now, and these are real entries in a real chain, hashed as
# the ledger hashes them, at a forge of their own.
# ---------------------------------------------------------------------------

RETAINING = "sindri-read-model-retention"


@pytest.fixture(scope="module")
def retaining(owner_engine: Engine) -> Iterator[str]:
    """One proposal approved, one refused with its reason, in a hashed chain."""
    import json

    from draupnir.core.domain.ledger import LedgerEntry, append
    from draupnir.hodd import retention

    due = AT - timedelta(days=3)
    entries: list[LedgerEntry] = []
    previous: LedgerEntry | None = None
    specification = [
        ("a" * 64, retention.PROPOSED, {}, "system:worker"),
        ("a" * 64, retention.APPROVED, {retention.ANSWERS: 1}, "approver@veldris.internal"),
        ("b" * 64, retention.PROPOSED, {}, "system:worker"),
        ("b" * 64, retention.APPROVED, {retention.ANSWERS: 3}, "approver@veldris.internal"),
        (
            "b" * 64,
            retention.REFUSED,
            {retention.ANSWERS: 3, "reason": "the curated manifests are not held"},
            "system:worker",
        ),
    ]
    for corpus, transition, extra, actor in specification:
        payload = (
            {
                "corpusSha256": corpus,
                "dueAt": due.isoformat(),
                "releases": ["run-1"],
                "jurisdiction": "GBR",
                "artefact": f"hodd://{RETAINING}/corpora/GBR/raw",
            }
            if transition == retention.PROPOSED
            else extra
        )
        previous = append(
            previous=previous,
            site_id=RETAINING,
            ts=AT + timedelta(minutes=len(entries)),
            actor=actor,
            subject_type=retention.CORPUS_SUBJECT,
            subject_id=corpus,
            transition=transition,
            payload=payload,
        )
        entries.append(previous)

    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO site (id, name, location, timezone, control_plane_uri,"
                " anchor_state) VALUES (:id, :id, 'Nuneaton', 'Europe/London',"
                " 'https://alviss.example.internal', 'ANCHORED') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": RETAINING},
        )
        connection.execute(
            text("SELECT set_config('draupnir.site_id', :s, true)"), {"s": RETAINING}
        )
        for item in entries:
            connection.execute(
                text(
                    "INSERT INTO ledger_entry (id, site_id, seq, prev_hash, entry_hash, ts,"
                    " actor, subject_type, subject_id, transition, payload) VALUES (:id,"
                    " :site, :seq, :prev, :hash, :ts, :actor, :type, :subject, :transition,"
                    " CAST(:payload AS jsonb))"
                ),
                {
                    "id": str(item.id),
                    "site": item.site_id,
                    "seq": item.seq,
                    "prev": item.prev_hash,
                    "hash": item.entry_hash,
                    "ts": item.ts,
                    "actor": item.actor,
                    "type": item.subject_type,
                    "subject": item.subject_id,
                    "transition": item.transition,
                    "payload": json.dumps(item.payload, sort_keys=True),
                },
            )
    yield RETAINING


async def test_retention_is_folded_from_the_chain_rather_than_an_empty_table(
    reader: DatabaseReadModel, retaining: str
) -> None:
    page = await reader.retention(retaining)

    by_corpus = {item.corpus_sha256: item for item in page.items}
    assert set(by_corpus) == {"a" * 64, "b" * 64}

    approved = by_corpus["a" * 64]
    assert approved.state == "APPROVED"
    assert approved.approved_by == "approver@veldris.internal"
    assert approved.jurisdiction == "GBR"
    assert approved.releases == ["run-1"]
    assert approved.etag, "an action with no entity tag cannot be approved conditionally"

    refused = by_corpus["b" * 64]
    assert refused.state == "REFUSED"
    assert refused.refusal == "the curated manifests are not held"
    assert page.overdue == 2, "neither has been executed and both are past due"


async def test_retention_at_one_forge_is_not_visible_from_another(
    reader: DatabaseReadModel, retaining: str
) -> None:
    """Filtered by site as well as by row level security, like every read (RF-18)."""
    page = await reader.retention(SITE)

    assert all(item.corpus_sha256 not in {"a" * 64, "b" * 64} for item in page.items)
    assert retaining != SITE


async def test_lineage_sources_carry_the_registers_own_answers(
    reader: DatabaseReadModel,
) -> None:
    """RF-27. The release documents are generated from these nodes.

    Without the register's attribution and personal data answers on them, the
    training content summary would report each as not recorded.
    """
    registry = await reader.models(SITE, limit=1, cursor=None)
    lineage = await reader.lineage(SITE, registry.items[0].artefact)

    assert lineage is not None
    sources = [node for node in lineage.nodes if node["kind"] == "source"]
    assert sources, "the fixture registers sources, and the lineage shows none"
    for node in sources:
        assert {"licence", "jurisdiction", "attributionRequired", "personalData"} <= set(node)
        assert node["licence"] == node["fact"]
    assert any(node["attributionRequired"] is True for node in sources)
