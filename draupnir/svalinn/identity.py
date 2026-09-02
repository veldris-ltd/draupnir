"""Who is calling: OIDC claims resolved into a principal, and what MFA they used.

MEGINGJORD is the OIDC issuer and the RBAC source of truth (SAD 11A.1), so
roles arrive in a token rather than being held here. This module turns a
verified claim set into a `Principal` and answers two questions about it: what
may it do, and did it authenticate strongly enough for what it is about to do.

The second question is AC-S15, and it is the compensating control that carries
the assurance weight where separation of duties is unavailable (constraint
C-11). "The approver identity requires hardware backed multi factor
authentication" is checked from the `amr` claim, which is where an OIDC
provider records how the subject actually authenticated -- not from a role, a
configuration flag, or a note in a runbook.

Nothing here verifies a token signature. That is the edge's, and it belongs
with the HTTP layer that has the JWKS client; this module is handed claims that
have already been verified and refuses to guess whether they were.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from draupnir.svalinn.roles import Permission, Role, allows, parse, permissions_of

#: `amr` values that mean a hardware authenticator was used. From RFC 8176 plus
#: the values an OIDC provider emits for a WebAuthn or FIDO2 credential.
HARDWARE_AMR: Final[frozenset[str]] = frozenset({"hwk", "swk-hw", "fido", "fido2", "webauthn"})

#: `amr` values that mean a second factor of some kind was presented.
SECOND_FACTOR_AMR: Final[frozenset[str]] = frozenset(
    {"mfa", "otp", "hwk", "fido", "fido2", "webauthn"}
)

#: Sessions are short lived (SAD 12.1, T4). Beyond this a principal is stale
#: whatever the token says, because a token that outlives its session is the
#: thing an attacker keeps.
MAX_SESSION_AGE_SECONDS = 3600


class IdentityError(Exception):
    """Raised when a claim set cannot be resolved into a principal."""


class AuthenticationStrengthError(IdentityError):
    """Raised when an action needs stronger authentication than was presented.

    AC-S15. Distinct from an authorisation failure on purpose: the subject may
    well hold the role, and telling them they lack permission would send them
    to an administrator instead of to their security key.
    """

    def __init__(self, subject: str, action: str, presented: Iterable[str]) -> None:
        """Name the subject, the action and what they actually presented."""
        self.subject = subject
        self.action = action
        self.presented = tuple(sorted(presented))
        super().__init__(
            f"{subject} may perform {action} but authenticated with "
            f"{', '.join(self.presented) or 'no recorded method'}. That action requires "
            "hardware backed multi factor authentication (AC-S15). This is not a "
            "permissions problem: the role is held, the authentication is not strong "
            "enough. Re-authenticate with the hardware authenticator."
        )


@dataclass(frozen=True, slots=True)
class Principal:
    """One authenticated caller, as the edge resolved them."""

    subject: str
    roles: frozenset[Role]
    #: The OIDC issuer that vouched for them. Recorded because a token from an
    #: unexpected issuer with the right shape is exactly threat T4.
    issuer: str
    #: `amr`: how the subject authenticated. RFC 8176.
    authentication_methods: frozenset[str] = frozenset()
    #: `auth_time`: when they actually authenticated, not when the token was
    #: minted. A refreshed token carries a fresh `iat` and the original
    #: `auth_time`, and it is the latter that says how old the session is.
    authenticated_at: datetime | None = None
    claims: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse a principal that cannot be held to anything."""
        if not self.subject:
            msg = "a principal has a subject; an anonymous principal is not a principal"
            raise IdentityError(msg)
        if not self.roles:
            msg = (
                f"{self.subject} carries no role. A principal with no role is refused "
                "rather than treated as a viewer: authorisation fails closed, and a "
                "default role is the shape that decision gets reversed in."
            )
            raise IdentityError(msg)
        if self.authenticated_at is not None and self.authenticated_at.tzinfo is None:
            msg = "authentication timestamps carry an explicit offset (SAD 11E.2)"
            raise IdentityError(msg)

    @property
    def permissions(self) -> frozenset[Permission]:
        """Everything this principal may do."""
        return permissions_of(self.roles)

    @property
    def hardware_backed(self) -> bool:
        """Whether a hardware authenticator was used. AC-S15."""
        return bool(self.authentication_methods & HARDWARE_AMR)

    @property
    def multi_factor(self) -> bool:
        """Whether a second factor of any kind was presented."""
        return bool(self.authentication_methods & SECOND_FACTOR_AMR)

    def may(self, permission: Permission) -> bool:
        """Whether this principal holds `permission`."""
        return allows(self.roles, permission)

    def session_age(self, now: datetime) -> float | None:
        """Seconds since the subject actually authenticated."""
        if self.authenticated_at is None:
            return None
        return (now - self.authenticated_at).total_seconds()

    def as_log_context(self) -> dict[str, Any]:
        """What goes in the audit line. AC-S4 requires both outcomes logged.

        Carries the subject and the issuer and no token, no claim set and no
        bearer value. A log line that quotes a credential is a credential that
        now lives in the log aggregator.
        """
        return {
            "subject": self.subject,
            "issuer": self.issuer,
            "roles": sorted(str(item) for item in self.roles),
            "amr": sorted(self.authentication_methods),
            "hardwareBacked": self.hardware_backed,
        }


#: Actions that require a hardware authenticator, whatever role permits them.
#: Publishing is the one AC-S15 names; deciding a gate is here because under
#: C-11 it is the same person and the same credential.
REQUIRES_HARDWARE_MFA: Final[frozenset[Permission]] = frozenset(
    {Permission.PUBLISH_RELEASE, Permission.DECIDE_GATE}
)


def from_claims(claims: Mapping[str, Any]) -> Principal:
    """Build a principal from a set of already-verified OIDC claims.

    Raises rather than returning an anonymous principal. A function that can
    return "nobody" invites a caller to treat "nobody" as a viewer.
    """
    subject = str(claims.get("sub") or "")
    issuer = str(claims.get("iss") or "")
    if not issuer:
        msg = (
            "the claim set names no issuer. A token of the right shape from an "
            "unexpected issuer is threat T4; the issuer is recorded and checked."
        )
        raise IdentityError(msg)

    raw_roles = claims.get("roles") or claims.get("groups") or ()
    if isinstance(raw_roles, str):
        raw_roles = raw_roles.split()
    roles = frozenset(parse(str(item)) for item in raw_roles)

    authenticated_at = claims.get("auth_time")
    when: datetime | None = None
    if isinstance(authenticated_at, datetime):
        when = authenticated_at

    return Principal(
        subject=subject,
        roles=roles,
        issuer=issuer,
        authentication_methods=frozenset(str(item) for item in claims.get("amr", ())),
        authenticated_at=when,
        claims=dict(claims),
    )


def require_hardware_mfa(principal: Principal, permission: Permission) -> None:
    """Raise unless the principal authenticated strongly enough for this action.

    AC-S15. Checked at the point of the action rather than at login, because a
    session that began with a password and was elevated by a role change would
    otherwise carry the weaker authentication into the stronger action.
    """
    if permission not in REQUIRES_HARDWARE_MFA:
        return
    if not principal.hardware_backed:
        raise AuthenticationStrengthError(
            principal.subject, str(permission), principal.authentication_methods
        )
