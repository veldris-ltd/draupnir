"""Breaking change gate for the OpenAPI contract.

SAD 11E.2: "Additive changes only within a version. The OpenAPI diff gate fails
a build on a breaking change." SAD 11H stage 2 runs this as `API contract`.

The rules below are deliberately asymmetric. A request schema is contravariant:
demanding something new of a client breaks it. A response schema is covariant:
withdrawing something a client was promised breaks it. Everything else is
additive and passes.

The implementation is local rather than a third party differ so that the gate
has no toolchain of its own, is unit tested in `tests/unit/test_openapi_diff.py`,
and states its rules where an architect can read them.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Document = dict[str, Any]
Direction = Literal["request", "response"]

METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


@dataclass(frozen=True, slots=True)
class Finding:
    """One breaking change."""

    location: str
    rule: str
    detail: str

    def __str__(self) -> str:
        """Render as one line for a build log."""
        return f"{self.location}: {self.detail}  [{self.rule}]"


def _resolve(document: Document, node: Any) -> Any:
    """Follow a local `$ref` one level. Foreign refs are returned untouched."""
    seen: set[str] = set()
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            return node
        seen.add(ref)
        target: Any = document
        for part in ref[2:].split("/"):
            if not isinstance(target, dict) or part not in target:
                return node
            target = target[part]
        node = target
    return node


def _operations(document: Document) -> dict[tuple[str, str], Document]:
    """Return every operation keyed by (path, method)."""
    found: dict[tuple[str, str], Document] = {}
    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in METHODS:
            operation = item.get(method)
            if isinstance(operation, dict):
                found[path, method] = operation
    return found


def _parameters(document: Document, operation: Document) -> dict[tuple[str, str], Document]:
    """Return parameters keyed by (name, in)."""
    result: dict[tuple[str, str], Document] = {}
    for raw in operation.get("parameters") or []:
        parameter = _resolve(document, raw)
        if isinstance(parameter, dict) and "name" in parameter:
            result[parameter["name"], parameter.get("in", "query")] = parameter
    return result


def _schema_of(document: Document, container: Any) -> Document | None:
    """Extract the JSON schema of a request or response body, if any."""
    container = _resolve(document, container)
    if not isinstance(container, dict):
        return None
    content = container.get("content")
    if not isinstance(content, dict):
        return None
    for media_type in ("application/json", "application/problem+json"):
        media = content.get(media_type)
        if isinstance(media, dict) and isinstance(media.get("schema"), dict):
            schema: Document = media["schema"]
            return schema
    for media in content.values():
        if isinstance(media, dict) and isinstance(media.get("schema"), dict):
            fallback: Document = media["schema"]
            return fallback
    return None


def _type_of(schema: Document) -> str | None:
    value = schema.get("type")
    return value if isinstance(value, str) else None


def _compare_schema(
    old_document: Document,
    new_document: Document,
    old_schema: Any,
    new_schema: Any,
    location: str,
    direction: Direction,
    findings: list[Finding],
    seen: set[tuple[int, int]],
) -> None:
    """Walk two schemas in parallel, recording breaking differences."""
    old_schema = _resolve(old_document, old_schema)
    new_schema = _resolve(new_document, new_schema)
    if not isinstance(old_schema, dict) or not isinstance(new_schema, dict):
        return

    marker = (id(old_schema), id(new_schema))
    if marker in seen:
        return
    seen.add(marker)

    old_type, new_type = _type_of(old_schema), _type_of(new_schema)
    if old_type and new_type and old_type != new_type:
        findings.append(
            Finding(location, "type-changed", f"type changed from {old_type} to {new_type}")
        )

    old_enum, new_enum = old_schema.get("enum"), new_schema.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        if direction == "request" and (
            removed := set(map(repr, old_enum)) - set(map(repr, new_enum))
        ):
            findings.append(
                Finding(location, "enum-narrowed", f"accepted values withdrawn: {sorted(removed)}")
            )
        if direction == "response" and (
            added := set(map(repr, new_enum)) - set(map(repr, old_enum))
        ):
            findings.append(
                Finding(location, "enum-widened", f"values a client cannot know: {sorted(added)}")
            )

    old_props = old_schema.get("properties") or {}
    new_props = new_schema.get("properties") or {}
    old_required = set(old_schema.get("required") or [])
    new_required = set(new_schema.get("required") or [])

    if direction == "request":
        for name in sorted(new_required - old_required):
            findings.append(
                Finding(
                    f"{location}.{name}", "required-added", "a client is now required to send it"
                )
            )
    else:
        for name in sorted(set(old_props) - set(new_props)):
            findings.append(
                Finding(f"{location}.{name}", "property-removed", "a promised field is gone")
            )
        for name in sorted(old_required - new_required):
            if name in new_props:
                findings.append(
                    Finding(
                        f"{location}.{name}",
                        "required-withdrawn",
                        "a field guaranteed present is now optional",
                    )
                )

    for name in sorted(set(old_props) & set(new_props)):
        _compare_schema(
            old_document,
            new_document,
            old_props[name],
            new_props[name],
            f"{location}.{name}",
            direction,
            findings,
            seen,
        )

    if "items" in old_schema and "items" in new_schema:
        _compare_schema(
            old_document,
            new_document,
            old_schema["items"],
            new_schema["items"],
            f"{location}[]",
            direction,
            findings,
            seen,
        )


def diff(old: Document, new: Document) -> list[Finding]:
    """Return every breaking change from `old` to `new`. Empty means additive."""
    findings: list[Finding] = []

    old_ops = _operations(old)
    new_ops = _operations(new)

    for key in sorted(set(old_ops) - set(new_ops)):
        path, method = key
        findings.append(
            Finding(
                f"{method.upper()} {path}", "operation-removed", "the operation no longer exists"
            )
        )

    for key in sorted(set(old_ops) & set(new_ops)):
        path, method = key
        location = f"{method.upper()} {path}"
        old_op, new_op = old_ops[key], new_ops[key]

        old_id, new_id = old_op.get("operationId"), new_op.get("operationId")
        if old_id and old_id != new_id:
            findings.append(
                Finding(
                    location,
                    "operation-id-changed",
                    f"operationId changed from {old_id!r} to {new_id!r}, "
                    "which renames the generated client method",
                )
            )

        if not (old_op.get("security") or old.get("security")) and new_op.get("security"):
            findings.append(
                Finding(location, "security-added", "the operation now demands authentication")
            )

        old_params = _parameters(old, old_op)
        new_params = _parameters(new, new_op)
        for name, where in sorted(set(new_params) - set(old_params)):
            if new_params[name, where].get("required"):
                findings.append(
                    Finding(
                        f"{location} ?{name}",
                        "required-parameter-added",
                        f"a new required {where} parameter",
                    )
                )
        for name, where in sorted(set(old_params) & set(new_params)):
            if not old_params[name, where].get("required") and new_params[name, where].get(
                "required"
            ):
                findings.append(
                    Finding(
                        f"{location} ?{name}",
                        "parameter-now-required",
                        f"the {where} parameter became required",
                    )
                )

        old_body = _schema_of(old, old_op.get("requestBody"))
        new_body = _schema_of(new, new_op.get("requestBody"))
        if old_body is not None and new_body is not None:
            _compare_schema(
                old, new, old_body, new_body, f"{location} body", "request", findings, set()
            )
        elif old_body is None and new_body is not None:
            body = _resolve(new, new_op.get("requestBody"))
            if isinstance(body, dict) and body.get("required"):
                findings.append(
                    Finding(location, "body-now-required", "the operation now requires a body")
                )

        old_responses = old_op.get("responses") or {}
        new_responses = new_op.get("responses") or {}
        for status in sorted(set(old_responses) - set(new_responses)):
            if str(status).startswith(("2", "3")):
                findings.append(
                    Finding(
                        f"{location} -> {status}",
                        "response-removed",
                        "a documented success response is gone",
                    )
                )
        for status in sorted(set(old_responses) & set(new_responses)):
            old_schema = _schema_of(old, old_responses[status])
            new_schema = _schema_of(new, new_responses[status])
            if old_schema is not None and new_schema is not None:
                _compare_schema(
                    old,
                    new,
                    old_schema,
                    new_schema,
                    f"{location} -> {status}",
                    "response",
                    findings,
                    set(),
                )

    return findings


def main(argv: list[str] | None = None) -> int:
    """Compare two OpenAPI documents and fail on a breaking change."""
    parser = argparse.ArgumentParser(description="OpenAPI breaking change gate")
    parser.add_argument("baseline", type=Path, help="The previously released document")
    parser.add_argument("candidate", type=Path, help="The document produced by this build")
    args = parser.parse_args(argv)

    if not args.baseline.exists():
        print(f"no baseline at {args.baseline}; treating this build as the first release")
        return 0

    old = json.loads(args.baseline.read_text(encoding="utf-8"))
    new = json.loads(args.candidate.read_text(encoding="utf-8"))

    findings = diff(old, new)
    if not findings:
        print("OpenAPI diff: additive only")
        return 0

    print(f"OpenAPI diff: {len(findings)} breaking change(s) against {args.baseline}")
    for finding in findings:
        print(f"  {finding}")
    print("\nA breaking change requires a new path version, not an edit to /v1 (SAD 11E.2).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
