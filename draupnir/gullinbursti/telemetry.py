"""Reading the estate's own measurements back out of Prometheus.

SAD 11.3 gives appliance thermal and throttle a source — the DCGM exporter —
and a surface: CON-B dashboard 1, and CON-A locally on DVALIN. The source
exists. VLD-INF-SINDRI-001 section 34 step 6 installs `datacenter-gpu-manager`
and the DCGM exporter on every appliance, and step 7 has Prometheus on REGIN
scraping them at `:9400`.

What was missing is the wire between that and the control plane. ALVISS is not
a scrape target, DRAUPNIR queries nothing, and `Kiosk.tsx` filled the gap by
deriving "under load" and "idle" from run state — which is not a temperature,
on a panel whose heading says thermal. This is the wire.

**Read only, and deliberately so.** GULLINBURSTI reports capacity and health
(SAD 11A.1); it does not collect. The DCGM exporter is the collector and
Prometheus is the store, both of them the estate's, and a control plane that
built a second path to the same GPUs would be a second thing to keep in step
with a driver upgrade.

**Nothing here invents a reading.** A query that fails, times out, returns no
series or returns something unparseable produces a `Reading` with no value and
a reason. SAD 11.3's alarm thresholds are meaningless against a fabricated
zero, and a panel showing 0 °C in green is worse than a panel saying it does
not know: the first is believed.

**The metric names are configuration.** They are the exporter's, they change
between its versions, and section 48.2 already warns that an update can rename
things underneath this estate. A name compiled in here would be one nobody
could correct without a release.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Protocol

#: Where Prometheus answers at Sindri. REGIN, on the management fabric.
DEFAULT_PROMETHEUS: Final = "http://regin.sindri.veldris.internal:9090"

#: What the call declares to the egress broker. SVALINN holds the allow list
#: and this module may not import it -- they are siblings in the layering, and
#: a sibling import is the edge nobody notices until the two disagree. So the
#: declaration lives here, beside the call that makes it, and a test asserts
#: `svalinn.egress.ALLOW_LIST` carries a destination matching all three. That
#: test failing is the intended way to find out somebody moved Prometheus.
EGRESS_PURPOSE: Final = "reading appliance thermal, throttle and fabric measurements"
EGRESS_POLICY: Final = "observability/2026.01"

#: The one status that means Prometheus answered the question asked.
OK: Final = 200

#: How long a panel is willing to wait. A wall display refreshing on a timer
#: would rather say "unmeasured" than hang: the staleness banner is only
#: honest if the page renders.
TIMEOUT_SECONDS: Final = 5


@dataclass(frozen=True, slots=True)
class Reading:
    """One measurement, or the reason there is not one.

    The two are one type because every caller has to handle both, and a
    `float | None` with the reason somewhere else is how a caller comes to
    render `None` as zero.
    """

    metric: str
    subject: str
    unit: str
    value: float | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        """A reading is a value or a reason, never neither and never both."""
        if self.value is None and not self.reason:
            msg = (
                f"a reading of {self.metric} for {self.subject} has no value and no reason. "
                "Something has to be shown on the panel, and 'unmeasured' without a cause "
                "is what an operator ignores."
            )
            raise ValueError(msg)
        if self.value is not None and self.reason:
            msg = f"a reading of {self.metric} for {self.subject} has both a value and a reason"
            raise ValueError(msg)

    @property
    def measured(self) -> bool:
        """Whether there is a number here."""
        return self.value is not None

    def as_payload(self) -> dict[str, Any]:
        """The shape a console renders. `null` is never a zero."""
        return {
            "metric": self.metric,
            "subject": self.subject,
            "unit": self.unit,
            "value": self.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class Queries:
    """The PromQL this forge's exporters answer to.

    Configuration rather than constants: these are the exporter's names, they
    change between its versions, and correcting one should be a configuration
    change rather than a release.

    `fabric_bandwidth` is DRAUPNIR's own: RF-E13 settled that the control plane
    owns the hourly probe, so the number reaches Prometheus by ALVISS being
    scraped rather than by a second probe job. Until that scrape target exists
    the query returns nothing, and the panel says so.
    """

    gpu_temperature: str = "DCGM_FI_DEV_GPU_TEMP"
    throttle_reasons: str = "DCGM_FI_DEV_CLOCK_THROTTLE_REASONS"
    fabric_bandwidth: str = "draupnir_fabric_bus_bandwidth_gbps"

    #: The label carrying the machine. DCGM reports `instance` as `host:port`,
    #: so the port is trimmed before it is matched against an appliance name.
    instance_label: str = "instance"


class Response(Protocol):
    """The part of an HTTP response this needs."""

    status_code: int

    def json(self) -> Any:
        """The decoded body."""
        ...


class Client(Protocol):
    """The part of an HTTP client this needs.

    Injected, like the slurmrestd driver's, so the composition root can supply
    one that goes through SVALINN's egress broker. `regin.sindri.veldris
    .internal` is a declared destination; a client built here would be an
    outbound call nobody declared.
    """

    def get(self, url: str, *, params: Mapping[str, str] | None = ...) -> Response:
        """Perform one query."""
        ...


@dataclass
class Telemetry:
    """Reads the estate's measurements. Never collects, never invents one."""

    base_url: str = DEFAULT_PROMETHEUS
    queries: Queries = field(default_factory=Queries)
    client: Client | None = field(default=None, repr=False)
    timeout: float = TIMEOUT_SECONDS

    def appliance_temperatures(self, appliances: Sequence[str]) -> tuple[Reading, ...]:
        """The GPU temperature of each appliance, in degrees Celsius.

        Every named appliance gets a reading, including the ones Prometheus
        knows nothing about: a panel that simply omitted a machine would look
        identical to a panel showing an estate that is one machine smaller.
        """
        return self._per_appliance(
            appliances, self.queries.gpu_temperature, metric="gpu_temperature", unit="C"
        )

    def appliance_throttles(self, appliances: Sequence[str]) -> tuple[Reading, ...]:
        """The clock throttle reason bitmask of each appliance.

        A number rather than a word, because the exporter reports a bitmask and
        naming the bits is the console's job. Zero here is a real reading and
        means nothing is throttling, which is exactly why an unavailable one
        must not also be zero.
        """
        return self._per_appliance(
            appliances, self.queries.throttle_reasons, metric="throttle_reasons", unit="bitmask"
        )

    def fabric_bandwidth(self) -> Reading:
        """The last bus bandwidth the hourly probe recorded, in GB/s."""
        samples, failure = self._query(self.queries.fabric_bandwidth)
        if failure:
            return Reading("fabric_bandwidth", "baugr", "GB/s", reason=failure)
        if not samples:
            return Reading(
                "fabric_bandwidth",
                "baugr",
                "GB/s",
                reason=(
                    "Prometheus holds no reading for the fabric probe. The control plane "
                    "records it hourly (SAD 11.3); it reaches Prometheus by ALVISS being a "
                    "scrape target."
                ),
            )
        return Reading("fabric_bandwidth", "baugr", "GB/s", value=next(iter(samples.values())))

    def read(self, appliances: Sequence[str], *, now: datetime) -> EstateTelemetry:
        """Every reading for `appliances`, plus the fabric, in one answer."""
        return EstateTelemetry(
            readings=(
                *self.appliance_temperatures(appliances),
                *self.appliance_throttles(appliances),
                self.fabric_bandwidth(),
            ),
            read_at=now,
            source=self.base_url,
        )

    # -- internals ---------------------------------------------------------

    def _per_appliance(
        self, appliances: Sequence[str], query: str, *, metric: str, unit: str
    ) -> tuple[Reading, ...]:
        samples, failure = self._query(query)
        readings: list[Reading] = []
        for name in appliances:
            if failure:
                readings.append(Reading(metric, name, unit, reason=failure))
                continue
            found = samples.get(name)
            if found is None:
                readings.append(
                    Reading(
                        metric,
                        name,
                        unit,
                        reason=(
                            f"Prometheus reports no {query} for {name}. The DCGM exporter "
                            "may not be running on it (VLD-INF-SINDRI-001 section 34 step 6)."
                        ),
                    )
                )
                continue
            readings.append(Reading(metric, name, unit, value=found))
        return tuple(readings)

    def _query(self, query: str) -> tuple[dict[str, float], str]:
        """Run one instant query. Returns the samples by machine, or a reason.

        Every failure here is a reason rather than an exception. A wall panel
        refreshing on a timer has nothing useful to do with a traceback, and
        the alternative to a reason is a blank tile.
        """
        if self.client is None:
            return {}, "no telemetry client is configured, so nothing was asked"

        try:
            response = self.client.get(
                f"{self.base_url.rstrip('/')}/api/v1/query", params={"query": query}
            )
        # Broad, and deliberately: an HTTP client raises several unrelated types
        # for the same operational fact, and a panel has nothing useful to do
        # with any of them beyond saying the collector did not answer.
        except Exception as error:
            return {}, f"Prometheus at {self.base_url} could not be reached: {type(error).__name__}"

        if response.status_code != OK:
            return {}, f"Prometheus answered {response.status_code} for {query}"

        try:
            payload = response.json()
        except Exception:
            return {}, f"Prometheus returned a body that is not JSON for {query}"

        if not isinstance(payload, Mapping) or payload.get("status") != "success":
            return {}, f"Prometheus refused {query}: {_error_of(payload)}"

        result = (payload.get("data") or {}).get("result") or []
        samples: dict[str, float] = {}
        for series in result:
            if not isinstance(series, Mapping):
                continue
            instance = str((series.get("metric") or {}).get(self.queries.instance_label, ""))
            machine = instance.split(":", 1)[0]
            value = _value_of(series.get("value"))
            if machine and value is not None:
                # The maximum across a machine's GPUs. A Spark has one, but a
                # panel reporting the mean of a hot GPU and a cold one would
                # under-report exactly the case the alarm exists for.
                samples[machine] = max(value, samples.get(machine, value))
        return samples, ""


@dataclass(frozen=True, slots=True)
class EstateTelemetry:
    """Every reading a panel needs, in one answer.

    One call rather than three, because three calls give a panel three
    freshnesses and a chance to render two of them beside a stale third.
    """

    readings: tuple[Reading, ...]
    read_at: datetime
    source: str

    @property
    def measured(self) -> tuple[Reading, ...]:
        """The readings that have a number."""
        return tuple(item for item in self.readings if item.measured)

    @property
    def unmeasured(self) -> tuple[Reading, ...]:
        """The readings that do not, each with the reason why."""
        return tuple(item for item in self.readings if not item.measured)

    def as_payload(self) -> dict[str, Any]:
        """The wire shape."""
        return {
            "readAt": self.read_at.isoformat(),
            "source": self.source,
            "readings": [item.as_payload() for item in self.readings],
        }


def _value_of(raw: Any) -> float | None:
    """Prometheus reports a sample as `[timestamp, "value"]`, the value a string."""
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes) or len(raw) < 2:
        return None
    try:
        return float(raw[1])
    except (TypeError, ValueError):
        return None


def _error_of(payload: Any) -> str:
    """Whatever Prometheus said about why it refused."""
    if isinstance(payload, Mapping):
        return str(payload.get("error") or payload.get("errorType") or "no reason given")
    return "no reason given"


def unavailable(appliances: Sequence[str], reason: str) -> tuple[Reading, ...]:
    """Readings for an estate nothing could be asked about.

    Used where telemetry is not configured at all, so that a console renders
    the same shape either way and there is one code path through it.
    """
    return tuple(
        Reading(metric, name, unit, reason=reason)
        for name in appliances
        for metric, unit in (("gpu_temperature", "C"), ("throttle_reasons", "bitmask"))
    )


#: Injected by the composition root, so a test can supply one that answers.
TelemetryFactory = Callable[[], Telemetry]

__all__ = [
    "DEFAULT_PROMETHEUS",
    "EGRESS_POLICY",
    "EGRESS_PURPOSE",
    "TIMEOUT_SECONDS",
    "Client",
    "EstateTelemetry",
    "Queries",
    "Reading",
    "Response",
    "Telemetry",
    "TelemetryFactory",
    "unavailable",
]
