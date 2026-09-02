"""The FastAPI edge.

SAD 11B: the edge knows HTTP and contains no domain logic. SAD 11E.2 fixes the
conventions: `/v1` path versioning, RFC 9457 problem documents, cursor
pagination, ETag concurrency, UUIDv7 identifiers, RFC 3339 timestamps.

This module is also the composition root, and the only place above the
infrastructure layer permitted to import from it.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from draupnir import __version__
from draupnir.api.guards import enforce_declarations
from draupnir.api.problems import CONTENT_TYPE, EXCEPTION_HANDLERS, Problem
from draupnir.api.routers import health

API_VERSION = "v1"

DESCRIPTION = """
The DRAUPNIR control plane API.

Errors are RFC 9457 problem documents served as `application/problem+json`.
Mutating endpoints accept an `Idempotency-Key`. Collections are cursor
paginated. Mutable resources carry an `ETag` and require `If-Match`.
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
    )

    # Operator probes, unversioned.
    app.include_router(health.router)

    # The versioned surface. SAD 11E.2: additive changes only within a version,
    # and the OpenAPI diff gate fails a build on a breaking change.
    v1 = APIRouter(prefix=f"/{API_VERSION}")
    app.include_router(v1)

    # AC-B6, and the prompt's sharper form of it: a route without an explicit
    # role declaration must fail to register at startup, not fail open at
    # runtime. Checked here, over the routes that were actually registered,
    # so a missing declaration stops the application before it opens a socket.
    enforce_declarations(app)

    return app


app = create_app()
