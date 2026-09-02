"""Authorisation: AC-S4, AC-S15, AC-B6, and Decision S6.

AC-S4: an unauthenticated request to any `/v1` path returns 401; a `viewer`
attempting to submit a run returns 403; both are logged.

AC-S15: the approver identity requires hardware-backed multi-factor
authentication.

AC-B6, in the prompt's sharper form: a route without an explicit role
declaration must fail to register at startup, not fail open at runtime. That is
the last test in this file and it builds a real application to prove it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import APIRouter, FastAPI

from draupnir.api.guards import (
    ATTRIBUTE,
    enforce_declarations,
    iter_api_routes,
    needs,
    permissions_table,
    requirement_of,
    unauthenticated,
    undeclared_routes,
    unrecognised_routes,
)
from draupnir.svalinn import roles
from draupnir.svalinn.authz import (
    AuthorisationError,
    ForbiddenError,
    UnauthenticatedError,
    UndeclaredRouteError,
    decide,
    enforce,
    public,
    requires,
)
from draupnir.svalinn.identity import (
    AuthenticationStrengthError,
    IdentityError,
    Principal,
    from_claims,
    require_hardware_mfa,
)
from draupnir.svalinn.roles import Permission, Role, RoleError

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
ISSUER = "https://megingjord.veldris.internal"


def principal(
    *granted: Role, amr: tuple[str, ...] = ("pwd",), subject: str = "someone"
) -> Principal:
    """A principal holding the given roles."""
    return Principal(
        subject=subject,
        roles=frozenset(granted),
        issuer=ISSUER,
        authentication_methods=frozenset(amr),
        authenticated_at=AT,
    )


# ---------------------------------------------------------------------------
# The role table. SAD 9.4 and Decision S6.
# ---------------------------------------------------------------------------


def test_the_five_roles_are_the_five_the_sad_names() -> None:
    assert {str(item) for item in Role} == {
        "viewer",
        "curator",
        "operator",
        "approver",
        "admin",
    }


def test_no_role_may_both_submit_a_run_and_approve_its_release() -> None:
    """Decision S6, checked rather than trusted.

    The way this breaks is somebody adding a convenience role for a small
    team, not somebody arguing against the decision.
    """
    submit, publish = roles.SEPARATED

    for role, granted in roles.GRANTS.items():
        assert not (submit in granted and publish in granted), (
            f"the {role} role holds both {submit} and {publish}. No role may both "
            "submit a run and approve its release (Decision S6). Constraint C-11's "
            "single approver is handled by recording sole_approver_exception on the "
            "approval, not by widening a role."
        )


def test_the_curator_cannot_submit_training_runs() -> None:
    """SAD 9.4's "cannot" clauses are absences, and they are load bearing."""
    assert not roles.allows([Role.CURATOR], Permission.SUBMIT_RUN)


def test_the_operator_cannot_approve_or_publish() -> None:
    assert not roles.allows([Role.OPERATOR], Permission.PUBLISH_RELEASE)
    assert not roles.allows([Role.OPERATOR], Permission.DECIDE_GATE)


def test_the_admin_cannot_decide_gates_or_delete_ledger_entries() -> None:
    """SAD 9.4: admin manages users, plug-ins and policy, and no more."""
    assert not roles.allows([Role.ADMIN], Permission.DECIDE_GATE)
    assert not roles.allows([Role.ADMIN], Permission.DELETE_LEDGER_ENTRY)


def test_no_role_at_all_may_delete_a_ledger_entry() -> None:
    """The ledger is append only. There is no role to add this to."""
    assert Permission.DELETE_LEDGER_ENTRY in roles.UNGRANTED
    assert roles.roles_with(Permission.DELETE_LEDGER_ENTRY) == ()


def test_every_role_may_read() -> None:
    for role in Role:
        assert roles.allows([role], Permission.READ)


def test_an_unknown_role_name_is_refused() -> None:
    with pytest.raises(RoleError, match="not a DRAUPNIR role"):
        roles.parse("superuser")


# ---------------------------------------------------------------------------
# AC-S4
# ---------------------------------------------------------------------------


def test_an_unauthenticated_call_is_401_and_logged() -> None:
    """AC-S4, first clause."""
    decision = decide(requires(Permission.READ), None)

    assert not decision
    assert decision.status == 401
    assert decision.as_log_context()["permitted"] is False
    assert decision.as_log_context()["subject"] is None


def test_a_viewer_submitting_a_run_is_403_and_logged() -> None:
    """AC-S4, second clause."""
    decision = decide(requires(Permission.SUBMIT_RUN), principal(Role.VIEWER))

    assert not decision
    assert decision.status == 403
    assert "requires operator" in decision.reason
    context = decision.as_log_context()
    assert context["permitted"] is False
    assert context["subject"] == "someone"
    assert context["permission"] == "submit_run"


def test_an_operator_submitting_a_run_is_permitted_and_logged() -> None:
    decision = decide(requires(Permission.SUBMIT_RUN), principal(Role.OPERATOR))

    assert decision
    assert decision.status == 200
    assert decision.as_log_context()["permitted"] is True


def test_the_audit_line_carries_no_token_or_claims() -> None:
    """A log line that quotes a credential is a credential in the aggregator."""
    who = Principal(
        subject="someone",
        roles=frozenset({Role.OPERATOR}),
        issuer=ISSUER,
        authentication_methods=frozenset({"pwd"}),
        authenticated_at=AT,
        claims={"sub": "someone", "access_token": "supersecret", "iss": ISSUER},
    )

    rendered = repr(decide(requires(Permission.SUBMIT_RUN), who).as_log_context())

    assert "supersecret" not in rendered
    assert "claims" not in rendered


def test_a_public_route_permits_an_unauthenticated_call() -> None:
    assert decide(public("operator probe"), None)


def test_enforce_raises_the_right_exception_for_each_outcome() -> None:
    with pytest.raises(UnauthenticatedError):
        enforce(requires(Permission.READ), None)

    with pytest.raises(ForbiddenError):
        enforce(requires(Permission.SUBMIT_RUN), principal(Role.VIEWER))

    assert enforce(requires(Permission.READ), principal(Role.VIEWER)) is not None


# ---------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------


def test_a_principal_with_no_role_is_refused_rather_than_treated_as_a_viewer() -> None:
    """A default role is the shape this decision gets reversed in."""
    with pytest.raises(IdentityError, match="carries no role"):
        Principal(subject="someone", roles=frozenset(), issuer=ISSUER)


def test_a_claim_set_with_no_issuer_is_refused() -> None:
    """A token of the right shape from an unexpected issuer is threat T4."""
    with pytest.raises(IdentityError, match="names no issuer"):
        from_claims({"sub": "someone", "roles": ["viewer"]})


def test_a_requirement_that_says_nothing_is_refused() -> None:
    with pytest.raises(AuthorisationError, match="names the permission it requires"):
        requires(None)  # type: ignore[arg-type]


def test_a_public_route_with_a_permission_is_a_contradiction() -> None:
    from draupnir.svalinn.authz import Requirement

    with pytest.raises(AuthorisationError, match="which check runs first"):
        Requirement(permission=Permission.READ, public=True, reason="both")


def test_a_public_route_must_say_why_it_is_public() -> None:
    with pytest.raises(AuthorisationError, match="nobody can justify"):
        public("")


# ---------------------------------------------------------------------------
# AC-S15: hardware-backed multi-factor authentication
# ---------------------------------------------------------------------------


def test_publishing_requires_a_hardware_authenticator() -> None:
    """AC-S15, and the compensating control that carries C-11's weight."""
    with_password = principal(Role.APPROVER, amr=("pwd",))

    decision = decide(requires(Permission.PUBLISH_RELEASE), with_password)

    assert not decision
    assert decision.status == 403
    assert "hardware backed multi factor" in decision.reason
    assert decision.context["authenticationStrength"] == "insufficient"


def test_a_hardware_authenticator_satisfies_it() -> None:
    with_key = principal(Role.APPROVER, amr=("pwd", "hwk"))

    assert with_key.hardware_backed
    assert decide(requires(Permission.PUBLISH_RELEASE), with_key)


def test_an_otp_second_factor_is_not_hardware_backed() -> None:
    """Multi-factor and hardware-backed are different claims."""
    with_otp = principal(Role.APPROVER, amr=("pwd", "otp"))

    assert with_otp.multi_factor
    assert not with_otp.hardware_backed
    with pytest.raises(AuthenticationStrengthError):
        require_hardware_mfa(with_otp, Permission.PUBLISH_RELEASE)


def test_deciding_a_gate_also_requires_it() -> None:
    """Under C-11 it is the same person and the same credential."""
    with pytest.raises(AuthenticationStrengthError):
        require_hardware_mfa(principal(Role.APPROVER, amr=("pwd",)), Permission.DECIDE_GATE)


def test_the_strength_failure_is_distinguished_from_a_permissions_failure() -> None:
    """Telling them they lack permission sends them to an administrator."""
    weak = principal(Role.APPROVER, amr=("pwd",))

    message = str(decide(requires(Permission.PUBLISH_RELEASE), weak).reason)

    assert "not a permissions problem" in message
    assert "hardware authenticator" in message


def test_submitting_a_run_does_not_require_a_hardware_authenticator() -> None:
    """The requirement is scoped to the actions AC-S15 names."""
    assert decide(requires(Permission.SUBMIT_RUN), principal(Role.OPERATOR, amr=("pwd",)))


def test_a_principal_is_built_from_verified_claims() -> None:
    who = from_claims(
        {
            "sub": "akuma",
            "iss": ISSUER,
            "roles": "approver operator",
            "amr": ["pwd", "fido2"],
            "auth_time": AT,
        }
    )

    assert who.roles == {Role.APPROVER, Role.OPERATOR}
    assert who.hardware_backed
    assert who.session_age(AT) == 0.0


# ---------------------------------------------------------------------------
# AC-B6: a route without a declaration prevents startup
# ---------------------------------------------------------------------------


def test_a_route_without_a_role_declaration_prevents_startup() -> None:
    """The exit condition, stated directly.

    Builds an application with one undeclared route and asserts that assembling
    it raises. Not that the call is refused at runtime -- that it does not
    start.
    """
    app = FastAPI()
    router = APIRouter()

    @router.get("/v1/runs")
    async def list_runs() -> dict[str, str]:
        """A route nobody decided the authorisation for."""
        return {}

    app.include_router(router)

    with pytest.raises(UndeclaredRouteError) as raised:
        enforce_declarations(app)

    assert "/v1/runs" in str(raised.value)
    assert "will not start" in str(raised.value)
    assert "a decision nobody made" in str(raised.value)


def test_the_sweep_actually_finds_the_applications_routes() -> None:
    """The canary, and it caught a real defect.

    FastAPI does not flatten included routers into `app.routes`; it wraps each
    `include_router` in a router object holding the original and a prefix. The
    first version of the sweep only looked at the top level, found no
    `APIRoute` at all, and therefore reported that every route was declared.

    A check that passes because it examined nothing is worse than no check.
    So this asserts the sweep finds something, and
    `test_an_unrecognised_route_object_prevents_startup` asserts it refuses
    rather than passes when it cannot read the structure.
    """
    from draupnir.api.app import create_app

    found = {path for path, _ in iter_api_routes(create_app().routes)}

    assert found == {"/healthz", "/readyz"}


def test_the_real_application_starts_because_every_route_is_declared() -> None:
    """The other half: the check is satisfiable and is actually wired in."""
    from draupnir.api.app import create_app

    app = create_app()

    assert undeclared_routes(app.routes) == ()
    assert unrecognised_routes(app.routes) == ()


def test_an_unrecognised_route_object_prevents_startup() -> None:
    """A structure the sweep cannot read must refuse, not pass.

    This is how a fail-closed check becomes a fail-open one without anybody
    editing it: a framework upgrade changes the shape, the sweep silently finds
    nothing, and every route is reported as declared.
    """

    class Strange:
        """Something that is neither a route nor a router."""

        path = "/v1/mystery"

    app = FastAPI()
    app.router.routes.append(Strange())  # type: ignore[arg-type]

    with pytest.raises(UndeclaredRouteError, match="does not recognise"):
        enforce_declarations(app)


def test_a_declared_route_passes_the_check() -> None:
    app = FastAPI()
    router = APIRouter()

    @router.post("/v1/runs")
    @needs(Permission.SUBMIT_RUN)
    async def submit() -> dict[str, str]:
        """Submit a run."""
        return {}

    app.include_router(router)
    enforce_declarations(app)

    table = permissions_table(app)
    assert table[0]["permission"] == "submit_run"
    assert table[0]["roles"] == ["operator"]


def test_the_declaration_lives_on_the_endpoint_not_in_a_registry() -> None:
    """A registry keyed by path drifts when somebody renames a path."""

    @needs(Permission.READ)
    async def endpoint() -> None:
        """Read something."""

    assert requirement_of(endpoint) is not None
    assert hasattr(endpoint, ATTRIBUTE)


def test_the_declaration_is_published_in_the_route_documentation() -> None:
    """The permissions table and the enforced rule are one thing."""

    @needs(Permission.PUBLISH_RELEASE)
    async def endpoint() -> None:
        """Publish a release."""

    assert "Requires: `approver`" in (endpoint.__doc__ or "")


def test_a_public_declaration_satisfies_the_check() -> None:
    app = FastAPI()
    router = APIRouter()

    @router.get("/healthz")
    @unauthenticated("an orchestrator probes this before a token exists")
    async def probe() -> dict[str, str]:
        """Liveness."""
        return {}

    app.include_router(router)
    enforce_declarations(app)

    assert permissions_table(app)[0]["public"] is True


def test_generated_documentation_routes_need_no_declaration() -> None:
    """Requiring one would mean requiring it on code this repository lacks."""
    from draupnir.api.app import create_app

    app = create_app()
    paths = {getattr(route, "path", None) for route in app.routes}

    assert "/openapi.json" in paths
    assert undeclared_routes(app.routes) == ()
