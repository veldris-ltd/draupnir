"""What `/metrics` reports, and what it must never carry. RF-18.

The endpoint exposed four Python garbage-collector counters and no DRAUPNIR
signal at all, while its docstring stated a constraint about metrics that did
not exist -- "a metric labelled by actor is a metric with an unbounded label
set, and one labelled by artefact is a cardinality problem that also happens to
leak what is being built". These make that a rule rather than a sentence.

The rendering is exercised without a database, because `SiteCollector` takes
the numbers rather than fetching them: the reading and the rendering can be
wrong separately, and only one of them needs PostgreSQL to be wrong in.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from draupnir.api import metrics
from draupnir.worker.duties import Duty

REPO_ROOT = Path(__file__).resolve().parents[2]

AT = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)


def rendered(facts: metrics.SiteFacts | None) -> str:
    """The exposition for one set of facts, in a registry of its own."""
    registry = CollectorRegistry()
    registry.register(metrics.SiteCollector(lambda: facts))
    return generate_latest(registry).decode("utf-8")


def families(body: str) -> dict[str, list[str]]:
    """Metric name to its sample lines, from an exposition."""
    found: dict[str, list[str]] = {}
    for line in body.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name = line.split("{")[0].split(" ")[0]
        found.setdefault(name.removesuffix("_total"), []).append(line)
    return found


FULL = metrics.SiteFacts(
    site_id="sindri",
    runs={"QUEUED": 3, "TRAINING": 1, "RELEASED": 12},
    gate_results={("safety", True): 9, ("safety", False): 1, ("quality", True): 7},
    gate_margins={"safety": 0.04, "quality": 0.011},
    vault_used_ratio=0.62,
    chain_verified=True,
    anchor_age_seconds=900.0,
    duty_alarms={"vault-capacity": False, "anchor-freshness": True},
    fabric_bandwidth_gbps=188.4,
    fabric_baseline_gbps=235.6,
)


# ---------------------------------------------------------------------------
# The signals reach the exposition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "draupnir_runs",
        "draupnir_gate_results",
        "draupnir_gate_margin",
        "draupnir_vault_used_ratio",
        "draupnir_chain_verified",
        "draupnir_anchor_age_seconds",
        "draupnir_duty_alarm",
        "draupnir_fabric_bus_bandwidth_gbps",
        "draupnir_fabric_baseline_gbps",
    ],
)
def test_each_site_signal_is_exposed(name: str) -> None:
    """SAD 11.3 gives eight signals a source and a surface. None reached a scrape."""
    assert name in families(rendered(FULL)), name


def test_the_queue_depth_is_the_queued_run_count() -> None:
    body = rendered(FULL)

    assert 'draupnir_runs{state="QUEUED"} 3.0' in body


def test_a_gate_reports_both_outcomes_separately() -> None:
    """A pass rate needs a denominator, and one counter cannot be one."""
    body = rendered(FULL)

    assert 'draupnir_gate_results{gate="safety",state="passed"} 9.0' in body
    assert 'draupnir_gate_results{gate="safety",state="failed"} 1.0' in body


# ---------------------------------------------------------------------------
# Absence is not zero
# ---------------------------------------------------------------------------


def test_a_signal_the_worker_has_not_measured_is_absent_rather_than_zero() -> None:
    """A vault of unknown fullness is not an empty vault.

    Reporting 0.0 for a duty that has never run draws a graph of a vault with
    nothing in it, which is a reading an operator would act on -- and there is
    no way to tell it from the real thing.
    """
    body = rendered(metrics.SiteFacts(site_id="sindri", runs={"QUEUED": 1}))

    assert "draupnir_vault_used_ratio" not in body
    assert "draupnir_chain_verified" not in body
    assert "draupnir_anchor_age_seconds" not in body
    assert "draupnir_fabric_bus_bandwidth_gbps" not in body
    assert "draupnir_fabric_baseline_gbps" not in body


def test_a_database_that_cannot_be_read_yields_no_series_rather_than_zeroes() -> None:
    """A gap is what happened, and a gap is what a graph should show.

    Zeroes would draw a queue draining to nothing at the moment PostgreSQL went
    away, and somebody would act on it.
    """

    def unavailable() -> metrics.SiteFacts:
        msg = "connection refused"
        raise OSError(msg)

    registry = CollectorRegistry()
    registry.register(metrics.SiteCollector(unavailable))

    body = generate_latest(registry).decode("utf-8")

    assert "draupnir_runs" not in body


def test_a_site_that_has_never_anchored_reports_no_age() -> None:
    """Rather than seconds since the epoch, which is arithmetic not a measurement."""
    assert metrics.age_of(None) is None
    assert metrics.age_of(AT, now=AT + timedelta(minutes=5)) == pytest.approx(300.0)


def test_an_age_is_never_negative() -> None:
    """Clocks disagree, and a negative age reads as data corruption."""
    assert metrics.age_of(AT, now=AT - timedelta(minutes=5)) == 0.0


# ---------------------------------------------------------------------------
# Cardinality, which is the constraint the endpoint already claimed
# ---------------------------------------------------------------------------


def label_names(body: str) -> set[str]:
    """Every label name on a DRAUPNIR metric in an exposition.

    Ours only. `python_info` carries `version`, `implementation` and four more
    from `prometheus_client`'s own platform collector; they are bounded, they
    are not ours, and the rule being enforced here is about metrics this
    codebase declares.
    """
    found: set[str] = set()
    for line in body.splitlines():
        if not line.startswith("draupnir_") or "{" not in line:
            continue
        found.update(re.findall(r'[{,]([a-zA-Z_][a-zA-Z0-9_]*)="', line))
    return found


def test_no_metric_carries_a_label_outside_the_permitted_set() -> None:
    """`LABELS` is the complete permitted set, and each member is bounded.

    Fourteen run states, a handful of gate names, the HTTP methods, the route
    templates this application registers, and the status codes it returns.
    Anything else is a series set nobody has counted.
    """
    metrics.observe(method="GET", route="/v1/runs/{run_id}", status=200, seconds=0.01)
    carried = label_names(rendered(FULL)) | {
        name
        for name in label_names(generate_latest().decode("utf-8"))
        # `le` is the histogram bucket boundary, which Prometheus owns.
        if name != "le"
    }

    assert carried <= metrics.LABELS, f"unpermitted labels: {sorted(carried - metrics.LABELS)}"


def test_no_metric_is_labelled_by_actor_run_or_artefact() -> None:
    """Two of them are unbounded and all three name what is being built.

    `/metrics` is served without a credential -- SAD 8.1 puts it on loopback and
    makes the binding the control -- so a label carrying a run identifier is
    both a series per run forever and a list of this forge's work.
    """
    metrics.observe(method="GET", route="/v1/runs/{run_id}", status=200, seconds=0.01)
    body = rendered(FULL) + generate_latest().decode("utf-8")

    forbidden = label_names(body) & metrics.FORBIDDEN_LABELS

    assert not forbidden, f"these labels are unbounded or leak: {sorted(forbidden)}"


def test_a_request_is_labelled_by_route_template_and_never_by_path() -> None:
    """The trap this actually guards.

    `/v1/runs/019cb993-.../events` as a label is a new series for every run
    ever created. The template is one series.
    """
    metrics.observe(method="GET", route="/v1/runs/{run_id}/events", status=200, seconds=0.01)
    body = generate_latest().decode("utf-8")

    assert 'route="/v1/runs/{run_id}/events"' in body
    assert not re.search(r'route="[^"]*[0-9a-f]{8}-[0-9a-f]{4}-', body), (
        "a request path with an identifier in it reached a label"
    )


# ---------------------------------------------------------------------------
# Every metric has a documented surface
# ---------------------------------------------------------------------------


def test_every_metric_names_a_signal_it_surfaces() -> None:
    """A metric with no documented surface fails the build.

    SAD 11.3 is a table of signals with a source and a surface; a metric that
    matches no row is a number nobody agreed to collect and nobody will read.
    """
    metrics.observe(method="GET", route="/v1/runs", status=200, seconds=0.01)
    exposed = set(families(rendered(FULL))) | {
        name.removesuffix("_total")
        for name in families(generate_latest().decode("utf-8"))
        if name.startswith("draupnir")
    }

    undocumented = {
        name
        for name in exposed
        if name.startswith("draupnir")
        and name not in metrics.SIGNALS
        and name.removesuffix("_sum").removesuffix("_count").removesuffix("_bucket")
        not in metrics.SIGNALS
    }

    assert not undocumented, f"these metrics surface no SAD 11.3 signal: {sorted(undocumented)}"


def test_every_named_signal_is_a_row_of_the_sad_table_or_a_proposed_one() -> None:
    """The mapping is checked against the document, not against itself.

    Seven of the eight metrics name a row of SAD 11.3. The eighth -- request
    latency and status -- names no row, because the table has none for the
    control plane's own request path; that is written up as an amendment rather
    than quietly mapped onto a row that means something else.
    """
    sad = (REPO_ROOT / "docs/build/draupnir-sad.md").read_text(encoding="utf-8")
    section = sad.split("### 11.3")[1].split("### 11.4")[0]
    rows = {line.split("|")[1].strip() for line in section.splitlines() if line.startswith("|")}

    proposed = (REPO_ROOT / "docs/fixes/proposed-sad-amendments.md").read_text(encoding="utf-8")

    for metric, signal in metrics.SIGNALS.items():
        assert signal in rows or signal in proposed, (
            f"{metric} claims to surface {signal!r}, which is neither a row of "
            "SAD 11.3 nor a proposed amendment"
        )


def test_the_duty_names_are_the_workers_own() -> None:
    """The metrics module writes them out; the worker's enum defines them.

    `api` and `worker` are siblings under the import contracts, so the scrape
    endpoint cannot import the enum to learn a string. This is the join that
    keeps the two spellings in step, and it is why they are constants rather
    than literals buried in a query.
    """
    assert Duty.VAULT.value == metrics.VAULT_DUTY
    assert Duty.CHAIN.value == metrics.CHAIN_DUTY
    assert Duty.FABRIC.value == metrics.FABRIC_DUTY


@pytest.mark.parametrize(
    ("measured", "key", "positive", "expected"),
    [
        ({"busBandwidthGbps": 188.4}, "busBandwidthGbps", False, 188.4),
        # A dead fabric reads zero, and zero is a reading.
        ({"busBandwidthGbps": 0.0}, "busBandwidthGbps", False, 0.0),
        # A probe that did not run recorded no bandwidth, which is not zero.
        ({"exitCode": 1}, "busBandwidthGbps", False, None),
        ({"baselineGbps": 235.6}, "baselineGbps", True, 235.6),
        # The worker records zero for "no baseline configured".
        ({"baselineGbps": 0.0}, "baselineGbps", True, None),
        ({"baselineGbps": True}, "baselineGbps", True, None),
        ({"busBandwidthGbps": float("nan")}, "busBandwidthGbps", False, None),
    ],
)
def test_the_fabric_reading_is_read_from_what_the_probe_recorded(
    measured: dict[str, Any], key: str, positive: bool, expected: float | None
) -> None:
    """RF-28. The collector queried names nothing exposed, so the panel said unmeasured."""
    assert metrics._gbps(measured, key, positive=positive) == expected


# ---------------------------------------------------------------------------
# Reading a measurement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("measured", "expected"),
    [
        ({"used_ratio": 0.5}, 0.5),
        ({"bytes_total": 1000, "bytes_free": 250}, 0.75),
        ({"bytes_total": 0, "bytes_free": 0}, None),
        ({}, None),
        ({"something_else": 3}, None),
    ],
)
def test_the_vault_ratio_is_read_from_whatever_the_duty_recorded(
    measured: dict[str, Any], expected: float | None
) -> None:
    assert metrics._ratio(measured) == expected


def test_a_chain_nobody_has_verified_is_not_a_verified_chain() -> None:
    """`None`, not 1. Reporting intact is a claim this process cannot make."""
    assert metrics._verified(None) is None
    assert metrics._verified((False, {})) is True
    assert metrics._verified((True, {"divergence": "seq 9"})) is False
