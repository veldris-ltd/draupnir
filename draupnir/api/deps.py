"""FastAPI dependencies: who, where, and the conventions every route obeys.

The guard is the piece worth reading. `declare` (in `guards`) records what a
route requires so that startup can check it; this is what enforces the same
declaration at request time. Both read one attribute on the endpoint, so a
route cannot be enforced against a different requirement from the one the
startup check validated and the OpenAPI document published.

The enforcement is a dependency rather than middleware for one reason: a
dependency runs after path resolution, so it knows *which* route was matched
and can therefore read that route's declaration. Middleware runs before, and
would have to re-derive the route from the path, which is a second router that
disagrees with the first one on the day somebody adds a path parameter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, Query, Request

from draupnir.api import context as request_context
from draupnir.api import telemetry
from draupnir.api.concurrency import (
    ConcurrencyError,
    PreconditionFailedError,
    PreconditionRequiredError,
)
from draupnir.api.context import RequestContext
from draupnir.api.guards import requirement_of
from draupnir.api.idempotency import (
    IdempotencyStore,
    InFlightError,
    KeyReusedError,
)
from draupnir.api.pagination import PaginationError, clamp
from draupnir.api.problems import ProblemError
from draupnir.core.domain.sites import SiteScope
from draupnir.core.infrastructure.config import get_settings
from draupnir.svalinn.authz import decide
from draupnir.svalinn.identity import Principal, from_claims

#: The process-wide idempotency store. Reached through the functions below
#: rather than imported by name: a module that does `from deps import STORE`
#: captures the object, and a test or a deployment that replaces the store then
#: has reservations landing in one and completions in another. That failure
#: looks like "the key was never reserved" and is a 500.
STORE = IdempotencyStore()


def store() -> IdempotencyStore:
    """The current idempotency store, resolved at call time."""
    return STORE


def complete(
    key: str,
    ctx: RequestContext,
    *,
    status: int,
    body: Any = None,
    location: str | None = None,
) -> None:
    """Record what a request returned, for a later replay."""
    store().complete(
        key,
        site_id=ctx.site_id,
        actor=ctx.actor,
        status=status,
        body=body,
        location=location,
    )


def release(key: str, ctx: RequestContext) -> None:
    """Drop a reservation whose request failed and therefore did not act."""
    store().release(key, site_id=ctx.site_id, actor=ctx.actor)


def now() -> datetime:
    """The current instant, with an explicit offset. SAD 11E.2."""
    return datetime.now(UTC)


def principal_from(request: Request) -> Principal | None:
    """Resolve the caller from claims the authentication layer verified.

    Reads `request.state.claims`, which the OIDC middleware sets after
    verifying a token against MEGINGJORD's JWKS. This module never verifies a
    signature and never sees a bearer value: an unverified claim set reaching
    here would be indistinguishable from a verified one, so the verification
    lives where the token is, and this reads only its result.
    """
    claims = getattr(request.state, "claims", None)
    if not claims:
        return None
    return from_claims(claims)


def scope_for(principal: Principal | None) -> SiteScope:
    """The site this request is scoped to.

    From the principal's claim, falling back to the deployment's own site.
    Never from a header or a query parameter: the row level security variable
    is set from whatever resolves here, and a site the caller can name is a
    site the caller can change.
    """
    settings = get_settings()
    if principal is not None:
        claimed = principal.claims.get("site_id")
        if isinstance(claimed, str) and claimed:
            return SiteScope(site_id=claimed)
    return SiteScope(site_id=settings.site_id)


async def context(
    request: Request,
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
) -> AsyncIterator[RequestContext]:
    """Build and bind the request context. Every route depends on this.

    A `yield` dependency rather than middleware, because a context variable
    must be reset in the context that set it. Starlette runs middleware in a
    different task from the endpoint, so a token taken in a dependency and
    reset in middleware raises -- and the resulting 500 replaces every status
    the API meant to return.
    """
    principal = principal_from(request)
    built = request_context.build(
        scope=scope_for(principal),
        principal=principal,
        correlation_id=correlation_id,
        method=request.method,
        path=request.url.path,
    )
    request.state.context = built
    token = request_context.bind(built)
    try:
        yield built
    finally:
        request_context.unbind(token)


Context = Annotated[RequestContext, Depends(context)]


async def guard(request: Request, ctx: Context) -> RequestContext:
    """Enforce the route's own declaration. AC-S4, and it fails closed.

    A matched route with no declaration is a 500 rather than a permit. It
    should be unreachable -- `enforce_declarations` refuses to start an
    application containing one -- and if it is reached anyway then the startup
    check has a hole, which is not a thing to serve a request through.
    """
    endpoint = request.scope.get("endpoint")
    requirement = requirement_of(endpoint) if endpoint else None

    if requirement is None:
        telemetry.log("authorisation.undeclared", path=request.url.path)
        raise ProblemError(
            status=500,
            code="route-undeclared",
            title="Route has no authorisation declaration",
            detail=(
                "This route declares no role requirement. It is refused rather than "
                "permitted: startup should have rejected the application (AC-B6), so "
                "reaching this means the startup check has a hole."
            ),
        )

    decision = decide(requirement, ctx.principal)
    # `as_log_context` already names the event, so it is splatted whole
    # rather than passed alongside a second event name.
    telemetry.log(**decision.as_log_context())

    if decision.permitted:
        return ctx

    if decision.status == 401:
        raise ProblemError(
            status=401,
            code="unauthenticated",
            title="Authentication required",
            detail=decision.reason,
        )
    raise ProblemError(
        status=403, code="forbidden", title="Insufficient permissions", detail=decision.reason
    )


Guarded = Annotated[RequestContext, Depends(guard)]


# ---------------------------------------------------------------------------
# Conventions of SAD 11E.2, as dependencies
# ---------------------------------------------------------------------------


async def page_size(
    limit: Annotated[int | None, Query(ge=1, le=200, description="Page size.")] = None,
) -> int:
    """A validated page size. Cursor pagination throughout (AC-B3)."""
    try:
        return clamp(limit)
    except PaginationError as error:
        raise ProblemError(
            status=422, code="invalid-page-size", title="Invalid page size", detail=str(error)
        ) from error


PageSize = Annotated[int, Depends(page_size)]

Cursor = Annotated[
    str | None,
    Query(alias="cursor", description="Opaque cursor from a previous page's `nextCursor`."),
]

IfMatch = Annotated[
    str | None,
    Header(alias="If-Match", description="Entity tag the write is conditional on."),
]

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description="Replaying a request with the same key returns the original result.",
    ),
]


def require_idempotency_key(key: str | None) -> str:
    """Refuse a mutating request that carries no key.

    Required rather than optional. SAD 11E.2 says every mutating endpoint
    accepts one; making it optional means the retry that actually duplicated
    something was the one that omitted it.
    """
    if not key:
        raise ProblemError(
            status=428,
            code="idempotency-key-required",
            title="Idempotency-Key required",
            detail=(
                "Every mutating endpoint requires an Idempotency-Key so that a retry "
                "returns the original result rather than acting twice (SAD 11E.2, "
                "AC-B1). Generate a UUID per logical request and reuse it on retry."
            ),
        )
    return key


def replay_or_reserve(
    key: str, ctx: RequestContext, payload: Any, *, using: IdempotencyStore | None = None
) -> Any:
    """Claim the key, or return the stored body a replay should receive.

    Returns `None` when the caller should proceed with the work.
    """
    chosen = using or store()
    try:
        record = chosen.reserve(
            key, site_id=ctx.site_id, actor=ctx.actor, payload=payload, now=now()
        )
    except InFlightError as error:
        raise ProblemError(
            status=409,
            code="request-in-flight",
            title="Request already in progress",
            detail=str(error),
        ) from error
    except KeyReusedError as error:
        raise ProblemError(
            status=422,
            code="idempotency-key-reused",
            title="Idempotency-Key reused for a different request",
            detail=str(error),
        ) from error
    return record


def as_problem(error: ConcurrencyError) -> ProblemError:
    """Map a precondition failure onto its problem document. AC-B4."""
    if isinstance(error, PreconditionRequiredError):
        return ProblemError(
            status=428,
            code="precondition-required",
            title="If-Match required",
            detail=str(error),
        )
    if isinstance(error, PreconditionFailedError):
        return ProblemError(
            status=412,
            code="precondition-failed",
            title="Resource has changed",
            detail=str(error),
        )
    return ProblemError(status=409, code="conflict", title="Conflicting request", detail=str(error))


def accepted(run_id: UUID, *, detail: str) -> dict[str, Any]:
    """The 202 body every long operation returns. AC-B9.

    Nothing blocks an HTTP request on training, so a submission returns the
    identifier of the run it started and the client watches the event stream.
    """
    return {
        "runId": str(run_id),
        "status": "accepted",
        "detail": detail,
        "events": f"/v1/runs/{run_id}/events",
    }
