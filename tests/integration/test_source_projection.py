"""A source registered through the API is read back from the chain. RF-42.

`registerSource` recorded a `source` entry and wrote no row, and the seed was
the only writer of `source`. So a source registered on an estate appeared
neither in S04's register nor in a release's lineage, and the lineage's training
content summary was refused for want of one.

Nothing here inserts a `source` row. Two sources are registered through a real
API process; a run of their jurisdiction is walked through the real
orchestrator between the two registrations, to a stored artefact; and both are
read from `listSources` and from that artefact's lineage.
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
from draupnir.core.infrastructure.repositories import SourceProjection
from tests.integration.test_release_publication import _SPINE

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SITE = "sindri-sources"
PORT = 8952
BASE = f"http://127.0.0.1:{PORT}"
ACTOR = "dev@veldris.internal"


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
            "anchor_state) VALUES (:id, 'Source test', 'Belfast', 'Europe/London', "
            "'https://sindri.veldris.internal', 'ANCHORED') ON CONFLICT (id) DO NOTHING"
        ),
        {"id": SITE},
    )
    owner.commit()
    return SITE


def _get(path: str) -> Any:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _register(url: str, *, personal_data: bool = False) -> dict[str, Any]:
    """POST a source as the development principal, as S04 does."""
    body = {
        "jurisdiction": "GBR",
        "url": url,
        "licenceSpdx": "OGL-UK-3.0",
        "attributionRequired": True,
        "retrievedAt": "2026-03-02T09:00:00+00:00",
        "sha256": uuid.uuid4().hex * 2,
        "personalData": personal_data,
        "dpiaRef": "DPIA-2026-099" if personal_data else None,
        "residencyConstraint": [SITE],
    }
    request = urllib.request.Request(  # noqa: S310 -- fixed http, fixed host
        f"{BASE}/v1/sources",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Idempotency-Key": str(uuid.uuid4())},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        assert response.status == 201
        return dict(json.loads(response.read().decode("utf-8")))


def _curated_run(owner_engine: Engine) -> str:
    """A GBR run walked through its corpus's licence decision to a stored artefact."""
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
        # Through CORPUS_REGISTERED, LICENCE_CLEARED and CURATED to EVALUATING.
        for target, facts, entry in _SPINE[:-1]:
            orchestrator.transition(run_id, target, facts=facts, payload=entry)
        orchestrator.transition(
            run_id, RunState.MERGED, facts={"failing_gates": []}, payload={"gate_results": {}}
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
    return digest


def test_a_registered_source_is_read_from_the_register_and_a_lineage_with_no_row_inserted(
    api: str, owner: Connection, owner_engine: Engine, site: str
) -> None:
    del api, site
    judged = _register("https://www.legislation.gov.uk/ukpga", personal_data=True)
    digest = _curated_run(owner_engine)
    unjudged = _register("https://www.legislation.gov.uk/uksi")

    # 1. S04's register. The first source followed its corpus through the
    # licence decision and curation; the second was registered after both, so
    # nothing has judged it.
    register = {item["id"]: item for item in _get("/v1/sources?limit=100")["items"]}
    assert register[judged["id"]]["state"] == "CURATED"
    assert register[judged["id"]]["dpiaRef"] == "DPIA-2026-099"
    assert register[unjudged["id"]]["state"] == "DRAFT"

    # Projected at this site, with the residency the registration recorded.
    stored = {
        str(row.id): row
        for row in owner.execute(
            text("SELECT id, site_id, residency_constraint FROM source WHERE site_id = :site"),
            {"site": SITE},
        )
    }
    assert set(stored) >= {judged["id"], unjudged["id"]}
    assert stored[judged["id"]].residency_constraint == [SITE]

    # 2. The lineage of the artefact the run stored, which walks back to its
    # jurisdiction's sources -- and so do the release documents built on it.
    lineage = _get(f"/v1/lineage/{digest}")
    roots = {node["label"]: node for node in lineage["nodes"] if node["kind"] == "source"}
    assert {judged["url"], unjudged["url"]} <= set(roots)
    assert roots[judged["url"]]["fact"] == "OGL-UK-3.0"

    # 3. A rebuild of the register reproduces it.
    with owner_engine.begin() as connection:
        SourceProjection(connection, SiteScope(SITE)).rebuild()
    after = _get("/v1/sources?limit=100")["items"]
    assert {item["id"]: item["state"] for item in after if item["id"] in register} == {
        item_id: item["state"] for item_id, item in register.items()
    }


def test_the_register_is_projected_on_the_append_that_registers(
    api: str, owner: Connection, site: str
) -> None:
    """Advanced by the append itself, not by a rebuild somebody remembers to run."""
    del api, site
    source = _register("https://www.legislation.gov.uk/asp")
    row = owner.execute(
        text("SELECT state, site_id FROM source WHERE id = :id"), {"id": UUID(source["id"])}
    ).one()
    assert (row.state, row.site_id) == ("DRAFT", SITE)
