"""The SAD 11.3 signals, as something a scrape can read. RF-18.

`GET /metrics` returned `prometheus_client.generate_latest()` and nothing
registered a collector, so it exposed four Python garbage-collector counters
and no DRAUPNIR signal at all. SAD 8.1 lists the endpoint as operational and
SAD 11.3 gives eight signals a source and a surface; the ones that reach a
structlog finding did, and nothing reached a scrape.

**No unbounded label, anywhere.** The endpoint's own docstring already said so
-- "a metric labelled by actor is a metric with an unbounded label set, and one
labelled by artefact is a cardinality problem that also happens to leak what is
being built" -- and nothing enforced it, because there were no metrics to
enforce it on. `LABELS` below is the complete permitted set and a test fails on
anything else. The trap this actually guards is the request histogram: labelling
by request *path* puts a run identifier in a label, which is both an unbounded
series and a leak, so it labels by the route *template* instead.

**Where each number comes from.** Most are columns: run state and queue depth
from `run`, gate pass rates and margins from `gate_result`, anchor freshness
from `site.last_anchored_at`. Two are not, and those the worker measures and
writes to `duty_measurement` (migration 0005) for this to read:

* verifying the chain is the hourly duty's entire cost, and doing it inside a
  scrape would make Prometheus's interval the rate at which this site rehashes
  its ledger;
* asking an NFS mount how full it is can block uninterruptibly, and a scrape
  that blocks is a target Prometheus marks down -- reporting the control plane
  as gone because the vault is slow.

Request latency and status are per process and stay in this process's registry,
which is right: they describe *this* process's work. Everything else is a fact
about the site, so every API process reports the same value -- SAD 5.1 runs two
to four of them. Aggregate those with `max by (site)` rather than `sum`; the
`HELP` text on each says so, because a dashboard that sums them reports three
times the queue depth and nothing anywhere says why.

**A scrape reads the database, and that is proportionate.** Four small
aggregates at Prometheus's interval, on a control plane doing tens of jobs a
day. The alternative -- a background refresher holding numbers in memory -- adds
a staleness nobody can see and a task to supervise, to save a query that costs
less than the HTTP request wrapping it.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from prometheus_client import CollectorRegistry, Histogram, disable_created_metrics
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

logger = structlog.get_logger(__name__)

# `prometheus_client` emits a `_created` gauge beside every counter and
# histogram, carrying the unix time the series was first observed. Nothing here
# reads it, it doubles the series count, and it is per process -- so on a
# restart it changes for every metric at once, which reads on a graph as though
# something happened. Off.
# `prometheus_client` ships no annotation for it, hence the ignore.
disable_created_metrics()  # type: ignore[no-untyped-call]

#: Every label any DRAUPNIR metric may carry. Bounded, each of them: fourteen
#: run states, a handful of gate names, the HTTP methods, the route templates
#: the application registers, and the status codes it returns. A test asserts
#: nothing else appears -- and in particular that `actor`, `run_id` and
#: `artefact` do not.
LABELS: frozenset[str] = frozenset({"state", "gate", "method", "route", "status", "duty"})

#: Names that must never be a label. Two of them are unbounded -- a run
#: identifier and an artefact digest are new on every row -- and all three name
#: the thing being built or the person building it, which is what SAD 8.1 keeps
#: off an endpoint served without a credential.
FORBIDDEN_LABELS: frozenset[str] = frozenset(
    {"actor", "run_id", "runid", "artefact", "artefact_sha256", "subject", "user", "principal"}
)

#: Which SAD 11.3 signal each metric surfaces. A metric with no row here has no
#: documented surface, and a test refuses it -- which is the whole point of
#: writing the mapping down rather than assuming it.
SIGNALS: Mapping[str, str] = {
    "draupnir_runs": "Run state and queue depth",
    "draupnir_gate_results": "Gate pass rates and margins",
    "draupnir_gate_margin": "Gate pass rates and margins",
    "draupnir_vault_used_ratio": "Vault capacity",
    "draupnir_chain_verified": "Ledger chain integrity",
    "draupnir_anchor_age_seconds": "Anchor freshness",
    "draupnir_duty_alarm": "Ledger chain integrity",
    "draupnir_fabric_bus_bandwidth_gbps": "Fabric bandwidth probe",
    "draupnir_fabric_baseline_gbps": "Fabric bandwidth probe",
    # SAD 11.3 has no row for this one. See `docs/fixes/proposed-sad-amendments.md`:
    # the table gives eight signals a source and a surface and omits the
    # control plane's own request path, so an operator asking "is the API
    # slow" has no signal to read. Recorded here as the amendment names it
    # rather than silently mapped onto a row that means something else.
    "draupnir_http_request_duration_seconds": "Control plane request latency and status",
}

#: Two of SAD 11E.4's five named metrics are not here, and the reason is the
#: same for both: they are the worker's, and the worker has no scrape surface.
#:
#: **Transition latency** and **driver failure rate** are properties of work
#: the worker performs -- how long a run took to move between states, and how
#: often a driver call failed. This process can see neither. The two signals
#: below that the worker measures reach a scrape through `duty_measurement`
#: because they are periodic readings with a natural place to be written; a
#: latency histogram and a failure counter are not readings, they are
#: distributions, and a table holding the latest one would lose the shape that
#: makes them worth having.
#:
#: Giving the worker a scrape endpoint is the answer and it is its own piece of
#: work: it needs a port, a binding decision to match SAD 8.1's loopback rule,
#: and a second target in the scrape configuration. Recorded in RF-18's entry
#: rather than half-built here.
UNEXPOSED: Mapping[str, str] = {
    "transition latency": "the worker performs transitions; it has no scrape endpoint",
    "driver failure rate": "the worker calls drivers; it has no scrape endpoint",
}

#: How long a request took, by method, route template and status. Per process
#: and deliberately: this describes the work this process did, so `sum by` is
#: the right aggregation across instances -- unlike everything else here.
#:
#: Buckets chosen against AC-N4, which wants a 500-run list under 300 ms at the
#: 95th percentile: there are three buckets either side of that figure, so the
#: quantile can be estimated near the threshold rather than interpolated across
#: a decade.
REQUEST_SECONDS = Histogram(
    "draupnir_http_request_duration_seconds",
    "Control plane request latency by route template and status. Labelled by "
    "template rather than path: a path carries run identifiers, which is an "
    "unbounded label set and a leak. Per process; aggregate with sum by.",
    labelnames=("method", "route", "status"),
    buckets=(0.005, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.5, 5.0, 10.0),
)

#: What a route is called when the request matched none. A 404 for
#: `/v1/runs/<uuid>/nonsense` must not create a series per path tried, which is
#: how an unauthenticated scrape endpoint becomes a way to fill a disk.
UNMATCHED = "unmatched"


@dataclass
class SiteFacts:
    """One scrape's worth of numbers, read from the database.

    A value object rather than a query, so the collector below can be exercised
    without a database and so the reading and the rendering can be wrong
    separately.
    """

    site_id: str
    #: Run count by state. Absent states are absent rather than zero: a gauge
    #: that reports zero for a state this forge has never used is a series
    #: carried forever for a number that means nothing.
    runs: Mapping[str, int] = field(default_factory=dict)
    #: Gate outcomes, `(gate, passed) -> count`.
    gate_results: Mapping[tuple[str, bool], int] = field(default_factory=dict)
    #: The mean margin by gate, where a margin was recorded.
    gate_margins: Mapping[str, float] = field(default_factory=dict)
    #: Fraction of the vault in use, from the worker's duty. `None` when it has
    #: not run yet, which is not the same as zero.
    vault_used_ratio: float | None = None
    #: Whether the last chain verification found the chain intact.
    chain_verified: bool | None = None
    #: How long since this site last anchored its head, in seconds.
    anchor_age_seconds: float | None = None
    #: Which duties are currently alarming, by name.
    duty_alarms: Mapping[str, bool] = field(default_factory=dict)
    #: The fabric probe's last bus bandwidth, in GB/s (RF-28). `None` where the
    #: probe has not produced one, which is not a dead fabric.
    fabric_bandwidth_gbps: float | None = None
    #: The commissioned baseline the probe compares against. `None` where none
    #: is configured, so a panel can say the alarm cannot be judged.
    fabric_baseline_gbps: float | None = None


class SiteCollector(Collector):
    """Renders `SiteFacts` as Prometheus metrics, on scrape.

    A custom collector rather than gauges the application sets, because these
    are facts about the site rather than events in this process. A gauge that
    something has to remember to update is a gauge that is stale exactly when
    the thing that updates it has stopped -- which is the moment an operator
    starts reading it.
    """

    def __init__(self, read: Any) -> None:
        """Collect by calling `read()`, which returns `SiteFacts` or `None`."""
        self._read = read

    def describe(self) -> Iterator[Metric]:
        """The families this collector can produce, without producing them.

        `CollectorRegistry.register` builds its name index by calling
        `collect()` unless a collector offers this -- so without it, *starting*
        the process performs a database query. Against a database that is down
        that query waits out the connection timeout, and startup takes as long
        as the timeout allows: the readiness test that starts an API pointed at
        a port nothing is listening on went from seconds to over a minute, and
        the process it was meant to prove still answers was reported dead.
        Which is precisely the failure SAD 11.2 says must not happen -- the
        database being unreachable must be visible on `/readyz`, not fatal at
        startup.

        Empty families, because describing is about names and labels. A
        registry only needs to know what this collector claims so it can refuse
        a second one claiming the same.
        """
        yield GaugeMetricFamily("draupnir_runs", "Runs by state.", labels=["state"])
        yield GaugeMetricFamily(
            "draupnir_gate_results", "Gate results by gate and outcome.", labels=["gate", "state"]
        )
        yield GaugeMetricFamily(
            "draupnir_gate_margin", "Mean margin over baseline by gate.", labels=["gate"]
        )
        yield GaugeMetricFamily("draupnir_vault_used_ratio", "Fraction of the vault in use.")
        yield GaugeMetricFamily("draupnir_chain_verified", "Whether the chain verified.")
        yield GaugeMetricFamily("draupnir_anchor_age_seconds", "Seconds since the last anchor.")
        yield GaugeMetricFamily(
            "draupnir_duty_alarm", "Whether a duty's last reading alarmed.", labels=["duty"]
        )

    def collect(self) -> Iterator[Metric]:
        """Yield the site's signals, or nothing if they cannot be read.

        Nothing, rather than zeroes. A scrape that cannot reach PostgreSQL
        reporting `draupnir_runs{state="QUEUED"} 0` would draw a graph of a
        queue draining to nothing at the moment the database went away, and an
        operator would act on it. An absent series is a gap, and a gap is what
        happened.
        """
        try:
            facts = self._read()
        except Exception as unavailable:
            logger.warning("metrics.site.unavailable", reason=str(unavailable))
            return
        if facts is None:
            return

        runs = GaugeMetricFamily(
            "draupnir_runs",
            "Runs by state, the queue depth being the QUEUED one. Site-wide: "
            "every API process reports the same value, so aggregate with max by.",
            labels=["state"],
        )
        for state, count in sorted(facts.runs.items()):
            runs.add_metric([state], float(count))
        yield runs

        results = GaugeMetricFamily(
            "draupnir_gate_results",
            "Gate results by gate and outcome. Site-wide; aggregate with max by.",
            labels=["gate", "state"],
        )
        for (gate, passed), count in sorted(facts.gate_results.items()):
            results.add_metric([gate, "passed" if passed else "failed"], float(count))
        yield results

        margins = GaugeMetricFamily(
            "draupnir_gate_margin",
            "Mean margin over baseline by gate, where a baseline was recorded. "
            "Site-wide; aggregate with max by.",
            labels=["gate"],
        )
        for gate, margin in sorted(facts.gate_margins.items()):
            margins.add_metric([gate], margin)
        yield margins

        if facts.vault_used_ratio is not None:
            yield GaugeMetricFamily(
                "draupnir_vault_used_ratio",
                "Fraction of the HODD vault in use, as the worker last measured it. "
                "SAD 11.3 alarms at 0.85. Site-wide; aggregate with max by.",
                value=facts.vault_used_ratio,
            )

        if facts.chain_verified is not None:
            yield GaugeMetricFamily(
                "draupnir_chain_verified",
                "1 if the last hourly verification found the chain intact, 0 if it "
                "found a divergence. Site-wide; aggregate with min by, because one "
                "process seeing a divergence is a divergence.",
                value=1.0 if facts.chain_verified else 0.0,
            )

        if facts.anchor_age_seconds is not None:
            yield GaugeMetricFamily(
                "draupnir_anchor_age_seconds",
                "Seconds since this site last anchored its chain head to MEGINGJORD. "
                "Site-wide; aggregate with max by.",
                value=facts.anchor_age_seconds,
            )

        # The fabric probe's reading and the baseline it is judged against
        # (RF-28). The collector reading CON-B's panel back out of Prometheus
        # queried these names and nothing exposed them, so the fabric tile could
        # only ever say unmeasured.
        if facts.fabric_bandwidth_gbps is not None:
            yield GaugeMetricFamily(
                "draupnir_fabric_bus_bandwidth_gbps",
                "The fabric probe's last average bus bandwidth across BAUGR, in GB/s. "
                "Absent until the probe has run. Site-wide; aggregate with max by.",
                value=facts.fabric_bandwidth_gbps,
            )
        if facts.fabric_baseline_gbps is not None:
            yield GaugeMetricFamily(
                "draupnir_fabric_baseline_gbps",
                "The commissioned bus bandwidth the probe alarms below 80 per cent of, "
                "in GB/s. Absent where none is configured. Site-wide; aggregate with max by.",
                value=facts.fabric_baseline_gbps,
            )

        alarms = GaugeMetricFamily(
            "draupnir_duty_alarm",
            "1 where a periodic duty's last reading crossed its threshold. The "
            "thresholds live with the duties, because a gauge cannot say where the "
            "line is. Site-wide; aggregate with max by.",
            labels=["duty"],
        )
        for duty, alarming in sorted(facts.duty_alarms.items()):
            alarms.add_metric([duty], 1.0 if alarming else 0.0)
        yield alarms


def read_facts(engine: Any, site_id: str) -> SiteFacts:
    """One scrape's worth of numbers, from the synchronous engine.

    Synchronous because a Prometheus collector is: `collect()` cannot await.
    The route offloads `generate_latest()` to a thread so the query does not
    run on the event loop, which is the same arrangement the repositories
    already have -- they are synchronous too (SAD 11B), and the application
    builds this engine for them.

    Scoped *and* filtered, which is not a belt-and-braces flourish. The row
    level security policies of SAD 11C are the second layer and they are the
    one that stops being there: they do not apply to a superuser or to a role
    holding `BYPASSRLS`, and the first thing this test suite found was a scrape
    reporting two forges' runs added together on a container whose default role
    is a superuser. Every other read in `reading.py` names the site in its
    `WHERE` for the same reason, so this does too.

    `gate_result` carries no site of its own -- it belongs to a run -- so it
    joins rather than filtering, which is also what makes the count a count of
    this forge's gates rather than the estate's.
    """
    from sqlalchemy import text

    with engine.connect() as connection:
        connection.execute(
            text("SELECT set_config('draupnir.site_id', :site, true)"), {"site": site_id}
        )
        scope = {"site": site_id}
        runs = {
            str(row.state): int(row.count)
            for row in connection.execute(
                text(
                    "SELECT state, count(*) AS count FROM run WHERE site_id = :site GROUP BY state"
                ),
                scope,
            )
        }
        results: dict[tuple[str, bool], int] = {}
        margins: dict[str, float] = {}
        for row in connection.execute(
            text(
                "SELECT g.gate, g.passed, count(*) AS count, avg(g.margin) AS mean_margin "
                "FROM gate_result g JOIN run r ON r.id = g.run_id "
                "WHERE r.site_id = :site GROUP BY g.gate, g.passed"
            ),
            scope,
        ):
            results[(str(row.gate), bool(row.passed))] = int(row.count)
            if row.mean_margin is not None:
                margins[str(row.gate)] = float(row.mean_margin)

        anchored = connection.execute(
            text("SELECT last_anchored_at FROM site WHERE id = :site"), scope
        ).scalar_one_or_none()

        measurements = {
            str(row.duty): (bool(row.alarm), dict(row.measurements or {}))
            for row in connection.execute(
                text(
                    "SELECT duty, alarm, measurements FROM duty_measurement WHERE site_id = :site"
                ),
                scope,
            )
        }

    return SiteFacts(
        site_id=site_id,
        runs=runs,
        gate_results=results,
        gate_margins=margins,
        vault_used_ratio=_ratio(measurements.get(VAULT_DUTY, (False, {}))[1]),
        chain_verified=_verified(measurements.get(CHAIN_DUTY)),
        anchor_age_seconds=age_of(anchored),
        duty_alarms={duty: alarm for duty, (alarm, _found) in measurements.items()},
        fabric_bandwidth_gbps=_gbps(
            measurements.get(FABRIC_DUTY, (False, {}))[1], "busBandwidthGbps"
        ),
        fabric_baseline_gbps=_gbps(
            measurements.get(FABRIC_DUTY, (False, {}))[1], "baselineGbps", positive=True
        ),
    )


#: The duties whose measurements this reads, named as `worker.duties.Duty`
#: names them. Written out rather than imported: `api` and the worker's module
#: are siblings under the import contracts, and a scrape endpoint reaching into
#: the worker to learn a string is a dependency the layering exists to refuse.
#: The names are asserted equal to the enum's in the tests, which is the join
#: that keeps them in step.
VAULT_DUTY = "vault-capacity"
CHAIN_DUTY = "ledger-chain-integrity"
FABRIC_DUTY = "fabric-bandwidth-probe"


def _gbps(measurements: Mapping[str, Any], key: str, *, positive: bool = False) -> float | None:
    """A bandwidth the fabric probe recorded, in GB/s, or `None`.

    A measured bandwidth of zero is a reading -- a dead fabric -- and is kept.
    A baseline must be positive: the worker records zero when none is
    configured, and a baseline of zero would make every reading look healthy.
    """
    import math

    value = measurements.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        return None
    return number


def _ratio(measurements: Mapping[str, Any]) -> float | None:
    """The fraction of the vault in use, from whatever the duty recorded.

    `None` where the duty has not run or recorded no capacity, because a vault
    of unknown fullness is not an empty vault -- and a dashboard cannot tell a
    reading of zero from an absence of readings.
    """
    used = measurements.get("used_ratio")
    if isinstance(used, int | float):
        return float(used)
    total = measurements.get("bytes_total")
    free = measurements.get("bytes_free")
    if isinstance(total, int | float) and isinstance(free, int | float) and total:
        return float(total - free) / float(total)
    return None


def _verified(found: tuple[bool, Mapping[str, Any]] | None) -> bool | None:
    """Whether the last verification found the chain intact.

    The duty alarms on divergence, so its alarm flag is the answer. `None`
    where it has not run: a chain nobody has verified is not a verified chain,
    and reporting 1 would be a claim this process cannot make.
    """
    if found is None:
        return None
    alarm, _measurements = found
    return not alarm


#: The collector this process has installed, if any. Held so the lifespan can
#: remove it on shutdown: the default registry is process-wide, and a test that
#: builds two applications would otherwise scrape through a collector holding a
#: disposed engine.
_INSTALLED: SiteCollector | None = None


def install(read: Any, registry: CollectorRegistry | None = None) -> SiteCollector:
    """Register the site collector, replacing any already installed."""
    from prometheus_client import REGISTRY

    global _INSTALLED
    target = registry if registry is not None else REGISTRY
    remove(registry=target)
    _INSTALLED = SiteCollector(read)
    target.register(_INSTALLED)
    return _INSTALLED


def remove(registry: CollectorRegistry | None = None) -> None:
    """Unregister the site collector, if one is installed."""
    from prometheus_client import REGISTRY

    global _INSTALLED
    if _INSTALLED is None:
        return
    target = registry if registry is not None else REGISTRY
    # A `KeyError` means it was registered against a different registry, which
    # a test may have replaced. Nothing to undo, and raising would fail a
    # shutdown over bookkeeping.
    with contextlib.suppress(KeyError):
        target.unregister(_INSTALLED)
    _INSTALLED = None


def observe(*, method: str, route: str, status: int, seconds: float) -> None:
    """Record one request. `route` is a template, never a path."""
    REQUEST_SECONDS.labels(method=method, route=route or UNMATCHED, status=str(status)).observe(
        seconds
    )


def age_of(moment: datetime | None, *, now: datetime | None = None) -> float | None:
    """Seconds since `moment`, or `None` if it never happened.

    `None` rather than a large number: a site that has never anchored has not
    been waiting since the epoch, and a gauge saying so would alarm with a
    figure that is arithmetic rather than a measurement. Absent is the honest
    answer, and the anchor duty is what alarms about never having anchored.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, ((now or datetime.now(UTC)) - moment).total_seconds())


__all__ = [
    "CHAIN_DUTY",
    "FORBIDDEN_LABELS",
    "LABELS",
    "REQUEST_SECONDS",
    "SIGNALS",
    "UNEXPOSED",
    "UNMATCHED",
    "VAULT_DUTY",
    "SiteCollector",
    "SiteFacts",
    "age_of",
    "install",
    "observe",
    "read_facts",
    "remove",
]
