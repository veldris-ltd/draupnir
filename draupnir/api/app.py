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

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from sqlalchemy import create_engine as sync_engine

from draupnir import __version__
from draupnir.api import context as request_context
from draupnir.api import deps, development, writing
from draupnir.api.guards import enforce_declarations
from draupnir.api.problems import CONTENT_TYPE, EXCEPTION_HANDLERS, Problem
from draupnir.api.reading import DatabaseReadModel, EmptyReadModel
from draupnir.api.routers import (
    approvals,
    audit,
    corpora,
    governance,
    health,
    models,
    plugins,
    runs,
    sites,
)
from draupnir.core.infrastructure.config import get_settings
from draupnir.core.infrastructure.database import create_engine, session_factory

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

    # The write side is synchronous, because the repositories are (SAD 11B),
    # and it therefore needs its own engine rather than the async one. Two
    # engines against one database is not a duplication: they speak different
    # drivers, and the alternative is an async orchestrator over synchronous
    # repositories, which is the shape that makes a transaction hard to see.
    writer_engine = sync_engine(get_settings().database_url_sync, future=True)
    writing.set_writer(writing.DatabaseWriter(writer_engine))
    try:
        yield
    finally:
        deps.set_reader(EmptyReadModel())
        writing.set_writer(writing.NoWriter())
        writer_engine.dispose()
        await engine.dispose()


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

    # A development principal, when DRAUPNIR_DEV=1 and never otherwise. Without
    # it a running stack answers 401 to everything and the console can read
    # nothing, which makes the four journeys of AC-U1 unrunnable outside a full
    # identity deployment. See `development.py` for why it is shaped the way
    # it is.
    development.install(app)

    # Operator probes, unversioned.
    app.include_router(health.router)

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
    app.include_router(v1)

    # AC-B6, and the prompt's sharper form of it: a route without an explicit
    # role declaration must fail to register at startup, not fail open at
    # runtime. Checked here, over the routes that were actually registered,
    # so a missing declaration stops the application before it opens a socket.
    enforce_declarations(app)

    return app


app = create_app()
