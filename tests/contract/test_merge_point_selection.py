"""S15's primary action: choosing a merge point. RF-27.

"Select a merge point" had no operation, and the screen compared five points
invented from one run's gate values and called the first that passed selected.
This is the operation. What is asserted is that it chooses only from what the
sweep recorded, only a point RAUN passed, only once, and under every
convention SAD 11E.2 puts on a mutating route.

The double is a real `Writer` over a list of entries, answering the two
questions the handler asks. The fold it reads through is BRISINGAMEN's, the
same one the read model and the worker use.
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
from draupnir.brisingamen import sweep as sweeps
from draupnir.core.application.orchestrator import RunFacts, UnknownRunError
from draupnir.core.domain.evidence import Evidence
from draupnir.core.domain.identifiers import new_id
from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry
from draupnir.core.domain.states import RunState
from draupnir.interfaces.types import GateOutcome

pytestmark = pytest.mark.contract

OPERATOR = {
    "sub": "operator-1",
    "iss": "https://megingjord.veldris.internal",
    "roles": ["operator"],
    "amr": ["pwd"],
}
VIEWER = {**OPERATOR, "sub": "viewer-1", "roles": ["viewer"]}

RUN = UUID("019cf270-ba80-76c9-84ca-7374e16c7630")
AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def _evaluated() -> sweeps.Sweep:
    """Five points, the first and last failing a blocking gate."""
    current = sweeps.linear(method="slerp", base_sha256="b" * 64, adapter_sha256="a" * 64)
    for index, point in enumerate(current.points, start=1):
        passed = index not in {1, 5}
        digest = f"{index:x}" * 64
        current = current.with_result(
            point.parameters,
            artefact_sha256=digest,
            evidence=Evidence(
                artefact_sha256=digest,
                artefact_kind="merged",
                outcomes=(
                    GateOutcome(
                        gate="E1",
                        suite_version="2026.01",
                        value=0.70 + index / 100,
                        baseline_value=0.72,
                        margin=round(0.70 + index / 100 - 0.72, 6),
                        passed=passed,
                    ),
                ),
                passed=passed,
                suite="general-core",
                suite_version="2026.01",
                evaluated_at=AT,
                measurements={"E1": 0.70 + index / 100},
            ),
        )
    return current


SWEEP = _evaluated()


def _entry(seq: int, transition: str, payload: dict[str, Any]) -> LedgerEntry:
    return LedgerEntry(
        id=new_id(),
        site_id="sindri",
        seq=seq,
        prev_hash=GENESIS_HASH,
        entry_hash="a" * 64,
        ts=AT,
        actor="system:worker",
        subject_type=sweeps.SWEEP_SUBJECT,
        subject_id=str(RUN),
        transition=transition,
        payload=payload,
    )


class RunWriter:
    """A writer holding one run's facts and its entries."""

    def __init__(
        self, *, state: RunState = RunState.MERGED, evaluated: bool = True, known: bool = True
    ) -> None:
        self.state = state
        self.known = known
        self.entries: list[LedgerEntry] = (
            [_entry(1, sweeps.EVALUATED, sweeps.record(SWEEP))] if evaluated else []
        )

    @property
    def records(self) -> bool:
        return True

    async def register_run(self, **_: Any) -> Any:
        raise AssertionError("a selection registers no run")

    async def transition_run(self, **_: Any) -> Any:
        raise AssertionError("a selection moves no run; the worker quantises")

    async def record(self, **kwargs: Any) -> LedgerEntry:
        entry = _entry(len(self.entries) + 1, kwargs["transition"], kwargs["payload"])
        self.entries.append(entry)
        return entry

    async def read(self, *, site_id: str, actor: str, question: Any) -> Any:
        del site_id, actor
        return question(self)

    def facts_of(self, run_id: UUID) -> RunFacts:
        if not self.known:
            raise UnknownRunError(run_id, "sindri")
        return RunFacts(
            run_id=run_id,
            name="cim-gbr-v0.2",
            state=self.state,
            submitter="operator@veldris.internal",
            spec_hash="d" * 64,
        )

    def history(self, run_id: UUID) -> tuple[LedgerEntry, ...]:
        del run_id
        return tuple(self.entries)

    @property
    def selections(self) -> list[LedgerEntry]:
        return [item for item in self.entries if item.transition == sweeps.SELECTED]


def install(writer: RunWriter) -> Iterator[RunWriter]:
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
def merged() -> Iterator[RunWriter]:
    yield from install(RunWriter())


def client(claims: dict[str, Any] = OPERATOR) -> TestClient:
    app = create_app()

    @app.middleware("http")
    async def inject(request: Any, call_next: Any) -> Any:
        request.state.claims = claims
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=False)


def current_tag(writer: RunWriter) -> str:
    return concurrency.etag(sweeps.version(sweeps.fold(writer.entries)))


def choose(
    writer: RunWriter,
    point: int,
    *,
    claims: dict[str, Any] = OPERATOR,
    key: str | None = "fresh",
    if_match: str | None = "current",
    parameters: dict[str, float] | None = None,
) -> Any:
    headers: dict[str, str] = {}
    if key is not None:
        headers["Idempotency-Key"] = str(uuid.uuid4()) if key == "fresh" else key
    if if_match is not None:
        headers["If-Match"] = current_tag(writer) if if_match == "current" else if_match
    chosen = parameters if parameters is not None else dict(SWEEP.points[point].parameters)
    return client(claims).post(
        f"/v1/sweeps/{RUN}/select", json={"parameters": chosen}, headers=headers
    )


def _refused(response: Any, status: int, code: str, writer: RunWriter) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == code
    assert writer.selections == [], "a refused choice was recorded"


# ---------------------------------------------------------------------------
# The choice
# ---------------------------------------------------------------------------


def test_an_operator_chooses_a_passing_point_and_the_choice_is_recorded(
    merged: RunWriter,
) -> None:
    before = current_tag(merged)
    wanted = SWEEP.points[2]

    response = choose(merged, 2)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["selected"] == wanted.label
    assert body["selectedParameters"] == dict(wanted.parameters)
    assert body["evaluated"] is True
    assert response.headers["etag"] == body["etag"] != before
    (selection,) = merged.selections
    assert selection.payload["configHash"] == wanted.config_hash()
    assert selection.payload["artefactSha256"] == wanted.artefact_sha256


def test_the_points_shown_are_the_sweeps_own(merged: RunWriter) -> None:
    """Measured, not scaled from one run's gates."""
    body = choose(merged, 2).json()

    assert [point["label"] for point in body["points"]] == [p.label for p in SWEEP.points]
    assert [point["passed"] for point in body["points"]] == [False, True, True, True, False]


# ---------------------------------------------------------------------------
# Refusals, each a problem document and nothing recorded
# ---------------------------------------------------------------------------


def test_a_choice_without_an_idempotency_key_is_refused(merged: RunWriter) -> None:
    _refused(choose(merged, 2, key=None), 428, "idempotency-key-required", merged)


def test_a_choice_without_if_match_is_refused(merged: RunWriter) -> None:
    _refused(choose(merged, 2, if_match=None), 428, "precondition-required", merged)


def test_a_choice_made_from_a_sweep_that_has_moved_on_is_refused() -> None:
    """Somebody chose after this operator read the matrix. 412, not a second choice."""
    stale = concurrency.etag(sweeps.version(SWEEP))
    writer = RunWriter()
    writer.entries.append(
        _entry(2, sweeps.SELECTED, {"parameters": dict(SWEEP.points[1].parameters)})
    )
    for _ in install(writer):
        response = choose(writer, 3, if_match=stale)

        assert response.status_code == 412, response.text
        assert len(writer.selections) == 1


def test_a_point_is_chosen_once() -> None:
    writer = RunWriter()
    writer.entries.append(
        _entry(2, sweeps.SELECTED, {"parameters": dict(SWEEP.points[1].parameters)})
    )
    for _ in install(writer):
        response = choose(writer, 3)

        assert response.status_code == 409
        assert response.json()["code"] == "merge-point-already-selected"
        assert len(writer.selections) == 1


def test_a_point_that_failed_a_gate_cannot_be_chosen(merged: RunWriter) -> None:
    """RAUN decided; the operator chooses among what it passed."""
    response = choose(merged, 0)

    _refused(response, 409, "merge-point-not-selectable", merged)
    assert "failed gate" in response.json()["detail"]


def test_a_point_the_sweep_does_not_hold_cannot_be_chosen(merged: RunWriter) -> None:
    _refused(
        choose(merged, 0, parameters={"weight": 0.33}), 409, "merge-point-not-selectable", merged
    )


def test_a_run_whose_sweep_is_not_evaluated_has_nothing_to_choose() -> None:
    writer = RunWriter(evaluated=False)
    for _ in install(writer):
        response = client().post(
            f"/v1/sweeps/{RUN}/select",
            json={"parameters": {"weight": 0.4}},
            headers={"Idempotency-Key": "k", "If-Match": "*"},
        )

        _refused(response, 409, "sweep-not-evaluated", writer)


def test_a_run_that_has_moved_past_merged_is_refused() -> None:
    writer = RunWriter(state=RunState.QUANTISED)
    for _ in install(writer):
        _refused(choose(writer, 2), 409, "run-not-awaiting-selection", writer)


def test_an_unknown_run_is_404() -> None:
    writer = RunWriter(known=False)
    for _ in install(writer):
        _refused(choose(writer, 2), 404, "run-not-found", writer)


def test_a_viewer_may_not_choose(merged: RunWriter) -> None:
    response = choose(merged, 2, claims=VIEWER)

    assert response.status_code == 403
    assert merged.selections == []


def test_a_replayed_choice_returns_the_original_and_records_once(merged: RunWriter) -> None:
    key = str(uuid.uuid4())
    tag = current_tag(merged)

    first = choose(merged, 2, key=key, if_match=tag)
    second = choose(merged, 2, key=key, if_match=tag)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(merged.selections) == 1


def test_the_operation_is_documented_as_conditional_and_operator_only() -> None:
    operation = create_app().openapi()["paths"]["/v1/sweeps/{run_id}/select"]["post"]

    headers = {item["name"] for item in operation["parameters"] if item["in"] == "header"}
    assert {"If-Match", "Idempotency-Key"} <= headers
    assert "Requires: `operator`" in operation["description"]
    assert "default" in operation["responses"]
