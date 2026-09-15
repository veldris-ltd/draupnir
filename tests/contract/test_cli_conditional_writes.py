"""`draupnirctl`'s conditional writes, against the application. RF-39.

`cancel-run`, `retry-run`, `decide-gate` and `publish-release` -- and
`approve-retention` and `select-merge-point`, which the finding did not count --
sent no `If-Match`, so the API refused every one of them with 428.

Each test runs the command as an operator would, with the CLI's HTTP routed
into the real application over the doubles the API's own conditional-write
tests use. Given no tag, the command reads the current one and the write goes
through. Given a tag the state has since moved past, it is refused with 412.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from draupnir.api import deps
from draupnir.api.reading import EmptyReadModel, retention_out
from draupnir.api.schemas import RetentionPage, RunOut
from draupnir.brisingamen import sweep as sweeps
from draupnir.core.domain.states import RunState
from draupnir.hodd import retention
from draupnirctl import cli
from draupnirctl._generated import OPERATIONS
from tests.contract import test_conditional_writes as conditional
from tests.contract import test_merge_point_selection as merging
from tests.contract import test_retention_approval as retaining

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[2]

#: Every operation the API makes conditional, by name.
CONDITIONAL = {
    "cancelRun",
    "retryRun",
    "decideGate",
    "publishRelease",
    "approveRetention",
    "selectMergePoint",
}


@dataclass(frozen=True, slots=True)
class Exchange:
    """One request the CLI made, and what the application answered."""

    method: str
    path: str
    headers: dict[str, str]
    status: int


def route(monkeypatch: pytest.MonkeyPatch, api: TestClient) -> list[Exchange]:
    """Send the CLI's requests to the application, and record them."""
    exchanges: list[Exchange] = []

    def request(
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        del timeout
        parts = urlsplit(url)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        response: httpx.Response = api.request(method, target, headers=headers, json=json)
        exchanges.append(Exchange(method, parts.path, dict(headers or {}), response.status_code))
        return response

    # `httpx.request` itself, which is what `draupnirctl.cli` calls. The test
    # client sends through its own transport, so it is untouched by this.
    monkeypatch.setattr(httpx, "request", request)
    return exchanges


def run(*arguments: str) -> Any:
    return CliRunner().invoke(cli.app, list(arguments))


def writes(exchanges: list[Exchange]) -> list[Exchange]:
    return [item for item in exchanges if item.method == "POST"]


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def test_every_operation_that_takes_if_match_carries_its_read_in_the_table() -> None:
    """Derived from the document, so a seventh conditional write arrives covered."""
    document = json.loads((ROOT / "docs" / "api" / "openapi.json").read_text(encoding="utf-8"))
    declared = {
        operation["operationId"]
        for item in document["paths"].values()
        for operation in item.values()
        if isinstance(operation, dict)
        and any(
            parameter.get("in") == "header" and parameter.get("name") == "If-Match"
            for parameter in operation.get("parameters", [])
        )
    }
    table = {operation.operation_id: operation for operation in OPERATIONS}

    assert declared == CONDITIONAL
    assert {name for name, operation in table.items() if operation.if_match} == declared
    for name in declared:
        precondition = table[name].precondition
        assert precondition is not None, f"{name} names no read for its If-Match"
        assert table[precondition.read].method == "GET"


def test_an_unconditional_command_refuses_a_tag() -> None:
    result = run("get-health", "--if-match", '"anything"')

    assert result.exit_code == 2
    assert "takes no --if-match" in result.output


# ---------------------------------------------------------------------------
# Runs, gates and releases
# ---------------------------------------------------------------------------


@pytest.fixture
def training() -> Iterator[conditional.Chain]:
    yield from conditional.install(RunState.TRAINING)


@pytest.fixture
def evaluating() -> Iterator[conditional.Chain]:
    yield from conditional.install(RunState.EVALUATING)


@pytest.fixture
def awaiting() -> Iterator[conditional.Chain]:
    yield from conditional.install(RunState.AWAITING_APPROVAL)


def test_cancel_run_reads_the_tag_and_is_refused_once_it_is_stale(
    training: conditional.Chain, monkeypatch: pytest.MonkeyPatch
) -> None:
    exchanges = route(monkeypatch, conditional.client())
    arguments = ("cancel-run", "-p", f"run_id={conditional.RUN}", "--body", '{"reason": "x"}')

    current = run(*arguments)
    read, write = exchanges
    stale = run(*arguments, "--if-match", write.headers["If-Match"])

    assert current.exit_code == 0, current.output
    assert (read.method, read.path) == ("GET", f"/v1/runs/{conditional.RUN}")
    assert write.status == 202
    assert "If-Match" in current.output, "the command did not say what it was conditional on"
    assert training.state is RunState.FAILED
    assert stale.exit_code == 1
    assert writes(exchanges)[-1].status == 412


def test_retry_run_is_refused_on_a_tag_read_before_somebody_else_requeued(
    evaluating: conditional.Chain, monkeypatch: pytest.MonkeyPatch
) -> None:
    exchanges = route(monkeypatch, conditional.client())
    earlier = conditional.client().get(f"/v1/runs/{conditional.RUN}").json()["etag"]
    evaluating.retry_count = 1
    arguments = ("retry-run", "-p", f"run_id={conditional.RUN}")

    stale = run(*arguments, "--if-match", earlier)
    current = run(*arguments)

    assert stale.exit_code == 1
    assert writes(exchanges)[0].status == 412
    assert current.exit_code == 0, current.output
    assert writes(exchanges)[1].status == 202


def test_decide_gate_reads_the_gate_as_the_run_it_is(
    awaiting: conditional.Chain, monkeypatch: pytest.MonkeyPatch
) -> None:
    exchanges = route(monkeypatch, conditional.client())
    decision = json.dumps({"decision": "rejected", "reason": "no DPIA reference", "signature": "s"})
    arguments = ("decide-gate", "-p", f"gate_id={conditional.RUN}", "--body", decision)

    current = run(*arguments)
    read, write = exchanges
    stale = run(*arguments, "--if-match", write.headers["If-Match"])

    assert current.exit_code == 0, current.output
    assert read.path == f"/v1/runs/{conditional.RUN}"
    assert write.status == 201
    assert awaiting.state is RunState.QUARANTINED
    assert stale.exit_code == 1
    assert writes(exchanges)[-1].status == 412


def test_publish_release_reads_the_lineage_the_publication_is_conditional_on(
    awaiting: conditional.Chain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The precondition, not the publication: these doubles hold no publication facts."""
    exchanges = route(monkeypatch, conditional.client())
    arguments = ("publish-release", "-p", f"artefact={conditional.ARTEFACT}")

    run(*arguments)
    read, write = exchanges
    awaiting.published_seq = 11  # Somebody else published it meanwhile.
    stale = run(*arguments, "--if-match", write.headers["If-Match"])

    assert read.path == f"/v1/lineage/{conditional.ARTEFACT}"
    assert write.status not in (412, 428), "the tag the lineage returned was not accepted"
    assert stale.exit_code == 1
    assert writes(exchanges)[-1].status == 412


# ---------------------------------------------------------------------------
# Retention actions and merge points
# ---------------------------------------------------------------------------


class RetentionReader(EmptyReadModel):
    """The retention list, folded from the writer's chain as the read model folds it."""

    def __init__(self, writer: retaining.ChainWriter) -> None:
        self.writer = writer

    async def retention(self, site_id: str) -> RetentionPage:
        del site_id
        now = datetime.now(UTC)
        items = [retention_out(item, now=now) for item in retention.fold(self.writer.entries)]
        return RetentionPage(items=items, overdue=0)


@pytest.fixture
def due() -> Iterator[retaining.ChainWriter]:
    writer = retaining.ChainWriter(retaining.proposal())
    original = deps.READER
    deps.set_reader(RetentionReader(writer))
    try:
        yield from retaining.install(writer)
    finally:
        deps.set_reader(original)


def test_approve_retention_reads_the_action_from_the_list(
    due: retaining.ChainWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    exchanges = route(monkeypatch, retaining.client())
    arguments = ("approve-retention", "-p", f"action_id={due.entries[0].id}")

    current = run(*arguments)
    read, write = exchanges
    stale = run(*arguments, "--if-match", write.headers["If-Match"])

    assert current.exit_code == 0, current.output
    assert read.path == "/v1/retention"
    assert write.status == 200
    assert len(due.approvals) == 1
    assert stale.exit_code == 1
    assert writes(exchanges)[-1].status == 412


def test_approve_retention_sends_nothing_for_an_action_the_list_does_not_hold(
    due: retaining.ChainWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    exchanges = route(monkeypatch, retaining.client())

    result = run("approve-retention", "-p", "action_id=00000000-0000-0000-0000-000000000000")

    assert result.exit_code == 1
    assert not writes(exchanges), "a write was sent with no tag to be conditional on"
    assert not due.approvals


class SweepReader(EmptyReadModel):
    """The run and its sweep, folded from the writer's chain."""

    def __init__(self, writer: merging.RunWriter) -> None:
        self.writer = writer

    async def run(self, site_id: str, run_id: UUID) -> RunOut | None:
        del site_id
        return RunOut(
            id=run_id,
            site_id="sindri",
            name="cim-gbr-v0.2",
            state=self.writer.state,
            spec_hash="d" * 64,
        )

    async def sweep(self, site_id: str, run_id: UUID) -> Any:
        del site_id, run_id
        return sweeps.fold(self.writer.entries)


@pytest.fixture
def merged() -> Iterator[merging.RunWriter]:
    writer = merging.RunWriter()
    original = deps.READER
    deps.set_reader(SweepReader(writer))
    try:
        yield from merging.install(writer)
    finally:
        deps.set_reader(original)


def test_select_merge_point_reads_the_sweep_the_choice_is_conditional_on(
    merged: merging.RunWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    exchanges = route(monkeypatch, merging.client())
    chosen = json.dumps({"parameters": dict(merging.SWEEP.points[1].parameters)})
    arguments = ("select-merge-point", "-p", f"run_id={merging.RUN}", "--body", chosen)

    current = run(*arguments)
    read, write = exchanges
    stale = run(*arguments, "--if-match", write.headers["If-Match"])

    assert current.exit_code == 0, current.output
    assert read.path == f"/v1/sweeps/{merging.RUN}"
    assert write.status == 200
    assert len(merged.selections) == 1
    assert stale.exit_code == 1
    assert writes(exchanges)[-1].status == 412
