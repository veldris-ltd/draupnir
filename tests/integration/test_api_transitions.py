"""Every mutating endpoint writes to the ledger, or refuses and says why.

The reconciliation recorded this as NOT BUILT 1: `POST /v1/runs` wrote and
nothing else did, so a corpus ingested through the console left no trace and a
gate decided there produced a signed approval nobody could find. This is the
evidence that it is built.

Two shapes, and the difference is the point. A source, a corpus and a release
are not runs -- SAD 7.1 gives each its own entity -- so those endpoints append
an entry the projector passes through untouched. A decision, a cancellation and
a requeue are lifecycle transitions, so those go through the state machine,
which checks them against SAD 6.1 and refuses what the table has no row for.

The refusals are as much the deliverable as the writes. Cancelling stops a
scheduler job, so it applies to a run that has one; requeueing is for a run
that failed a gate within its budget. A handler that moved a run anyway would
be a handler with its own opinion about the lifecycle.
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
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text

from draupnir.api import concurrency
from draupnir.core.domain.identifiers import new_id
from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import RunState
from draupnir.core.infrastructure.orchestration import for_connection
from draupnir.core.infrastructure.repositories import LedgerRepository

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SITE = "sindri"
PORT = 8934
BASE = f"http://127.0.0.1:{PORT}"

#: What the development principal presents as. The sole approver exception is
#: `approver == submitter`, so a test of it has to know which identity the API
#: is acting under rather than assuming one.
DEV_ACTOR = "dev@veldris.internal"


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


@pytest.fixture(scope="module")
def api(request: pytest.FixtureRequest) -> Iterator[str]:
    """One API process for the module. Killing it per test costs more than it buys."""
    migrated = request.getfixturevalue("migrated")
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


def post(
    path: str, body: dict[str, Any] | None = None, *, if_match: str | None = None
) -> tuple[int, dict[str, Any]]:
    """POST as the development principal, with a fresh idempotency key.

    `if_match` is passed through rather than filled in: cancel, retry, decide
    and publish are conditional writes (AC-B4), and a helper that always
    supplied the right tag would make it impossible to test that they refuse
    without one.
    """
    headers = {"Content-Type": "application/json", "Idempotency-Key": str(uuid.uuid4())}
    if if_match is not None:
        headers["If-Match"] = if_match
    request = urllib.request.Request(  # noqa: S310 -- fixed http, fixed host
        f"{BASE}{path}",
        data=json.dumps(body or {}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.status or 0, json.loads(error.read().decode("utf-8"))


def tag_for(state: dict[str, Any]) -> str:
    """The entity tag a handler will compute for this state.

    Derived with the real function rather than hard-coded: a tag written into a
    test is a tag that stops matching the moment the derivation changes, and
    the test then fails for a reason that has nothing to do with what it checks.
    """
    return concurrency.etag(state)


def entries(owner: Connection, subject_id: str) -> list[Any]:
    """Every ledger entry about one subject, at Sindri."""
    return list(LedgerRepository(owner, SiteScope(SITE)).entries_for_subject(subject_id))


def place_run(owner_engine: Engine, *, reaching: RunState, actor: str = DEV_ACTOR) -> UUID:
    """Walk a run to `reaching` through the real guards, and commit it.

    Committed because the API is a separate process: a run inside this test's
    transaction is a run the API cannot see, and a test that seeded through the
    API instead would be testing the endpoints it is about to exercise.
    """
    run_id: UUID = new_id()
    spine: tuple[tuple[RunState, dict[str, Any], dict[str, Any]], ...] = (
        (
            RunState.CORPUS_REGISTERED,
            {"sources_without_declaration": []},
            {"sources": ["s1"], "source_sha256": "b" * 64, "curator": actor},
        ),
        (
            RunState.LICENCE_CLEARED,
            {"sources_failing_policy": [], "base_model_cleared": True},
            {"policy_version": "gleipnir-licence/2026.01", "evaluation_result": "PASS"},
        ),
        (
            RunState.CURATED,
            {"curation_complete": True, "decontamination_confirmed": True},
            {"stage_retention": {}, "output_sha256": "c" * 64, "token_count": 1},
        ),
        (
            RunState.QUEUED,
            {"specification_hash": "d" * 64, "specification_valid": True},
            {"spec_hash": "d" * 64, "input_artefact_sha256": ["c" * 64]},
        ),
        (
            RunState.TRAINING,
            {"scheduler_job_id": "job-1"},
            {"scheduler_job_id": "job-1", "node": "dvalin", "placement": {"partition": "adapters"}},
        ),
        (
            RunState.TRAINED,
            {"exit_code": 0, "checkpoint_sha256": "e" * 64},
            {"checkpoint_sha256": "e" * 64, "steps": 10, "final_loss": 1.0},
        ),
        (
            RunState.EVALUATING,
            {"suite_version": "2026.01"},
            {"suite_version": "2026.01", "baseline": "run://base"},
        ),
        (RunState.MERGED, {"failing_gates": []}, {"gate_results": {"E1": {"passed": True}}}),
        (
            RunState.QUANTISED,
            {"failing_gates": []},
            {"merge_config_hash": "f" * 64, "sweep_result": {"points": 5}},
        ),
        (
            RunState.AWAITING_APPROVAL,
            {"formats_regated": ["nvfp4"], "formats_failing": []},
            {"format_gate_results": {"nvfp4": {"passed": True}}},
        ),
    )

    with owner_engine.begin() as connection:
        orchestrator = for_connection(connection, SiteScope(SITE), actor=actor)
        orchestrator.register(
            run_id,
            name=f"cim-gbr-{run_id.hex[:8]}",
            spec_hash="d" * 64,
            kind="adapter",
            identity=uuid.uuid4().hex * 2,
            payload={"retry_budget": 2},
        )
        for target, facts, payload in spine:
            orchestrator.transition(run_id, target, facts=facts, payload=payload)
            if target is reaching:
                break
    return run_id


# ---------------------------------------------------------------------------
# Subjects that are not runs
# ---------------------------------------------------------------------------


def test_registering_a_source_records_the_facts_hodd_holds(
    api: str, owner: Connection, site: str
) -> None:
    """HODD records; GLEIPNIR judges. What goes in the chain is the facts."""
    del api, site
    status, body = post(
        "/v1/sources",
        {
            "jurisdiction": "GBR",
            "url": "https://hansard.parliament.uk",
            "licenceSpdx": "CC-BY-4.0",
            "attributionRequired": True,
            "retrievedAt": "2026-03-02T09:00:00+00:00",
            "sha256": "a" * 64,
            "personalData": False,
        },
    )

    assert status == 201, body
    recorded = entries(owner, str(body["id"]))
    assert [entry.transition for entry in recorded] == ["registered"]
    assert recorded[0].subject_type == "source"
    assert recorded[0].payload["licence_spdx"] == "CC-BY-4.0"
    # The determination and its reference are one fact, and both are recorded.
    assert recorded[0].payload["personal_data"] is False


def test_ingest_and_curate_record_against_the_corpus_not_a_run(
    api: str, owner: Connection, site: str
) -> None:
    """A corpus is an input to many runs, so it is its own subject.

    The projector folds `run` entries and passes these through, which is what
    lets a corpus have a history without inventing a run to hang it on.
    """
    del api, site
    ingest, first = post("/v1/corpora/GBR/ingest")
    curate, second = post("/v1/corpora/GBR/curate")

    assert (ingest, curate) == (202, 202), (first, second)
    recorded = entries(owner, "GBR")
    transitions = [entry.transition for entry in recorded]
    assert "ingest-accepted" in transitions
    assert "curate-accepted" in transitions
    assert {entry.subject_type for entry in recorded} == {"corpus"}


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def test_approving_a_gate_moves_the_run_to_released(
    api: str, owner: Connection, owner_engine: Engine, site: str
) -> None:
    """AWAITING_APPROVAL -> RELEASED, recorded with the signature."""
    del api, site
    run_id = place_run(owner_engine, reaching=RunState.AWAITING_APPROVAL, actor="operator@veldris")

    status, body = post(
        f"/v1/gates/{run_id}/decide",
        {
            "decision": "approved",
            "reason": "gates pass and the lineage is complete",
            "signature": "a-detached-signature",
        },
        if_match=tag_for({"id": str(run_id)}),
    )

    assert status == 201, body
    recorded = entries(owner, str(run_id))
    assert recorded[-1].transition == "AWAITING_APPROVAL->RELEASED"
    assert recorded[-1].payload["signature"] == "a-detached-signature"
    assert recorded[-1].actor == DEV_ACTOR


def test_the_sole_approver_exception_is_computed_from_the_chain(
    api: str, owner: Connection, owner_engine: Engine, site: str
) -> None:
    """AC-S15 and constraint C-11.

    The submitter is read from the run's registration entry. There is no
    argument that sets the exception, so suppressing it means editing the
    orchestrator -- a code review rather than a deployment.
    """
    del api, site
    same = place_run(owner_engine, reaching=RunState.AWAITING_APPROVAL, actor=DEV_ACTOR)
    other = place_run(owner_engine, reaching=RunState.AWAITING_APPROVAL, actor="someone@else")

    _, exception = post(
        f"/v1/gates/{same}/decide",
        {"decision": "approved", "reason": "same identity", "signature": "sig"},
        if_match=tag_for({"id": str(same)}),
    )
    _, separated = post(
        f"/v1/gates/{other}/decide",
        {"decision": "approved", "reason": "two identities", "signature": "sig"},
        if_match=tag_for({"id": str(other)}),
    )

    assert exception["soleApproverException"] is True
    assert separated["soleApproverException"] is False
    # And it is in the chain, not only in the response: the lineage renders it.
    assert entries(owner, str(same))[-1].payload["sole_approver_exception"] is True


def test_rejecting_a_gate_quarantines_the_run(
    api: str, owner: Connection, owner_engine: Engine, site: str
) -> None:
    """The other row SAD 6.1 gives a decision."""
    del api, site
    run_id = place_run(owner_engine, reaching=RunState.AWAITING_APPROVAL)

    status, body = post(
        f"/v1/gates/{run_id}/decide",
        {"decision": "rejected", "reason": "the DPIA reference is missing", "signature": "sig"},
        if_match=tag_for({"id": str(run_id)}),
    )

    assert status == 201, body
    last = entries(owner, str(run_id))[-1]
    assert last.transition == "AWAITING_APPROVAL->QUARANTINED"
    assert "DPIA" in last.payload["rejection_reason"]


def test_deciding_a_run_that_is_not_awaiting_approval_is_refused(
    api: str, owner_engine: Engine, site: str
) -> None:
    """409, naming the state. The handler has no opinion about the lifecycle."""
    del api, site
    run_id = place_run(owner_engine, reaching=RunState.TRAINING)

    status, body = post(
        f"/v1/gates/{run_id}/decide",
        {"decision": "approved", "reason": "too early", "signature": "sig"},
        if_match=tag_for({"id": str(run_id)}),
    )

    assert status == 409, body
    assert body["code"] == "gate-not-decidable"
    assert "TRAINING" in body["detail"]


def test_cancelling_a_training_run_leaves_it_in_a_defined_state(
    api: str, owner: Connection, owner_engine: Engine, site: str
) -> None:
    """AC-F13. A cancelled scheduler job exits non-zero, which is FAILED."""
    del api, site
    run_id = place_run(owner_engine, reaching=RunState.TRAINING)

    status, body = post(
        f"/v1/runs/{run_id}/cancel",
        {"reason": "the corpus was wrong"},
        if_match=tag_for({"id": str(run_id)}),
    )

    assert status == 202, body
    last = entries(owner, str(run_id))[-1]
    assert last.transition == "TRAINING->FAILED"
    assert last.payload["cancelled_by"] == DEV_ACTOR
    assert "the corpus was wrong" in last.payload["last_log_lines"][0]


def test_cancelling_a_queued_run_is_refused_and_says_why(
    api: str, owner_engine: Engine, site: str
) -> None:
    """A queued run holds no allocation, so there is nothing to stop.

    SAD 6.1 has no transition out of QUEUED except to TRAINING, so there is
    nowhere to put a withdrawn one. That is a gap in the table rather than in
    this handler, and the refusal names it instead of inventing a state.
    """
    del api, site
    run_id = place_run(owner_engine, reaching=RunState.QUEUED)

    status, body = post(
        f"/v1/runs/{run_id}/cancel",
        {"reason": "changed my mind"},
        if_match=tag_for({"id": str(run_id)}),
    )

    assert status == 409, body
    assert body["code"] == "run-not-cancellable"
    assert "QUEUED" in body["detail"]
    assert "scheduler job" in body["title"]


def test_requeueing_an_evaluating_run_that_failed_a_gate(
    api: str, owner: Connection, owner_engine: Engine, site: str
) -> None:
    """AC-F7's requeue, driven by an operator rather than by RAUN."""
    del api, site
    run_id = place_run(owner_engine, reaching=RunState.EVALUATING)
    # A failure the chain records, because the requeue is for a run that had one.
    with owner_engine.begin() as connection:
        for_connection(connection, SiteScope(SITE), actor="raun").record(
            subject_type="evaluation",
            subject_id=str(run_id),
            transition="gates-judged",
            payload={"failing_gates": ["E3"], "run_id": str(run_id)},
        )

    status, body = post(f"/v1/runs/{run_id}/retry", if_match=tag_for({"id": str(run_id)}))

    assert status == 202, body
    last = [entry for entry in entries(owner, str(run_id)) if entry.subject_type == "run"][-1]
    assert last.transition == "EVALUATING->QUEUED"
    assert last.payload["failing_gate"] == "E3"
    assert "2 of 2 retries remaining" in last.payload["requeue_reason"]


def test_requeueing_a_run_with_no_recorded_failure_is_refused(
    api: str, owner_engine: Engine, site: str
) -> None:
    """A run that failed nothing has nothing to try again."""
    del api, site
    run_id = place_run(owner_engine, reaching=RunState.EVALUATING)

    status, body = post(f"/v1/runs/{run_id}/retry", if_match=tag_for({"id": str(run_id)}))

    assert status == 409, body
    assert body["code"] == "nothing-to-retry"


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


def test_publishing_without_an_approval_is_refused(api: str, site: str) -> None:
    """SAD 5.2 and AC-S5, and now conditional rather than unconditional."""
    del api, site
    artefact = "9" * 64
    status, body = post(
        f"/v1/releases/{artefact}/publish", if_match=tag_for({"artefact": artefact})
    )

    assert status == 409, body
    assert body["code"] == "release-unapproved"


def test_publishing_an_approved_artefact_records_the_publication(
    api: str, owner: Connection, owner_engine: Engine, site: str
) -> None:
    """The approval is the permission, and the publication is a second event.

    Two entries because they are two things: the run reached RELEASED when the
    approver signed, and this records what was then published under that
    approval. An auditor asks the second question.
    """
    del api, site
    run_id = place_run(owner_engine, reaching=RunState.AWAITING_APPROVAL)
    artefact = uuid.uuid4().hex * 2

    # The approval names the bytes. Recorded through the orchestrator because
    # a decision through the API carries no artefact -- an API-driven run has
    # produced none, there being no worker to produce it.
    with owner_engine.begin() as connection:
        for_connection(connection, SiteScope(SITE), actor=DEV_ACTOR).transition(
            run_id,
            RunState.RELEASED,
            facts={"approver_has_role": True, "decision": "APPROVED", "signature": "sig"},
            payload={
                "approver": DEV_ACTOR,
                "signature": "sig",
                "decided_at": "2026-03-02T09:00:00+00:00",
                "artefact_sha256": artefact,
                "model": "cim-gbr-v1.0",
                "formats": ["nvfp4"],
            },
        )

    status, body = post(
        f"/v1/releases/{artefact}/publish", if_match=tag_for({"artefact": artefact})
    )

    assert status == 202, body
    assert body["model"] == "cim-gbr-v1.0"
    assert body["formats"] == ["nvfp4"]

    published = entries(owner, artefact)
    assert [entry.transition for entry in published] == ["published"]
    assert published[0].subject_type == "release"
    assert published[0].payload["run_id"] == str(run_id)
