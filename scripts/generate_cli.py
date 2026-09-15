"""Generate the `draupnirctl` command table from the OpenAPI document.

SAD 11H: "The client regeneration stage exists specifically to fail the build
when the CLI or the TypeScript client has drifted from the OpenAPI
specification, since a hand edited client is the most common way a generated
interface quietly stops being generated."

The pipeline regenerates and then asserts a clean working tree, so editing
`draupnirctl/_generated.py` by hand fails the build rather than surviving it.

**Conditional writes (RF-39).** An operation that takes `If-Match` carries the
fact in the table, with the read the document names as the source of its tag
(`x-draupnir-precondition`). The CLI sent no `If-Match`, so all six of them were
refused with 428. An operation that takes `If-Match` and names no read, or names
one the document does not hold, is refused here: a client given no tag would
have nowhere to find one.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = REPO_ROOT / "docs" / "api" / "openapi.json"
DEFAULT_OUTPUT = REPO_ROOT / "draupnirctl" / "_generated.py"

METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
PATH_PARAM = re.compile(r"\{([^{}]+)\}")

#: The extension `draupnir.api.concurrency.precondition_read` writes. Spelled
#: here rather than imported, because the generator reads a document and must
#: not need the application to do it.
PRECONDITION = "x-draupnir-precondition"

HEADER = '''\
"""Generated command table. Do not edit.

Produced by `scripts/generate_cli.py` from `docs/api/openapi.json`.
The pipeline regenerates this file and fails on any diff (AC-Q2).
"""

from __future__ import annotations

from dataclasses import dataclass

GENERATED_FROM_OPENAPI_VERSION = {version!r}


@dataclass(frozen=True, slots=True)
class Precondition:
    """Where a conditional write reads the tag it is conditional on. RF-39."""

    #: The operation that returns the tag.
    read: str
    #: The read's path parameters, each taken from the write's parameter named.
    parameters: tuple[tuple[str, str], ...] = ()
    #: For a list read: the collection, the field that identifies an entry, and
    #: the write's path parameter holding its value. The tag is the entry's etag.
    collection: str | None = None
    key: str | None = None
    parameter: str | None = None


@dataclass(frozen=True, slots=True)
class Operation:
    """One operation of the API contract, as the CLI sees it."""

    operation_id: str
    command: str
    method: str
    path: str
    summary: str
    path_params: tuple[str, ...]
    has_body: bool
    #: Whether the operation takes `If-Match` (SAD 11E.2).
    if_match: bool = False
    #: Where to read the tag when none is given.
    precondition: Precondition | None = None


OPERATIONS: tuple[Operation, ...] = (
'''

FOOTER = ")\n"


def _command_name(operation_id: str) -> str:
    """Turn `getRunLedger` into `get-run-ledger`."""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", "-", operation_id)
    return re.sub(r"[^a-z0-9]+", "-", spaced.lower()).strip("-")


def _takes_if_match(operation: dict[str, Any]) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("in") == "header"
        and str(item.get("name", "")).lower() == "if-match"
        for item in operation.get("parameters") or []
    )


def collect(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every operation of the document, sorted for a stable file."""
    operations: list[dict[str, Any]] = []
    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if not operation_id:
                msg = (
                    f"{method.upper()} {path} has no operationId. Every operation needs one: "
                    "it is the name of the generated client method."
                )
                raise SystemExit(msg)
            if_match = _takes_if_match(operation)
            declared = operation.get(PRECONDITION)
            if if_match and not isinstance(declared, dict):
                msg = (
                    f"{operation_id} takes If-Match and declares no {PRECONDITION}. A client "
                    "given no tag would have nowhere to read one, and would be refused with "
                    "428 every time (RF-39)."
                )
                raise SystemExit(msg)
            operations.append(
                {
                    "operation_id": operation_id,
                    "command": _command_name(operation_id),
                    "method": method.upper(),
                    "path": path,
                    "summary": operation.get("summary") or operation.get("description") or "",
                    "path_params": tuple(PATH_PARAM.findall(path)),
                    "has_body": "requestBody" in operation,
                    "if_match": if_match,
                    "precondition": declared if if_match else None,
                }
            )
    operations.sort(key=lambda item: (item["path"], item["method"]))
    _check_preconditions(operations)
    return operations


def _check_preconditions(operations: list[dict[str, Any]]) -> None:
    """Refuse a precondition that names a read the document does not hold."""
    reads = {item["operation_id"]: item for item in operations if item["method"] == "GET"}
    for operation in operations:
        declared = operation["precondition"]
        if declared is None:
            continue
        read = reads.get(str(declared.get("read")))
        if read is None:
            msg = (
                f"{operation['operation_id']} reads its tag from {declared.get('read')!r}, "
                "which is not a GET operation in the document."
            )
            raise SystemExit(msg)
        mapping = declared.get("parameters") or {}
        sources = [mapping.get(name, name) for name in read["path_params"]]
        unknown = [name for name in sources if name not in operation["path_params"]]
        if unknown:
            msg = (
                f"{operation['operation_id']} reads its tag from {read['operation_id']}, whose "
                f"path parameters it cannot supply: {', '.join(unknown)}."
            )
            raise SystemExit(msg)


def _precondition(declared: dict[str, Any]) -> str:
    """The `Precondition(...)` literal for one declaration."""
    parts = [f"read={declared['read']!r}"]
    mapping = declared.get("parameters") or {}
    if mapping:
        pairs = tuple(sorted((str(read), str(write)) for read, write in mapping.items()))
        parts.append(f"parameters={pairs!r}")
    item = declared.get("item") or {}
    for field in ("collection", "key", "parameter"):
        if field in item:
            parts.append(f"{field}={str(item[field])!r}")
    return f"Precondition({', '.join(parts)})"


def render(document: dict[str, Any]) -> str:
    """Return the full text of the generated module."""
    version = (document.get("info") or {}).get("version", "0")
    lines = [HEADER.format(version=version)]
    for operation in collect(document):
        lines.append("    Operation(\n")
        lines.append(f"        operation_id={operation['operation_id']!r},\n")
        lines.append(f"        command={operation['command']!r},\n")
        lines.append(f"        method={operation['method']!r},\n")
        lines.append(f"        path={operation['path']!r},\n")
        lines.append(f"        summary={operation['summary']!r},\n")
        lines.append(f"        path_params={operation['path_params']!r},\n")
        lines.append(f"        has_body={operation['has_body']!r},\n")
        if operation["if_match"]:
            lines.append("        if_match=True,\n")
            lines.append(f"        precondition={_precondition(operation['precondition'])},\n")
        lines.append("    ),\n")
    lines.append(FOOTER)
    return "".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Write the command table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    document = json.loads(args.spec.read_text(encoding="utf-8"))
    # LF on every platform. See `openapi_export.py` (RF-23).
    args.output.write_text(render(document), encoding="utf-8", newline="\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
