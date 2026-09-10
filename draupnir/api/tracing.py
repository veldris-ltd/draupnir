"""Getting a span off this host. RF-18.

`telemetry.Tracer` collected spans into a list and nothing ever read it, so
"OpenTelemetry spans from edge through orchestrator to driver boundary" was a
shape a test could assert and a trace nobody could look at. Three OpenTelemetry
distributions sat in the lock file with nothing importing them.

**OTLP over HTTP with JSON, written here, rather than the SDK's exporter.**
That is not a preference about dependencies; it is the only option this
codebase's own rules leave open. Every outbound call in DRAUPNIR goes through
`svalinn.egress.BrokeredClient`, which refuses a destination the allow list does
not carry -- the JWKS fetch, the telemetry read, the anchor submission and the
readiness probes all do. An SDK exporter holds its own transport and opens its
own socket, so wiring one in would put the single call in this process that
nobody decided next to four that are, and threat T11 is egress with no allow
list decided. The encoding is a published part of the OTLP specification and
the whole of it that matters here is one JSON document.

**The redaction stays in the emitter.** A span's attributes are scrubbed by
`telemetry.Span.set` and by `Tracer.span` before they are ever held, so what
this exports has already been through `scrubbed`. Nothing here re-implements
that judgement, and nothing here can be handed an attribute that skipped it.

**No collector configured means no export, not a queue.** A forge without an
observability stack is the ordinary case -- the estate has one Prometheus on
REGIN and no collector at all -- so an unconfigured tracer discards its spans,
which is what it did before, and says nothing about it. What it must not do is
accumulate them: a list nobody drains is a memory leak with a plausible reason.

**An export that fails is a log line.** Losing a trace is not worth failing a
request that succeeded, and a control plane that 500s because its observability
stack is down has made the observability stack a dependency of the work.
"""

from __future__ import annotations

import os
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from draupnir.api.telemetry import Span

logger = structlog.get_logger(__name__)

#: OTLP/HTTP's path for traces, from the specification. Appended to the
#: configured endpoint unless it already names a path, which is the convention
#: every OTLP client follows.
TRACES_PATH = "/v1/traces"

#: How many spans one export carries. A trace of a request is a handful of
#: spans; this bounds a pathological case rather than describing a normal one.
MAX_SPANS = 512


def _nanoseconds(moment: datetime) -> str:
    """OTLP timestamps are nanoseconds since the epoch, as a decimal string.

    A string because the numbers exceed what JSON's `number` is required to
    represent exactly, and a collector reading 1.7e18 as a float loses the
    microseconds -- which is most of what a span is for.
    """
    return str(int(moment.timestamp() * 1_000_000_000))


def _attribute(key: str, value: Any) -> dict[str, Any]:
    """One OTLP key/value. Everything not a scalar becomes its string form."""
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _identifier(length: int) -> str:
    """A trace or span identifier: hex, of the length OTLP requires."""
    return os.urandom(length).hex()


def encode(
    spans: Sequence[Span],
    *,
    service: str,
    site_id: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    """One OTLP/HTTP JSON document for these span trees.

    Each root gets its own trace identifier and its children are parented to
    it, which is what makes "edge through orchestrator to driver boundary"
    readable in a collector rather than three unrelated spans.

    The instants are approximate and say so: `telemetry.Span` records a tree
    and not a clock, because what it was built to assert is the shape of a
    trace. Exporting a start and an end that are both "now" would draw
    zero-width bars; exporting the tree with one timestamp is honest about
    holding structure rather than duration.
    """
    moment = at or datetime.now(UTC)
    stamp = _nanoseconds(moment)
    collected: list[dict[str, Any]] = []

    def walk(span: Span, trace_id: str, parent: str | None) -> None:
        if len(collected) >= MAX_SPANS:
            return
        span_id = _identifier(8)
        record: dict[str, Any] = {
            "traceId": trace_id,
            "spanId": span_id,
            "name": span.name,
            "kind": 1,
            "startTimeUnixNano": stamp,
            "endTimeUnixNano": stamp,
            "attributes": [
                _attribute("draupnir.layer", span.layer),
                *(_attribute(key, value) for key, value in sorted(span.attributes.items())),
            ],
        }
        if parent:
            record["parentSpanId"] = parent
        collected.append(record)
        for child in span.children:
            walk(child, trace_id, span_id)

    for root in spans:
        walk(root, _identifier(16), None)

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attribute("service.name", service),
                        # The forge, so a collector serving the Forge Matrix can
                        # tell one site's traces from another's. This is the one
                        # resource attribute that is not boilerplate.
                        _attribute("draupnir.site_id", site_id),
                    ]
                },
                "scopeSpans": [{"scope": {"name": "draupnir"}, "spans": collected}],
            }
        ]
    }


@dataclass
class OtlpExporter:
    """Posts collected spans to a collector, through the broker.

    Sampled, because a trace per request at a forge's request rate is a lot of
    documents to carry for a signal whose value is in the shape rather than in
    the census -- and an exporter that cannot be turned down is one that gets
    turned off.
    """

    #: Where the collector answers. Empty means no export, which is the
    #: ordinary case and not a fault.
    endpoint: str = ""
    #: The brokered client. `None` alongside a configured endpoint would be a
    #: misconfiguration, and `export` says so once rather than per span.
    client: Any = None
    service: str = "draupnir-api"
    site_id: str = "sindri"
    #: Fraction of traces exported. 1.0 exports everything, which is right for
    #: a forge doing tens of jobs a day and adjustable for one that is not.
    sample: float = 1.0
    #: Injected so a test does not have to win a coin toss.
    roll: Any = field(default=random.random)

    @property
    def configured(self) -> bool:
        """Whether anything would be exported."""
        return bool(self.endpoint)

    def url(self) -> str:
        """Where to post, appending OTLP's path when the endpoint omits it."""
        base = self.endpoint.rstrip("/")
        return base if base.endswith(TRACES_PATH) else f"{base}{TRACES_PATH}"

    def export(self, spans: Iterable[Span]) -> bool:
        """Send these spans, returning whether anything was sent.

        `False` covers three different situations that are all fine: nothing
        configured, nothing to send, and this trace not sampled. It is a
        statement about what happened, not a failure -- a failure is logged and
        also returns `False`, because a caller has nothing useful to do with
        either.
        """
        collected = list(spans)
        if not self.configured or not collected:
            return False
        if self.sample < 1.0 and self.roll() >= self.sample:
            return False
        if self.client is None:
            logger.warning("tracing.export.unconfigured", endpoint=self.endpoint)
            return False

        document = encode(collected, service=self.service, site_id=self.site_id)
        try:
            self.client.post(self.url(), json=document)
        except Exception as unreachable:
            # Never fatal. Losing a trace must not fail a request that
            # succeeded, and a control plane that 500s because its collector is
            # down has made the collector a dependency of the work.
            logger.warning("tracing.export.failed", reason=str(unreachable))
            return False
        return True


#: The process-wide exporter. Unconfigured by default, so a process that never
#: wires one discards its spans exactly as it did before RF-18.
EXPORTER = OtlpExporter()


def exporter() -> OtlpExporter:
    """The current exporter, resolved at call time."""
    return EXPORTER


def set_exporter(chosen: OtlpExporter) -> None:
    """Install the exporter. Called by the lifespan, and by tests."""
    global EXPORTER
    EXPORTER = chosen


def drain(tracer: Any, *, using: OtlpExporter | None = None) -> bool:
    """Export whatever a tracer has collected, and forget it either way.

    Cleared even when nothing was exported, and that is the point: the tracer
    was a list that grew for the life of the process because nothing ever read
    it. A forge with no collector configured must not accumulate spans it will
    never send, so the discard is unconditional and the export is what is
    optional.
    """
    roots = list(getattr(tracer, "roots", ()))
    try:
        return (using or exporter()).export(roots)
    finally:
        tracer.clear()


def attributes_of(document: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten an encoded document's span attributes, for a test to read."""
    found: dict[str, Any] = {}
    for resource in document.get("resourceSpans", []):
        for scope in resource.get("scopeSpans", []):
            for span in scope.get("spans", []):
                for item in span.get("attributes", []):
                    found[item["key"]] = next(iter(item["value"].values()))
    return found


__all__ = [
    "EXPORTER",
    "MAX_SPANS",
    "TRACES_PATH",
    "OtlpExporter",
    "attributes_of",
    "drain",
    "encode",
    "exporter",
    "set_exporter",
]
