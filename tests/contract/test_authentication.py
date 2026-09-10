"""Bearer token verification. RF-01.

`deps.principal_from` said the OIDC middleware set `request.state.claims` after
verifying a token against MEGINGJORD's JWKS. There was no such middleware —
nothing read an `Authorization` header, nothing fetched a JWKS, nothing
verified a JWT. So the control plane answered 401 to every `/v1` route in the
only configuration it is meant to be deployed in.

The tests below sign real tokens with keys generated here, so the verification
path is exercised rather than mocked. Each negative case is a specific attack
or a specific mistake, named in the test.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from draupnir.api import authentication
from draupnir.api.app import create_app
from draupnir.api.routers import auth
from draupnir.svalinn.identity import TOKEN_ALGORITHMS

pytestmark = pytest.mark.contract

ISSUER = "https://megingjord.veldris.internal"
AUDIENCE = "draupnir-control-plane"
KID = "forge-2026-01"


def a_key() -> rsa.RSAPrivateKey:
    """One RSA key. 2048 is what an OIDC provider actually issues."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


SIGNING = a_key()
OTHER = a_key()


def jwks_of(key: rsa.RSAPrivateKey, kid: str = KID) -> dict[str, Any]:
    """The public half of `key`, as a JWKS document."""
    entry = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    entry.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return {"keys": [entry]}


@dataclass
class StubResponse:
    """One canned JWKS answer."""

    status_code: int = 200
    body: Any = None

    def json(self) -> Any:
        """The decoded body."""
        return self.body


@dataclass
class StubClient:
    """Serves a JWKS without a socket, and counts how often it is asked."""

    document: Any = None
    status_code: int = 200
    calls: list[str] = field(default_factory=list)
    error: Exception | None = None

    def get(self, url: str, *, params: Any = None) -> StubResponse:
        """Answer the fetch."""
        del params
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return StubResponse(status_code=self.status_code, body=self.document)


def a_token(
    key: rsa.RSAPrivateKey = SIGNING,
    *,
    kid: str = KID,
    algorithm: str = "RS256",
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expires_in: timedelta = timedelta(minutes=5),
    roles: tuple[str, ...] = ("viewer",),
    **extra: Any,
) -> str:
    """A signed token, valid unless a test asks for it not to be."""
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": "operator@veldris.internal",
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
        "roles": list(roles),
        "amr": ["pwd", "hwk"],
        "site_id": "sindri",
    }
    claims.update(extra)
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})


def a_verifier(client: StubClient | None = None, **overrides: Any) -> authentication.Verifier:
    """A verifier whose keys come from a stub."""
    settings: dict[str, Any] = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "keys": authentication.Keys(
            jwks_url=f"{ISSUER}/.well-known/jwks.json",
            client=client or StubClient(document=jwks_of(SIGNING)),
        ),
    }
    settings.update(overrides)
    return authentication.Verifier(**settings)


def client_with(verifier: authentication.Verifier | None) -> TestClient:
    """An application with `verifier` installed and nothing else."""
    app = create_app()
    if verifier is not None:
        authentication.install(app, verifier)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# The refusals. Each is a specific attack or a specific mistake.
# ---------------------------------------------------------------------------


def test_a_request_with_no_header_is_refused() -> None:
    """The baseline, and the state the whole API was in before RF-01."""
    assert client_with(a_verifier()).get("/v1/sites").status_code == 401


def test_a_token_signed_by_a_key_not_in_the_jwks_is_refused() -> None:
    """Threat T4: a token of the right shape from an unexpected issuer."""
    response = client_with(a_verifier()).get(
        "/v1/sites", headers={"Authorization": f"Bearer {a_token(OTHER)}"}
    )

    assert response.status_code == 401


def test_an_expired_token_is_refused() -> None:
    """A token that outlives its session is the thing an attacker keeps."""
    token = a_token(expires_in=timedelta(minutes=-10))

    response = client_with(a_verifier()).get(
        "/v1/sites", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401


def test_a_token_with_alg_none_is_refused() -> None:
    """The oldest JWT attack there is.

    A verifier that reads `alg` from the header and dispatches on it accepts an
    unsigned token. This one is given an allow-list instead, and `none` is not
    in it.
    """
    unsigned = jwt.encode(
        {"sub": "attacker", "iss": ISSUER, "aud": AUDIENCE, "exp": 9_999_999_999, "iat": 0},
        key="",
        algorithm="none",
        headers={"kid": KID},
    )

    response = client_with(a_verifier()).get(
        "/v1/sites", headers={"Authorization": f"Bearer {unsigned}"}
    )

    assert response.status_code == 401


def test_a_token_signed_with_hmac_over_the_public_key_is_refused() -> None:
    """Algorithm confusion, which `alg: none` is only the crudest form of.

    The issuer's public key is public. A verifier that accepted `HS256` would
    treat that key as a shared secret, so anybody holding the JWKS — everybody
    — could mint a token that verifies.

    Forged by hand rather than with `jwt.encode`, because PyJWT refuses to
    *sign* this way: it recognises a PEM being passed as an HMAC secret and
    raises. That refusal is a defence on the signing side and says nothing
    about what a verifier accepts, so the attacker's half is built here.
    """
    public = SIGNING.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    def segment(payload: dict[str, Any]) -> bytes:
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")

    signing_input = b".".join(
        (
            segment({"alg": "HS256", "typ": "JWT", "kid": KID}),
            segment(
                {
                    "sub": "attacker",
                    "iss": ISSUER,
                    "aud": AUDIENCE,
                    "exp": 9_999_999_999,
                    "iat": 0,
                    "roles": ["admin"],
                }
            ),
        )
    )
    signature = base64.urlsafe_b64encode(
        hmac.new(public, signing_input, hashlib.sha256).digest()
    ).rstrip(b"=")
    forged = (signing_input + b"." + signature).decode()

    response = client_with(a_verifier()).get(
        "/v1/sites", headers={"Authorization": f"Bearer {forged}"}
    )

    assert response.status_code == 401


def test_a_token_for_another_audience_is_refused() -> None:
    """A token minted for a different client of the same issuer.

    Without an audience check, any service MEGINGJORD issues for becomes a
    token factory for this one.
    """
    response = client_with(a_verifier()).get(
        "/v1/sites", headers={"Authorization": f"Bearer {a_token(audience='some-other-client')}"}
    )

    assert response.status_code == 401


def test_a_token_from_another_issuer_is_refused() -> None:
    """Same shape, same key, wrong authority."""
    response = client_with(a_verifier()).get(
        "/v1/sites", headers={"Authorization": f"Bearer {a_token(issuer='https://elsewhere')}"}
    )

    assert response.status_code == 401


def test_a_header_that_is_not_a_bearer_is_ignored() -> None:
    """Basic authentication is not a fallback. There is no fallback."""
    for header in ("", "Basic abc", "Bearer", "Bearer   ", "bearertoken"):
        assert authentication.bearer(header) is None


def test_a_bearer_header_is_read_case_insensitively() -> None:
    """RFC 7235 makes the scheme case-insensitive, and clients differ."""
    assert authentication.bearer("bearer abc") == "abc"
    assert authentication.bearer("BEARER abc") == "abc"


# ---------------------------------------------------------------------------
# The one that must work
# ---------------------------------------------------------------------------


def test_a_valid_token_reaches_the_guard_with_its_roles() -> None:
    """The whole point: a verified caller gets the access their roles allow."""
    response = client_with(a_verifier()).get(
        "/v1/sites", headers={"Authorization": f"Bearer {a_token(roles=('viewer',))}"}
    )

    assert response.status_code == 200


def test_the_roles_in_the_token_are_the_roles_enforced() -> None:
    """A verified token is not an authorised one.

    Authentication says who; `guards` says what they may do. A viewer's token
    reaching a submit route must still be refused, or the middleware has become
    a second authorisation decision.
    """
    viewer = a_token(roles=("viewer",))

    response = client_with(a_verifier()).post(
        "/v1/runs",
        headers={"Authorization": f"Bearer {viewer}", "Idempotency-Key": "k" * 24},
        json={},
    )

    assert response.status_code == 403


def test_the_amr_claim_survives_verification() -> None:
    """SAD 9.4: an approver must have used a hardware factor.

    `identity.from_claims` reads `amr`, so it has to arrive intact rather than
    being flattened on the way through.
    """
    verifier = a_verifier()

    claims = verifier.verify(a_token(roles=("approver",)))

    assert claims is not None
    assert "hwk" in claims["amr"]


# ---------------------------------------------------------------------------
# The key set
# ---------------------------------------------------------------------------


def test_an_unknown_kid_refreshes_the_key_set_once() -> None:
    """Key rotation looks like an unknown `kid` from here.

    And so does an attacker sending random ones, which is why the refresh has a
    floor: between refreshes an unknown `kid` is simply a token that does not
    verify, rather than a fetch pointed at the identity provider.
    """
    clock = [1000.0]
    stub = StubClient(document=jwks_of(SIGNING))
    keys = authentication.Keys(
        jwks_url="https://megingjord.veldris.internal/jwks",
        client=stub,
        clock=lambda: clock[0],
    )

    assert keys.for_kid(KID) is not None
    assert len(stub.calls) == 1

    for _ in range(20):
        assert keys.for_kid("made-up") is None
    assert len(stub.calls) == 1, "an unknown kid fetched on every request"

    clock[0] += authentication.MIN_REFRESH_SECONDS + 1
    keys.for_kid("made-up")
    assert len(stub.calls) == 2, "the refresh floor never lifts"


def test_a_key_the_issuer_withdrew_stops_verifying() -> None:
    """Replaced wholesale rather than merged.

    A key the issuer removed is a key it has stopped trusting. Merging would
    keep verifying tokens signed by it for as long as the process lived, which
    is exactly the window a rotation exists to close.
    """
    clock = [1000.0]
    stub = StubClient(document=jwks_of(SIGNING))
    keys = authentication.Keys(
        jwks_url="https://megingjord.veldris.internal/jwks",
        client=stub,
        clock=lambda: clock[0],
    )
    assert keys.for_kid(KID) is not None

    stub.document = jwks_of(OTHER, kid="forge-2026-02")
    clock[0] += authentication.CACHE_SECONDS + 1

    assert keys.for_kid("forge-2026-02") is not None
    assert keys.for_kid(KID) is None, "the withdrawn key still verifies"


def test_an_unreachable_issuer_does_not_raise() -> None:
    """A verifier that raised would turn an identity outage into a 500."""
    keys = authentication.Keys(
        jwks_url="https://megingjord.veldris.internal/jwks",
        client=StubClient(error=OSError("connection refused")),
    )

    assert keys.for_kid(KID) is None


def test_an_oversized_key_set_is_refused() -> None:
    """A JWKS is a handful of keys, not an unbounded document."""
    many = {
        "keys": [{"kid": str(index), "kty": "oct"} for index in range(authentication.MAX_KEYS + 1)]
    }
    keys = authentication.Keys(
        jwks_url="https://megingjord.veldris.internal/jwks",
        client=StubClient(document=many),
    )

    assert keys.for_kid("0") is None


def test_one_unparseable_key_does_not_lose_the_others() -> None:
    """A provider adding a key type this build cannot read must not lock it out."""
    document = jwks_of(SIGNING)
    document["keys"].append({"kid": "nonsense", "kty": "not-a-key-type"})

    keys = authentication.Keys(
        jwks_url="https://megingjord.veldris.internal/jwks",
        client=StubClient(document=document),
    )

    assert keys.for_kid(KID) is not None
    assert keys.for_kid("nonsense") is None


# ---------------------------------------------------------------------------
# Configuration, and the refusal to start without it
# ---------------------------------------------------------------------------


def test_the_algorithm_allow_list_is_asymmetric_only() -> None:
    """The control that makes algorithm confusion unreachable."""
    assert "none" not in TOKEN_ALGORITHMS
    assert not any(name.startswith("HS") for name in TOKEN_ALGORITHMS)
    assert TOKEN_ALGORITHMS, "an empty allow-list would refuse everything"


def test_the_api_refuses_to_start_with_no_way_to_authenticate() -> None:
    """RF-01's acceptance criterion, and the reason it is a refusal.

    An API that starts and answers 401 to everything looks like a broken
    deployment. It gets debugged for an afternoon and then somebody sets
    DRAUPNIR_DEV to make it stop.
    """

    @dataclass
    class Unconfigured:
        oidc_issuer: str = ""
        oidc_audience: str = ""

    with pytest.raises(authentication.AuthenticationError) as raised:
        authentication.refuse_unconfigured(Unconfigured(), development=False)

    message = str(raised.value)
    assert "DRAUPNIR_OIDC_ISSUER" in message, "the refusal does not name the missing setting"
    assert "DRAUPNIR_OIDC_AUDIENCE" in message
    assert "DRAUPNIR_DEV" in message, "the refusal does not name the development escape"


def test_a_development_machine_is_allowed_to_start_unconfigured() -> None:
    """DRAUPNIR_DEV=1 is the documented concession and stays one."""

    @dataclass
    class Unconfigured:
        oidc_issuer: str = ""
        oidc_audience: str = ""

    authentication.refuse_unconfigured(Unconfigured(), development=True)


def test_the_leeway_is_capped_however_it_is_configured() -> None:
    """Beyond a minute this stops being skew and starts extending a token."""

    @dataclass
    class Generous:
        oidc_issuer: str = ISSUER
        oidc_audience: str = AUDIENCE
        oidc_jwks_url: str = ""
        oidc_leeway_seconds: int = 86_400

    built = authentication.verifier_from(Generous())

    assert built is not None
    assert built.leeway_seconds == authentication.DEFAULT_LEEWAY_SECONDS


def test_the_jwks_url_is_derived_from_the_issuer_when_unset() -> None:
    """One setting fewer to get wrong, by the discovery convention."""

    @dataclass
    class Derived:
        oidc_issuer: str = ISSUER + "/"
        oidc_audience: str = AUDIENCE
        oidc_jwks_url: str = ""
        oidc_leeway_seconds: int = 60

    built = authentication.verifier_from(Derived())

    assert built is not None
    assert built.keys.jwks_url == f"{ISSUER}/.well-known/jwks.json"


# ---------------------------------------------------------------------------
# No token value ever reaches a log line
# ---------------------------------------------------------------------------


def test_no_token_value_reaches_a_log_line(caplog: pytest.LogCaptureFixture) -> None:
    """Threat T6. A rejected token is still a credential.

    Every refusal logs the *type* of the failure and never the token, so a log
    aggregator does not become a place where bearer tokens accumulate.
    """
    token = a_token(expires_in=timedelta(minutes=-10))

    with caplog.at_level("DEBUG"):
        assert a_verifier().verify(token) is None

    captured = caplog.text
    assert token not in captured
    for segment in token.split("."):
        if len(segment) > 16:
            assert segment not in captured, "a token segment reached the log"


def test_a_verifier_never_reprs_its_client() -> None:
    """A client can carry a credential; a dataclass repr carries everything."""
    built = a_verifier()

    assert "StubClient" not in repr(built.keys)


# ---------------------------------------------------------------------------
# The session cookie, and the flow that sets it. RF-03.
# ---------------------------------------------------------------------------


def test_a_session_cookie_is_accepted_like_a_bearer_header() -> None:
    """The console holds no token, and that is the point.

    A static bundle given a token must keep it somewhere JavaScript can read,
    which makes every cross-site scripting bug a credential disclosure. An
    HttpOnly cookie is unreadable from script and the console needs no token
    handling at all.
    """
    api = client_with(a_verifier())
    api.cookies.set(auth.SESSION_COOKIE, a_token())

    assert api.get("/v1/sites").status_code == 200


def test_an_explicit_header_wins_over_a_cookie() -> None:
    """A cookie is attached by the browser whether the caller meant it or not.

    A bearer header is a credential somebody chose, so it takes precedence and
    a stale cookie cannot shadow a fresh token.
    """
    api = client_with(a_verifier())
    api.cookies.set(auth.SESSION_COOKIE, a_token(expires_in=timedelta(minutes=-10)))

    response = api.get("/v1/sites", headers={"Authorization": f"Bearer {a_token()}"})

    assert response.status_code == 200


def test_a_forged_session_cookie_is_refused() -> None:
    """The cookie is verified, not trusted for being a cookie."""
    api = client_with(a_verifier())
    api.cookies.set(auth.SESSION_COOKIE, a_token(OTHER))

    assert api.get("/v1/sites").status_code == 401


def test_the_sign_in_button_reaches_an_endpoint_that_exists() -> None:
    """`SignIn.tsx` links to `/auth/login`, which existed nowhere. RF-03."""
    api = client_with(a_verifier())

    response = api.get("/auth/login?return_to=/runs", follow_redirects=False)

    assert response.status_code != 404, "the only button on the sign-in screen is a dead link"
    assert response.status_code == 303


def test_the_login_redirect_carries_pkce_and_a_state_cookie() -> None:
    """Without PKCE an intercepted code is redeemable by whoever has it."""
    api = client_with(a_verifier())

    response = api.get("/auth/login", follow_redirects=False)
    location = response.headers["location"]

    assert "code_challenge=" in location
    assert "code_challenge_method=S256" in location
    assert "state=" in location
    assert auth.STATE_COOKIE in response.cookies
    assert auth.VERIFIER_COOKIE in response.cookies


def test_the_flow_cookies_are_httponly() -> None:
    """The PKCE verifier is a secret for the duration of the flow."""
    api = client_with(a_verifier())

    raw = api.get("/auth/login", follow_redirects=False).headers.get_list("set-cookie")

    for name in (auth.STATE_COOKIE, auth.VERIFIER_COOKIE):
        (header,) = [item for item in raw if item.startswith(f"{name}=")]
        assert "HttpOnly" in header, f"{name} is readable from script"
        assert "samesite=lax" in header.lower()


def test_a_callback_with_a_mismatched_state_is_refused() -> None:
    """The CSRF defence: a code delivered to a browser that never asked."""
    api = client_with(a_verifier())
    api.cookies.set(auth.STATE_COOKIE, "the-one-we-issued", path="/auth")
    api.cookies.set(auth.VERIFIER_COOKIE, "v" * 43, path="/auth")

    response = api.get("/auth/callback?code=abc&state=someone-elses", follow_redirects=False)

    assert response.status_code == 303
    assert "/signin" in response.headers["location"]
    assert auth.SESSION_COOKIE not in response.cookies


def test_a_callback_with_no_flow_cookies_is_refused() -> None:
    """A code presented cold, without the browser that began the flow."""
    response = client_with(a_verifier()).get(
        "/auth/callback?code=abc&state=xyz", follow_redirects=False
    )

    assert "/signin" in response.headers["location"]
    assert auth.SESSION_COOKIE not in response.cookies


def test_the_refusal_does_not_echo_a_reason_into_the_url() -> None:
    """A sign-in page that renders attacker-chosen text is a phishing surface.

    And the sign-in page is the one place where trust matters most.
    """
    response = client_with(a_verifier()).get(
        "/auth/callback?code=abc&state=xyz", follow_redirects=False
    )

    assert response.headers["location"] == "/signin?error=sign-in-failed"


def test_a_return_path_that_leaves_the_origin_is_discarded() -> None:
    """An open redirect on a sign-in endpoint is worth more than most.

    The link is one the user was going to click anyway, and it arrives from a
    host they trust.
    """
    for hostile in (
        "https://evil.example/steal",
        "//evil.example/steal",
        r"/\evil.example",
        "\\evil.example",
        "javascript:alert(1)",
    ):
        assert auth.safe_return(hostile) == "/", f"{hostile} was accepted as a return path"

    assert auth.safe_return("/runs/abc") == "/runs/abc"


def test_the_pkce_challenge_is_the_s256_of_the_verifier() -> None:
    """RFC 7636. A plain challenge would be no defence at all."""
    verifier = "a" * 43

    challenge = auth.challenge_for(verifier)

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert challenge == expected
    assert challenge != verifier


def test_signing_out_clears_the_session() -> None:
    """And works for a session the API can no longer verify.

    An expired token, or one from a rotated key. Requiring a principal here
    would mean the only sessions that could be discarded are the valid ones.
    """
    api = client_with(a_verifier())
    api.cookies.set(auth.SESSION_COOKIE, a_token(expires_in=timedelta(minutes=-10)))

    response = api.get("/auth/logout", follow_redirects=False)

    assert response.status_code == 303
    (header,) = [
        item
        for item in response.headers.get_list("set-cookie")
        if item.startswith(f"{auth.SESSION_COOKIE}=")
    ]
    assert "Max-Age=0" in header or '""' in header or "expires" in header.lower()
