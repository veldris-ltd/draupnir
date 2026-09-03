"""The write path at the edge, without a database.

The `Writer` protocol exists so that a mechanism test needs no PostgreSQL, and
this is the test that spends it. What is checked here is what the endpoint does
with the answer the write path gives it -- which status, which problem code,
which words -- and none of that needs a chain to exist.

The refusals matter as much as the writes. Cancelling stops a scheduler job, so
it applies to a run that has one; a requeue is for a run that failed a gate; a
decision moves a run out of AWAITING_APPROVAL and nowhere else. Each is a row
of SAD 6.1 the handler could not find, and each refusal names it -- otherwise an
operator cannot tell whether the handler or the lifecycle said no.

The double is a real `Writer`. It records what it was asked to do and answers
the questions the handlers ask, so a handler that stopped asking, or started
asking for something else, fails here rather than at a keyboard.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from draupnir.api import concurrency, deps, writing
from draupnir.api.app import create_app
from draupnir.core.application.orchestrator import Applied, RunFacts
from draupnir.core.domain.identifiers import new_id
from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry
from draupnir.core.domain.projector import ProjectedRun
from draupnir.core.domain.states import (
    GuardRefusedError,
    RunState,
    TransitionContext,
    assert_allowed,
    evaluate,
)
from draupnir.interfaces.testing import sample_spec

pytestmark = pytest.mark.contract

APPROVER = {
    "sub": "akuma",
    "iss": "https://megingjord.veldris.internal",
    "roles": ["approver", "operator", "curator"],
    "amr": ["pwd", "hwk"],
}


class FakeWriter:
    """A writer with a chain in a dictionary.

    It runs the *real* state machine: `assert_allowed` and `evaluate` are the
    same functions the orchestrator calls, so a transition this refuses is one
    the orchestrator would refuse. A double that accepted everything would test
    the handler against a system that does not exist.
    """

    def __init__(self, facts: dict[UUID, RunFacts] | None = None) -> None:
        """Start from whatever runs the chain is supposed to already hold."""
        self.facts = facts or {}
        self.approvals: dict[str, LedgerEntry] = {}
        self.written: list[tuple[str, str, str]] = []

    @property
    def records(self) -> bool:
        """Yes. The point of the double is to be a writer that writes."""
        return True

    async def register_run(self, **kwargs: Any) -> Applied:
        """Record a run at DRAFT."""
        run_id = kwargs["run_id"]
        self.written.append(("run", str(run_id), "->DRAFT"))
        return self._applied(run_id, kwargs["name"], RunState.DRAFT, "->DRAFT")

    async def transition_run(self, **kwargs: Any) -> Applied:
        """Move a run through the real state machine, or raise as it would."""
        run_id: UUID = kwargs["run_id"]
        target: RunState = kwargs["target"]
        known = self.facts[run_id]

        transition = assert_allowed(known.state, target)
        outcome = evaluate(known.state, target, TransitionContext(facts=kwargs["facts"]))
        if not outcome.passed:
            raise GuardRefusedError(transition, outcome)

        self.written.append(("run", str(run_id), transition.name))
        self.facts[run_id] = RunFacts(
            run_id=run_id,
            name=known.name,
            state=target,
            submitter=known.submitter,
            spec_hash=known.spec_hash,
            retry_count=known.retry_count,
            retry_budget=known.retry_budget,
            failing_gates=known.failing_gates,
        )
        return self._applied(run_id, known.name, target, transition.name)

    async def record(self, **kwargs: Any) -> LedgerEntry:
        """Append an entry about something that is not a run."""
        self.written.append((kwargs["subject_type"], kwargs["subject_id"], kwargs["transition"]))
        return _entry(kwargs["subject_type"], kwargs["subject_id"], kwargs["transition"], {})

    async def read(self, *, site_id: str, actor: str, question: Any) -> Any:
        """Answer against this double's own idea of the chain."""
        del site_id, actor
        return question(self)

    # -- what the questions call --------------------------------------------

    def facts_of(self, run_id: UUID) -> RunFacts | None:
        """What the handlers ask before they decide."""
        return self.facts.get(run_id)

    def released_entry_for(self, artefact_sha256: str) -> LedgerEntry | None:
        """The approval that released these bytes, if this double holds one."""
        return self.approvals.get(artefact_sha256)

    def _applied(self, run_id: UUID, name: str, state: RunState, transition: str) -> Applied:
        return Applied(
            entry=_entry("run", str(run_id), transition, {}),
            run=ProjectedRun(
                id=str(run_id),
                site_id="sindri",
                name=name,
                spec_hash="d" * 64,
                kind="adapter",
                state=state,
            ),
        )


def _entry(subject_type: str, subject_id: str, transition: str, payload: Any) -> LedgerEntry:
    return LedgerEntry(
        id=new_id(),
        site_id="sindri",
        seq=1,
        prev_hash=GENESIS_HASH,
        entry_hash="a" * 64,
        ts=datetime.now(UTC),
        actor="tester",
        subject_type=subject_type,
        subject_id=subject_id,
        transition=transition,
        payload=payload,
    )


def facts_at(state: RunState, **overrides: Any) -> RunFacts:
    """A run the double knows about, resting in `state`."""
    run_id = overrides.pop("run_id", new_id())
    return RunFacts(
        run_id=run_id,
        name="cim-gbr-v1.0",
        state=state,
        submitter=overrides.pop("submitter", "operator@veldris.internal"),
        spec_hash="d" * 64,
        **overrides,
    )


@pytest.fixture
def installed() -> Iterator[FakeWriter]:
    """Install the double for one test, and take it out afterwards."""
    fake = FakeWriter()
    # No cast: the double satisfies the protocol, which is the point of
    # `Writer` being one. A cast here would hide a double that had drifted.
    writing.set_writer(fake)
    try:
        yield fake
    finally:
        writing.set_writer(writing.NoWriter())


@pytest.fixture(autouse=True)
def isolated_store() -> Iterator[None]:
    """A fresh idempotency store per test, so keys do not leak between them."""
    from draupnir.api.idempotency import IdempotencyStore

    original = deps.STORE
    deps.STORE = IdempotencyStore()
    try:
        yield
    finally:
        deps.STORE = original


def client() -> TestClient:
    """A client arriving as an approver who also holds operator and curator."""
    app = create_app()

    @app.middleware("http")
    async def inject(request: Any, call_next: Any) -> Any:
        request.state.claims = APPROVER
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=False)


def tag(state: dict[str, Any]) -> str:
    """The entity tag the handler will compute, from the real function."""
    return concurrency.etag(state)


def headers(state: dict[str, Any] | None = None) -> dict[str, str]:
    """An idempotency key, and the conditional tag where one is needed."""
    values = {"Idempotency-Key": str(uuid.uuid4())}
    if state is not None:
        values["If-Match"] = tag(state)
    return values


# ---------------------------------------------------------------------------
# Subjects that are not runs
# ---------------------------------------------------------------------------


def test_registering_a_source_appends_a_source_entry(installed: FakeWriter) -> None:
    response = client().post(
        "/v1/sources",
        json={
            "jurisdiction": "GBR",
            "url": "https://hansard.parliament.uk",
            "licenceSpdx": "CC-BY-4.0",
            "attributionRequired": True,
            "retrievedAt": "2026-03-02T09:00:00+00:00",
            "sha256": "a" * 64,
            "personalData": False,
        },
        headers=headers(),
    )

    assert response.status_code == 201, response.text
    assert [kind for kind, _, _ in installed.written] == ["source"]


def test_ingest_and_curate_append_corpus_entries(installed: FakeWriter) -> None:
    """A corpus is an input to many runs, so it is not one of them."""
    # Two clients rather than one as a context manager: entering the context
    # runs the application's lifespan, which installs the database writer over
    # the double. Found here, and it is the reason every test in this file
    # calls `client()` per request.
    assert client().post("/v1/corpora/GBR/ingest", headers=headers()).status_code == 202
    assert client().post("/v1/corpora/GBR/curate", headers=headers()).status_code == 202

    assert installed.written == [
        ("corpus", "GBR", "ingest-accepted"),
        ("corpus", "GBR", "curate-accepted"),
    ]


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def test_approving_a_gate_records_the_transition(installed: FakeWriter) -> None:
    facts = facts_at(RunState.AWAITING_APPROVAL)
    installed.facts[facts.run_id] = facts

    response = client().post(
        f"/v1/gates/{facts.run_id}/decide",
        json={"decision": "approved", "reason": "the gates pass", "signature": "sig"},
        headers=headers({"id": str(facts.run_id)}),
    )

    assert response.status_code == 201, response.text
    assert installed.written == [("run", str(facts.run_id), "AWAITING_APPROVAL->RELEASED")]
    assert installed.facts[facts.run_id].state is RunState.RELEASED


def test_the_sole_approver_exception_is_computed_not_supplied(installed: FakeWriter) -> None:
    """AC-S15 and constraint C-11. The submitter comes from the chain."""
    same = facts_at(RunState.AWAITING_APPROVAL, submitter="akuma")
    other = facts_at(RunState.AWAITING_APPROVAL, submitter="somebody-else")
    installed.facts.update({same.run_id: same, other.run_id: other})

    exception = (
        client()
        .post(
            f"/v1/gates/{same.run_id}/decide",
            json={"decision": "approved", "reason": "one identity", "signature": "sig"},
            headers=headers({"id": str(same.run_id)}),
        )
        .json()
    )
    separated = (
        client()
        .post(
            f"/v1/gates/{other.run_id}/decide",
            json={"decision": "approved", "reason": "two identities", "signature": "sig"},
            headers=headers({"id": str(other.run_id)}),
        )
        .json()
    )

    assert exception["soleApproverException"] is True
    assert separated["soleApproverException"] is False


def test_deciding_a_run_that_is_not_awaiting_approval_is_refused(installed: FakeWriter) -> None:
    """409, naming the state. The handler has no opinion about the lifecycle."""
    facts = facts_at(RunState.TRAINING)
    installed.facts[facts.run_id] = facts

    response = client().post(
        f"/v1/gates/{facts.run_id}/decide",
        json={"decision": "approved", "reason": "too early", "signature": "sig"},
        headers=headers({"id": str(facts.run_id)}),
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "gate-not-decidable"
    assert "TRAINING" in response.json()["detail"]
    assert installed.written == []


def test_deciding_a_run_this_site_does_not_hold_is_a_404(installed: FakeWriter) -> None:
    """A run at another site is not visible here (SAD 11C constraint 3)."""
    absent = new_id()

    response = client().post(
        f"/v1/gates/{absent}/decide",
        json={"decision": "approved", "reason": "nothing here", "signature": "sig"},
        headers=headers({"id": str(absent)}),
    )

    assert response.status_code == 404, response.text
    assert response.json()["code"] == "gate-not-found"


# ---------------------------------------------------------------------------
# Cancellation and requeue
# ---------------------------------------------------------------------------


def test_cancelling_a_training_run_moves_it_to_failed(installed: FakeWriter) -> None:
    """AC-F13: a cancelled scheduler job exits non-zero, which is FAILED."""
    facts = facts_at(RunState.TRAINING)
    installed.facts[facts.run_id] = facts

    response = client().post(
        f"/v1/runs/{facts.run_id}/cancel",
        json={"reason": "the corpus was wrong"},
        headers=headers({"id": str(facts.run_id)}),
    )

    assert response.status_code == 202, response.text
    assert installed.written == [("run", str(facts.run_id), "TRAINING->FAILED")]


def test_cancelling_a_queued_run_is_refused_with_the_gap_named(installed: FakeWriter) -> None:
    """SAD 6.1 has no transition out of QUEUED except to TRAINING."""
    facts = facts_at(RunState.QUEUED)
    installed.facts[facts.run_id] = facts

    response = client().post(
        f"/v1/runs/{facts.run_id}/cancel",
        json={"reason": "changed my mind"},
        headers=headers({"id": str(facts.run_id)}),
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "run-not-cancellable"
    assert "QUEUED" in body["detail"]
    assert "no transition" in body["detail"]


def test_requeueing_a_run_that_failed_a_gate_within_budget(installed: FakeWriter) -> None:
    facts = facts_at(RunState.EVALUATING, retry_budget=2, retry_count=0, failing_gates=("E3",))
    installed.facts[facts.run_id] = facts

    response = client().post(
        f"/v1/runs/{facts.run_id}/retry", headers=headers({"id": str(facts.run_id)})
    )

    assert response.status_code == 202, response.text
    assert installed.written == [("run", str(facts.run_id), "EVALUATING->QUEUED")]


def test_requeueing_with_the_budget_exhausted_is_refused(installed: FakeWriter) -> None:
    """The guard reads the budget from the chain, so this is the real refusal."""
    facts = facts_at(RunState.EVALUATING, retry_budget=2, retry_count=2, failing_gates=("E3",))
    installed.facts[facts.run_id] = facts

    response = client().post(
        f"/v1/runs/{facts.run_id}/retry", headers=headers({"id": str(facts.run_id)})
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "run-not-retryable"
    assert "0 of 2 retries remain" in body["detail"]


def test_requeueing_a_run_with_no_recorded_failure_is_refused(installed: FakeWriter) -> None:
    facts = facts_at(RunState.EVALUATING, retry_budget=2)
    installed.facts[facts.run_id] = facts

    response = client().post(
        f"/v1/runs/{facts.run_id}/retry", headers=headers({"id": str(facts.run_id)})
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "nothing-to-retry"
    assert installed.written == []


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


def test_publishing_without_an_approval_is_refused(installed: FakeWriter) -> None:
    """SAD 5.2 and AC-S5, and conditional rather than unconditional."""
    artefact = "9" * 64

    response = client().post(
        f"/v1/releases/{artefact}/publish", headers=headers({"artefact": artefact})
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "release-unapproved"
    assert installed.written == []


def test_publishing_an_approved_artefact_records_the_publication(
    installed: FakeWriter,
) -> None:
    """The approval permits it; the publication is a second event."""
    artefact = "7" * 64
    run_id = new_id()
    installed.approvals[artefact] = _entry(
        "run",
        str(run_id),
        "AWAITING_APPROVAL->RELEASED",
        {
            "approver": "akuma",
            "artefact_sha256": artefact,
            "model": "cim-gbr-v1.0",
            "formats": ["nvfp4", "mlx4"],
        },
    )

    response = client().post(
        f"/v1/releases/{artefact}/publish", headers=headers({"artefact": artefact})
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["model"] == "cim-gbr-v1.0"
    assert body["formats"] == ["nvfp4", "mlx4"]
    assert installed.written == [("release", artefact, "published")]


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def test_a_submission_records_the_run_and_its_retry_budget(installed: FakeWriter) -> None:
    """The budget is read from the specification at registration.

    Recorded there rather than taken from a later request, because a budget the
    caller supplies is a budget the caller can raise, one requeue at a time.
    """
    response = client().post(
        "/v1/runs", json={"specification": sample_spec().as_mapping()}, headers=headers()
    )

    assert response.status_code == 202, response.text
    assert [kind for kind, _, _ in installed.written] == ["run"]
    assert installed.written[0][2] == "->DRAFT"


def test_a_conditional_write_without_if_match_is_still_refused(installed: FakeWriter) -> None:
    """AC-B4. Wiring the write path did not loosen the precondition."""
    facts = facts_at(RunState.TRAINING)
    installed.facts[facts.run_id] = facts

    response = client().post(
        f"/v1/runs/{facts.run_id}/cancel",
        json={"reason": "no tag"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 428, response.text
    assert installed.written == []
