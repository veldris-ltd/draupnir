"""Spans reaching a collector, and not reaching anything else. RF-18.

`telemetry.Tracer` collected spans into a list and nothing ever read it, so
"OpenTelemetry spans from edge through orchestrator to driver boundary" was a
shape a test could assert and a trace nobody could look at. Three OpenTelemetry
distributions sat in the lock file with nothing importing them.

The stub collector here is a client, not a server: what matters is that a span
becomes a well formed OTLP document and that the document goes through the
egress broker, and both of those are decided before a socket would be opened.
The broker's own refusal is exercised too, because a tracer that quietly went
round it would be the one outbound call in the process nobody decided.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from draupnir.api import telemetry, tracing
from draupnir.svalinn.egress import BrokeredClient, EgressError

AT = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
COLLECTOR = "http://regin.sindri.veldris.internal:4318"


class Collector:
    """Records what was posted, without opening anything."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, Any]] = []

    def post(self, url: str, *, json: Any = None, headers: Any = None) -> Any:
        del headers
        self.posts.append((url, json))
        return type("Response", (), {"status_code": 200})()

    def get(self, url: str, *, params: Any = None) -> Any:  # pragma: no cover - unused
        del url, params
        raise AssertionError("the exporter should post, not get")


class Failing:
    """A collector that is down, which must not fail the request."""

    def post(self, url: str, *, json: Any = None, headers: Any = None) -> Any:
        del url, json, headers
        msg = "connection refused"
        raise OSError(msg)


def traced() -> telemetry.Tracer:
    """A tracer holding one edge span with a driver span beneath it."""
    tracer = telemetry.Tracer()
    # Nested rather than combined into one `with`, which is equivalent and
    # reads as three unrelated spans. The indentation is the shape being
    # asserted below.
    with tracer.span("runs.submit", telemetry.EDGE, runId="019cb993"):  # noqa: SIM117
        with tracer.span("orchestrator.transition", "application"):
            with tracer.span("slurmrest.submit", "driver"):
                pass
    return tracer


# ---------------------------------------------------------------------------
# A span reaches a collector
# ---------------------------------------------------------------------------


def test_a_span_reaches_a_configured_collector() -> None:
    collector = Collector()
    exporter = tracing.OtlpExporter(endpoint=COLLECTOR, client=collector, site_id="sindri")

    assert exporter.export(traced().roots) is True
    assert len(collector.posts) == 1
    url, document = collector.posts[0]
    assert url == f"{COLLECTOR}/v1/traces"
    assert document["resourceSpans"], document


def test_the_trace_keeps_the_shape_from_edge_to_driver() -> None:
    """The claim the local `Tracer` was built to make, now visible in a collector.

    Three layers, parented: an exporter that flattened them would produce three
    unrelated spans and lose the one thing this was for.
    """
    collector = Collector()
    tracing.OtlpExporter(endpoint=COLLECTOR, client=collector).export(traced().roots)

    _url, document = collector.posts[0]
    spans = document["resourceSpans"][0]["scopeSpans"][0]["spans"]
    by_name = {span["name"]: span for span in spans}

    assert set(by_name) == {"runs.submit", "orchestrator.transition", "slurmrest.submit"}
    assert "parentSpanId" not in by_name["runs.submit"]
    assert by_name["orchestrator.transition"]["parentSpanId"] == by_name["runs.submit"]["spanId"]
    assert (
        by_name["slurmrest.submit"]["parentSpanId"]
        == (by_name["orchestrator.transition"]["spanId"])
    )
    assert len({span["traceId"] for span in spans}) == 1, "one request is one trace"


def test_the_layer_travels_as_an_attribute() -> None:
    document = tracing.encode(traced().roots, service="draupnir-api", site_id="sindri")

    assert tracing.attributes_of(document)["draupnir.layer"] in {"edge", "application", "driver"}


def test_the_site_travels_as_a_resource_attribute() -> None:
    """So a collector serving the Forge Matrix can tell one site from another."""
    document = tracing.encode(traced().roots, service="draupnir-api", site_id="brokkr")
    resource = document["resourceSpans"][0]["resource"]["attributes"]

    assert {"key": "draupnir.site_id", "value": {"stringValue": "brokkr"}} in resource


def test_timestamps_are_nanosecond_strings() -> None:
    """Strings because the value exceeds what a JSON number represents exactly.

    A collector reading 1.7e18 as a float loses the microseconds, which is most
    of what a span is for.
    """
    document = tracing.encode(traced().roots, service="s", site_id="sindri", at=AT)
    span = document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]

    assert isinstance(span["startTimeUnixNano"], str)
    assert span["startTimeUnixNano"] == str(int(AT.timestamp() * 1_000_000_000))


# ---------------------------------------------------------------------------
# And nothing reaches anywhere it should not
# ---------------------------------------------------------------------------


def test_nothing_is_exported_when_no_collector_is_configured() -> None:
    """The ordinary case: the estate has no collector at all."""
    collector = Collector()

    assert tracing.OtlpExporter(endpoint="", client=collector).export(traced().roots) is False
    assert collector.posts == []


def test_the_tracer_is_cleared_even_when_nothing_is_exported() -> None:
    """The leak, which is the half that was actually costing something.

    A list nobody drains grows for the life of the process. It grew for the
    life of every process this system has ever run, because the export was
    never wired -- so the discard has to be unconditional and the export the
    optional part.
    """
    tracer = traced()
    assert tracer.roots

    assert tracing.drain(tracer, using=tracing.OtlpExporter()) is False

    assert tracer.roots == []


def test_a_collector_that_is_down_does_not_fail_the_request() -> None:
    """A collector that is down is not the request's problem.

    A control plane that 500s because its observability stack is down has made
    the observability stack a dependency of the work.
    """
    exporter = tracing.OtlpExporter(endpoint=COLLECTOR, client=Failing())

    assert exporter.export(traced().roots) is False


def test_an_unsampled_trace_is_not_exported() -> None:
    collector = Collector()
    exporter = tracing.OtlpExporter(
        endpoint=COLLECTOR, client=collector, sample=0.5, roll=lambda: 0.9
    )

    assert exporter.export(traced().roots) is False
    assert collector.posts == []


def test_a_sampled_trace_is() -> None:
    collector = Collector()
    exporter = tracing.OtlpExporter(
        endpoint=COLLECTOR, client=collector, sample=0.5, roll=lambda: 0.1
    )

    assert exporter.export(traced().roots) is True


# ---------------------------------------------------------------------------
# The broker, which is why the SDK exporter is not used
# ---------------------------------------------------------------------------


def test_the_export_goes_through_the_broker() -> None:
    """Every outbound call in this system does, and this is no exception.

    An SDK exporter holds its own transport and opens its own socket, so wiring
    one in would put the single call in the process that no allow list decided
    next to four that are -- which is threat T11 exactly, and the reason the
    encoding is written here rather than imported.
    """
    from draupnir.svalinn.egress import TRACING_POLICY, TRACING_PURPOSE

    inner = Collector()
    brokered = BrokeredClient(inner=inner, purpose=TRACING_PURPOSE, approving_policy=TRACING_POLICY)

    assert tracing.OtlpExporter(endpoint=COLLECTOR, client=brokered).export(traced().roots) is True
    assert inner.posts, "the broker permitted the call but nothing was sent"


def test_a_collector_nobody_allow_listed_is_refused() -> None:
    """And the refusal is a warning, not a failed request."""
    from draupnir.svalinn.egress import TRACING_POLICY, TRACING_PURPOSE

    inner = Collector()
    brokered = BrokeredClient(inner=inner, purpose=TRACING_PURPOSE, approving_policy=TRACING_POLICY)
    exporter = tracing.OtlpExporter(endpoint="http://somewhere.invalid:4318", client=brokered)

    assert exporter.export(traced().roots) is False
    assert inner.posts == [], "a call the broker refused was made anyway"


def test_the_scheduling_approval_cannot_be_spent_on_the_collector() -> None:
    """Two entries on one host mean two permissions, and this is the third.

    REGIN carries slurmrestd, Prometheus and -- once one is deployed -- a
    collector. Each is a different port, a different reason and a different
    approving policy, so a driver holding the scheduling approval cannot post
    traces with it.
    """
    from draupnir.svalinn.egress import SCHEDULING_POLICY, SCHEDULING_PURPOSE

    inner = Collector()
    wrong = BrokeredClient(
        inner=inner, purpose=SCHEDULING_PURPOSE, approving_policy=SCHEDULING_POLICY
    )

    with pytest.raises(EgressError):
        wrong.post(f"{COLLECTOR}/v1/traces", json={})

    assert inner.posts == []


# ---------------------------------------------------------------------------
# The redaction is the emitter's, and it still is
# ---------------------------------------------------------------------------


def test_an_attribute_is_scrubbed_before_it_can_be_exported() -> None:
    """Nothing here re-implements that judgement, and nothing here can skip it.

    A span's attributes are scrubbed by `Tracer.span` and by `Span.set` before
    they are held at all, so what reaches the encoder has already been through
    it.
    """
    tracer = telemetry.Tracer()
    with tracer.span("runs.submit", telemetry.EDGE, authorization="Bearer secret-value") as span:
        span.set(client_secret="another-one")

    document = tracing.encode(tracer.roots, service="s", site_id="sindri")
    attributes = tracing.attributes_of(document)

    assert attributes["authorization"] == telemetry.REDACTED
    assert attributes["client_secret"] == telemetry.REDACTED
    assert "secret-value" not in str(document)
    assert "another-one" not in str(document)


# ---------------------------------------------------------------------------
# One tracer per request
# ---------------------------------------------------------------------------


def test_a_request_collects_into_its_own_tracer() -> None:
    """Two requests in flight must not nest into each other.

    A process-wide tracer holds one open-span stack, so whichever request
    opened a span first becomes the parent of whatever the other opens next.
    That was invisible while the spans were discarded; exported, it is a trace
    showing one operator's approval under another operator's submission.
    """
    with telemetry.collecting() as first, telemetry.span("runs.submit"):
        pass

    with telemetry.collecting() as second, telemetry.span("gates.decide"):
        pass

    assert [root.name for root in first.roots] == ["runs.submit"]
    assert [root.name for root in second.roots] == ["gates.decide"]


def test_outside_a_request_spans_go_to_the_process_tracer() -> None:
    """The worker and the procedures still have somewhere to put a span."""
    telemetry.TRACER.clear()

    with telemetry.span("worker.tick", "application"):
        pass

    assert [root.name for root in telemetry.TRACER.roots] == ["worker.tick"]
    telemetry.TRACER.clear()
