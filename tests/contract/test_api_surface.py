"""The API surface of SAD 8.1, exercised through real requests.

AC-B2: every error is a problem document with a stable type URI; no bare 500.
AC-B5: the surface is described completely enough to generate both clients.
AC-B6: a route with no role declaration prevents startup.
AC-B9: long operations return 202 with a run identifier.
AC-N10: the OpenAPI specification is complete and both clients come from it.

The unit tests in `tests/unit/test_api_conventions.py` cover the mechanisms.
These cover the wiring, which is the half that actually breaks: a convention
implemented and not attached to a route is a convention the API does not have.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from draupnir.api import deps
from draupnir.api.app import create_app
from draupnir.api.guards import (
    enforce_declarations,
    iter_api_routes,
    permissions_table,
)
from draupnir.api.idempotency import IdempotencyStore
from draupnir.api.problems import CONTENT_TYPE, PROBLEM_BASE
from draupnir.interfaces.testing import sample_spec
from draupnir.svalinn.authz import UndeclaredRouteError

pytestmark = pytest.mark.contract

#: Claims the OIDC middleware would set after verifying a token. Injected here
#: so the surface can be exercised without an identity provider; the
#: verification itself is SVALINN's and is tested there.
OPERATOR = {
    "sub": "operator-1",
    "iss": "https://megingjord.veldris.internal",
    "roles": ["operator"],
    "amr": ["pwd", "hwk"],
}
VIEWER = {**OPERATOR, "sub": "viewer-1", "roles": ["viewer"]}
CURATOR = {**OPERATOR, "sub": "curator-1", "roles": ["curator"]}
APPROVER = {**OPERATOR, "sub": "akuma", "roles": ["approver"]}
#: An approver who authenticated with a password alone. AC-S15.
WEAK_APPROVER = {**APPROVER, "amr": ["pwd"]}

#: A specification the API can actually read. Submission now computes the
#: run identity of AC-F1, and an identity cannot be computed over a payload
#: that is not a specification, so these tests send a real one.
SPEC = sample_spec().as_mapping()
OTHER_SPEC = {
    **SPEC,
    "metadata": {**SPEC["metadata"], "name": "cim-irl-v0.1", "jurisdiction": "IRL"},
}


def client_as(claims: dict[str, Any] | None) -> TestClient:
    """A client whose requests arrive with `claims` already verified."""
    app = create_app()

    @app.middleware("http")
    async def inject(request: Any, call_next: Any) -> Any:
        request.state.claims = claims
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def isolated_store() -> Iterator[None]:
    """A fresh idempotency store per test, so keys do not leak between them."""
    original = deps.STORE
    deps.STORE = IdempotencyStore()
    try:
        yield
    finally:
        deps.STORE = original


# ---------------------------------------------------------------------------
# AC-B6
# ---------------------------------------------------------------------------


def test_a_route_without_a_role_declaration_prevents_startup() -> None:
    """The exit condition, through the real assembly path."""
    app = FastAPI()
    router = APIRouter()

    @router.get("/v1/undeclared")
    async def undeclared() -> dict[str, str]:
        """A route nobody decided the authorisation for."""
        return {}

    app.include_router(router)

    with pytest.raises(UndeclaredRouteError, match="will not start"):
        enforce_declarations(app)


def test_the_real_application_declares_every_route() -> None:
    app = create_app()

    table = permissions_table(app)

    assert len(table) == len(list(iter_api_routes(app.routes)))
    assert all(row["permission"] or row["public"] for row in table)


def test_every_sad_8_1_endpoint_is_present() -> None:
    """SAD 8.1's table, checked line by line."""
    paths = {path for path, _ in iter_api_routes(create_app().routes)}

    for expected in (
        "/v1/sources",
        "/v1/corpora/{iso3}/ingest",
        "/v1/corpora/{iso3}/curate",
        "/v1/runs",
        "/v1/runs/{run_id}",
        "/v1/runs/{run_id}/cancel",
        "/v1/runs/{run_id}/retry",
        "/v1/gates",
        "/v1/gates/{gate_id}/decide",
        "/v1/releases/{artefact}/publish",
        "/v1/lineage/{artefact}",
        "/v1/ledger",
        "/v1/plugins",
        "/healthz",
        "/readyz",
        "/metrics",
    ):
        assert expected in paths, f"SAD 8.1 lists {expected} and it is not registered"


# ---------------------------------------------------------------------------
# AC-S4 and AC-B2
# ---------------------------------------------------------------------------


def test_an_unauthenticated_request_to_a_v1_path_returns_401() -> None:
    """AC-S4, first clause."""
    response = client_as(None).get("/v1/runs")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith(CONTENT_TYPE)
    assert response.json()["type"] == f"{PROBLEM_BASE}/unauthenticated"


def test_a_viewer_submitting_a_run_returns_403() -> None:
    """AC-S4, second clause."""
    response = client_as(VIEWER).post(
        "/v1/runs",
        json={"specification": SPEC},
        headers={"Idempotency-Key": "k1"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
    assert "requires operator" in response.json()["detail"]


def test_an_operator_may_submit_a_run() -> None:
    response = client_as(OPERATOR).post(
        "/v1/runs",
        json={"specification": SPEC},
        headers={"Idempotency-Key": "k1"},
    )

    assert response.status_code == 202


def test_an_approver_without_a_hardware_authenticator_is_refused() -> None:
    """AC-S15, reaching the edge through the guard rather than a special case."""
    response = client_as(WEAK_APPROVER).post(
        "/v1/releases/" + "a" * 64 + "/publish",
        headers={"Idempotency-Key": "k1", "If-Match": "*"},
    )

    assert response.status_code == 403
    assert "hardware backed multi factor" in response.json()["detail"]


def test_every_error_is_a_problem_document_with_a_stable_type_uri() -> None:
    """AC-B2."""
    for method, path, headers in (
        ("get", "/v1/runs", {}),
        ("get", "/v1/lineage/" + "a" * 64, {}),
        ("post", "/v1/runs", {"Idempotency-Key": "k"}),
    ):
        response = getattr(client_as(None), method)(path, headers=headers)
        body = response.json()

        assert response.headers["content-type"].startswith(CONTENT_TYPE)
        assert body["type"].startswith(PROBLEM_BASE)
        assert body["status"] == response.status_code
        assert body["title"] and body["code"]


def test_a_validation_failure_is_a_problem_document_not_a_422_blob() -> None:
    response = client_as(OPERATOR).post(
        "/v1/runs", json={"unexpected": 1}, headers={"Idempotency-Key": "k1"}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request-invalid"


def test_an_unhandled_error_does_not_reach_the_client_as_a_bare_500() -> None:
    """AC-B2: no bare 500 under any tested failure."""
    app = create_app()
    router = APIRouter()

    from draupnir.api.guards import unauthenticated

    @router.get("/boom")
    @unauthenticated("a deliberately failing route, for this test only")
    async def boom() -> dict[str, str]:
        """Fail."""
        raise RuntimeError("a secret value that must not reach the client")

    app.include_router(router)
    enforce_declarations(app)

    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith(CONTENT_TYPE)
    assert response.json()["code"] == "internal-error"
    assert "a secret value" not in response.text


# ---------------------------------------------------------------------------
# AC-B1, AC-B4, AC-B9 through the wire
# ---------------------------------------------------------------------------


def test_a_mutating_endpoint_without_an_idempotency_key_is_refused() -> None:
    """SAD 11E.2 says every mutating endpoint.

    Optional means the retry that duplicated something was the one that
    omitted it.
    """
    response = client_as(OPERATOR).post("/v1/runs", json={"specification": SPEC})

    assert response.status_code == 428
    assert response.json()["code"] == "idempotency-key-required"


def test_a_replayed_submission_returns_the_original_result(monkeypatch: Any) -> None:
    """AC-B1, through the wire."""
    client = client_as(OPERATOR)
    body = {"specification": SPEC}
    headers = {"Idempotency-Key": "submit-1"}

    first = client.post("/v1/runs", json=body, headers=headers)
    second = client.post("/v1/runs", json=body, headers=headers)

    assert first.status_code == second.status_code == 202
    assert first.json()["runId"] == second.json()["runId"]


def test_the_same_key_with_a_different_body_is_refused_over_the_wire() -> None:
    client = client_as(OPERATOR)
    headers = {"Idempotency-Key": "submit-1"}

    client.post("/v1/runs", json={"specification": SPEC}, headers=headers)
    response = client.post("/v1/runs", json={"specification": OTHER_SPEC}, headers=headers)

    assert response.status_code == 422
    assert response.json()["code"] == "idempotency-key-reused"


def test_a_long_operation_returns_202_with_a_run_identifier() -> None:
    """AC-B9. Nothing blocks an HTTP request on training."""
    response = client_as(OPERATOR).post(
        "/v1/runs",
        json={"specification": SPEC},
        headers={"Idempotency-Key": "k1"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["runId"]
    assert body["events"].endswith("/events")


def test_ingest_and_curate_also_return_202() -> None:
    client = client_as(CURATOR)

    for action in ("ingest", "curate"):
        response = client.post(
            f"/v1/corpora/GBR/{action}", headers={"Idempotency-Key": f"{action}-1"}
        )
        assert response.status_code == 202, action
        assert response.json()["status"] == "accepted"


def test_a_conditional_write_without_if_match_is_refused() -> None:
    response = client_as(OPERATOR).post(
        f"/v1/runs/{'0' * 8}-0000-7000-8000-000000000000/cancel",
        json={"reason": "operator changed their mind"},
        headers={"Idempotency-Key": "cancel-1"},
    )

    assert response.status_code == 428
    assert response.json()["code"] == "precondition-required"


def test_a_stale_conditional_write_returns_412() -> None:
    """AC-B4, through the wire."""
    response = client_as(OPERATOR).post(
        f"/v1/runs/{'0' * 8}-0000-7000-8000-000000000000/cancel",
        json={"reason": "operator changed their mind"},
        headers={"Idempotency-Key": "cancel-1", "If-Match": '"stale"'},
    )

    assert response.status_code == 412
    assert response.json()["code"] == "precondition-failed"


def test_a_source_holding_personal_data_needs_a_dpia() -> None:
    response = client_as(CURATOR).post(
        "/v1/sources",
        json={
            "jurisdiction": "GBR",
            "url": "https://example.gov.uk",
            "licenceSpdx": "OGL-UK-3.0",
            "attributionRequired": False,
            "retrievedAt": "2026-03-02T09:00:00+00:00",
            "sha256": "a" * 64,
            "personalData": True,
        },
        headers={"Idempotency-Key": "src-1"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "dpia-reference-required"


def test_a_registered_source_is_returned_with_a_location() -> None:
    response = client_as(CURATOR).post(
        "/v1/sources",
        json={
            "jurisdiction": "GBR",
            "url": "https://hansard.parliament.uk",
            "licenceSpdx": "CC-BY-4.0",
            "attributionRequired": True,
            "retrievedAt": "2026-03-02T09:00:00+00:00",
            "sha256": "b" * 64,
            "personalData": False,
        },
        headers={"Idempotency-Key": "src-2"},
    )

    assert response.status_code == 201
    assert response.headers["Location"].startswith("/v1/sources/")
    assert response.json()["licenceSpdx"] == "CC-BY-4.0"


# ---------------------------------------------------------------------------
# Pagination, events and correlation
# ---------------------------------------------------------------------------


def test_a_collection_returns_a_cursor_shaped_page() -> None:
    response = client_as(OPERATOR).get("/v1/runs?limit=25")

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 25
    assert body["nextCursor"] is None
    assert body["items"] == []


def test_an_invalid_page_size_is_a_problem_document() -> None:
    response = client_as(OPERATOR).get("/v1/runs?limit=0")

    assert response.status_code == 422
    assert response.json()["type"].startswith(PROBLEM_BASE)


def test_the_event_stream_is_text_event_stream() -> None:
    response = client_as(OPERATOR).get(f"/v1/runs/{'0' * 8}-0000-7000-8000-000000000000/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "retry:" in response.text


def test_a_bad_last_event_id_is_refused() -> None:
    response = client_as(OPERATOR).get(
        f"/v1/runs/{'0' * 8}-0000-7000-8000-000000000000/events",
        headers={"Last-Event-ID": "latest"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid-last-event-id"


def test_a_correlation_id_is_echoed_and_a_request_id_is_issued() -> None:
    response = client_as(OPERATOR).get("/v1/runs", headers={"X-Correlation-Id": "abc-123"})

    assert response.headers["X-Correlation-Id"] == "abc-123"
    assert response.headers["X-Request-Id"]


# ---------------------------------------------------------------------------
# AC-N10 and AC-B5: the OpenAPI document
# ---------------------------------------------------------------------------


def test_the_specification_is_openapi_3_1_and_describes_every_route() -> None:
    """AC-N10: complete enough that both clients are generated from it."""
    app = create_app()
    document = app.openapi()

    assert document["openapi"].startswith("3.1")

    described = {
        (path, method.upper())
        for path, operations in document["paths"].items()
        for method in operations
    }
    registered = {
        (path, method)
        for path, route in iter_api_routes(app.routes)
        for method in (route.methods or {"GET"})
        if method not in {"HEAD", "OPTIONS"}
    }

    assert registered <= described


def test_every_operation_has_a_stable_operation_id() -> None:
    """A generated client's method names come from these."""
    document = create_app().openapi()

    ids = [
        operation["operationId"]
        for operations in document["paths"].values()
        for operation in operations.values()
    ]

    assert all(ids)
    assert len(ids) == len(set(ids)), "operation ids must be unique to generate a client"


def test_every_operation_declares_the_problem_response() -> None:
    """So a generated client has a typed error path rather than a hand written one."""
    document = create_app().openapi()

    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            assert "default" in operation["responses"], f"{method.upper()} {path}"


def test_the_published_document_matches_the_application() -> None:
    """The exported file is the single source for both clients (AC-N10)."""
    import json
    from pathlib import Path

    exported = json.loads(Path("docs/api/openapi.json").read_text(encoding="utf-8"))

    assert exported["paths"].keys() == create_app().openapi()["paths"].keys()


def test_the_documented_roles_come_from_the_enforced_declarations() -> None:
    """The permissions table and the guard read one attribute, so they agree."""
    app = create_app()
    document = app.openapi()

    submit = document["paths"]["/v1/runs"]["post"]

    assert "Requires: `operator`" in submit["description"]


def test_an_approver_with_a_hardware_authenticator_may_decide() -> None:
    """The success path through `decide`, which records the decision.

    Worth its own test: the failure paths all return before the completion
    step, so a broken completion is invisible until somebody actually approves
    something.
    """
    response = client_as(APPROVER).post(
        f"/v1/gates/{'0' * 8}-0000-7000-8000-000000000000/decide",
        json={"decision": "approved", "reason": "gates clear", "signature": "sig"},
        headers={"Idempotency-Key": "decide-1", "If-Match": "*"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["approver"] == "akuma"
    assert body["decision"] == "approved"
    assert body["soleApproverException"] is False


def test_a_replayed_decision_returns_the_original_record() -> None:
    """AC-B1 on a 201 rather than a 202."""
    client = client_as(APPROVER)
    path = f"/v1/gates/{'0' * 8}-0000-7000-8000-000000000000/decide"
    body = {"decision": "approved", "reason": "gates clear", "signature": "sig"}
    headers = {"Idempotency-Key": "decide-2", "If-Match": "*"}

    first = client.post(path, json=body, headers=headers)
    second = client.post(path, json=body, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_a_curator_may_not_decide_a_gate() -> None:
    """SAD 9.4: a curator registers sources and does not approve anything."""
    response = client_as(CURATOR).post(
        f"/v1/gates/{'0' * 8}-0000-7000-8000-000000000000/decide",
        json={"decision": "approved", "reason": "x", "signature": "sig"},
        headers={"Idempotency-Key": "decide-3", "If-Match": "*"},
    )

    assert response.status_code == 403


def test_publication_without_an_approval_record_is_refused() -> None:
    """SAD 5.2: SKIDBLADNIR must not publish without a GLEIPNIR approval."""
    response = client_as(APPROVER).post(
        f"/v1/releases/{'a' * 64}/publish",
        headers={"Idempotency-Key": "pub-1", "If-Match": "*"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "release-unapproved"


# ---------------------------------------------------------------------------
# The audit and operations surface
# ---------------------------------------------------------------------------


def test_the_ledger_slice_carries_its_own_verification() -> None:
    """The verification travels with the slice.

    An endpoint returning entries without saying whether they chain makes
    tampering look like data.
    """
    response = client_as(OPERATOR).get("/v1/ledger?limit=10")

    assert response.status_code == 200
    body = response.json()
    assert body["verified"] is True
    assert body["divergence"] is None


def test_a_ledger_range_that_ends_before_it_begins_is_refused() -> None:
    response = client_as(OPERATOR).get("/v1/ledger?from=10&to=2")

    assert response.status_code == 422
    assert response.json()["code"] == "invalid-range"


def test_a_ledger_slice_accepts_a_range() -> None:
    response = client_as(OPERATOR).get("/v1/ledger?from=1&to=100&limit=10")

    assert response.status_code == 200
    assert response.json()["limit"] == 10


def test_lineage_for_an_unknown_artefact_is_a_problem_document() -> None:
    response = client_as(OPERATOR).get(f"/v1/lineage/{'b' * 64}")

    assert response.status_code == 404
    assert response.json()["code"] == "artefact-not-found"
    assert "row level security" in response.json()["detail"]


def test_lineage_rejects_something_that_is_not_a_hash() -> None:
    """The path pattern is part of the contract, so a client cannot send a name."""
    response = client_as(OPERATOR).get("/v1/lineage/not-a-hash")

    assert response.status_code == 422


def test_the_plugin_list_reports_signature_status() -> None:
    """AC-S7's operator-facing half: which loaded, which were refused and why."""
    response = client_as(OPERATOR).get("/v1/plugins")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["items"], list)
    assert isinstance(body["failures"], list)
    for item in body["items"]:
        assert set(item) >= {"name", "group", "version", "signatureVerified"}


def test_the_approval_queue_is_readable_and_filterable() -> None:
    response = client_as(APPROVER).get("/v1/gates?state=pending&limit=10")

    assert response.status_code == 200
    assert response.json()["limit"] == 10


def test_an_unknown_queue_filter_is_refused() -> None:
    response = client_as(APPROVER).get("/v1/gates?state=whatever")

    assert response.status_code == 422


def test_sources_are_listable() -> None:
    response = client_as(CURATOR).get("/v1/sources?limit=5")

    assert response.status_code == 200
    assert response.json()["limit"] == 5


def test_a_run_may_be_retried_conditionally() -> None:
    response = client_as(OPERATOR).post(
        f"/v1/runs/{'0' * 8}-0000-7000-8000-000000000000/retry",
        headers={"Idempotency-Key": "retry-1", "If-Match": "*"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"


def test_a_retry_without_a_precondition_is_refused() -> None:
    response = client_as(OPERATOR).post(
        f"/v1/runs/{'0' * 8}-0000-7000-8000-000000000000/retry",
        headers={"Idempotency-Key": "retry-2"},
    )

    assert response.status_code == 428


def test_inspecting_an_unknown_run_is_a_problem_document() -> None:
    response = client_as(OPERATOR).get(f"/v1/runs/{'0' * 8}-0000-7000-8000-000000000000")

    assert response.status_code == 404
    assert response.json()["code"] == "run-not-found"


def test_the_operational_probes_need_no_credential() -> None:
    """SAD 8.1 lists them as unauthenticated; the binding is the control."""
    client = client_as(None)

    assert client.get("/healthz").status_code == 200
    assert client.get("/metrics").status_code == 200


def test_the_metrics_endpoint_returns_prometheus_text() -> None:
    response = client_as(None).get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_ingest_replays_rather_than_ingesting_twice() -> None:
    """AC-B1 on the corpus path, which is the expensive one to repeat."""
    client = client_as(CURATOR)
    headers = {"Idempotency-Key": "ingest-1"}

    first = client.post("/v1/corpora/GBR/ingest", headers=headers)
    second = client.post("/v1/corpora/GBR/ingest", headers=headers)

    assert first.json()["runId"] == second.json()["runId"]


def test_an_unknown_jurisdiction_code_is_refused() -> None:
    response = client_as(CURATOR).post(
        "/v1/corpora/gb/ingest", headers={"Idempotency-Key": "ingest-2"}
    )

    assert response.status_code == 422


def test_a_blank_signature_is_refused_by_the_schema() -> None:
    """An unsigned approval is a row somebody could have written.

    Refused by the schema rather than by the handler, so the constraint is
    published in the OpenAPI document and a generated client enforces it too.
    A single space passes `min_length=1` without stripping, which is how this
    was wrong the first time.
    """
    response = client_as(APPROVER).post(
        f"/v1/gates/{'0' * 8}-0000-7000-8000-000000000000/decide",
        json={"decision": "approved", "reason": "x", "signature": " "},
        headers={"Idempotency-Key": "decide-4", "If-Match": "*"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request-invalid"


def test_a_stale_precondition_on_a_decision_returns_412() -> None:
    response = client_as(APPROVER).post(
        f"/v1/gates/{'0' * 8}-0000-7000-8000-000000000000/decide",
        json={"decision": "approved", "reason": "x", "signature": "sig"},
        headers={"Idempotency-Key": "decide-5", "If-Match": '"stale"'},
    )

    assert response.status_code == 412


def test_publication_without_a_precondition_is_refused() -> None:
    response = client_as(APPROVER).post(
        f"/v1/releases/{'a' * 64}/publish", headers={"Idempotency-Key": "pub-2"}
    )

    assert response.status_code == 428


def test_a_released_artefact_may_not_be_published_twice_by_replay() -> None:
    """The refusal releases the key, so a retry after a fix is not blocked."""
    client = client_as(APPROVER)
    headers = {"Idempotency-Key": "pub-3", "If-Match": "*"}
    path = f"/v1/releases/{'a' * 64}/publish"

    first = client.post(path, headers=headers)
    second = client.post(path, headers=headers)

    assert first.status_code == second.status_code == 409


def test_curating_replays_rather_than_curating_twice() -> None:
    client = client_as(CURATOR)
    headers = {"Idempotency-Key": "curate-1"}

    first = client.post("/v1/corpora/GBR/curate", headers=headers)
    second = client.post("/v1/corpora/GBR/curate", headers=headers)

    assert first.json()["runId"] == second.json()["runId"]


def test_a_source_registration_replays(monkeypatch: Any) -> None:
    client = client_as(CURATOR)
    body = {
        "jurisdiction": "KEN",
        "url": "https://kenyalaw.org",
        "licenceSpdx": "CC-BY-4.0",
        "attributionRequired": True,
        "retrievedAt": "2026-03-02T09:00:00+00:00",
        "sha256": "c" * 64,
        "personalData": False,
    }
    headers = {"Idempotency-Key": "src-3"}

    first = client.post("/v1/sources", json=body, headers=headers)
    second = client.post("/v1/sources", json=body, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
