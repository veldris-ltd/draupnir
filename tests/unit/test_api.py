"""The edge behaves the way SAD 11E.2 says it does."""

from __future__ import annotations

from fastapi.testclient import TestClient

from draupnir.api.app import create_app
from draupnir.api.problems import CONTENT_TYPE, PROBLEM_BASE


def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def test_healthz_answers_without_touching_a_dependency() -> None:
    response = client().get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_an_unknown_route_returns_a_problem_document() -> None:
    response = client().get("/v1/nothing-here")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(CONTENT_TYPE)

    problem = response.json()
    assert problem["type"].startswith(PROBLEM_BASE)
    assert problem["status"] == 404
    assert problem["code"] == "http-404"
    assert problem["instance"] == "/v1/nothing-here"


def test_no_bare_500_reaches_a_client() -> None:
    app = create_app()

    @app.get("/v1/boom", operation_id="boom")
    async def boom() -> None:
        raise RuntimeError("a secret value that must not be rendered")

    with TestClient(app, raise_server_exceptions=False) as connected:
        response = connected.get("/v1/boom")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith(CONTENT_TYPE)
    body = response.json()
    assert body["code"] == "internal-error"
    assert "secret value" not in response.text


def test_the_openapi_document_is_servable() -> None:
    document = client().get("/openapi.json").json()
    assert document["info"]["title"] == "DRAUPNIR"
    assert "/healthz" in document["paths"]


def test_every_operation_declares_an_operation_id() -> None:
    # The operationId is the name of the generated client method. An operation
    # without one silently produces a FastAPI-invented name that changes when
    # the function is renamed, which the drift gate would then report forever.
    document = create_app().openapi()
    missing = [
        f"{method.upper()} {path}"
        for path, item in document["paths"].items()
        for method, operation in item.items()
        if isinstance(operation, dict) and "operationId" not in operation
    ]
    assert missing == []
