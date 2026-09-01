"""Generated command table. Do not edit.

Produced by `scripts/generate_cli.py` from `docs/api/openapi.json`.
The pipeline regenerates this file and fails on any diff (AC-Q2).
"""

from __future__ import annotations

from dataclasses import dataclass

GENERATED_FROM_OPENAPI_VERSION = "0.1.0"


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
    Operation(
        operation_id="getHealth",
        command="get-health",
        method="GET",
        path="/healthz",
        summary="Liveness",
        path_params=(),
        has_body=False,
    ),
    Operation(
        operation_id="getReadiness",
        command="get-readiness",
        method="GET",
        path="/readyz",
        summary="Readiness",
        path_params=(),
        has_body=False,
    ),
)
