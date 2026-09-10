"""Liveness and readiness.

These two endpoints are the smoke test of the deployment stage (SAD 11H,
stage 4): `healthz`, `readyz`, then a ledger chain verification. They are
deliberately unversioned; an operator probe is not part of the `/v1` contract.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response

from draupnir.api import readiness
from draupnir.api.guards import unauthenticated
from draupnir.api.schemas import Wire
from draupnir.core.infrastructure.config import get_settings

router = APIRouter(tags=["operations"])


class Health(Wire):
    """Liveness answer.

    `Wire`, not a bare `BaseModel`: every other response on this API is
    camelCase and a probe that answers `site_id` while the rest answer `siteId`
    makes "one API" false in the one place every client reads first.
    """

    status: Literal["ok"]
    version: str
    site_id: str


class Readiness(Wire):
    """Readiness answer, one entry per configured dependency.

    A dependency this deployment does not have is absent rather than `false`
    (RF-17): a forge with no scheduler is not degraded for want of one, and a
    probe that says otherwise is a probe operators learn to ignore. What each
    name means, and which section of `docs/runbook.md` it sends an operator to,
    is in `readiness.RUNBOOK_SECTIONS`.
    """

    status: Literal["ready", "degraded"]
    checks: dict[str, bool]


@router.get("/healthz", summary="Liveness", operation_id="getHealth")
@unauthenticated(
    "An orchestrator probes liveness before a token can be obtained, and a probe "
    "that needs credentials is a probe that reports a dead service when the "
    "identity provider is down."
)
async def healthz() -> Health:
    """Return liveness. This touches no dependency by design."""
    from draupnir import __version__

    settings = get_settings()
    return Health(status="ok", version=__version__, site_id=settings.site_id)


@router.get("/readyz", summary="Readiness", operation_id="getReadiness")
@unauthenticated(
    "Same as liveness. Readiness reports which dependencies answered and carries "
    "no run, artefact or ledger content, so there is nothing here to protect."
)
async def readyz() -> Readiness:
    """Return readiness, having probed each configured dependency.

    SAD 11.2 requires degraded modes to be visible rather than fatal, so a
    failed dependency reports `degraded` rather than raising.

    This constructs nothing (RF-17). It used to call `create_engine()` and
    `engine.dispose()` on every probe, so an orchestrator checking every few
    seconds built and tore down a connection pool at that rate -- next to the
    pooled engine the lifespan already owns. The dependencies are wired once,
    at startup, and this reads them.
    """
    checks = await readiness.dependencies().checks()
    status: Literal["ready", "degraded"] = "ready" if all(checks.values()) else "degraded"
    return Readiness(status=status, checks=checks)


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    operation_id="getMetrics",
    response_class=Response,
    responses={200: {"content": {"text/plain": {}}}},
)
@unauthenticated(
    "SAD 8.1 lists /metrics with /healthz and /readyz as unauthenticated on loopback "
    "only. The binding is the control: the scrape endpoint is not published beyond "
    "the host, so it needs no credential and carrying one would put a credential in "
    "a scrape configuration."
)
async def metrics() -> Response:
    """Return the Prometheus exposition.

    Metrics are counters and histograms. Nothing here carries a run
    specification, a corpus path or an actor identity: a metric labelled by
    actor is a metric with an unbounded label set, and one labelled by artefact
    is a cardinality problem that also happens to leak what is being built.
    """
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
