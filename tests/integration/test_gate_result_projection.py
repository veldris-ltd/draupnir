"""Gate results, projected from the chain and read three ways. RF-41.

`gate_result` was written by the seed and by nothing else, so on an estate the
approval queue, the model detail and `/metrics` read an empty table while the
worker recorded every outcome in the chain. This walks a run through the real
orchestrator with the worker's payloads -- the adapter evaluation, the stored
quantised artefact and its re-gate -- inserts no gate row, and reads the gates
back through all three.

The site is its own, because `/metrics` counts every gate result a site holds.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text

from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import RunState
from draupnir.core.infrastructure.orchestration import for_connection
from draupnir.core.infrastructure.repositories import RunProjection
from tests.integration.test_metrics_endpoint import sample
from tests.integration.test_release_publication import _SPINE

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SITE = "sindri-gates"
PORT = 8951
BASE = f"http://127.0.0.1:{PORT}"
ACTOR = "dev@veldris.internal"

ADAPTER_SUITE = "raun-suite/2026.02"
RELEASE_SUITE = "raun-release/2026.02"


@pytest.fixture(scope="module")
def api(request: pytest.FixtureRequest) -> Iterator[str]:
    """An API process scoped to this test's site."""
    migrated = request.getfixturevalue("migrated")
    environment = {
        **os.environ,
        "DRAUPNIR_DEV": "1",
        "DRAUPNIR_DATABASE_URL": migrated.replace("postgresql+psycopg", "postgresql+asyncpg"),
        "DRAUPNIR_DATABASE_URL_SYNC": migrated,
        "DRAUPNIR_SITE_ID": SITE,
        "PYTHONIOENCODING": "utf-8",
    }
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "draupnir.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=environment,
    )
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{BASE}/healthz", timeout=1) as response:  # noqa: S310
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.1)
        else:
            pytest.fail("the API did not start")
        yield BASE
    finally:
        process.kill()
        process.wait(timeout=30)


@pytest.fixture
def site(owner: Connection) -> str:
    owner.execute(
        text(
            "INSERT INTO site (id, name, location, timezone, control_plane_uri, "
            "anchor_state) VALUES (:id, 'Gate test', 'Belfast', 'Europe/London', "
            "'https://sindri.veldris.internal', 'ANCHORED') ON CONFLICT (id) DO NOTHING"
        ),
        {"id": SITE},
    )
    owner.commit()
    return SITE


def _get(path: str) -> Any:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if path.startswith("/v1") else body


def _evidence(
    suite_version: str,
    gates: dict[str, tuple[float, float | None, float | None, bool]],
    *,
    kind: str,
    digest: str,
) -> dict[str, Any]:
    """An `Evidence.as_payload()`, exactly as the worker records one."""
    return {
        "artefactSha256": digest,
        "artefactKind": kind,
        "format": "nvfp4" if kind == "quantised" else None,
        "suite": "general-core" if kind == "adapter" else "release",
        "suiteVersion": suite_version,
        "baselineSha256": None,
        "evaluatedAt": "2026-09-15T08:00:00+00:00",
        "passed": all(passed for *_, passed in gates.values()),
        "failing": [gate for gate, (*_, passed) in gates.items() if not passed],
        "gates": {
            gate: {"value": value, "baseline": baseline, "margin": margin, "passed": passed}
            for gate, (value, baseline, margin, passed) in gates.items()
        },
    }


def _evaluated_run(owner_engine: Engine) -> tuple[UUID, str]:
    """A run walked to AWAITING_APPROVAL with the worker's gate payloads."""
    run_id = uuid.uuid4()
    digest = uuid.uuid4().hex * 2
    uri = f"hodd://{SITE}/quantised/{run_id}/nvfp4.bin"

    with owner_engine.begin() as connection:
        orchestrator = for_connection(connection, SiteScope(SITE), actor=ACTOR)
        orchestrator.register(
            run_id,
            name=f"cim-gbr-{run_id.hex[:8]}",
            spec_hash="d" * 64,
            kind="adapter",
            identity=uuid.uuid4().hex * 2,
            payload={"retry_budget": 2},
        )
        # Up to EVALUATING; the spine's own MERGED entry records no measurement.
        for target, facts, entry in _SPINE[:-1]:
            orchestrator.transition(run_id, target, facts=facts, payload=entry)

        orchestrator.transition(
            run_id,
            RunState.MERGED,
            facts={"failing_gates": []},
            payload={
                "gate_results": _evidence(
                    ADAPTER_SUITE,
                    {"E1": (0.76, 0.72, 0.04, True), "E2": (0.91, None, None, True)},
                    kind="adapter",
                    digest="e" * 64,
                )
            },
        )
        orchestrator.transition(
            run_id,
            RunState.QUANTISED,
            facts={"failing_gates": []},
            payload={
                "merge_config_hash": "f" * 64,
                "sweep_result": {"points": 5},
                "formats_built": {"nvfp4": digest},
                "artefacts": [{"uri": uri, "sha256": digest, "kind": "quantised", "size": 1024}],
            },
        )
        orchestrator.transition(
            run_id,
            RunState.AWAITING_APPROVAL,
            facts={"formats_regated": ["nvfp4"], "formats_failing": []},
            payload={
                "format_gate_results": {
                    "nvfp4": _evidence(
                        RELEASE_SUITE,
                        {"E1": (0.73, 0.72, 0.01, True)},
                        kind="quantised",
                        digest=digest,
                    )
                },
                "formats": ["nvfp4"],
                "artefact_sha256": digest,
            },
        )
    return run_id, digest


def _gates(items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(gate["gate"], gate["suiteVersion"]): gate for gate in items}


def test_the_gates_the_worker_recorded_are_read_three_ways_with_no_row_inserted(
    api: str, owner: Connection, owner_engine: Engine, site: str
) -> None:
    del api, site
    run_id, digest = _evaluated_run(owner_engine)

    # Written by the projection an append advanced, and by nothing in this test.
    stored = owner.execute(
        text("SELECT count(*) FROM gate_result WHERE run_id = :run"), {"run": run_id}
    ).scalar_one()
    assert stored == 3

    # 1. The approval queue, whose evidence S13 puts above the decision.
    queue = _get("/v1/gates?state=pending&limit=100")["items"]
    row = next(item for item in queue if item["id"] == str(run_id))
    queued = _gates(row["gates"])
    assert set(queued) == {("E1", ADAPTER_SUITE), ("E2", ADAPTER_SUITE), ("E1", RELEASE_SUITE)}
    assert queued[("E1", RELEASE_SUITE)]["margin"] == pytest.approx(0.01)
    assert queued[("E1", ADAPTER_SUITE)]["baselineValue"] == pytest.approx(0.72)
    assert queued[("E2", ADAPTER_SUITE)]["baselineValue"] is None

    # 2. The model detail, found by the artefact the re-gate measured.
    detail = _get(f"/v1/models/{digest}")
    assert _gates(detail["gates"]) == queued

    # 3. `/metrics`, whose gate counts are this site's rows.
    exposition = _get("/metrics")
    assert sample(exposition, "draupnir_gate_results", gate="E1", state="passed") == 2.0
    assert sample(exposition, "draupnir_gate_results", gate="E2", state="passed") == 1.0

    # A run registry rebuilt from the chain keeps the rows that name its runs.
    with owner_engine.begin() as connection:
        RunProjection(connection, SiteScope(SITE)).rebuild()
    rebuilt = owner.execute(
        text("SELECT count(*) FROM gate_result WHERE run_id = :run"), {"run": run_id}
    ).scalar_one()
    assert rebuilt == stored
