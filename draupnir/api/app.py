"""The FastAPI edge.

SAD 11B: the edge knows HTTP and contains no domain logic. SAD 11E.2 fixes the
conventions: `/v1` path versioning, RFC 9457 problem documents, cursor
pagination, ETag concurrency, UUIDv7 identifiers, RFC 3339 timestamps.

This module is also the composition root, and the only place above the
infrastructure layer permitted to import from it.

Two things happen here that happen nowhere else.

`enforce_declarations` runs before the application is returned, so a route
without a role declaration prevents startup rather than failing open at
runtime (AC-B6). There is no environment variable that relaxes it.

The context middleware binds one request context -- request id, site scope,
actor -- for the duration of a request, so that every log line and every span
below carries run id, site id and actor without being passed them.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from sqlalchemy import create_engine as sync_engine

from draupnir import __version__
from draupnir.api import (
    authentication,
    deps,
    development,
    ledger_events,
    metrics,
    readiness,
    telemetry,
    tracing,
    writing,
)
from draupnir.api import context as request_context
from draupnir.api.guards import enforce_declarations
from draupnir.api.idempotency import IdempotencyStore
from draupnir.api.idempotency_store import DatabaseIdempotencyStore
from draupnir.api.problems import CONTENT_TYPE, EXCEPTION_HANDLERS, Problem
from draupnir.api.reading import DatabaseReadModel, EmptyReadModel
from draupnir.api.routers import (
    approvals,
    audit,
    auth,
    corpora,
    estate,
    governance,
    health,
    models,
    plugins,
    runs,
    sites,
)
from draupnir.core.infrastructure.config import get_settings
from draupnir.core.infrastructure.database import create_engine, session_factory
from draupnir.gullinbursti.telemetry import (
    EGRESS_POLICY,
    EGRESS_PURPOSE,
    TIMEOUT_SECONDS,
    Queries,
    Telemetry,
)
from draupnir.svalinn.egress import (
    FEDERATION_POLICY,
    FEDERATION_PURPOSE,
    SCHEDULING_POLICY,
    SCHEDULING_PURPOSE,
    TRACING_POLICY,
    TRACING_PURPOSE,
    BrokeredClient,
)

API_VERSION = "v1"

DESCRIPTION = """
The DRAUPNIR control plane API.

Errors are RFC 9457 problem documents served as `application/problem+json`.
Mutating endpoints require an `Idempotency-Key`. Collections are cursor
paginated. Mutable resources carry an `ETag` and require `If-Match`.
Long operations return 202 with a run identifier; nothing blocks on training.
"""

#: Every response that is not the documented success shape is a problem
#: document, so it is declared once rather than on each route.
DEFAULT_RESPONSES: dict[int | str, dict[str, object]] = {
    "default": {
        "description": "An RFC 9457 problem document",
        "content": {CONTENT_TYPE: {"schema": Problem.model_json_schema()}},
        "model": Problem,
    }
}


async def bind_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Echo the request and correlation identifiers on the way out.

    Binding and unbinding the context variable is the `context` dependency's,
    not this middleware's: a context variable must be reset in the task that
    set it, and Starlette runs middleware in a different task from the
    endpoint. This only reads what the dependency recorded on the request.
    """
    response = await call_next(request)

    bound = getattr(request.state, "context", None)
    if bound is not None:
        response.headers["X-Request-Id"] = str(bound.request_id)
        if bound.correlation_id:
            response.headers[request_context.CORRELATION_HEADER] = bound.correlation_id
    return response


async def record_request(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Time every request and record it by route template. RF-18.

    The template and never the path. `/v1/runs/{run_id}` is one series;
    `/v1/runs/019cb993-.../events` is a new series for every run ever created,
    which is an unbounded label set on an endpoint served without a credential
    -- so it would be both a way to exhaust a disk and a list of what this
    forge is building, published to anybody who can reach the port.

    A request that matched no route is recorded under one name for the same
    reason: a 404 sweep must not mint a series per path tried.

    Failures are timed too. A route that raises is a route whose latency an
    operator wants, and the status the error mapper produced is what a
    dashboard alarms on.
    """
    started = time.perf_counter()
    status = 500
    # One tracer for this request (RF-18). A process-wide one holds a single
    # open-span stack, so two requests in flight nest into each other -- which
    # was invisible while the spans were collected and discarded, and becomes a
    # trace showing one operator's approval under another's submission as soon
    # as anything exports them.
    with telemetry.collecting() as collector:
        try:
            response: Response = await call_next(request)
        except Exception:
            metrics.observe(
                method=request.method,
                route=_template(request),
                status=status,
                seconds=time.perf_counter() - started,
            )
            # Exported before the exception continues: a request that failed is
            # the one whose trace is worth having.
            tracing.drain(collector)
            raise
        metrics.observe(
            method=request.method,
            route=_template(request),
            status=response.status_code,
            seconds=time.perf_counter() - started,
        )
        # Drained whether or not an exporter is configured, which is the point:
        # the tracer was a list that grew for the life of the process because
        # nothing ever read it.
        tracing.drain(collector)
    return response


def _template(request: Request) -> str:
    """The matched route's path template, or a single name for no match.

    Rebuilt from the path and the parameters Starlette matched, rather than
    read off the route's `path_format`. The routers are mounted under `/v1`,
    and a mounted route's `path_format` is relative to its mount -- it reports
    `/runs/{run_id}`, not `/v1/runs/{run_id}`. Labelling by that would merge
    two API versions' latencies into one series the day a `/v2` exists, and the
    graph would look like a route that suddenly doubled in traffic.

    Whole segments only. Replacing a parameter's value as a substring would
    rewrite a coincidental match earlier in the path, and a label that is
    usually right is worse than one that is obviously wrong.
    """
    if request.scope.get("route") is None:
        return metrics.UNMATCHED
    matched = {str(value): name for name, value in (request.scope.get("path_params") or {}).items()}
    return "/".join(
        "{" + matched[segment] + "}" if segment in matched else segment
        for segment in request.url.path.split("/")
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Install the database read model for the life of the process.

    The default read model answers every list with nothing, which is what the
    contract tests want and what a mechanism test should see. A running
    deployment needs the other one, and installing it here rather than at
    import time means the engine is created once, when there is an event loop
    to own it, and disposed when the process stops.

    A database that cannot be reached does not stop startup: SAD 11.2 requires
    degraded modes to be visible rather than fatal, and `/readyz` is where that
    visibility lives. Refusing to start would take the readiness probe down
    with the database and leave an operator with nothing to read.
    """
    engine = create_engine()
    deps.set_reader(DatabaseReadModel(session_factory(engine)))

    deps.set_estate(estate_telemetry())

    # The write side is synchronous, because the repositories are (SAD 11B),
    # and it therefore needs its own engine rather than the async one. Two
    # engines against one database is not a duplication: they speak different
    # drivers, and the alternative is an async orchestrator over synchronous
    # repositories, which is the shape that makes a transaction hard to see.
    writer_engine = sync_engine(get_settings().database_url_sync, future=True)
    writing.set_writer(writing.DatabaseWriter(writer_engine))

    # The idempotency store, on the same synchronous engine (RF-14). It was a
    # dictionary in one process, so "a second click while the first request is
    # still running is refused rather than acting twice" was true of one
    # process and false of the deployment SAD 5.1 describes -- and every
    # reservation was lost on restart.
    deps.set_store(DatabaseIdempotencyStore(engine=writer_engine))

    # The event stream's source (RF-15). Every change in this system is an
    # entry in the chain, the append notifies, and this process listens and
    # fans the notification into its own per-site streams. Without it the
    # stream carried one event kind published by one handler in one process,
    # and the worker -- which performs every transition -- reached no console
    # at all.
    listener = ledger_events.LedgerListener(
        dsn=ledger_events.dsn_of(get_settings().database_url),
        stream_for=runs.stream_for,
    )
    listener.start()

    # What `/readyz` probes (RF-17). Wired here, with the engine the read model
    # already uses, because the probe used to build a connection pool and throw
    # it away on every check -- and because a probe reaching the scheduler or
    # MEGINGJORD is outbound traffic, so it goes through the broker like every
    # other call rather than being the one nobody decided (threat T11).
    settings = get_settings()
    readiness.set_dependencies(
        readiness.Dependencies(
            engine=engine,
            vault_root=settings.vault_root,
            site_id=settings.site_id,
            object_store=readiness.object_store_probe(settings),
            scheduler_url=settings.scheduler_url,
            registry_url=settings.registry_url,
            scheduler_client=probe_client(purpose=SCHEDULING_PURPOSE, policy=SCHEDULING_POLICY),
            federation_client=probe_client(purpose=FEDERATION_PURPOSE, policy=FEDERATION_POLICY),
        )
    )
    # The SAD 11.3 signals a scrape can read (RF-18). On the synchronous
    # engine, because a Prometheus collector cannot await; the route gathers in
    # a thread so the query stays off the event loop.
    metrics.install(lambda: metrics.read_facts(writer_engine, settings.site_id))

    # And where a span goes. Through the broker like every other outbound call:
    # an SDK exporter holds its own transport and would be the one call in this
    # process nobody decided (threat T11). An empty endpoint exports nothing,
    # which is the ordinary case -- the estate has no collector.
    tracing.set_exporter(
        tracing.OtlpExporter(
            endpoint=settings.otlp_endpoint,
            client=(
                probe_client(purpose=TRACING_PURPOSE, policy=TRACING_POLICY)
                if settings.otlp_endpoint
                else None
            ),
            site_id=settings.site_id,
            sample=settings.otlp_sample,
        )
    )
    try:
        yield
    finally:
        metrics.remove()
        tracing.set_exporter(tracing.OtlpExporter())
        readiness.set_dependencies(readiness.Dependencies())
        await listener.stop()
        deps.set_reader(EmptyReadModel())
        deps.set_estate(Telemetry(client=None))
        deps.set_store(IdempotencyStore())
        writing.set_writer(writing.NoWriter())
        writer_engine.dispose()
        await engine.dispose()


def jwks_client(settings: Any) -> authentication.Client | None:
    """The client the key fetch uses, through the egress broker.

    `PyJWKClient` would have fetched the key set itself, with its own urllib
    call. That call is outbound traffic to MEGINGJORD, and threat T11 is
    egress no allow list decided -- so the fetch goes through the broker like
    every other one, and the driver takes its client as an argument precisely
    so that this can be the thing that supplies it.
    """
    if not settings.oidc_issuer:
        return None

    import httpx

    return BrokeredClient(
        inner=httpx.Client(timeout=TIMEOUT_SECONDS),
        purpose=FEDERATION_PURPOSE,
        approving_policy=FEDERATION_POLICY,
    )


def probe_client(*, purpose: str, policy: str) -> BrokeredClient:
    """A client readiness reaches one destination with. RF-17.

    Brokered for the same reason the JWKS fetch is: a readiness probe is a call
    out of this host, threat T11 is egress with no allow list decided, and the
    broker's value is only realised where something is obliged to consult it.
    A probe holding a raw client would be the one outbound call in the process
    that went round the decision.

    Per destination, because the broker approves a destination under a policy
    and REGIN's is not MEGINGJORD's. One client for both would be the thing the
    allow list's two separate entries exist to prevent.

    Its own client rather than the JWKS one, which is built only when an issuer
    is configured -- a forge with no OIDC still has a scheduler to reach.
    """
    import httpx

    return BrokeredClient(
        inner=httpx.Client(timeout=readiness.CHECK_TIMEOUT_SECONDS),
        purpose=purpose,
        approving_policy=policy,
    )


def estate_telemetry() -> Telemetry:
    """Build the telemetry reader, with a client that goes through the broker.

    The composition root is the only place that may put the two together.
    GULLINBURSTI declares what the call is for; SVALINN holds the allow list
    and decides it; they are siblings in the layering and neither imports the
    other, which is what stops the declaration and the decision drifting into
    one module that agrees with itself.

    An unconfigured deployment gets a reader with no client, which answers
    every query with a reason. That is the honest answer for a developer
    machine, and it is why this never raises during startup: SAD 11.2 makes a
    missing collector a degraded mode, and a control plane that refused to
    start without Prometheus would take the API down over a wall panel.
    """
    settings = get_settings()
    queries = Queries(
        gpu_temperature=settings.metric_gpu_temperature,
        throttle_reasons=settings.metric_throttle_reasons,
        fabric_bandwidth=settings.metric_fabric_bandwidth,
        fabric_baseline=settings.metric_fabric_baseline,
    )
    if not settings.prometheus_url:
        return Telemetry(queries=queries, client=None)

    import httpx

    return Telemetry(
        base_url=settings.prometheus_url,
        queries=queries,
        client=BrokeredClient(
            inner=httpx.Client(timeout=TIMEOUT_SECONDS),
            purpose=EGRESS_PURPOSE,
            approving_policy=EGRESS_POLICY,
        ),
    )


def create_app() -> FastAPI:
    """Build the application.

    Kept as a factory so that a test can construct an isolated instance and so
    that the OpenAPI export script does not start a server.
    """
    app = FastAPI(
        title="DRAUPNIR",
        summary="CIM-56 model factory control plane",
        description=DESCRIPTION,
        version=__version__,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
        exception_handlers=EXCEPTION_HANDLERS,
        responses=DEFAULT_RESPONSES,
        lifespan=lifespan,
    )

    app.middleware("http")(bind_context)

    # Outermost of the two, so the time it records includes everything the
    # inner middleware and the handler do (RF-18). Starlette runs the last
    # registered first, so this goes after.
    app.middleware("http")(record_request)

    # Authentication, and the refusal that goes with it. RF-01: `deps` resolves
    # the caller from `request.state.claims` and said the OIDC middleware set
    # it; there was no such middleware, so a deployment with DRAUPNIR_DEV unset
    # -- the only configuration Sindri runs in -- answered 401 to every `/v1`
    # route.
    #
    # The refusal comes first. A control plane that starts with no way to
    # authenticate anybody is worse than one that will not start: the first
    # looks like a broken deployment and gets debugged, and the second names
    # the setting that is missing.
    settings = get_settings()
    in_development = development.enabled()
    authentication.refuse_unconfigured(settings, development=in_development)

    verifier = authentication.verifier_from(settings, client=jwks_client(settings))
    if verifier is not None:
        authentication.install(app, verifier)

    # A development principal, when DRAUPNIR_DEV=1 and never otherwise. Without
    # it a running stack answers 401 to everything and the console can read
    # nothing, which makes the four journeys of AC-U1 unrunnable outside a full
    # identity deployment. See `development.py` for why it is shaped the way
    # it is.
    #
    # Installed after the verifier, so in the configuration where somebody has
    # set both it runs last and wins. That case should not exist; it is logged
    # loudly at startup, and making the concession lose silently would be worse
    # than making it obvious.
    development.install(app)

    # Operator probes, unversioned.
    app.include_router(health.router)

    # The authorisation-code flow, also unversioned: a browser redirect is not
    # part of the `/v1` contract, and its URLs are registered with the identity
    # provider — where a version in the path would be a second thing to change
    # on every release.
    app.include_router(auth.router)

    # The versioned surface. SAD 11E.2: additive changes only within a version,
    # and the OpenAPI diff gate fails a build on a breaking change.
    v1 = APIRouter(prefix=f"/{API_VERSION}")
    v1.include_router(corpora.router)
    v1.include_router(runs.router)
    v1.include_router(approvals.router)
    v1.include_router(audit.router)
    v1.include_router(plugins.router)
    v1.include_router(sites.router)
    v1.include_router(models.router)
    v1.include_router(governance.router)
    v1.include_router(estate.router)
    app.include_router(v1)

    # AC-B6, and the prompt's sharper form of it: a route without an explicit
    # role declaration must fail to register at startup, not fail open at
    # runtime. Checked here, over the routes that were actually registered,
    # so a missing declaration stops the application before it opens a socket.
    enforce_declarations(app)

    return app


def __getattr__(name: str) -> Any:
    """Build the application on first access to `app`, not on import.

    `uvicorn draupnir.api.app:app` and the Dockerfile's CMD both want a module
    attribute, so the name stays. What changed is when it is built: RF-01 gave
    `create_app` a reason to *refuse*, and a module that raises on import
    cannot be imported by a test, a generator, or the acceptance pack -- none
    of which want a running application and all of which want the module.

    PEP 562. The attribute is resolved when something asks for it, which for a
    server is after import and for everything else is never.
    """
    if name == "app":
        return create_app()
    raise AttributeError(name)
