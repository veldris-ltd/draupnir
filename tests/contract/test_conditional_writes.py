"""Every conditional write is conditional on the state it would change. RF-32.

`cancelRun`, `retryRun`, `decideGate` and `publishRelease` checked `If-Match`
against a tag computed over the identifier alone, which never changes. `getRun`
returned a tag over the identifier and the state, and the gate and release reads
returned none. So the tag a client was given was refused with 412, a client that
sent none was refused with 428, and any tag over the identifier passed -- which
can never be stale. AC-B4's "a stale write returns 412" was unreachable by a
client that behaved correctly, and the console sent no tag at all.

Each test below does what a client does: it reads, takes the tag the read
returned, lets the state move under it, and shows that the write with the old
tag is refused with 412 -- and that the same tag was accepted while it was
current.

The doubles share one idea of the chain, so the read and the write disagree only
if the handlers compute different tags from the same state.
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
from draupnir.api.reading import EmptyReadModel
from draupnir.api.schemas import ApprovalItem, ApprovalPage, LineageOut, RunOut
from draupnir.core.application.orchestrator import RunFacts
from draupnir.core.domain.identifiers import new_id
from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry
from draupnir.core.domain.states import RunState

pytestmark = pytest.mark.contract

CALLER = {
    "sub": "akuma",
    "iss": "https://megingjord.veldris.internal",
    "roles": ["approver", "operator", "curator"],
    "amr": ["pwd", "hwk"],
}

RUN = UUID("019cf270-ba80-76c9-84ca-7374e16c7631")
ARTEFACT = "7" * 64
AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


class Chain:
    """What both doubles agree the chain holds, and what a test moves."""

    def __init__(self, state: RunState) -> None:
        self.state = state
        self.retry_count = 0
        self.approval_seq: int | None = 5
        self.published_seq: int | None = None


def _entry(seq: int, subject_type: str, subject_id: str, transition: str) -> LedgerEntry:
    return LedgerEntry(
        id=new_id(),
        site_id="sindri",
        seq=seq,
        prev_hash=GENESIS_HASH,
        entry_hash="a" * 64,
        ts=AT,
        actor="someone-else",
        subject_type=subject_type,
        subject_id=subject_id,
        transition=transition,
        payload={"artefact_sha256": ARTEFACT},
    )


class Reader(EmptyReadModel):
    """The reads an operator acts from, over the shared chain."""

    def __init__(self, chain: Chain) -> None:
        self.chain = chain

    async def run(self, site_id: str, run_id: UUID) -> RunOut | None:
        del site_id
        return RunOut(
            id=run_id,
            site_id="sindri",
            name="cim-gbr-v1.0",
            state=self.chain.state,
            spec_hash="d" * 64,
            retry_count=self.chain.retry_count,
        )

    async def approvals(self, site_id: str, *, limit: int, cursor: str | None) -> ApprovalPage:
        del site_id, cursor
        items = (
            [
                ApprovalItem(
                    id=RUN,
                    run_id=RUN,
                    model="cim-gbr-v1.0",
                    artefact_sha256=ARTEFACT,
                    submitted_by="operator@veldris.internal",
                    awaiting_since=AT,
                    retry_count=self.chain.retry_count,
                )
            ]
            if self.chain.state is RunState.AWAITING_APPROVAL
            else []
        )
        return ApprovalPage(items=items, next_cursor=None, limit=limit)

    async def lineage(self, site_id: str, artefact: str) -> LineageOut | None:
        del site_id
        return LineageOut(artefact=artefact, complete=True)


class Writer:
    """A writer over the shared chain, answering the questions the handlers ask."""

    def __init__(self, chain: Chain) -> None:
        self.chain = chain

    @property
    def records(self) -> bool:
        return True

    async def register_run(self, **_: Any) -> Any:
        raise AssertionError("no conditional write registers a run")

    async def transition_run(self, **kwargs: Any) -> None:
        self.chain.state = kwargs["target"]

    async def record(self, **kwargs: Any) -> None:
        if kwargs["transition"] == writing.PUBLISHED:
            self.chain.published_seq = (self.chain.published_seq or 10) + 1

    async def read(self, *, site_id: str, actor: str, question: Any) -> Any:
        del site_id, actor
        return question(self)

    # The orchestrator's questions, answered from the shared chain.

    def facts_of(self, run_id: UUID) -> RunFacts:
        return RunFacts(
            run_id=run_id,
            name="cim-gbr-v1.0",
            state=self.chain.state,
            submitter="operator@veldris.internal",
            spec_hash="d" * 64,
            retry_count=self.chain.retry_count,
            retry_budget=3,
            failing_gates=("E3",),
        )

    def released_entry_for(self, artefact_sha256: str) -> LedgerEntry | None:
        if self.chain.approval_seq is None:
            return None
        return _entry(self.chain.approval_seq, "run", str(RUN), "AWAITING_APPROVAL->RELEASED")

    def entries_of_type(self, subject_type: str) -> tuple[LedgerEntry, ...]:
        if subject_type != writing.RELEASE_SUBJECT or self.chain.published_seq is None:
            return ()
        return (_entry(self.chain.published_seq, subject_type, ARTEFACT, writing.PUBLISHED),)

    def publication_facts(self, artefact_sha256: str) -> None:
        """No publication facts: the precondition is what is under test, not admission."""
        del artefact_sha256


def install(state: RunState) -> Iterator[Chain]:
    chain = Chain(state)
    original = deps.READER
    deps.set_reader(Reader(chain))
    writing.set_writer(Writer(chain))
    try:
        yield chain
    finally:
        deps.set_reader(original)
        writing.set_writer(writing.NoWriter())


@pytest.fixture
def training() -> Iterator[Chain]:
    yield from install(RunState.TRAINING)


@pytest.fixture
def evaluating() -> Iterator[Chain]:
    yield from install(RunState.EVALUATING)


@pytest.fixture
def awaiting() -> Iterator[Chain]:
    yield from install(RunState.AWAITING_APPROVAL)


def client() -> TestClient:
    app = create_app()

    @app.middleware("http")
    async def inject(request: Any, call_next: Any) -> Any:
        request.state.claims = CALLER
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=False)


def write_headers(tag: str) -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4()), "If-Match": tag}


def test_the_run_read_returns_one_tag_in_the_header_and_the_body(training: Chain) -> None:
    """The console reads bodies, so the tag has to be in the body as well."""
    del training
    response = client().get(f"/v1/runs/{RUN}")

    assert response.status_code == 200, response.text
    assert response.headers["ETag"] == response.json()["etag"]


def test_a_cancel_with_the_tag_the_run_read_returned_is_refused_once_stale(
    training: Chain,
) -> None:
    tag = client().get(f"/v1/runs/{RUN}").json()["etag"]

    accepted = client().post(
        f"/v1/runs/{RUN}/cancel", json={"reason": "wrong corpus"}, headers=write_headers(tag)
    )
    # The run has moved to FAILED under the first cancel; a second operator who
    # read it at TRAINING is told so rather than cancelling again.
    stale = client().post(
        f"/v1/runs/{RUN}/cancel", json={"reason": "wrong corpus"}, headers=write_headers(tag)
    )

    assert accepted.status_code == 202, accepted.text
    assert training.state is RunState.FAILED
    assert stale.status_code == 412, stale.text
    assert stale.json()["code"] == "precondition-failed"


def test_a_retry_read_before_somebody_else_requeued_is_refused(evaluating: Chain) -> None:
    """The retry count, not only the state: the run is back at EVALUATING."""
    tag = client().get(f"/v1/runs/{RUN}").json()["etag"]

    # Somebody else requeued it, and it was evaluated again.
    evaluating.retry_count = 1
    stale = client().post(f"/v1/runs/{RUN}/retry", headers=write_headers(tag))

    fresh = client().get(f"/v1/runs/{RUN}").json()["etag"]
    accepted = client().post(f"/v1/runs/{RUN}/retry", headers=write_headers(fresh))

    assert stale.status_code == 412, stale.text
    assert accepted.status_code == 202, accepted.text


def test_a_decision_read_from_the_queue_is_refused_once_somebody_else_decided(
    awaiting: Chain,
) -> None:
    items = client().get("/v1/gates", params={"state": "pending", "limit": 10}).json()["items"]
    tag = next(item for item in items if item["id"] == str(RUN))["etag"]
    rejection = {"decision": "rejected", "reason": "no DPIA reference", "signature": "s"}

    accepted = client().post(f"/v1/gates/{RUN}/decide", json=rejection, headers=write_headers(tag))
    stale = client().post(f"/v1/gates/{RUN}/decide", json=rejection, headers=write_headers(tag))

    assert accepted.status_code == 201, accepted.text
    assert awaiting.state is RunState.QUARANTINED
    assert stale.status_code == 412, stale.text


def test_a_publication_read_before_somebody_else_published_is_refused(awaiting: Chain) -> None:
    response = client().get(f"/v1/lineage/{ARTEFACT}")
    tag = response.json()["etag"]
    assert response.headers["ETag"] == tag

    current = client().post(f"/v1/releases/{ARTEFACT}/publish", headers=write_headers(tag))
    # Somebody else published it meanwhile.
    awaiting.published_seq = 11
    stale = client().post(f"/v1/releases/{ARTEFACT}/publish", headers=write_headers(tag))

    # While current the precondition passed, and the publication was then
    # refused for a reason of its own: these doubles hold no publication facts.
    assert current.status_code not in (412, 428), current.text
    assert stale.status_code == 412, stale.text


@pytest.mark.parametrize(
    ("method_path", "body"),
    [
        (f"/v1/runs/{RUN}/cancel", {"reason": "x"}),
        (f"/v1/runs/{RUN}/retry", None),
    ],
)
def test_a_tag_over_the_identifier_alone_no_longer_passes(
    training: Chain, method_path: str, body: dict[str, str] | None
) -> None:
    """The tag every test and no client used to send."""
    del training
    identifier_only = concurrency.etag({"id": str(RUN)})

    response = client().post(method_path, json=body, headers=write_headers(identifier_only))

    assert response.status_code == 412, response.text
