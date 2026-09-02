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
        operation_id="getMetrics",
        command="get-metrics",
        method="GET",
        path="/metrics",
        summary="Prometheus metrics",
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
    Operation(
        operation_id="curateCorpus",
        command="curate-corpus",
        method="POST",
        path="/v1/corpora/{iso3}/curate",
        summary="Run the curation pipeline",
        path_params=("iso3",),
        has_body=False,
    ),
    Operation(
        operation_id="ingestCorpus",
        command="ingest-corpus",
        method="POST",
        path="/v1/corpora/{iso3}/ingest",
        summary="Ingest and hash a jurisdiction's sources",
        path_params=("iso3",),
        has_body=False,
    ),
    Operation(
        operation_id="listGates",
        command="list-gates",
        method="GET",
        path="/v1/gates",
        summary="The approval queue",
        path_params=(),
        has_body=False,
    ),
    Operation(
        operation_id="decideGate",
        command="decide-gate",
        method="POST",
        path="/v1/gates/{gate_id}/decide",
        summary="Approve or reject",
        path_params=("gate_id",),
        has_body=True,
    ),
    Operation(
        operation_id="getLedger",
        command="get-ledger",
        method="GET",
        path="/v1/ledger",
        summary="Ledger slice with chain verification",
        path_params=(),
        has_body=False,
    ),
    Operation(
        operation_id="getLineage",
        command="get-lineage",
        method="GET",
        path="/v1/lineage/{artefact}",
        summary="Full lineage attestation",
        path_params=("artefact",),
        has_body=False,
    ),
    Operation(
        operation_id="listPlugins",
        command="list-plugins",
        method="GET",
        path="/v1/plugins",
        summary="Installed plug-ins and signature status",
        path_params=(),
        has_body=False,
    ),
    Operation(
        operation_id="publishRelease",
        command="publish-release",
        method="POST",
        path="/v1/releases/{artefact}/publish",
        summary="Publish a release",
        path_params=("artefact",),
        has_body=False,
    ),
    Operation(
        operation_id="listRuns",
        command="list-runs",
        method="GET",
        path="/v1/runs",
        summary="List runs",
        path_params=(),
        has_body=False,
    ),
    Operation(
        operation_id="submitRun",
        command="submit-run",
        method="POST",
        path="/v1/runs",
        summary="Submit a run specification",
        path_params=(),
        has_body=True,
    ),
    Operation(
        operation_id="getRun",
        command="get-run",
        method="GET",
        path="/v1/runs/{run_id}",
        summary="Inspect a run",
        path_params=("run_id",),
        has_body=False,
    ),
    Operation(
        operation_id="cancelRun",
        command="cancel-run",
        method="POST",
        path="/v1/runs/{run_id}/cancel",
        summary="Cancel a run",
        path_params=("run_id",),
        has_body=True,
    ),
    Operation(
        operation_id="streamRunEvents",
        command="stream-run-events",
        method="GET",
        path="/v1/runs/{run_id}/events",
        summary="Watch a run's state deltas",
        path_params=("run_id",),
        has_body=False,
    ),
    Operation(
        operation_id="retryRun",
        command="retry-run",
        method="POST",
        path="/v1/runs/{run_id}/retry",
        summary="Retry a run",
        path_params=("run_id",),
        has_body=False,
    ),
    Operation(
        operation_id="listSources",
        command="list-sources",
        method="GET",
        path="/v1/sources",
        summary="List registered sources",
        path_params=(),
        has_body=False,
    ),
    Operation(
        operation_id="registerSource",
        command="register-source",
        method="POST",
        path="/v1/sources",
        summary="Register a corpus source",
        path_params=(),
        has_body=True,
    ),
)
