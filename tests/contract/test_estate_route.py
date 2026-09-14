"""`GET /v1/estate/telemetry`, over a real request. RF-E15.

What is asserted here is the *degraded* answer, because that is the one the
route was written for. A telemetry outage is a degraded mode (SAD 11.2), and
the two obvious ways to handle it are both wrong: a 503 takes the whole panel
down over one unavailable exporter, and a zero renders 0 °C in green on a wall
nobody is standing close enough to question.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from draupnir.api import deps
from draupnir.api.app import create_app, estate_telemetry
from draupnir.gullinbursti.telemetry import Queries, Telemetry
from draupnir.svalinn.egress import BrokeredClient

pytestmark = pytest.mark.contract

VIEWER = {
    "sub": "viewer-1",
    "iss": "https://megingjord.veldris.internal",
    "roles": ["viewer"],
    "amr": ["pwd", "hwk"],
}


@dataclass
class StubResponse:
    """One canned Prometheus answer."""

    status_code: int = 200
    body: Any = None

    def json(self) -> Any:
        """The decoded body."""
        return self.body


@dataclass
class StubClient:
    """Answers every query with the same series."""

    rows: list[dict[str, Any]]

    def get(self, url: str, *, params: Mapping[str, str] | None = None) -> StubResponse:
        """Answer without a socket."""
        del url, params
        return StubResponse(body={"status": "success", "data": {"result": self.rows}})


#: Distinguishes "the caller did not say" from "the caller said nobody".
_DEFAULT = object()


def client(claims: dict[str, Any] | object | None = _DEFAULT) -> TestClient:
    """A client whose requests arrive with `claims` already verified."""
    app = create_app()
    presented = VIEWER if claims is _DEFAULT else claims

    @app.middleware("http")
    async def inject(request: Any, call_next: Any) -> Any:
        request.state.claims = presented
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _restore_estate() -> Iterator[None]:
    """Put the process-wide reader back, whatever a test installed."""
    original = deps.estate()
    yield
    deps.set_estate(original)


def test_an_unconfigured_deployment_answers_200_with_reasons() -> None:
    """A missing collector is a degraded mode, not a failed request.

    503 here would take the queue dashboard down with the thermal one, and the
    queue dashboard reads the database, which is fine.
    """
    deps.set_estate(Telemetry(client=None))

    response = client().get("/v1/estate/telemetry")

    assert response.status_code == 200
    readings = response.json()["readings"]
    assert readings, "an unconfigured deployment still names every appliance"
    assert all(item["value"] is None for item in readings)
    assert all(item["reason"] for item in readings)


def test_no_reading_is_ever_a_zero_standing_in_for_an_absence() -> None:
    """The property the whole finding rests on."""
    deps.set_estate(Telemetry(client=None))

    readings = client().get("/v1/estate/telemetry").json()["readings"]

    assert not any(item["value"] == 0 for item in readings)


def test_a_measured_temperature_reaches_the_wire() -> None:
    """CON-B's thermal panel renders a number when there is one."""
    deps.set_estate(
        Telemetry(
            client=StubClient(
                [{"metric": {"instance": "dvalin:9400"}, "value": [1, "63.5"]}],
            )
        )
    )

    readings = client().get("/v1/estate/telemetry").json()["readings"]
    dvalin = next(
        item
        for item in readings
        if item["subject"] == "dvalin" and item["metric"] == "gpu_temperature"
    )

    assert dvalin["value"] == 63.5
    assert dvalin["unit"] == "C"
    assert dvalin["reason"] is None


def test_every_appliance_is_named_even_when_only_one_answered() -> None:
    """A panel that omitted a machine would look like a smaller estate."""
    deps.set_estate(
        Telemetry(
            client=StubClient([{"metric": {"instance": "dvalin:9400"}, "value": [1, "63.5"]}])
        )
    )

    readings = client().get("/v1/estate/telemetry").json()["readings"]
    named = {item["subject"] for item in readings if item["metric"] == "gpu_temperature"}

    assert named == {"dvalin", "durin", "dain"}


def test_the_route_answers_camel_case_like_every_other_route() -> None:
    """`readAt`, not `read_at`. One API."""
    deps.set_estate(Telemetry(client=None))

    body = client().get("/v1/estate/telemetry").json()

    assert "readAt" in body
    assert "read_at" not in body


def test_an_unauthenticated_request_is_refused() -> None:
    """This is a read of the estate's physical state, not an operator probe."""
    deps.set_estate(Telemetry(client=None))

    assert client(claims=None).get("/v1/estate/telemetry").status_code == 401


def test_the_composition_root_builds_a_brokered_client_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A telemetry client that skipped the broker is undeclared egress.

    The wiring is the assertion: GULLINBURSTI declares the purpose, SVALINN
    decides it, and only the composition root may put the two together.
    """
    from draupnir.core.infrastructure import config

    settings = config.get_settings().model_copy(
        update={"prometheus_url": "http://regin.sindri.veldris.internal:9090"}
    )
    monkeypatch.setattr("draupnir.api.app.get_settings", lambda: settings)

    reader = estate_telemetry()

    assert isinstance(reader.client, BrokeredClient)
    assert reader.client.approving_policy == "observability/2026.01"


def test_the_configured_metric_names_reach_the_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 48.2 warns an exporter update can rename these underneath us."""
    from draupnir.core.infrastructure import config

    settings = config.get_settings().model_copy(update={"metric_gpu_temperature": "RENAMED"})
    monkeypatch.setattr("draupnir.api.app.get_settings", lambda: settings)

    assert estate_telemetry().queries == Queries(
        gpu_temperature="RENAMED",
        throttle_reasons=settings.metric_throttle_reasons,
        fabric_bandwidth=settings.metric_fabric_bandwidth,
        fabric_baseline=settings.metric_fabric_baseline,
    )


def test_the_fabric_reading_and_its_baseline_reach_the_wire() -> None:
    """RF-28. CON-B's second dashboard shows the bandwidth against its baseline."""
    deps.set_estate(Telemetry(client=StubClient([{"metric": {}, "value": [1, "188.4"]}])))

    readings = client().get("/v1/estate/telemetry").json()["readings"]
    fabric = {item["metric"]: item for item in readings if item["subject"] == "baugr"}

    assert set(fabric) == {"fabric_bandwidth", "fabric_baseline"}
    assert fabric["fabric_bandwidth"]["value"] == 188.4
    assert fabric["fabric_baseline"]["unit"] == "GB/s"


def test_an_unrecorded_baseline_is_a_reason_rather_than_a_zero() -> None:
    """A baseline of zero would make every reading look healthy."""
    deps.set_estate(Telemetry(client=None))

    readings = client().get("/v1/estate/telemetry").json()["readings"]
    baseline = next(item for item in readings if item["metric"] == "fabric_baseline")

    assert baseline["value"] is None
    assert baseline["reason"]
