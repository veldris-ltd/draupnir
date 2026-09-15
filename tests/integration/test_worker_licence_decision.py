"""A run submitted through the API has its licence decision taken by the worker. RF-43.

SAD 6.1 moves a run DRAFT -> CORPUS_REGISTERED -> LICENCE_CLEARED before
curation, and only the Sindri demonstration procedure took those steps: a run
submitted through the console or `draupnirctl` stayed at DRAFT on an estate.

Here sources are registered and runs submitted through a real API process, and a
real worker -- resolving the `draupnir.policy` driver the environment installs --
takes each run as far as its corpus allows:

- a Tier B run whose sources are permitted is cleared, recording the policy
  version and each decision, the base model's among them;
- a run with a refused licence is quarantined, naming the refusing rule
  (AC-S2);
- a Tier A run is quarantined on its base model, whose declared terms the policy
  in force refuses by default;
- a run whose jurisdiction has no registered source is left at DRAFT.
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
from sqlalchemy import Engine, create_engine, text

from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import RunState
from draupnir.core.infrastructure.orchestration import for_connection
from draupnir.gleipnir import licence
from draupnir.hamarr import tiers
from draupnir.worker.loop import Worker, WorkerSettings
from tests.specs import submittable_mapping

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SITE = "sindri-licence"
PORT = 8953
BASE = f"http://127.0.0.1:{PORT}"
TICKS = 6


@pytest.fixture(scope="module")
def engine(migrated: str) -> Iterator[Engine]:
    """An engine that commits. The worker's writes have to outlive its tick."""
    made = create_engine(migrated, future=True)
    with made.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO site (id, name, location, timezone, control_plane_uri, "
                "anchor_state) VALUES (:id, 'Licence test', 'Belfast', 'Europe/London', "
                "'https://licence.veldris.internal', 'ANCHORED') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": SITE},
        )
    yield made
    made.dispose()


@pytest.fixture(scope="module")
def api(migrated: str, engine: Engine) -> Iterator[str]:
    """An API process scoped to this test's site."""
    del engine
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


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 -- fixed http, fixed host
        f"{BASE}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Idempotency-Key": str(uuid.uuid4())},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return dict(json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as error:
        pytest.fail(f"POST {path} -> {error.status}: {error.read().decode('utf-8')}")


def _source(jurisdiction: str, url: str, licence_spdx: str) -> dict[str, Any]:
    return _post(
        "/v1/sources",
        {
            "jurisdiction": jurisdiction,
            "url": url,
            "licenceSpdx": licence_spdx,
            "attributionRequired": True,
            "retrievedAt": "2026-03-02T09:00:00+00:00",
            "sha256": uuid.uuid4().hex * 2,
            "personalData": False,
        },
    )


def _submit(jurisdiction: str) -> UUID:
    """Submit a specification for a jurisdiction, with the base its tier requires."""
    specification = submittable_mapping(
        dataset={
            "artefact": f"hodd://corpora/{jurisdiction}/curated",
            "expectSha256": uuid.uuid4().hex * 2,
            "cutoffPercentile": 99,
        }
    )
    specification["metadata"] = {
        **specification["metadata"],
        "name": f"cim-{jurisdiction.lower()}-v1.0",
        "jurisdiction": jurisdiction,
        "tier": str(tiers.tier_of(jurisdiction)),
    }
    specification["spec"]["base"] = {
        **specification["spec"]["base"],
        "artefact": tiers.base_artefact(jurisdiction, site=SITE),
    }
    return UUID(_post("/v1/runs", {"specification": specification})["runId"])


def _history(engine: Engine, run_id: UUID) -> tuple[RunState, dict[str, dict[str, Any]]]:
    """The run's state, and the payload of each transition by its target."""
    with engine.connect() as connection:
        transaction = connection.begin()
        auditor = for_connection(connection, SiteScope(SITE), actor="auditor@veldris.internal")
        state = auditor.state_of(run_id)
        payloads = {
            entry.transition.partition("->")[2]: dict(entry.payload)
            for entry in auditor.history(run_id)
        }
        transaction.rollback()
    return state, payloads


def test_the_worker_takes_each_submitted_run_s_licence_decision(
    api: str, engine: Engine, tmp_path: Path
) -> None:
    del api
    permitted = _source("NZL", "https://legislation.govt.nz", "CC-BY-4.0")
    refused = _source("FJI", "https://example.invalid/fji-nc", "CC-BY-NC-4.0")
    _source("GBR", "https://legislation.gov.uk/ukpga", "OGL-UK-3.0")

    cleared_run = _submit("NZL")
    refused_run = _submit("FJI")
    tier_a_run = _submit("GBR")
    unregistered_run = _submit("TON")

    worker = Worker(
        WorkerSettings(
            site_id=SITE,
            scratch=tmp_path / "worker",
            interval=0.05,
            perform_duties=False,
            # Nothing is dispatched here; the corpus half places no job.
            stand_in=True,
        ),
        engine=engine,
    )
    for _ in range(TICKS):
        worker.run_once()

    # 1. Cleared, under the version of the driver the deployment installs, with
    # every decision recorded -- the base model's included.
    state, recorded = _history(engine, cleared_run)
    assert state is RunState.LICENCE_CLEARED
    assert recorded["CORPUS_REGISTERED"]["sources"] == [permitted["sha256"]]
    assert recorded["LICENCE_CLEARED"]["policy_version"] == licence.CURRENT.version
    decisions = recorded["LICENCE_CLEARED"]["decisions"]
    assert [item["subject"] for item in decisions] == ["source", "base_model"]
    assert decisions[1]["licence"] == "Apache-2.0"
    assert all(item["policyVersion"] == licence.CURRENT.version for item in decisions)

    # 2. Refused a licence, quarantined naming the rule (AC-S2).
    state, recorded = _history(engine, refused_run)
    assert state is RunState.QUARANTINED
    assert recorded["QUARANTINED"]["rule"] == "licence-refused"
    assert recorded["QUARANTINED"]["failing_source"] == refused["url"]

    # 3. Tier A, whose base the policy in force refuses by default.
    state, recorded = _history(engine, tier_a_run)
    assert state is RunState.QUARANTINED
    assert recorded["QUARANTINED"]["failing_source"] == tiers.base_artefact("GBR", site=SITE)
    assert recorded["QUARANTINED"]["rule"] == "default"

    # 4. No source registered for its jurisdiction: nothing to register.
    state, _ = _history(engine, unregistered_run)
    assert state is RunState.DRAFT

    # And the register follows the decision taken on its corpus (RF-42).
    with urllib.request.urlopen(f"{BASE}/v1/sources?limit=100", timeout=30) as response:  # noqa: S310
        register = {item["id"]: item for item in json.loads(response.read())["items"]}
    assert register[permitted["id"]]["state"] == "LICENCE_CLEARED"
    assert register[refused["id"]]["state"] == "QUARANTINED"


def test_a_second_tick_records_nothing_more(api: str, engine: Engine, tmp_path: Path) -> None:
    """A decision is taken once; a run waiting on nothing new is left alone."""
    del api
    _source("KEN", "https://kenyalaw.org", "CC-BY-4.0")
    run_id = _submit("KEN")
    worker = Worker(
        WorkerSettings(
            site_id=SITE, scratch=tmp_path / "w", interval=0.05, perform_duties=False, stand_in=True
        ),
        engine=engine,
    )
    for _ in range(TICKS):
        worker.run_once()
    _, before = _history(engine, run_id)
    with engine.connect() as connection:
        transaction = connection.begin()
        entries = len(for_connection(connection, SiteScope(SITE), actor="auditor").history(run_id))
        transaction.rollback()

    worker.run_once()

    with engine.connect() as connection:
        transaction = connection.begin()
        after = len(for_connection(connection, SiteScope(SITE), actor="auditor").history(run_id))
        transaction.rollback()
    assert "LICENCE_CLEARED" in before
    assert after == entries
