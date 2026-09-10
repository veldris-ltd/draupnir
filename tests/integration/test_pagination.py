"""Every cursor-declaring collection, paged to the end. RF-16.

`listGates` and `listModels` both opened with `del cursor` and both returned
`nextCursor: null` unconditionally, while advertising a `cursor` query
parameter in the OpenAPI document. A site with more approvals or models than
`limit` truncated silently: nothing in the response said so, and there was no
way to reach the rest. The approval queue is ordered oldest first precisely so
that nothing ages out of sight, so a page that never advanced hid the oldest
work behind the newest.

The collections are enumerated from `docs/api/openapi.json` rather than listed
here, which is the point of doing it this way: a sixth collection that declares
a cursor and does not honour it fails this file without anybody remembering to
add it. A convention followed query by query is a convention the next query
skips.

Against a real API process over HTTP, because the defect was in SQL. A read
model stubbed in the contract suite would page perfectly while the query it
stands for ignored its cursor -- which is exactly what was happening.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, text

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PORT = 8937
BASE = f"http://127.0.0.1:{PORT}"
#: Its own site. The ledger rows below are committed and `ledger_entry` refuses
#: DELETE and TRUNCATE by design (SAD 11C), so they stay -- and `sindri` is the
#: site `test_repositories` and `test_projection` build chains on from fixed
#: sequence numbers, which a stray committed entry would collide with.
SITE = "sindri-pagination"

#: Enough rows that `limit=2` needs three pages. Two would let a query that
#: returns everything on page one and a null cursor pass by accident.
ROWS = 5

EPOCH = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)


def _get(path: str) -> tuple[int, dict[str, Any]]:
    """One read, returning the status and the decoded body."""
    request = urllib.request.Request(f"{BASE}{path}", method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as refused:
        return refused.code, json.loads(refused.read())


def cursor_operations() -> list[tuple[str, str]]:
    """Every operation in the published contract that declares a cursor.

    Read from the document rather than hard-coded, so this cannot fall behind
    the API it is guarding.
    """
    document = json.loads((ROOT / "docs/api/openapi.json").read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    for path, operations in sorted(document["paths"].items()):
        for method, operation in operations.items():
            if method != "get" or not isinstance(operation, dict):
                continue
            names = {parameter.get("name") for parameter in operation.get("parameters", [])}
            if "cursor" in names:
                found.append((operation["operationId"], path))
    return found


@pytest.fixture(scope="module")
def populated(owner_engine: Any) -> Iterator[None]:
    """Rows in every collection, committed, at distinct instants.

    Distinct because the sort key is `(instant, id)` and a page boundary that
    falls inside a group sharing an instant is the case a cursor on a
    non-unique column gets wrong. They are spread a minute apart so the
    ordering is unambiguous when a test reads it back.
    """
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO site (id, name, location, timezone, control_plane_uri, anchor_state)"
                " VALUES (:id, :id, 'Nuneaton', 'Europe/London',"
                " 'https://alviss.example.internal', 'ANCHORED') ON CONFLICT DO NOTHING"
            ),
            {"id": SITE},
        )
        connection.execute(text("SELECT set_config('draupnir.site_id', :s, true)"), {"s": SITE})
        for index in range(ROWS):
            _awaiting_approval(connection, index)
            _registered_model(connection, index)
            _licensed_source(connection, index)
        _chain(connection)
    yield


def _chain(connection: Connection) -> None:
    """A real chain, because `getLedger` re-links what it reads.

    Built with the domain's own `append` rather than by hand: an entry whose
    `prev_hash` does not match its predecessor is a broken chain, and the
    audit view is supposed to say so -- which would make this fixture look
    like a finding.
    """
    from draupnir.core.domain.ledger import LedgerEntry, append

    previous: LedgerEntry | None = None
    for index in range(ROWS):
        previous = append(
            previous=previous,
            site_id=SITE,
            ts=EPOCH + timedelta(minutes=index),
            actor="operator:akuma",
            subject_type="run",
            subject_id=f"pagination-{index:02d}",
            transition="QUEUED->TRAINING",
            payload={"i": index},
        )
        connection.execute(
            text(
                "INSERT INTO ledger_entry (id, site_id, seq, prev_hash, entry_hash, ts,"
                " actor, subject_type, subject_id, transition, payload)"
                " VALUES (:id, :site, :seq, :prev, :hash, :ts, :actor,"
                " :subject_type, :subject_id, :transition, CAST(:payload AS jsonb))"
            ),
            {
                "id": str(previous.id),
                "site": SITE,
                "seq": previous.seq,
                "prev": previous.prev_hash,
                "hash": previous.entry_hash,
                "ts": previous.ts,
                "actor": previous.actor,
                "subject_type": previous.subject_type,
                "subject_id": previous.subject_id,
                "transition": previous.transition,
                "payload": json.dumps(previous.payload),
            },
        )


def _awaiting_approval(connection: Connection, index: int) -> None:
    """A run in the approval queue, which is what `listGates` returns."""
    connection.execute(
        text(
            "INSERT INTO run (id, site_id, name, spec_hash, kind, state, started_at)"
            " VALUES (:id, :site, :name, :hash, 'adapter', 'AWAITING_APPROVAL', :at)"
        ),
        {
            "id": str(uuid.uuid4()),
            "site": SITE,
            "name": f"pagination-gbr-{index:02d}",
            "hash": uuid.uuid4().hex,
            "at": EPOCH + timedelta(minutes=index),
        },
    )


def _registered_model(connection: Connection, index: int) -> None:
    """An artefact, published so it carries an instant to sort on."""
    artefact = str(uuid.uuid4())
    approval = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO artefact (id, site_id, locality, kind, uri, sha256_manifest, size)"
            " VALUES (:id, :site, ARRAY[:site], 'quantised', :uri, :sha, 1024)"
        ),
        {
            "id": artefact,
            "site": SITE,
            "uri": f"hodd://{SITE}/pagination-{index:02d}",
            "sha": uuid.uuid4().hex * 2,
        },
    )
    connection.execute(
        text(
            "INSERT INTO approval (id, subject_id, approver, decision, signature, decided_at)"
            " VALUES (:id, :subject, 'akuma', 'APPROVED', 'signature', :at)"
        ),
        {"id": approval, "subject": artefact, "at": EPOCH + timedelta(minutes=index)},
    )
    connection.execute(
        text(
            "INSERT INTO release (id, artefact_id, approval_id, model_card_uri, sbom_uri,"
            " lineage_uri, training_summary_uri, copyright_policy_uri, signature, published_at)"
            " VALUES (:id, :artefact, :approval, 'a', 'b', 'c', 'd', 'e', 'f', :at)"
        ),
        {
            "id": str(uuid.uuid4()),
            "artefact": artefact,
            "approval": approval,
            "at": EPOCH + timedelta(minutes=index),
        },
    )


def _licensed_source(connection: Connection, index: int) -> None:
    """One entry in the licence register, which `listSources` pages."""
    connection.execute(
        text(
            "INSERT INTO source (id, jurisdiction, url, licence_spdx, attribution_required,"
            " retrieved_at, sha256, personal_data, state)"
            " VALUES (:id, 'GBR', :url, 'CC-BY-4.0', true, :at, :sha, false, 'LICENCE_CLEARED')"
        ),
        {
            "id": str(uuid.uuid4()),
            "url": f"https://example.invalid/pagination-{index:02d}",
            "at": EPOCH + timedelta(minutes=index),
            "sha": uuid.uuid4().hex * 2,
        },
    )


@pytest.fixture(scope="module")
def api(migrated: str, populated: None) -> Iterator[str]:
    """A real API process, reading the real database."""
    del populated
    environment = {
        **os.environ,
        "DRAUPNIR_DEV": "1",
        "DRAUPNIR_DATABASE_URL": migrated.replace("postgresql+psycopg", "postgresql+asyncpg"),
        "DRAUPNIR_DATABASE_URL_SYNC": migrated,
        "DRAUPNIR_SITE_ID": SITE,
        "PYTHONIOENCODING": "utf-8",
    }
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "draupnir.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=environment,
    )
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{BASE}/healthz", timeout=1) as response:  # noqa: S310
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.1)
        else:
            pytest.fail("the API did not start")
        yield BASE
    finally:
        process.kill()
        process.wait(timeout=30)


def walk(path: str, *, limit: int = 2, pages: int = 20) -> tuple[list[str], int]:
    """Page a collection to the end, returning every item's identity in order.

    `pages` is a stop rather than an expectation: a collection whose cursor
    does not advance would otherwise page forever, and a test that hangs says
    less than one that fails.
    """
    seen: list[str] = []
    cursor: str | None = None
    taken = 0
    while taken < pages:
        query = f"limit={limit}" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        status, body = _get(f"{path}?{query}")
        assert status == 200, f"{path} answered {status}: {body}"
        taken += 1
        seen.extend(_identity(item) for item in body["items"])
        cursor = body["nextCursor"]
        if cursor is None:
            break
    else:
        pytest.fail(f"{path} did not reach a last page in {pages} pages")
    return seen, taken


def _identity(item: dict[str, Any]) -> str:
    """Whatever the item calls its identifier, as a string.

    The collections do not agree on the field: a run has `id`, a model is
    identified by its artefact digest, a ledger entry by its hash.
    """
    for field in ("id", "runId", "artefact", "entryHash", "hash", "uri"):
        if item.get(field):
            return str(item[field])
    return json.dumps(item, sort_keys=True)


@pytest.mark.parametrize(("operation", "path"), cursor_operations())
def test_every_cursor_declaring_collection_reaches_every_row_once(
    api: str, operation: str, path: str
) -> None:
    """AC-B3, for all of them rather than for the ones somebody remembered.

    `listGates` and `listModels` failed this by returning one page and a null
    cursor no matter how many rows there were (RF-16). The others passed, which
    is how the two got missed: the convention was followed query by query, so
    the next query skipped it.
    """
    del api
    seen, pages = walk(path)

    assert seen, f"{operation} returned nothing to page through"
    assert len(seen) == len(set(seen)), f"{operation} returned a row twice: {seen}"
    assert pages > 1, (
        f"{operation} declares a cursor but answered everything on one page; "
        f"either it ignores the cursor or the fixture did not seed enough rows"
    )


@pytest.mark.parametrize(("operation", "path"), cursor_operations())
def test_a_second_page_is_a_different_page(api: str, operation: str, path: str) -> None:
    """The narrower claim, stated separately because it is the one that failed.

    A collection that ignores its cursor answers the same first page forever.
    Paging to the end catches that too, but only by the row-uniqueness
    assertion, and a reader looking at *why* would rather see this.
    """
    del api
    status, first = _get(f"{path}?limit=2")
    assert status == 200
    assert first["nextCursor"], f"{operation} offered no cursor for a second page"

    status, second = _get(f"{path}?limit=2&cursor={urllib.parse.quote(first['nextCursor'])}")

    assert status == 200
    assert second["items"], f"{operation}'s second page was empty"
    assert [_identity(item) for item in second["items"]] != [
        _identity(item) for item in first["items"]
    ], f"{operation} answered the same page twice, so its cursor is ignored"


def test_a_row_inserted_mid_pagination_never_removes_one_that_was_coming(
    api: str, owner_engine: Any
) -> None:
    """The property the README claims, and the reason cursors exist.

    With offset pagination a row inserted before the boundary pushes everything
    after it along by one, and the row that was at the boundary is never
    returned -- silently, with a well formed response either side of it. A
    keyset cursor names a position rather than a count, so the rows still to
    come are still to come.

    The queue is ascending, so the insert has to be *behind* the cursor to be
    the awkward case: a run whose `started_at` is earlier than the page
    boundary is one an offset would have shifted into the gap.
    """
    del api
    status, first = _get("/v1/gates?limit=2")
    assert status == 200
    cursor = first["nextCursor"]
    assert cursor

    _status, before = _get(f"/v1/gates?limit=20&cursor={urllib.parse.quote(cursor)}")
    still_to_come = [_identity(item) for item in before["items"]]
    assert still_to_come, "nothing was left to come, so this proves nothing"

    with owner_engine.begin() as connection:
        connection.execute(text("SELECT set_config('draupnir.site_id', :s, true)"), {"s": SITE})
        connection.execute(
            text(
                "INSERT INTO run (id, site_id, name, spec_hash, kind, state, started_at)"
                " VALUES (:id, :site, 'pagination-inserted', :hash, 'adapter',"
                " 'AWAITING_APPROVAL', :at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "site": SITE,
                "hash": uuid.uuid4().hex,
                # Behind the boundary: an offset would have shifted a row into
                # the gap this leaves.
                "at": EPOCH - timedelta(minutes=1),
            },
        )

    _status, after = _get(f"/v1/gates?limit=20&cursor={urllib.parse.quote(cursor)}")
    arrived = [_identity(item) for item in after["items"]]

    missing = set(still_to_come) - set(arrived)
    assert not missing, f"rows that were coming were dropped by an insert: {missing}"


@pytest.mark.parametrize(("operation", "path"), cursor_operations())
def test_a_cursor_the_api_did_not_issue_is_refused(api: str, operation: str, path: str) -> None:
    """Found while implementing RF-16, and true of every collection.

    `reading.Cursor.decode` returned `None` for anything malformed and every
    caller read that as "start from the beginning", so a client whose cursor
    was corrupted in transit was served page one and told nothing. It then
    pages forward, corrupts it again, and loops -- and every response it gets
    is a well formed page. `pagination.Cursor` in the same codebase refuses
    this in as many words; the two disagreed, and the quieter one was the one
    the collections used.

    The audit view had its own version of it: its cursor is a ledger sequence
    number, and anything that was not a number fell back to `None`, which
    reads as "start from the newest". Of everywhere in this system to silently
    show the wrong window, the chain is the worst.
    """
    del api, operation
    status, body = _get(f"{path}?limit=2&cursor=not-a-cursor")

    assert status == 422
    assert body["type"].endswith("invalid-cursor")
    assert "opaque" in body["detail"]


def test_the_last_page_says_it_is_the_last(api: str) -> None:
    """`nextCursor: null` has to mean "no more", not "no pagination here".

    It meant the second thing for two collections, and a client cannot tell
    the difference from the response -- which is why the truncation was silent.
    """
    del api
    _status, body = _get("/v1/gates?limit=200")

    assert body["items"]
    assert body["nextCursor"] is None
