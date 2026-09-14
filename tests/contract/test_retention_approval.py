"""S06's primary action: approving a deletion. RF-27.

"Approve a retention action" had no operation, and the console's button closed
its dialog and did nothing else. This is the operation, and deletion is the one
action in the system that cannot be undone -- so every convention SAD 11E.2
puts on a mutating route is asserted here rather than assumed from the others:
a problem document for every refusal, an `Idempotency-Key` that replays, an
`If-Match` over state that can actually change, a role, a hardware factor, and
timestamps with an offset.

The double is a real `Writer` over a list of entries, answering the one
question the handler asks. The fold it reads through is HODD's, the same one
the read model and the worker use.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from draupnir.api import concurrency, deps, writing
from draupnir.api.app import create_app
from draupnir.core.domain.identifiers import new_id
from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry
from draupnir.hodd import retention

pytestmark = pytest.mark.contract

APPROVER = {
    "sub": "akuma",
    "iss": "https://megingjord.veldris.internal",
    "roles": ["approver"],
    "amr": ["pwd", "hwk"],
}
PASSWORD_ONLY = {**APPROVER, "amr": ["pwd"]}
OPERATOR = {**APPROVER, "sub": "operator-1", "roles": ["operator"]}

CORPUS = "c" * 64
RAW = "hodd://sindri/corpora/GBR/raw"


def _entry(
    seq: int,
    transition: str,
    payload: dict[str, Any],
    *,
    actor: str = "system:worker",
) -> LedgerEntry:
    return LedgerEntry(
        id=new_id(),
        site_id="sindri",
        seq=seq,
        prev_hash=GENESIS_HASH,
        entry_hash="a" * 64,
        ts=datetime.now(UTC),
        actor=actor,
        subject_type=retention.CORPUS_SUBJECT,
        subject_id=CORPUS,
        transition=transition,
        payload=payload,
    )


def proposal(*, due_in: timedelta = timedelta(days=-3)) -> LedgerEntry:
    """A proposal as the daily duty records one."""
    return _entry(
        1,
        retention.PROPOSED,
        {
            "corpusSha256": CORPUS,
            "dueAt": (datetime.now(UTC) + due_in).isoformat(),
            "releases": ["run-1"],
            "jurisdiction": "GBR",
            "artefact": RAW,
        },
    )


class ChainWriter:
    """A writer whose chain is a list."""

    def __init__(self, *entries: LedgerEntry) -> None:
        self.entries = list(entries)

    @property
    def records(self) -> bool:
        return True

    async def register_run(self, **_: Any) -> Any:
        raise AssertionError("an approval registers no run")

    async def transition_run(self, **_: Any) -> Any:
        raise AssertionError("an approval moves no run")

    async def record(self, **kwargs: Any) -> LedgerEntry:
        entry = _entry(
            len(self.entries) + 1, kwargs["transition"], kwargs["payload"], actor=kwargs["actor"]
        )
        self.entries.append(entry)
        return entry

    async def read(self, *, site_id: str, actor: str, question: Any) -> Any:
        del site_id, actor
        return question(self)

    def entries_of_type(self, subject_type: str) -> tuple[LedgerEntry, ...]:
        return tuple(item for item in self.entries if item.subject_type == subject_type)

    @property
    def approvals(self) -> list[LedgerEntry]:
        return [item for item in self.entries if item.transition == retention.APPROVED]


def install(writer: ChainWriter) -> Iterator[ChainWriter]:
    writing.set_writer(writer)
    try:
        yield writer
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


@pytest.fixture
def due() -> Iterator[ChainWriter]:
    yield from install(ChainWriter(proposal()))


def client(claims: dict[str, Any] = APPROVER) -> TestClient:
    app = create_app()

    @app.middleware("http")
    async def inject(request: Any, call_next: Any) -> Any:
        request.state.claims = claims
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=False)


def current_tag(writer: ChainWriter) -> str:
    """The entity tag the list would show for the one action, from the real fold."""
    (action,) = retention.fold(writer.entries)
    return concurrency.etag(action.version())


def approve(
    writer: ChainWriter,
    *,
    claims: dict[str, Any] = APPROVER,
    key: str | None = "fresh",
    if_match: str | None = "current",
) -> Any:
    headers: dict[str, str] = {}
    if key is not None:
        headers["Idempotency-Key"] = str(uuid.uuid4()) if key == "fresh" else key
    if if_match is not None:
        headers["If-Match"] = current_tag(writer) if if_match == "current" else if_match
    action = writer.entries[0].id
    return client(claims).post(f"/v1/retention/{action}/approve", headers=headers)


# ---------------------------------------------------------------------------
# The action
# ---------------------------------------------------------------------------


def test_an_approver_approves_and_the_approval_is_recorded(due: ChainWriter) -> None:
    before = current_tag(due)

    response = approve(due)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "APPROVED"
    assert body["approvedBy"] == "akuma"
    assert response.headers["etag"] == body["etag"] != before
    (approval,) = due.approvals
    assert approval.payload[retention.ANSWERS] == 1
    assert approval.payload["artefact"] == RAW


def test_an_approval_deletes_nothing_itself(due: ChainWriter) -> None:
    """The worker carries it out and records the outcome. The request records a decision."""
    approve(due)

    assert [item.transition for item in due.entries] == [retention.PROPOSED, retention.APPROVED]


def test_timestamps_carry_an_offset(due: ChainWriter) -> None:
    """SAD 11E.2: no naive timestamps anywhere."""
    due_at = approve(due).json()["dueAt"]

    assert datetime.fromisoformat(due_at).tzinfo is not None


# ---------------------------------------------------------------------------
# SAD 11E.2, each refusal a problem document and nothing recorded
# ---------------------------------------------------------------------------


def _refused(response: Any, status: int, code: str, writer: ChainWriter) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == code
    assert writer.approvals == [], "a refused approval was recorded"


def test_an_approval_without_an_idempotency_key_is_refused(due: ChainWriter) -> None:
    _refused(approve(due, key=None), 428, "idempotency-key-required", due)


def test_an_approval_without_if_match_is_refused(due: ChainWriter) -> None:
    _refused(approve(due, if_match=None), 428, "precondition-required", due)


def test_an_approval_against_a_state_that_has_moved_on_is_refused() -> None:
    """Somebody else approved it after this approver read it. 412, not a second approval."""
    stale = concurrency.etag(retention.fold([proposal()])[0].version())
    writer = ChainWriter(proposal())
    writer.entries.append(
        _entry(2, retention.APPROVED, {retention.ANSWERS: 1}, actor="someone-else")
    )
    for _ in install(writer):
        response = approve(writer, if_match=stale)

        assert response.status_code == 412, response.text
        assert response.json()["code"] == "precondition-failed"
        assert len(writer.approvals) == 1


def test_an_approved_action_is_not_approved_twice() -> None:
    writer = ChainWriter(proposal())
    writer.entries.append(_entry(2, retention.APPROVED, {retention.ANSWERS: 1}))
    for _ in install(writer):
        response = approve(writer)

        assert response.status_code == 409
        assert response.json()["code"] == "retention-not-approvable"
        assert len(writer.approvals) == 1


def test_a_refused_action_may_be_approved_again() -> None:
    writer = ChainWriter(proposal())
    writer.entries.append(_entry(2, retention.APPROVED, {retention.ANSWERS: 1}))
    writer.entries.append(
        _entry(3, retention.REFUSED, {retention.ANSWERS: 1, "reason": "no store"})
    )
    for _ in install(writer):
        assert approve(writer).status_code == 200


def test_an_action_not_yet_due_is_refused() -> None:
    writer = ChainWriter(proposal(due_in=timedelta(days=30)))
    for _ in install(writer):
        _refused(approve(writer), 409, "retention-not-due", writer)


def test_an_unknown_action_is_404(due: ChainWriter) -> None:
    response = client().post(
        f"/v1/retention/{new_id()}/approve",
        headers={"Idempotency-Key": "k", "If-Match": "*"},
    )

    _refused(response, 404, "retention-action-not-found", due)


def test_an_operator_may_not_approve_a_deletion(due: ChainWriter) -> None:
    response = approve(due, claims=OPERATOR)

    assert response.status_code == 403
    assert due.approvals == []


def test_a_password_alone_does_not_approve_a_deletion(due: ChainWriter) -> None:
    """AC-S15's hardware factor, applied to the action that cannot be undone."""
    response = approve(due, claims=PASSWORD_ONLY)

    assert response.status_code == 403
    assert "hardware" in response.text
    assert due.approvals == []


def test_a_replayed_approval_returns_the_original_and_records_once(due: ChainWriter) -> None:
    """The response was lost and the console retried. One decision, not two."""
    key = str(uuid.uuid4())
    tag = current_tag(due)

    first = approve(due, key=key, if_match=tag)
    second = approve(due, key=key, if_match=tag)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(due.approvals) == 1


def test_the_operation_is_documented_as_conditional_and_approver_only() -> None:
    operation = create_app().openapi()["paths"]["/v1/retention/{action_id}/approve"]["post"]

    headers = {item["name"] for item in operation["parameters"] if item["in"] == "header"}
    assert {"If-Match", "Idempotency-Key"} <= headers
    assert "Requires: `approver`" in operation["description"]
    assert "default" in operation["responses"]
