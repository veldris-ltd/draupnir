"""The authorisation decision. Framework-free, and it fails closed.

SAD 11B keeps HTTP at the edge, so the FastAPI wiring lives in
`draupnir.api.guards`. What is here is the decision itself: given a principal
and a declared requirement, permit or refuse, with a reason either way.

"Fails closed" is the whole design. Every function here refuses in the absence
of information rather than in the presence of a denial:

* no principal is a refusal, not an anonymous read;
* no role is a refusal, not a viewer;
* no declared requirement is a *programming error* that raises, not an open
  route -- which is what makes the startup check in the edge possible.

That last one is the one worth arguing about. A route with no declared role
could reasonably default to `admin`, which is safe. It raises instead, because
a default that is safe is still a default: it means a route can be added
without anybody deciding who may call it, and the decision that gets skipped is
the one this module exists to record.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from draupnir.svalinn.identity import (
    AuthenticationStrengthError,
    Principal,
    require_hardware_mfa,
)
from draupnir.svalinn.roles import Permission, Role, roles_with


class AuthorisationError(Exception):
    """Raised when a call may not proceed."""


class UnauthenticatedError(AuthorisationError):
    """Raised when nobody is authenticated. AC-S4's 401."""

    def __init__(self, path: str = "") -> None:
        """Name what was reached for."""
        self.path = path
        where = f" {path}" if path else ""
        super().__init__(
            f"no authenticated principal for{where or ' this call'}. Every `/v1` path "
            "requires authentication (AC-S4)."
        )


class ForbiddenError(AuthorisationError):
    """Raised when the principal is known and may not do this. AC-S4's 403."""

    def __init__(self, principal: Principal, permission: Permission) -> None:
        """Name who, what, and which roles would have sufficed."""
        self.principal = principal
        self.permission = permission
        holders = ", ".join(str(item) for item in roles_with(permission))
        held = ", ".join(sorted(str(item) for item in principal.roles))
        super().__init__(
            f"{principal.subject} holds {held} and {permission} requires "
            f"{holders or 'a role no one holds'}. Refused (AC-S4)."
        )


class UndeclaredRouteError(AuthorisationError):
    """Raised when a route declares no requirement at all.

    Raised at registration, so the application does not start. That is the
    point of it: a route whose authorisation nobody decided must not be
    reachable, and discovering it at runtime means discovering it from a log
    line after somebody has already called it.
    """

    def __init__(self, name: str) -> None:
        """Name the route that has no declaration."""
        self.name = name
        super().__init__(
            f"the route {name!r} declares no role requirement, so the application will "
            "not start. Every route states who may call it; a route with no "
            "declaration is not an open route, it is a decision nobody made. Decorate "
            "it with `requires(...)`, or with `public()` if it is genuinely "
            "unauthenticated (SAD 11B, AC-B6)."
        )


@dataclass(frozen=True, slots=True)
class Requirement:
    """What a route declares about who may call it."""

    #: The permission a caller must hold. `None` only for a public route.
    permission: Permission | None
    #: True where the route is deliberately unauthenticated: an operator probe,
    #: the OpenAPI document. Stated rather than inferred from a missing
    #: permission, so that "public" is something somebody wrote down.
    public: bool = False
    #: Free text for the OpenAPI description and the audit evidence.
    reason: str = ""

    def __post_init__(self) -> None:
        """Refuse a requirement that says nothing."""
        if self.public and self.permission is not None:
            msg = (
                "a route is public or it requires a permission, not both. A public "
                "route with a permission is a route whose access control depends on "
                "which check runs first."
            )
            raise AuthorisationError(msg)
        if not self.public and self.permission is None:
            msg = "a non-public route names the permission it requires"
            raise AuthorisationError(msg)
        if self.public and not self.reason:
            msg = (
                "a public route states why it is public. An unauthenticated endpoint "
                "with no stated reason is one nobody will dare remove and nobody can "
                "justify."
            )
            raise AuthorisationError(msg)

    @property
    def satisfied_by(self) -> tuple[Role, ...]:
        """Which roles may call this route. For the OpenAPI description."""
        return () if self.permission is None else roles_with(self.permission)

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, for the evidence pack and the generated documents."""
        return {
            "public": self.public,
            "permission": str(self.permission) if self.permission else None,
            "roles": [str(item) for item in self.satisfied_by],
            "reason": self.reason or None,
        }


def requires(permission: Permission, *, reason: str = "") -> Requirement:
    """Declare that a route requires `permission`."""
    return Requirement(permission=permission, reason=reason)


def public(reason: str) -> Requirement:
    """Declare that a route is deliberately unauthenticated, and why."""
    return Requirement(permission=None, public=True, reason=reason)


@dataclass(frozen=True, slots=True)
class Decision:
    """Permit or refuse, with enough context for the audit line AC-S4 wants."""

    permitted: bool
    reason: str
    principal: Principal | None = None
    permission: Permission | None = None
    #: 401 where nobody is authenticated, 403 where they are and may not.
    status: int = 200
    context: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        """Allow `if decide(...)`."""
        return self.permitted

    def as_log_context(self) -> dict[str, Any]:
        """The structured audit line. Never carries a token or a claim set."""
        return {
            "event": "authorisation",
            "permitted": self.permitted,
            "status": self.status,
            "permission": str(self.permission) if self.permission else None,
            "reason": self.reason,
            **(self.principal.as_log_context() if self.principal else {"subject": None}),
            **self.context,
        }


def decide(requirement: Requirement, principal: Principal | None) -> Decision:
    """Permit or refuse one call. The only place that answer is produced.

    Both outcomes are returned rather than raised, because AC-S4 requires both
    to be logged and an exception makes the success path and the failure path
    look different to a caller that has to log either way.
    """
    if requirement.public:
        return Decision(permitted=True, reason="route is declared public", status=200)

    if principal is None:
        return Decision(
            permitted=False,
            reason="no authenticated principal",
            permission=requirement.permission,
            status=401,
        )

    permission = requirement.permission
    assert permission is not None  # noqa: S101 -- guaranteed by Requirement.__post_init__

    if not principal.may(permission):
        return Decision(
            permitted=False,
            reason=str(ForbiddenError(principal, permission)),
            principal=principal,
            permission=permission,
            status=403,
        )

    try:
        require_hardware_mfa(principal, permission)
    except AuthenticationStrengthError as weak:
        return Decision(
            permitted=False,
            reason=str(weak),
            principal=principal,
            permission=permission,
            status=403,
            context={"authenticationStrength": "insufficient"},
        )

    return Decision(
        permitted=True,
        reason=f"{principal.subject} holds {permission}",
        principal=principal,
        permission=permission,
        status=200,
    )


def enforce(requirement: Requirement, principal: Principal | None) -> Principal | None:
    """Decide, and raise on refusal. For a caller that wants exceptions."""
    decision = decide(requirement, principal)
    if decision.permitted:
        return principal
    if decision.status == 401:
        raise UnauthenticatedError
    assert decision.principal is not None and decision.permission is not None  # noqa: S101
    raise ForbiddenError(decision.principal, decision.permission)


def undeclared(names: Iterable[str]) -> tuple[str, ...]:
    """Every route name with no declaration, for the startup check."""
    return tuple(sorted(names))
