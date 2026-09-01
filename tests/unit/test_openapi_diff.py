"""The OpenAPI breaking change gate is itself tested.

A gate nobody has tried to fool is a gate nobody should trust. Each case below
is either a change the pipeline must refuse, or an additive change it must let
through.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.openapi_diff import Finding, diff


def document(paths: dict[str, Any], components: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "DRAUPNIR", "version": "0.1.0"},
        "paths": paths,
        "components": components or {},
    }


def operation(
    operation_id: str = "listRuns",
    *,
    parameters: list[dict[str, Any]] | None = None,
    request: dict[str, Any] | None = None,
    responses: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "operationId": operation_id,
        "responses": responses
        or {
            "200": {
                "description": "ok",
                "content": {"application/json": {"schema": {"type": "object"}}},
            }
        },
    }
    if parameters:
        body["parameters"] = parameters
    if request:
        body["requestBody"] = request
    return body


def json_response(schema: dict[str, Any]) -> dict[str, Any]:
    return {"200": {"description": "ok", "content": {"application/json": {"schema": schema}}}}


def json_body(schema: dict[str, Any]) -> dict[str, Any]:
    return {"content": {"application/json": {"schema": schema}}}


def rules(findings: list[Finding]) -> set[str]:
    return {finding.rule for finding in findings}


# -- additive changes pass --------------------------------------------------


def test_identical_documents_are_additive() -> None:
    spec = document({"/v1/runs": {"get": operation()}})
    assert diff(spec, spec) == []


def test_a_new_operation_is_additive() -> None:
    old = document({"/v1/runs": {"get": operation()}})
    new = document({"/v1/runs": {"get": operation(), "post": operation("createRun")}})
    assert diff(old, new) == []


def test_a_new_optional_parameter_is_additive() -> None:
    old = document({"/v1/runs": {"get": operation()}})
    new = document(
        {
            "/v1/runs": {
                "get": operation(parameters=[{"name": "cursor", "in": "query", "required": False}])
            }
        }
    )
    assert diff(old, new) == []


def test_a_new_response_property_is_additive() -> None:
    old = document(
        {
            "/v1/runs": {
                "get": operation(
                    responses=json_response(
                        {"type": "object", "properties": {"id": {"type": "string"}}}
                    )
                )
            }
        }
    )
    new = document(
        {
            "/v1/runs": {
                "get": operation(
                    responses=json_response(
                        {
                            "type": "object",
                            "properties": {"id": {"type": "string"}, "node": {"type": "string"}},
                        }
                    )
                )
            }
        }
    )
    assert diff(old, new) == []


# -- breaking changes fail --------------------------------------------------


def test_removing_a_path_breaks() -> None:
    old = document({"/v1/runs": {"get": operation()}})
    assert rules(diff(old, document({}))) == {"operation-removed"}


def test_removing_a_method_breaks() -> None:
    old = document({"/v1/runs": {"get": operation(), "post": operation("createRun")}})
    new = document({"/v1/runs": {"get": operation()}})
    assert rules(diff(old, new)) == {"operation-removed"}


def test_renaming_an_operation_id_breaks_the_generated_client() -> None:
    old = document({"/v1/runs": {"get": operation("listRuns")}})
    new = document({"/v1/runs": {"get": operation("getRuns")}})
    assert rules(diff(old, new)) == {"operation-id-changed"}


def test_a_new_required_parameter_breaks() -> None:
    old = document({"/v1/runs": {"get": operation()}})
    new = document(
        {
            "/v1/runs": {
                "get": operation(parameters=[{"name": "site", "in": "query", "required": True}])
            }
        }
    )
    assert rules(diff(old, new)) == {"required-parameter-added"}


def test_an_optional_parameter_becoming_required_breaks() -> None:
    def spec(required: bool) -> dict[str, Any]:
        return document(
            {
                "/v1/runs": {
                    "get": operation(
                        parameters=[{"name": "site", "in": "query", "required": required}]
                    )
                }
            }
        )

    assert rules(diff(spec(False), spec(True))) == {"parameter-now-required"}


def test_a_new_required_request_field_breaks() -> None:
    old = document(
        {
            "/v1/runs": {
                "post": operation(
                    "createRun",
                    request=json_body(
                        {"type": "object", "properties": {"name": {"type": "string"}}}
                    ),
                )
            }
        }
    )
    new = document(
        {
            "/v1/runs": {
                "post": operation(
                    "createRun",
                    request=json_body(
                        {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "site": {"type": "string"},
                            },
                            "required": ["site"],
                        }
                    ),
                )
            }
        }
    )
    assert rules(diff(old, new)) == {"required-added"}


def test_removing_a_response_field_breaks() -> None:
    def spec(properties: dict[str, Any]) -> dict[str, Any]:
        return document(
            {
                "/v1/runs": {
                    "get": operation(
                        responses=json_response({"type": "object", "properties": properties})
                    )
                }
            }
        )

    old = spec({"id": {"type": "string"}, "state": {"type": "string"}})
    new = spec({"id": {"type": "string"}})
    assert rules(diff(old, new)) == {"property-removed"}


def test_a_guaranteed_field_becoming_optional_breaks() -> None:
    def spec(required: list[str]) -> dict[str, Any]:
        return document(
            {
                "/v1/runs": {
                    "get": operation(
                        responses=json_response(
                            {
                                "type": "object",
                                "properties": {"id": {"type": "string"}},
                                "required": required,
                            }
                        )
                    )
                }
            }
        )

    assert rules(diff(spec(["id"]), spec([]))) == {"required-withdrawn"}


def test_changing_a_field_type_breaks() -> None:
    def spec(field_type: str) -> dict[str, Any]:
        return document(
            {
                "/v1/runs": {
                    "get": operation(
                        responses=json_response(
                            {
                                "type": "object",
                                "properties": {"retry_count": {"type": field_type}},
                            }
                        )
                    )
                }
            }
        )

    assert rules(diff(spec("integer"), spec("string"))) == {"type-changed"}


def test_changing_an_array_item_type_breaks() -> None:
    def spec(item_type: str) -> dict[str, Any]:
        return document(
            {
                "/v1/runs": {
                    "get": operation(
                        responses=json_response({"type": "array", "items": {"type": item_type}})
                    )
                }
            }
        )

    assert rules(diff(spec("string"), spec("integer"))) == {"type-changed"}


def test_removing_a_success_response_breaks() -> None:
    old = document(
        {
            "/v1/runs": {
                "post": operation(
                    "createRun",
                    responses={"201": {"description": "created"}, "202": {"description": "queued"}},
                )
            }
        }
    )
    new = document(
        {"/v1/runs": {"post": operation("createRun", responses={"202": {"description": "queued"}})}}
    )
    assert rules(diff(old, new)) == {"response-removed"}


def test_demanding_authentication_breaks() -> None:
    old = document({"/v1/runs": {"get": operation()}})
    new = document({"/v1/runs": {"get": {**operation(), "security": [{"oidc": []}]}}})
    assert rules(diff(old, new)) == {"security-added"}


def test_a_reference_is_followed() -> None:
    def spec(properties: dict[str, Any]) -> dict[str, Any]:
        return document(
            {
                "/v1/runs": {
                    "get": operation(responses=json_response({"$ref": "#/components/schemas/Run"}))
                }
            },
            {"schemas": {"Run": {"type": "object", "properties": properties}}},
        )

    old = spec({"id": {"type": "string"}})
    new = spec({})
    assert rules(diff(old, new)) == {"property-removed"}


def test_a_recursive_reference_terminates() -> None:
    spec = document(
        {
            "/v1/lineage": {
                "get": operation(
                    "getLineage", responses=json_response({"$ref": "#/components/schemas/Node"})
                )
            }
        },
        {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {"child": {"$ref": "#/components/schemas/Node"}},
                }
            }
        },
    )
    assert diff(spec, spec) == []


@pytest.mark.parametrize("direction", ["request", "response"])
def test_enum_changes_are_judged_by_direction(direction: str) -> None:
    def spec(values: list[str]) -> dict[str, Any]:
        schema = {"type": "object", "properties": {"state": {"type": "string", "enum": values}}}
        if direction == "request":
            return document(
                {"/v1/runs": {"post": operation("createRun", request=json_body(schema))}}
            )
        return document({"/v1/runs": {"get": operation(responses=json_response(schema))}})

    narrowed = diff(spec(["QUEUED", "TRAINING"]), spec(["QUEUED"]))
    widened = diff(spec(["QUEUED"]), spec(["QUEUED", "TRAINING"]))

    if direction == "request":
        assert rules(narrowed) == {"enum-narrowed"}
        assert widened == []
    else:
        assert narrowed == []
        assert rules(widened) == {"enum-widened"}


def test_a_finding_renders_for_a_build_log() -> None:
    findings = diff(
        document({"/v1/runs": {"get": operation()}}),
        document({}),
    )
    rendered = str(findings[0])
    assert "GET /v1/runs" in rendered
    assert "operation-removed" in rendered
