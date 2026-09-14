"""What the estate's own measurements are allowed to look like. RF-E15.

The single property under test everywhere below: **a reading is a number or a
reason, and never a zero standing in for an absence.** SAD 11.3 gives thermal
an alarm threshold, and a threshold evaluated against a fabricated zero reports
a cool estate no matter what the hardware is doing. The panel is on a wall and
nobody stands close enough to it to notice; the number is simply believed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from draupnir.gullinbursti import telemetry as tel
from draupnir.svalinn import egress

APPLIANCES = ("dvalin", "durin", "dain")
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


@dataclass
class StubResponse:
    """One canned Prometheus answer."""

    status_code: int = 200
    body: Any = None
    explode: bool = False

    def json(self) -> Any:
        """The decoded body, or a decoder that fails."""
        if self.explode:
            msg = "not JSON"
            raise ValueError(msg)
        return self.body


@dataclass
class StubClient:
    """A client that answers without a socket."""

    response: StubResponse | None = None
    error: Exception | None = None
    calls: list[tuple[str, dict[str, str]]] | None = None

    def get(self, url: str, *, params: Mapping[str, str] | None = None) -> StubResponse:
        """Record the query, then answer."""
        if self.calls is None:
            self.calls = []
        self.calls.append((url, dict(params or {})))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response

    def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> StubResponse:
        """Refuse. Telemetry reads; it never submits.

        Present because `Sends` requires it (RF-07 added `post` so that the
        anchor path had a brokered client at all), and raising rather than
        answering because a reader that posted would be doing something this
        allow-list entry's purpose does not describe.
        """
        del url, json, headers
        raise AssertionError("the telemetry reader does not post")


def series(instance: str, value: float) -> dict[str, Any]:
    """One Prometheus series, in the shape the HTTP API returns it."""
    return {"metric": {"instance": instance}, "value": [1_757_332_800, str(value)]}


def answering(*rows: dict[str, Any]) -> StubClient:
    """A client returning a successful instant query with `rows`."""
    return StubClient(
        response=StubResponse(body={"status": "success", "data": {"result": list(rows)}})
    )


# ---------------------------------------------------------------------------
# The type itself
# ---------------------------------------------------------------------------


def test_a_reading_with_neither_a_value_nor_a_reason_cannot_be_built() -> None:
    """The constructor is where 'unmeasured, cause unknown' is prevented."""
    with pytest.raises(ValueError, match="no value and no reason"):
        tel.Reading("gpu_temperature", "dvalin", "C")


def test_a_reading_with_both_a_value_and_a_reason_cannot_be_built() -> None:
    """A value with an excuse attached is a reading a panel would render twice."""
    with pytest.raises(ValueError, match="both a value and a reason"):
        tel.Reading("gpu_temperature", "dvalin", "C", value=61.0, reason="probably fine")


def test_zero_is_a_measurement() -> None:
    """A throttle bitmask of zero means nothing is throttling. It is a reading."""
    reading = tel.Reading("throttle_reasons", "dvalin", "bitmask", value=0.0)
    assert reading.measured is True
    assert reading.as_payload()["value"] == 0.0
    assert reading.as_payload()["reason"] == ""


# ---------------------------------------------------------------------------
# The unmeasured paths. One per way the read can fail.
# ---------------------------------------------------------------------------


def test_with_no_client_every_reading_says_so() -> None:
    """A developer machine has no collector, and the panel says that in words."""
    readings = tel.Telemetry(client=None).appliance_temperatures(APPLIANCES)

    assert len(readings) == len(APPLIANCES)
    assert all(not item.measured for item in readings)
    assert all(item.value is None for item in readings)
    assert all("no telemetry client is configured" in item.reason for item in readings)


def test_an_unreachable_collector_is_a_reason_and_not_an_exception() -> None:
    """A wall panel has nothing useful to do with a traceback."""
    reader = tel.Telemetry(client=StubClient(error=OSError("connection refused")))

    readings = reader.appliance_temperatures(APPLIANCES)

    assert all(item.value is None for item in readings)
    assert all("could not be reached" in item.reason for item in readings)
    assert all("OSError" in item.reason for item in readings)


def test_a_non_200_is_a_reason_carrying_the_status() -> None:
    """An operator needs to know it answered, and what it said."""
    reader = tel.Telemetry(client=StubClient(response=StubResponse(status_code=503)))

    (first, *_rest) = reader.appliance_temperatures(APPLIANCES)

    assert first.value is None
    assert "503" in first.reason


def test_a_body_that_is_not_json_is_a_reason() -> None:
    """Something is answering on that port. It is not Prometheus."""
    reader = tel.Telemetry(client=StubClient(response=StubResponse(explode=True)))

    (first, *_rest) = reader.appliance_temperatures(APPLIANCES)

    assert first.value is None
    assert "not JSON" in first.reason


def test_a_refused_query_carries_prometheus_own_words() -> None:
    """A PromQL error is the one failure an operator can act on directly."""
    refused = StubResponse(
        body={"status": "error", "errorType": "bad_data", "error": "parse error at char 3"}
    )
    reader = tel.Telemetry(client=StubClient(response=refused))

    (first, *_rest) = reader.appliance_temperatures(APPLIANCES)

    assert first.value is None
    assert "parse error at char 3" in first.reason


def test_an_appliance_prometheus_does_not_know_is_present_with_a_reason() -> None:
    """A missing machine is not a smaller estate.

    The tempting alternative -- render whatever came back -- makes an appliance
    whose exporter died vanish from the panel entirely, which looks exactly
    like an estate that has always had two machines.
    """
    reader = tel.Telemetry(client=answering(series("dvalin:9400", 61.0)))

    readings = reader.appliance_temperatures(APPLIANCES)
    by_name = {item.subject: item for item in readings}

    assert set(by_name) == set(APPLIANCES)
    assert by_name["dvalin"].value == 61.0
    assert by_name["durin"].value is None
    assert "DCGM exporter" in by_name["durin"].reason
    assert by_name["dain"].value is None


def test_a_sample_that_does_not_parse_is_not_a_reading() -> None:
    """A string where a float belongs is treated as an absence, not a zero."""
    broken = {"metric": {"instance": "dvalin:9400"}, "value": [1, "not-a-number"]}
    reader = tel.Telemetry(client=answering(broken))

    (dvalin, *_rest) = reader.appliance_temperatures(APPLIANCES)

    assert dvalin.value is None


# ---------------------------------------------------------------------------
# The measured path
# ---------------------------------------------------------------------------


def test_a_temperature_comes_back_as_a_temperature() -> None:
    """The whole point of the finding."""
    reader = tel.Telemetry(
        client=answering(
            series("dvalin:9400", 61.5), series("durin:9400", 58.0), series("dain:9400", 72.25)
        )
    )

    by_name = {item.subject: item for item in reader.appliance_temperatures(APPLIANCES)}

    assert by_name["dvalin"].value == 61.5
    assert by_name["dain"].value == 72.25
    assert all(item.unit == "C" for item in by_name.values())
    assert all(item.reason == "" for item in by_name.values())


def test_the_port_is_trimmed_from_the_instance_label() -> None:
    """DCGM reports `dvalin:9400`; the estate calls the machine `dvalin`."""
    reader = tel.Telemetry(client=answering(series("dvalin:9400", 61.0)))

    (dvalin, *_rest) = reader.appliance_temperatures(["dvalin"])

    assert dvalin.value == 61.0


def test_a_machine_with_two_gpus_reports_the_hotter_one() -> None:
    """The mean would under-report exactly the case the alarm exists for."""
    reader = tel.Telemetry(
        client=answering(series("dvalin:9400", 55.0), series("dvalin:9400", 88.0))
    )

    (dvalin, *_rest) = reader.appliance_temperatures(["dvalin"])

    assert dvalin.value == 88.0


def test_the_query_names_the_configured_metric() -> None:
    """The exporter's names are configuration, so a rename is not a release."""
    client = answering()
    reader = tel.Telemetry(client=client, queries=tel.Queries(gpu_temperature="DCGM_RENAMED_TEMP"))

    reader.appliance_temperatures(["dvalin"])

    assert client.calls is not None
    (_url, params) = client.calls[0]
    assert params["query"] == "DCGM_RENAMED_TEMP"


def test_the_fabric_reading_says_where_it_would_come_from() -> None:
    """RF-E13 made DRAUPNIR the owner of the probe; ALVISS must be scraped."""
    reading = tel.Telemetry(client=answering()).fabric_bandwidth()

    assert reading.value is None
    assert "scrape target" in reading.reason
    assert reading.unit == "GB/s"


def test_the_fabric_baseline_says_where_it_would_come_from() -> None:
    """RF-28. A bandwidth with nothing to compare it to cannot say the fabric is healthy."""
    reading = tel.Telemetry(client=answering()).fabric_baseline()

    assert reading.value is None
    assert "DRAUPNIR_FABRIC_BASELINE_GBPS" in reading.reason
    assert reading.unit == "GB/s"


def test_a_recorded_baseline_comes_back_as_a_baseline() -> None:
    client = answering({"metric": {}, "value": [1_757_332_800, "235.6"]})

    reading = tel.Telemetry(client=client).fabric_baseline()

    assert reading.value == 235.6
    assert reading.reason == ""
    assert client.calls is not None
    assert client.calls[0][1]["query"] == "draupnir_fabric_baseline_gbps"


def test_one_read_covers_every_panel() -> None:
    """Three calls would give one panel three freshnesses."""
    reader = tel.Telemetry(client=answering(series("dvalin:9400", 61.0)))

    result = reader.read(APPLIANCES, now=NOW)

    assert len(result.readings) == len(APPLIANCES) * 2 + 2, "the fabric and its baseline"
    assert result.read_at == NOW
    assert result.source == tel.DEFAULT_PROMETHEUS
    assert len(result.measured) + len(result.unmeasured) == len(result.readings)


# ---------------------------------------------------------------------------
# The egress declaration. The acceptance criterion asks for exactly this.
# ---------------------------------------------------------------------------


def test_the_prometheus_read_is_declared_in_the_allow_list() -> None:
    """GULLINBURSTI declares; SVALINN decides. This is the join between them.

    They are siblings in the layering and neither imports the other, so the
    two strings can only be kept in step by something that reads both. This
    test failing is the intended way to find out Prometheus moved.
    """
    declared = [
        item
        for item in egress.ALLOW_LIST
        if item.purpose == tel.EGRESS_PURPOSE and item.approving_policy == tel.EGRESS_POLICY
    ]

    assert len(declared) == 1, "the telemetry read must be declared exactly once"
    assert declared[0].host in tel.DEFAULT_PROMETHEUS
    assert declared[0].matches(f"{tel.DEFAULT_PROMETHEUS}/api/v1/query")


def test_the_telemetry_approval_does_not_also_approve_job_submission() -> None:
    """Two entries for one host, because one entry would grant both.

    The broker checks the approving policy on every call, so a driver holding
    the scheduling approval cannot use it to read metrics and a console holding
    this one cannot use it to cancel a job.
    """
    broker = egress.EgressBroker()

    record = broker.check(
        egress.Call(
            url="http://regin.sindri.veldris.internal:6820/slurm/v0.0.40/job/1",
            purpose="cancelling a job with the telemetry approval",
            run_id=None,
            approving_policy=tel.EGRESS_POLICY,
            requested_at=NOW,
        )
    )

    assert record.permitted is False


def test_a_brokered_client_cannot_reach_an_undeclared_host() -> None:
    """The broker only helps where something is obliged to consult it."""
    inner = answering()
    client = egress.BrokeredClient(
        inner=inner,
        purpose=tel.EGRESS_PURPOSE,
        approving_policy=tel.EGRESS_POLICY,
        clock=lambda: NOW,
    )

    with pytest.raises(egress.UndeclaredDestinationError):
        client.get("http://prometheus.example.com:9090/api/v1/query")

    assert inner.calls is None, "a refused call must not reach the network"


def test_a_brokered_client_records_the_call_it_permitted() -> None:
    """Every outbound call leaves a record naming its purpose and policy."""
    broker = egress.EgressBroker()
    client = egress.BrokeredClient(
        inner=answering(),
        purpose=tel.EGRESS_PURPOSE,
        approving_policy=tel.EGRESS_POLICY,
        broker=broker,
        clock=lambda: NOW,
    )

    client.get(f"{tel.DEFAULT_PROMETHEUS}/api/v1/query", params={"query": "up"})

    (record,) = broker.records
    assert record.permitted is True
    assert record.as_log_context()["approvingPolicy"] == tel.EGRESS_POLICY
    assert "query" not in str(record.as_log_context()), "the broker logs no request body"


def test_telemetry_through_the_broker_reads_a_real_temperature() -> None:
    """The two halves together, which is how the deployment wires them."""
    reader = tel.Telemetry(
        client=egress.BrokeredClient(
            inner=answering(series("dvalin:9400", 61.0)),
            purpose=tel.EGRESS_PURPOSE,
            approving_policy=tel.EGRESS_POLICY,
            clock=lambda: NOW,
        )
    )

    (dvalin, *_rest) = reader.appliance_temperatures(["dvalin"])

    assert dvalin.value == 61.0
