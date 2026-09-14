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
from tests.specs import submittable_mapping

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
        #: Where the chain says each artefact's bytes are. Empty means nothing
        #: recorded a location, which the publication path refuses (RF-05).
        self.artefact_uris: dict[str, str] = {}
        self.written: list[tuple[str, str, str]] = []
        #: What `record` appended, so a question about releases can be answered.
        self.recorded: list[LedgerEntry] = []

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
        entry = _entry(kwargs["subject_type"], kwargs["subject_id"], kwargs["transition"], {})
        self.recorded.append(entry)
        return entry

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

    def entries_of_type(self, subject_type: str) -> tuple[LedgerEntry, ...]:
        """What this double recorded about subjects of one type. RF-32 asks for releases."""
        return tuple(entry for entry in self.recorded if entry.subject_type == subject_type)

    def publication_facts(self, artefact_sha256: str) -> Any:
        """Everything a publication is decided against. RF-05.

        This double answers with an approval and no artefact location, which
        is the state a chain is in when nothing recorded where the bytes went.
        The handler refuses that — correctly — so the tests below that expect a
        publication to succeed carry a URI, and the ones that expect a refusal
        do not have to.
        """
        from draupnir.core.application.orchestrator import PublicationFacts
        from draupnir.core.domain.evidence import EvidenceLog

        approval = self.approvals.get(artefact_sha256)
        if approval is None:
            return None
        return PublicationFacts(
            approval=approval,
            evidence=EvidenceLog(),
            built_formats=(),
            artefact_uri=self.artefact_uris.get(artefact_sha256, ""),
            release_seq=approval.seq,
            anchored_through=approval.seq,
        )

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


def run_headers(facts: RunFacts) -> dict[str, str]:
    """An idempotency key, and the tag `getRun` gives for this run. RF-32.

    These tests sent a tag over the identifier alone, because that is what the
    handlers checked, and a client could never have been given one.
    """
    return headers(concurrency.run_version(facts.run_id, facts.state, facts.retry_count))


def release_headers(fake: FakeWriter, artefact: str) -> dict[str, str]:
    """An idempotency key, and the tag `getLineage` gives. RF-32.

    Through the real question, asked of this double as the handler asks it.
    """
    question: Any = writing.publication_version_of(artefact)
    return headers(question(fake))


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


def test_the_accepted_entries_are_the_ones_the_worker_drains(installed: FakeWriter) -> None:
    """The two halves of RF-12, joined.

    The handler records and the worker consumes, and they are in different
    deployable units -- so the transition string is the whole of the contract
    between them. This asserts the handler writes the one the queue reads,
    because a rename on either side would leave a curator pressing Ingest, a
    202 coming back, and nothing ever happening: which is what the finding was.
    """
    from draupnir.worker import corpora

    assert client().post("/v1/corpora/GBR/ingest", headers=headers()).status_code == 202
    assert client().post("/v1/corpora/GBR/curate", headers=headers()).status_code == 202

    subjects = {kind for kind, _, _ in installed.written}
    transitions = [transition for _, _, transition in installed.written]

    assert subjects == {corpora.CORPUS_SUBJECT}
    assert transitions == [corpora.INGEST_ACCEPTED, corpora.CURATE_ACCEPTED]


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def test_approving_a_gate_records_the_transition(installed: FakeWriter) -> None:
    facts = facts_at(RunState.AWAITING_APPROVAL)
    installed.facts[facts.run_id] = facts

    response = client().post(
        f"/v1/gates/{facts.run_id}/decide",
        json=_approval("the gates pass", facts.run_id),
        headers=run_headers(facts),
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
            json=_approval("one identity", same.run_id, exception=True),
            headers=run_headers(same),
        )
        .json()
    )
    separated = (
        client()
        .post(
            f"/v1/gates/{other.run_id}/decide",
            json=_approval("two identities", other.run_id),
            headers=run_headers(other),
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
        json=_approval("too early", facts.run_id),
        headers=run_headers(facts),
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
        json=_approval("nothing here", absent),
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
        headers=run_headers(facts),
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
        headers=run_headers(facts),
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "run-not-cancellable"
    assert "QUEUED" in body["detail"]
    assert "no transition" in body["detail"]


def test_requeueing_a_run_that_failed_a_gate_within_budget(installed: FakeWriter) -> None:
    facts = facts_at(RunState.EVALUATING, retry_budget=2, retry_count=0, failing_gates=("E3",))
    installed.facts[facts.run_id] = facts

    response = client().post(f"/v1/runs/{facts.run_id}/retry", headers=run_headers(facts))

    assert response.status_code == 202, response.text
    assert installed.written == [("run", str(facts.run_id), "EVALUATING->QUEUED")]


def test_requeueing_with_the_budget_exhausted_is_refused(installed: FakeWriter) -> None:
    """The guard reads the budget from the chain, so this is the real refusal."""
    facts = facts_at(RunState.EVALUATING, retry_budget=2, retry_count=2, failing_gates=("E3",))
    installed.facts[facts.run_id] = facts

    response = client().post(f"/v1/runs/{facts.run_id}/retry", headers=run_headers(facts))

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "run-not-retryable"
    assert "0 of 2 retries remain" in body["detail"]


def test_requeueing_a_run_with_no_recorded_failure_is_refused(installed: FakeWriter) -> None:
    facts = facts_at(RunState.EVALUATING, retry_budget=2)
    installed.facts[facts.run_id] = facts

    response = client().post(f"/v1/runs/{facts.run_id}/retry", headers=run_headers(facts))

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
        f"/v1/releases/{artefact}/publish", headers=release_headers(installed, artefact)
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "release-unapproved"
    assert installed.written == []


def test_publishing_refuses_when_the_chain_records_no_artefact_location(
    installed: FakeWriter,
) -> None:
    """RF-05. An approval alone is no longer enough to publish.

    This test used to assert a 202 here, and that was the finding: the handler
    read one entry from the chain and, if it existed, recorded a `published`
    entry. Its own docstring described four controls -- AC-S8's re-hash, AC-F9's
    per-format evidence, the approval signature and AC-S13's anchor -- and
    called none of them.

    Now the first of those applies. Nothing recorded where these bytes are, so
    they cannot be re-hashed, so the release is not admitted. Building a path
    from a naming convention instead would hash whatever happened to be there,
    which is the opposite of the control.

    The admitted path needs a real store and a real artefact, so it lives in
    `tests/integration/test_api_writes.py`.
    """
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
            "signature": "a" * 64,
            "decision": "approved",
        },
    )

    response = client().post(
        f"/v1/releases/{artefact}/publish", headers=release_headers(installed, artefact)
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "release-inadmissible"
    assert "re-hashed" in body["detail"]


def test_a_refused_publication_records_nothing(installed: FakeWriter) -> None:
    """A refusal must leave the chain exactly as it was.

    Otherwise the refusal is itself an event somebody has to explain, and the
    ledger stops being a record of what happened to releases and becomes a
    record of what was attempted.
    """
    artefact = "7" * 64
    installed.approvals[artefact] = _entry(
        "run",
        str(new_id()),
        "AWAITING_APPROVAL->RELEASED",
        {"approver": "akuma", "artefact_sha256": artefact, "signature": "a" * 64},
    )
    before = list(installed.written)

    client().post(f"/v1/releases/{artefact}/publish", headers=release_headers(installed, artefact))

    assert installed.written == before, "a refused publication wrote to the chain"


def test_a_submission_records_the_run_and_its_retry_budget(installed: FakeWriter) -> None:
    """The budget is read from the specification at registration.

    Recorded there rather than taken from a later request, because a budget the
    caller supplies is a budget the caller can raise, one requeue at a time.
    """
    response = client().post(
        "/v1/runs", json={"specification": submittable_mapping()}, headers=headers()
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


def _approval(reason: str, subject_id: Any, *, exception: bool = False) -> dict[str, Any]:
    """A decision body carrying a signature that actually verifies. RF-06."""
    from datetime import UTC, datetime

    from tests.conftest import sign_decision

    decided_at = datetime.now(UTC)
    return {
        "decision": "approved",
        "reason": reason,
        "decidedAt": decided_at.isoformat(),
        "signature": sign_decision(
            approver="akuma",
            subject_id=subject_id,
            decided_at=decided_at,
            sole_approver_exception=exception,
        ),
    }


def test_signing_a_payload_that_omits_the_exception_is_refused(installed: FakeWriter) -> None:
    """RF-06's sharpest case, and the one the whole arrangement exists for.

    The approver here *is* the submitter, so the sole-approver exception
    applies. They sign a payload claiming it does not — which is precisely the
    edit constraint C-11 forbids — and the signature no longer covers what the
    server computed, so it does not verify.

    The exception is computed from the chain and is inside the signed bytes.
    Neither half works alone: computing it without signing it would let a
    replayed signature carry the wrong flag, and signing it without computing
    it would let the approver choose.
    """
    facts = facts_at(RunState.AWAITING_APPROVAL, submitter="akuma")
    installed.facts[facts.run_id] = facts

    response = client().post(
        f"/v1/gates/{facts.run_id}/decide",
        json=_approval("suppressing the exception", facts.run_id, exception=False),
        headers=run_headers(facts),
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "approval-signature-invalid"
    assert installed.written == [], "a refused decision moved the run"


def test_a_decision_with_no_instant_is_refused(installed: FakeWriter) -> None:
    """`decidedAt` is inside the signed payload, so it has to be supplied.

    The first version of this endpoint generated the instant server-side, which
    meant no client could ever produce a signature that verified.
    """
    facts = facts_at(RunState.AWAITING_APPROVAL)
    installed.facts[facts.run_id] = facts

    response = client().post(
        f"/v1/gates/{facts.run_id}/decide",
        json={"decision": "approved", "reason": "undated", "signature": "a" * 64},
        headers=run_headers(facts),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "decision-undated"


def test_a_decision_dated_far_from_now_is_refused(installed: FakeWriter) -> None:
    """A signature prepared long in advance, or replayed long afterwards."""
    from datetime import UTC, datetime, timedelta

    from tests.conftest import sign_decision

    facts = facts_at(RunState.AWAITING_APPROVAL)
    installed.facts[facts.run_id] = facts
    stale = datetime.now(UTC) - timedelta(hours=2)

    response = client().post(
        f"/v1/gates/{facts.run_id}/decide",
        json={
            "decision": "approved",
            "reason": "prepared earlier",
            "decidedAt": stale.isoformat(),
            "signature": sign_decision(approver="akuma", subject_id=facts.run_id, decided_at=stale),
        },
        headers=run_headers(facts),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "decision-stale"


# ---------------------------------------------------------------------------
# Arrays. RF-13.
# ---------------------------------------------------------------------------


def test_submitting_an_array_records_one_accepted_entry(installed: FakeWriter) -> None:
    """One entry for the array, not fifty-six for its elements.

    The array is the subject: fifty-six entries would be fifty-six things to
    read back and join up, and the thing an operator asks about is the array.
    """
    from draupnir.motsognir import arrays

    response = client().post("/v1/arrays", json={}, headers=headers())

    assert response.status_code == 202, response.text
    assert installed.written == [("array", "cim-56-adapters", arrays.ARRAY_ACCEPTED)]


def test_an_array_over_a_jurisdiction_outside_the_programme_is_refused(
    installed: FakeWriter,
) -> None:
    """RF-11's rule, applied to every element before any of them is submitted.

    An element for a jurisdiction nobody assigned would train a fifty-seventh
    model against whichever base a defaulted tier named.
    """
    response = client().post("/v1/arrays", json={"subjects": ["GBR", "IRL"]}, headers=headers())

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "jurisdiction-unassigned"
    assert "IRL" in response.json()["detail"]
    assert installed.written == [], "a refused array submission wrote to the chain"


def test_requeueing_an_element_records_a_request_for_that_element(
    installed: FakeWriter,
) -> None:
    """S12's primary action, which had no operation at all (RF-13).

    The index is in the entry, so what the worker does is bounded by what was
    asked for: a requeue that recorded only "requeue this array" would leave
    the worker choosing which element, and there is no correct choice.
    """
    from draupnir.motsognir import arrays

    response = client().post("/v1/arrays/cim-56-adapters/elements/17/requeue", headers=headers())

    assert response.status_code == 202, response.text
    assert installed.written == [("array", "cim-56-adapters", arrays.ELEMENT_REQUEUE_ACCEPTED)]


def test_an_array_submission_replays_rather_than_submitting_twice(
    installed: FakeWriter,
) -> None:
    """Fifty-six elements are most of a week of compute.

    A console that retried a request whose response was lost must not queue the
    array again.
    """
    key = {"Idempotency-Key": "array-1"}

    first = client().post("/v1/arrays", json={}, headers=key)
    second = client().post("/v1/arrays", json={}, headers=key)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json() == first.json()
    assert len(installed.written) == 1, "a replayed submission queued the array twice"
