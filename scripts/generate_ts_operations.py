"""Generate the TypeScript operation table from the OpenAPI document.

The prompt is unambiguous: "One API. Console and CLI are both generated
clients. A hand-written client method fails review."

`openapi-typescript` already generates the *types* -- `paths`, and every
request and response body under it. What it does not generate is a way to call
them, and the usual answer to that is a hand-written wrapper per operation,
which is exactly the thing that fails review and exactly the thing that drifts.

So the call sites are generated too. This writes an operation table: method,
path, path parameters, whether there is a body. The client in
`packages/api-client/src/client.ts` takes an entry from that table and performs
the request generically, which means there is no per-operation method for
anyone to hand-write, edit or forget to regenerate. Adding an endpoint to the
API and running the generator is the whole of adding it to the console.

Shares `collect()` with the CLI generator, so the two clients cannot disagree
about what the API offers.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.generate_cli import collect

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = REPO_ROOT / "docs" / "api" / "openapi.json"
DEFAULT_OUTPUT = (
    REPO_ROOT / "web" / "packages" / "api-client" / "src" / "generated" / "operations.ts"
)

HEADER = """\
/* Generated operation table. Do not edit.
 *
 * Produced by `scripts/generate_ts_operations.py` from `docs/api/openapi.json`.
 * The pipeline regenerates this file and fails on any diff (AC-Q2).
 *
 * There is deliberately no method per operation here and none in `client.ts`
 * either: a generated table plus one generic caller means a hand-written
 * client method is not something a reviewer has to catch, because there is
 * nowhere to write one.
 */

export const GENERATED_FROM_OPENAPI_VERSION = {version} as const;

/** One operation of the API contract, as a client sees it. */
export interface Operation {{
  readonly operationId: string;
  readonly method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  readonly path: string;
  readonly summary: string;
  readonly pathParams: readonly string[];
  readonly hasBody: boolean;
}}

export const OPERATIONS = {{
"""

FOOTER = """} as const satisfies Record<string, Operation>;

/** Every operation identifier the API declares. */
export type OperationId = keyof typeof OPERATIONS;
"""


def render(document: dict[str, Any]) -> str:
    """Return the full text of the generated module."""
    version = (document.get("info") or {}).get("version", "0")
    lines = [HEADER.format(version=json.dumps(version))]
    for operation in collect(document):
        params = ", ".join(json.dumps(name) for name in operation["path_params"])
        lines.append(f"  {operation['operation_id']}: {{\n")
        lines.append(f"    operationId: {json.dumps(operation['operation_id'])},\n")
        lines.append(f"    method: {json.dumps(operation['method'])},\n")
        lines.append(f"    path: {json.dumps(operation['path'])},\n")
        lines.append(f"    summary: {json.dumps(operation['summary'])},\n")
        lines.append(f"    pathParams: [{params}],\n")
        lines.append(f"    hasBody: {json.dumps(operation['has_body'])},\n")
        lines.append("  },\n")
    lines.append(FOOTER)
    return "".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Write the operation table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    document = json.loads(args.spec.read_text(encoding="utf-8"))
    args.output.write_text(render(document), encoding="utf-8", newline="\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
