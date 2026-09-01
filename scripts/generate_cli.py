"""Generate the `draupnirctl` command table from the OpenAPI document.

SAD 11H: "The client regeneration stage exists specifically to fail the build
when the CLI or the TypeScript client has drifted from the OpenAPI
specification, since a hand edited client is the most common way a generated
interface quietly stops being generated."

The pipeline regenerates and then asserts a clean working tree, so editing
`draupnirctl/_generated.py` by hand fails the build rather than surviving it.
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

HEADER = '''\
"""Generated command table. Do not edit.

Produced by `scripts/generate_cli.py` from `docs/api/openapi.json`.
The pipeline regenerates this file and fails on any diff (AC-Q2).
"""

from __future__ import annotations

from dataclasses import dataclass

GENERATED_FROM_OPENAPI_VERSION = {version!r}


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


OPERATIONS: tuple[Operation, ...] = (
'''

FOOTER = ")\n"


def _command_name(operation_id: str) -> str:
    """Turn `getRunLedger` into `get-run-ledger`."""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", "-", operation_id)
    return re.sub(r"[^a-z0-9]+", "-", spaced.lower()).strip("-")


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
            operations.append(
                {
                    "operation_id": operation_id,
                    "command": _command_name(operation_id),
                    "method": method.upper(),
                    "path": path,
                    "summary": operation.get("summary") or operation.get("description") or "",
                    "path_params": tuple(PATH_PARAM.findall(path)),
                    "has_body": "requestBody" in operation,
                }
            )
    operations.sort(key=lambda item: (item["path"], item["method"]))
    return operations


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
    args.output.write_text(render(document), encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
