"""A run submitted through the API reaches the ledger. AC-F1, AC-F2, AC-B1.

Against a real API process, a real database and the real row level security,
because the claim is about the path an operator uses. The console and the CLI
both post here; a submission that recorded nothing would put a run on the board
that the ledger has never heard of, and SAD 1.1's whole argument is that the
audit record is a property of the system rather than of the operator.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, text

from draupnir.core.domain.sites import SiteScope
from draupnir.core.infrastructure.repositories import LedgerRepository, RunProjection
from tests.specs import submittable_mapping

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SITE = "sindri"
PORT = 8933
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def site(owner: Connection) -> Iterator[str]:
    """Register Sindri and commit it: another process has to see it."""
    owner.execute(
        text(
            "INSERT INTO site (id, name, location, timezone, control_plane_uri, anchor_state) "
            "VALUES (:id, 'Sindri', 'Belfast', 'Europe/London', "
            "'https://sindri.veldris.internal', 'ANCHORED') ON CONFLICT (id) DO NOTHING"
        ),
        {"id": SITE},
    )
    owner.commit()
    yield SITE


@pytest.fixture
def api(migrated: str, site: str) -> Iterator[str]:
    """A real API process, writing to the real database."""
    del site
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


def unique_spec() -> dict[str, Any]:
    """The SAD 6.2 sample with a dataset digest nothing else has used.

    The container is session scoped and the API commits, so a run written by
    one test is still there for the next. Two tests sharing a specification
    would share a run identity, and the second would be refused as a duplicate
    -- correctly, and for a reason that has nothing to do with what it is
    testing.
    """
    digest = uuid.uuid4().hex * 2
    return submittable_mapping(
        dataset={
            "artefact": "hodd://corpora/GBR/curated",
            "expectSha256": digest,
            "cutoffPercentile": 99,
        }
    )


def _post(path: str, body: dict[str, Any], *, key: str) -> tuple[int, dict[str, Any]]:
    """POST as an operator. The development principal supplies the identity."""
    request = urllib.request.Request(  # noqa: S310 -- fixed http, fixed host
        f"{BASE}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Idempotency-Key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.status or 0, json.loads(error.read().decode("utf-8"))


def test_a_submitted_run_is_recorded_in_the_ledger(api: str, owner: Connection, site: str) -> None:
    """The submission the console makes, and what it leaves behind."""
    del api
    status, body = _post("/v1/runs", {"specification": unique_spec()}, key=str(uuid.uuid4()))

    assert status == 202, body
    run_id = str(body["runId"])
    identity = str(body["runIdentity"])

    scope = SiteScope(site)
    entries = [
        entry for entry in LedgerRepository(owner, scope).stream(1) if entry.subject_id == run_id
    ]
    assert len(entries) == 1
    assert entries[0].transition == "->DRAFT"
    payload = entries[0].payload
    assert isinstance(payload, dict)
    assert payload["run_identity"] == identity

    # And the projection has it, so the run board is reading the chain rather
    # than an event nobody recorded.
    projected = {item.id: item for item in RunProjection(owner, scope).read()}
    assert run_id in projected
    assert projected[run_id].name == "cim-gbr-v0.1"


def test_the_same_specification_submitted_twice_is_refused_as_a_duplicate(
    api: str, owner: Connection, site: str
) -> None:
    """AC-F2, through the API rather than through the procedure runner.

    Two different idempotency keys: this is not a replay, it is a second
    submission of the same specification, which is the case AC-F2 is about.
    A replay returns the first result; a duplicate is told it is one.
    """
    del api, owner, site
    specification = unique_spec()

    first, body = _post("/v1/runs", {"specification": specification}, key=str(uuid.uuid4()))
    assert first == 202, body

    second, refusal = _post("/v1/runs", {"specification": specification}, key=str(uuid.uuid4()))

    assert second == 409, refusal
    assert refusal["code"] == "duplicate-run"
    assert str(body["runId"]) in str(refusal["detail"])


def test_a_replay_returns_the_original_result_and_records_one_run(
    api: str, owner: Connection, site: str
) -> None:
    """AC-B1 against the write path: a retry must not append twice."""
    del api
    key = str(uuid.uuid4())
    specification = unique_spec()

    first_status, first = _post("/v1/runs", {"specification": specification}, key=key)
    second_status, second = _post("/v1/runs", {"specification": specification}, key=key)

    assert (first_status, second_status) == (202, 202)
    assert first["runId"] == second["runId"]

    scope = SiteScope(site)
    entries = [
        entry
        for entry in LedgerRepository(owner, scope).stream(1)
        if entry.subject_id == str(first["runId"])
    ]
    assert len(entries) == 1, "the replay appended a second entry"


def test_simultaneous_submissions_queue_rather_than_refusing_one(
    api: str, owner: Connection, site: str
) -> None:
    """Two operators submitting at once are two runs, not one and a 503.

    A chain is serial -- the next entry is at seq N+1 -- so two writers that
    both read N both compute N+1, and the unique constraint refuses the second.
    That was the behaviour until the journeys ran six workers at once and one
    submission came back 503 saying "another writer appended seq 276". The
    constraint is the right backstop; queueing is the right answer, and
    `LedgerRepository.serialise` takes the site's advisory lock so contending
    writers wait instead of racing.
    """
    del api
    from concurrent.futures import ThreadPoolExecutor

    specifications = [unique_spec() for _ in range(6)]

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda spec: _post("/v1/runs", {"specification": spec}, key=str(uuid.uuid4())),
                specifications,
            )
        )

    statuses = [status for status, _ in results]
    assert statuses == [202] * 6, [body for _, body in results]

    # Six runs, six consecutive sequence numbers, no gap and no collision.
    submitted = {str(body["runId"]) for _, body in results}
    scope = SiteScope(site)
    entries = [
        entry for entry in LedgerRepository(owner, scope).stream(1) if entry.subject_id in submitted
    ]
    assert len(entries) == 6
    sequences = sorted(entry.seq for entry in entries)
    assert sequences == list(range(sequences[0], sequences[0] + 6))


# ---------------------------------------------------------------------------
# A refused submission costs nothing. RF-11.
# ---------------------------------------------------------------------------


def _ledger_length(owner: Connection) -> int:
    """How many entries this site's chain holds."""
    return LedgerRepository(owner, SiteScope(SITE)).length()


def test_a_specification_the_driver_refuses_is_422_and_writes_nothing(
    api: str, owner: Connection, site: str
) -> None:
    """The finding, against the real database.

    `submitRun` said "validate" and checked only that the specification was not
    empty, so one the dry run refused with 422 was accepted here with 202 --
    and by the time it failed it had consumed a run identifier, a ledger entry
    and a place in the queue. A ledger entry is the expensive part: the chain
    is append only, so a run recorded in error is a run that is in the record
    for ever and has to be explained rather than removed.
    """
    del api, site
    before = _ledger_length(owner)

    specification = unique_spec()
    specification["spec"]["train"] = {
        **specification["spec"]["train"],
        "method": "telepathy",
    }

    status, body = _post("/v1/runs", {"specification": specification}, key=str(uuid.uuid4()))

    assert status == 422, body
    assert body["code"] == "driver-unavailable"
    assert _ledger_length(owner) == before, "a refused submission appended to the chain"


def test_a_jurisdiction_outside_the_programme_is_422_and_writes_nothing(
    api: str, owner: Connection, site: str
) -> None:
    """AC-F16's other half: no default tier is resolved.

    A jurisdiction nobody assigned is not a Tier B jurisdiction by default. If
    one were resolved, this would be accepted, trained against whichever base
    that tier names, and appear on the board as the fifty-seventh model in a
    fifty-six model programme.
    """
    del api, site
    before = _ledger_length(owner)

    specification = unique_spec()
    specification["metadata"] = {**specification["metadata"], "jurisdiction": "IRL"}

    status, body = _post("/v1/runs", {"specification": specification}, key=str(uuid.uuid4()))

    assert status == 422, body
    assert body["code"] == "jurisdiction-unassigned"
    assert "IRL" in body["detail"]
    assert _ledger_length(owner) == before, "a refused submission appended to the chain"


def test_an_accepted_submission_records_the_settled_specification(
    api: str, owner: Connection, site: str
) -> None:
    """And it hashes to the `spec_hash` recorded beside it. RF-10 and RF-11.

    The chain records the *settled* form -- the tier checked, the checkpoint
    interval derived -- because that is what the worker renders. So the chain
    is self-verifying: read the specification back, hash it, and it equals the
    hash recorded with it.
    """
    del api, site
    status, body = _post("/v1/runs", {"specification": unique_spec()}, key=str(uuid.uuid4()))
    assert status == 202, body

    entries = LedgerRepository(owner, SiteScope(SITE)).entries_for_subject(str(body["runId"]))
    registration = entries[0]
    recorded = registration.payload["specification"]

    from draupnir.interfaces.types import RunSpec

    assert recorded["spec"]["train"]["params"]["save_steps"], (
        "the derived checkpoint interval was not recorded, so the worker would "
        "render a specification the driver refuses"
    )
    assert RunSpec.from_mapping(recorded).spec_hash() == registration.payload["spec_hash"]


# ---------------------------------------------------------------------------
# Idempotency across processes. RF-14.
# ---------------------------------------------------------------------------


def test_a_key_survives_the_api_process(api: str, owner: Connection, site: str) -> None:
    """The reservation is a row, not a dictionary entry.

    The store was process-local, so every reservation was lost on restart and a
    key reserved by one of SAD 5.1's two-to-four API processes was unknown to
    the others. This asserts the row is there and carries what a replay is
    answered from -- which is the half a second process would read.
    """
    del site
    key = str(uuid.uuid4())
    status, body = _post("/v1/runs", {"specification": unique_spec()}, key=key)
    assert status == 202, body

    row = (
        owner.execute(
            text(
                "SELECT status, body, request_fingerprint FROM idempotency_key "
                "WHERE site_id = :site AND key = :key"
            ),
            {"site": SITE, "key": key},
        )
        .mappings()
        .first()
    )

    assert row is not None, "the key was reserved in memory and not in the database"
    assert row["status"] == 202
    assert row["body"]["runId"] == body["runId"]
    assert row["request_fingerprint"]


def test_a_replay_through_the_api_returns_the_first_response(
    api: str, owner: Connection, site: str
) -> None:
    """And records one run, not two.

    The specification is the same, so AC-F2's identity check would refuse the
    second submission as a duplicate whichever process saw it -- but the reply
    an operator gets should be the original 202 rather than a 409, and that is
    the key's doing rather than the identity's.
    """
    del site
    key = str(uuid.uuid4())
    specification = unique_spec()
    before = _ledger_length(owner)

    first_status, first = _post("/v1/runs", {"specification": specification}, key=key)
    second_status, second = _post("/v1/runs", {"specification": specification}, key=key)

    assert first_status == 202, first
    assert second_status == 202, second
    assert second["runId"] == first["runId"]
    assert _ledger_length(owner) == before + 1, "a replay recorded a second run"


def test_a_key_reused_for_a_different_body_is_422(api: str, site: str) -> None:
    """422, and the stored response is not returned.

    Replaying it would tell the caller a request they did not make had
    succeeded, which is worse than either alternative.
    """
    del api, site
    key = str(uuid.uuid4())

    first, body = _post("/v1/runs", {"specification": unique_spec()}, key=key)
    assert first == 202, body

    status, refused = _post("/v1/runs", {"specification": unique_spec()}, key=key)

    assert status == 422, refused
    assert refused["code"] == "idempotency-key-reused"


def test_an_in_flight_key_is_409_for_a_second_caller(
    api: str, owner: Connection, site: str
) -> None:
    """The case a process-local store could never answer.

    A reservation is written before the work starts, so a second request --
    from any process -- finds it and is told to wait. Here the reservation is
    made directly, which is what the first process would have done a moment
    before the second request arrived.
    """
    del site
    key = str(uuid.uuid4())
    specification = unique_spec()

    owner.execute(
        text(
            "INSERT INTO idempotency_key "
            "(site_id, actor, key, request_fingerprint, created_at) "
            "VALUES (:site, :actor, :key, :fingerprint, now())"
        ),
        {
            "site": SITE,
            "actor": "dev@veldris.internal",
            "key": key,
            "fingerprint": _fingerprint_of({"specification": specification}),
        },
    )
    owner.commit()

    status, body = _post("/v1/runs", {"specification": specification}, key=key)

    assert status == 409, body
    assert body["code"] == "request-in-flight"


def _fingerprint_of(payload: dict[str, Any]) -> str:
    """The digest the API computes for a request body."""
    from draupnir.api.idempotency import fingerprint

    return fingerprint(payload)
