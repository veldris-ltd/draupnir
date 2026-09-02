"""Authorisation at the HTTP boundary, and the startup check that fails closed.

AC-B6: "A route registered without a role declaration prevents application
startup." The prompt puts it more sharply: a route without an explicit role
declaration must fail to register at startup, *not* fail open at runtime.

The difference between those two is the whole design. A runtime check on a
route with no declaration has to decide something, and every available answer
is wrong: allow, and the route is open; deny, and a route that should be public
is broken in production rather than in CI. So the check runs when the
application is assembled, over the routes that were actually registered, and
`create_app` raises before a socket is opened.

`declare` records the requirement on the endpoint function itself rather than
in a registry keyed by path. A registry drifts: somebody renames a path and the
entry is orphaned, leaving a route with no declaration and a declaration for no
route. An attribute on the function cannot be separated from the function.

SVALINN makes the decision (`draupnir.svalinn.authz`); this module knows HTTP
and holds no policy of its own -- no role list, no path pattern, no exception.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, TypeVar

from fastapi import FastAPI
from fastapi.routing import APIRoute

from draupnir.svalinn.authz import (
    Requirement,
    UndeclaredRouteError,
    public,
    requires,
)
from draupnir.svalinn.roles import Permission

#: Where the declaration is kept on the endpoint function.
ATTRIBUTE = "__draupnir_requirement__"

#: Paths FastAPI adds for itself. They are not application routes and nobody
#: writes an endpoint for them, so requiring a declaration would mean requiring
#: it on code this repository does not contain.
GENERATED: frozenset[str] = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})

Endpoint = TypeVar("Endpoint", bound=Callable[..., Any])


def declare(requirement: Requirement) -> Callable[[Endpoint], Endpoint]:
    """Record what a route requires, on the endpoint itself.

    Also appends the roles to the endpoint's docstring, so the generated
    OpenAPI document states who may call each operation. A permissions table
    that is published from the same declaration the guard reads cannot drift
    from it.
    """

    def decorate(endpoint: Endpoint) -> Endpoint:
        setattr(endpoint, ATTRIBUTE, requirement)
        if requirement.satisfied_by:
            roles = ", ".join(f"`{item}`" for item in requirement.satisfied_by)
            endpoint.__doc__ = f"{endpoint.__doc__ or ''}\n\nRequires: {roles}.".lstrip()
        return endpoint

    return decorate


def needs(permission: Permission, *, reason: str = "") -> Callable[[Endpoint], Endpoint]:
    """Declare that a route requires `permission`. The common case."""
    return declare(requires(permission, reason=reason))


def unauthenticated(reason: str) -> Callable[[Endpoint], Endpoint]:
    """Declare that a route is deliberately public, and why."""
    return declare(public(reason))


def requirement_of(endpoint: Callable[..., Any]) -> Requirement | None:
    """The requirement declared on an endpoint, or `None` if there is none."""
    found = getattr(endpoint, ATTRIBUTE, None)
    return found if isinstance(found, Requirement) else None


def iter_api_routes(routes: Iterable[Any], prefix: str = "") -> Iterator[tuple[str, APIRoute]]:
    """Every `APIRoute` under `routes`, with its full path.

    Recursive, because FastAPI does not flatten included routers into
    `app.routes` -- it wraps each `include_router` in a router object holding
    the original and a prefix. A sweep that only looked at the top level would
    find the generated documentation routes and none of the application's, and
    would then report that every route is declared.

    That is not hypothetical: it is what this function did when it was first
    written, and the check passed vacuously until a test asserted that the
    sweep actually finds something.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield f"{prefix}{route.path}", route
            continue

        nested = getattr(route, "original_router", None)
        if nested is not None:
            context = getattr(route, "include_context", None)
            yield from iter_api_routes(
                nested.routes, prefix + (getattr(context, "prefix", "") or "")
            )
            continue

        # A Mount or a sub-application: descend where we can.
        inner = getattr(route, "routes", None)
        if inner:
            yield from iter_api_routes(inner, prefix + (getattr(route, "path", "") or ""))


def unrecognised_routes(routes: Iterable[Any]) -> tuple[str, ...]:
    """Route objects the sweep can neither read nor descend into.

    The canary. If a future FastAPI release changes how routes are held, the
    sweep would otherwise find nothing and silently pass -- which is how a
    fail-closed check becomes a fail-open one without anybody editing it.
    """
    strange: list[str] = []
    for route in routes:
        if isinstance(route, APIRoute):
            continue
        if getattr(route, "original_router", None) is not None:
            continue
        path = getattr(route, "path", None)
        if path in GENERATED:
            continue
        if getattr(route, "routes", None):
            continue
        strange.append(f"{type(route).__name__} {path or '?'}")
    return tuple(sorted(strange))


def undeclared_routes(routes: Iterable[Any]) -> tuple[str, ...]:
    """Every application route carrying no declaration.

    Reads the routes the application actually registered, not a list somebody
    maintains. A route that exists and is not in this sweep is a route that
    does not exist.
    """
    missing: list[str] = []
    for path, route in iter_api_routes(routes):
        if path in GENERATED:
            continue
        if requirement_of(route.endpoint) is None:
            methods = ",".join(sorted(route.methods or {"GET"}))
            missing.append(f"{methods} {path}")
    return tuple(sorted(missing))


def enforce_declarations(app: FastAPI) -> None:
    """Raise unless every registered route declares who may call it. AC-B6.

    Called by `create_app` before the application is returned, so a missing
    declaration is a startup failure and therefore a CI failure. There is
    deliberately no environment variable that relaxes this: the development
    concession for unsigned plug-ins exists because a developer cannot sign,
    and no equivalent argument applies to writing down who may call a route.
    """
    strange = unrecognised_routes(app.routes)
    if strange:
        msg = (
            f"the route sweep does not recognise {', '.join(strange)}. It cannot "
            "establish that every route declares who may call it, and a check that "
            "cannot establish its property must not pass. This most likely means a "
            "FastAPI upgrade changed how routes are held; teach `iter_api_routes` "
            "about the new shape."
        )
        raise UndeclaredRouteError(msg)

    missing = undeclared_routes(app.routes)
    if missing:
        raise UndeclaredRouteError(", ".join(missing))


def permissions_table(app: FastAPI) -> tuple[dict[str, Any], ...]:
    """Every route and what it requires, for the compliance evidence pack.

    Generated from the same declarations the guard enforces, so the published
    table and the enforced rule are one thing.
    """
    rows: list[dict[str, Any]] = []
    for path, route in iter_api_routes(app.routes):
        if path in GENERATED:
            continue
        found = requirement_of(route.endpoint)
        if found is None:
            continue
        rows.append(
            {
                "path": path,
                "methods": sorted(route.methods or {"GET"}),
                "operationId": route.operation_id or route.name,
                **found.as_payload(),
            }
        )
    return tuple(sorted(rows, key=lambda row: (str(row["path"]), str(row["methods"]))))


def audit_lines(decisions: Sequence[Any]) -> tuple[dict[str, Any], ...]:
    """Render decisions for the audit log. AC-S4 requires both outcomes logged."""
    return tuple(decision.as_log_context() for decision in decisions)
