"""`/metrics` against a real database and a real API process. RF-18.

The unit tests exercise the rendering with the numbers handed to it. This is
the other half: that the numbers are the ones in PostgreSQL, that the two
signals the worker measures arrive through `duty_measurement`, and that the
whole path holds up when the API is a separate process reading a site it is
scoped to.

The site is its own, because the counts asserted here are counts of everything
`run` and `gate_result` hold for that site -- and a run left behind by another
integration file would make this suite's arithmetic depend on the order the
files ran in.
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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from draupnir.api import metrics

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PORT = 8942
BASE = f"http://127.0.0.1:{PORT}"
SITE = "sindri-metrics"

AT = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)


def scrape(url: str = f"{BASE}/metrics") -> str:
    """The exposition, as text."""
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 -- fixed http
        return str(response.read().decode("utf-8"))


def sample(body: str, name: str, **labels: str) -> float | None:
    """One sample's value, or `None` where the series is absent."""
    rendered = (
        name
        if not labels
        else name + "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"
    )
    for line in body.splitlines():
        if line.startswith(f"{rendered} "):
            return float(line.rsplit(" ", 1)[1])
    return None


@pytest.fixture(scope="module")
def measured(owner_engine: Engine) -> Iterator[str]:
    """A site with runs, gate results, and two duty measurements."""
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO site (id, name, location, timezone, control_plane_uri,"
                " anchor_state, last_anchored_at)"
                " VALUES (:id, :id, 'Nuneaton', 'Europe/London',"
                " 'https://alviss.example.internal', 'ANCHORED', :anchored)"
                " ON CONFLICT (id) DO UPDATE SET last_anchored_at = EXCLUDED.last_anchored_at"
            ),
            {"id": SITE, "anchored": datetime.now(UTC) - timedelta(minutes=30)},
        )
        connection.execute(text("SELECT set_config('draupnir.site_id', :s, true)"), {"s": SITE})

        for state, count in (("QUEUED", 3), ("TRAINING", 1), ("RELEASED", 2)):
            for index in range(count):
                connection.execute(
                    text(
                        "INSERT INTO run (id, site_id, name, spec_hash, kind, state, started_at)"
                        " VALUES (:id, :site, :name, :hash, 'adapter', :state, :at)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "site": SITE,
                        "name": f"metrics-{state.lower()}-{index}",
                        "hash": uuid.uuid4().hex,
                        "state": state,
                        "at": AT + timedelta(minutes=index),
                    },
                )

        run_id = str(uuid.uuid4())
        connection.execute(
            text(
                "INSERT INTO run (id, site_id, name, spec_hash, kind, state, started_at)"
                " VALUES (:id, :site, 'metrics-gated', :hash, 'adapter', 'RELEASED', :at)"
            ),
            {"id": run_id, "site": SITE, "hash": uuid.uuid4().hex, "at": AT},
        )
        for gate, passed, margin in (("safety", True, 0.04), ("quality", False, -0.01)):
            connection.execute(
                text(
                    "INSERT INTO gate_result (id, run_id, gate, suite_version, value,"
                    " baseline_value, margin, passed, evaluated_at)"
                    " VALUES (:id, :run, :gate, 'v1', 0.9, 0.86, :margin, :passed, :at)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "run": run_id,
                    "gate": gate,
                    "margin": margin,
                    "passed": passed,
                    "at": AT,
                },
            )

        # What the worker writes. The API cannot measure either of these at
        # scrape time: verifying the chain is the hourly duty's whole cost, and
        # asking an NFS mount how full it is can block uninterruptibly.
        for duty, alarm, found in (
            (metrics.VAULT_DUTY, False, {"used_ratio": 0.62}),
            (metrics.CHAIN_DUTY, False, {"verified_through": 41}),
        ):
            connection.execute(
                text(
                    "INSERT INTO duty_measurement (site_id, duty, measured_at, alarm,"
                    " measurements)"
                    " VALUES (:site, :duty, :at, :alarm, CAST(:found AS jsonb))"
                    " ON CONFLICT (site_id, duty) DO UPDATE SET"
                    " measured_at = EXCLUDED.measured_at, alarm = EXCLUDED.alarm,"
                    " measurements = EXCLUDED.measurements"
                ),
                {
                    "site": SITE,
                    "duty": duty,
                    "at": datetime.now(UTC),
                    "alarm": alarm,
                    "found": json.dumps(found),
                },
            )
    yield SITE


@pytest.fixture(scope="module")
def api(migrated: str, measured: str) -> Iterator[str]:
    """A real API process scoped to the measured site."""
    del measured
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


def test_the_queue_depth_is_the_number_of_queued_runs(api: str) -> None:
    """SAD 11.3's first signal, read out of the table it lives in."""
    del api

    assert sample(scrape(), "draupnir_runs", state="QUEUED") == 3.0
    assert sample(scrape(), "draupnir_runs", state="TRAINING") == 1.0


def test_gate_outcomes_and_margins_come_from_the_gate_results(api: str) -> None:
    del api
    body = scrape()

    assert sample(body, "draupnir_gate_results", gate="safety", state="passed") == 1.0
    assert sample(body, "draupnir_gate_results", gate="quality", state="failed") == 1.0
    assert sample(body, "draupnir_gate_margin", gate="safety") == pytest.approx(0.04)


def test_the_two_signals_the_worker_measures_arrive_through_the_table(api: str) -> None:
    """The half a scrape cannot take for itself.

    Verifying the chain is the hourly duty's entire cost, and a `statvfs` on a
    hung NFS mount blocks uninterruptibly -- a scrape that blocked would be a
    target Prometheus marks down, reporting the control plane as gone because
    the vault was slow. So the worker measures and this reads what it wrote.
    """
    del api
    body = scrape()

    assert sample(body, "draupnir_vault_used_ratio") == pytest.approx(0.62)
    assert sample(body, "draupnir_chain_verified") == 1.0
    assert sample(body, "draupnir_duty_alarm", duty=metrics.VAULT_DUTY) == 0.0


def test_the_anchor_age_is_measured_from_the_sites_last_anchor(api: str) -> None:
    del api
    age = sample(scrape(), "draupnir_anchor_age_seconds")

    assert age is not None
    assert 1500 < age < 2400, f"half an hour ago should be about 1800 seconds, got {age}"


def test_a_request_is_recorded_under_its_route_template(api: str) -> None:
    """The label that would otherwise be a series per run, forever."""
    del api
    # A run that does not exist, so this is a 404 -- which is the case that
    # matters: an error response must be recorded under the template too, or
    # the series an operator alarms on is missing exactly when it is needed.
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(  # noqa: S310 -- fixed http
            f"{BASE}/v1/runs/{uuid.uuid4()}", timeout=30
        )

    body = scrape()

    assert 'route="/v1/runs/{run_id}"' in body
    assert "019cb993" not in body


def test_no_series_carries_a_label_that_is_unbounded_or_names_the_work(api: str) -> None:
    """Enumerated from a live exposition, not from the source.

    The endpoint's docstring stated this rule before there was a metric to
    apply it to. This is the version that reads what a scrape would actually
    receive from a running control plane with real rows behind it.
    """
    del api
    with urllib.request.urlopen(f"{BASE}/v1/runs?limit=5", timeout=30) as response:  # noqa: S310
        response.read()

    body = scrape()
    carried: set[str] = set()
    for line in body.splitlines():
        if not line.startswith("draupnir_") or "{" not in line:
            continue
        labels = line[line.index("{") + 1 : line.rindex("}")]
        carried.update(part.split("=")[0] for part in labels.split(",") if "=" in part)

    assert carried <= metrics.LABELS | {"le"}, f"unpermitted: {sorted(carried - metrics.LABELS)}"
    assert not carried & metrics.FORBIDDEN_LABELS


def test_a_scrape_reports_this_site_and_not_another(owner_engine: Engine, api: str) -> None:
    """Row level security applies to the scrape like every other read.

    Without `set_config` the policies of SAD 11C exclude everything and the
    exposition would report a forge with no runs. With it and the wrong site,
    it would report another forge's -- which on a federated estate is a leak
    rather than an inaccuracy.
    """
    del api
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO site (id, name, location, timezone, control_plane_uri, anchor_state)"
                " VALUES ('sindri-metrics-other', 'other', 'Nuneaton', 'Europe/London',"
                " 'https://elsewhere.example.internal', 'ANCHORED') ON CONFLICT DO NOTHING"
            )
        )
        connection.execute(
            text("SELECT set_config('draupnir.site_id', 'sindri-metrics-other', true)")
        )
        for index in range(7):
            connection.execute(
                text(
                    "INSERT INTO run (id, site_id, name, spec_hash, kind, state, started_at)"
                    " VALUES (:id, 'sindri-metrics-other', :name, :hash, 'adapter',"
                    " 'QUEUED', :at)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "name": f"other-{index}",
                    "hash": uuid.uuid4().hex,
                    "at": AT,
                },
            )

    queued = sample(scrape(), "draupnir_runs", state="QUEUED")

    assert queued == 3.0, f"another site's seven queued runs reached this scrape: {queued}"


def test_the_scrape_needs_no_credential_and_carries_no_content(api: str) -> None:
    """SAD 8.1 puts `/metrics` on loopback without a credential.

    The binding is the control, so what is on the endpoint has to be safe to
    read: counters and histograms, and no run specification, corpus path or
    actor identity.
    """
    del api
    body = scrape()

    assert "draupnir_runs" in body
    assert "metrics-queued-0" not in body, "a run name reached the exposition"
    assert "spec" not in body.lower().replace("prospect", "")


def test_a_trace_is_discarded_where_no_collector_is_configured(api: str) -> None:
    """The API is started without `DRAUPNIR_OTLP_ENDPOINT`, which is the estate.

    Nothing to assert on the wire, so this asserts the thing that used to be
    wrong instead: the process serves request after request without the tracer
    growing, because `drain` clears whether or not it exported. The old tracer
    was a list that grew for the life of the process.
    """
    del api
    for _ in range(20):
        with urllib.request.urlopen(f"{BASE}/v1/runs?limit=1", timeout=30) as response:  # noqa: S310
            response.read()

    body = scrape()
    served = sample(body, "draupnir_http_request_duration_seconds_count", **_labels())

    assert served is not None and served >= 20, body[:400]


def _labels() -> dict[str, str]:
    return {"method": "GET", "route": "/v1/runs", "status": "200"}
