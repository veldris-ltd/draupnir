"""Verifying a bearer token, and nothing else. RF-01.

`deps.principal_from` reads `request.state.claims` and says it is set by "the
OIDC middleware ... after verifying a token against MEGINGJORD's JWKS". There
was no such middleware. Nothing read an `Authorization` header, nothing fetched
a JWKS, nothing verified a JWT — so a deployment with `DRAUPNIR_DEV` unset,
which is the only configuration Sindri is meant to run in, answered 401 to
every `/v1` route.

**This module authenticates and does not authorise.** It sets
`request.state.claims` to a verified claim set, or it leaves the attribute
unset. It never returns 401 itself and never inspects a role. The reason is
that `guards` already decides authorisation from a declaration attached to each
route, and startup checks that every route carries one; a middleware that also
made access decisions would be a second answer to the same question, and the
two would eventually disagree. So the failure mode is uniform: no verified
claims means no principal, and the existing guard refuses.

**Where the trust boundary is.** Everything below verifies a signature over a
public key obtained from the issuer. It never accepts a claim about *which* key
to use except by `kid`, never accepts an algorithm from the token, and never
falls back to an unverified decode. `jwt.decode` is called once, with an
explicit algorithm allow-list and explicit `iss` and `aud`, because every one
of those is a check that is skipped by default.

**On the JWKS fetch.** The key set is fetched through an injected client so the
composition root can route it through SVALINN's egress broker — the same
arrangement as the telemetry reader and the scheduler driver. `PyJWKClient`
would have done the fetch itself, with its own urllib call and its own cache,
and that call would have been outbound traffic no allow list had decided
(threat T11). Owning the fetch also means owning the cache, which the refresh
policy below needs.

**Why the cache has a floor as well as a ceiling.** An unknown `kid` triggers a
refresh, because that is what key rotation looks like from here. An attacker
who can send requests can therefore make this fetch, so a token carrying a
random `kid` each time would be a way to hammer the identity provider from
outside. `MIN_REFRESH_SECONDS` bounds that: between refreshes an unknown `kid`
is simply a token that does not verify.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import jwt
from fastapi import FastAPI, Request, Response

from draupnir.api import telemetry
from draupnir.api.routers.auth import SESSION_COOKIE
from draupnir.svalinn.identity import TOKEN_ALGORITHMS

#: How long a key set is used before it is fetched again. Keys rotate on the
#: order of days; an hour keeps a rotation from being noticed late without
#: making the identity provider part of the request path.
CACHE_SECONDS: Final = 3600.0

#: The shortest interval between two fetches, whatever prompts them. See the
#: module docstring: an unknown `kid` is a refresh trigger an unauthenticated
#: caller controls.
MIN_REFRESH_SECONDS: Final = 30.0

#: How much clock skew is tolerated on `exp`, `nbf` and `iat`. Sixty seconds is
#: the prompt's ceiling and is generous for an estate running `chrony` against
#: REGIN; anything larger starts to extend the life of a revoked token.
DEFAULT_LEEWAY_SECONDS: Final = 60

#: A key set larger than this is refused rather than cached. A JWKS is a
#: handful of keys; a large one is either a misconfigured endpoint or something
#: that is not a JWKS, and neither should be held in memory per process.
MAX_KEYS: Final = 32


class AuthenticationError(Exception):
    """Raised when the API cannot be configured to authenticate anybody."""


class Fetched(Protocol):
    """The part of an HTTP response the key fetch needs."""

    status_code: int

    def json(self) -> Any:
        """The decoded body."""
        ...


class Client(Protocol):
    """The part of an HTTP client the key fetch needs.

    Injected so the composition root supplies one that goes through the egress
    broker. `httpx.Client` satisfies it directly, which is what a development
    machine gets.
    """

    def get(self, url: str, *, params: Mapping[str, str] | None = ...) -> Fetched:
        """Fetch one document."""
        ...


@dataclass
class Keys:
    """MEGINGJORD's public keys, cached, refreshed on an unknown `kid`."""

    jwks_url: str
    client: Client | None = field(default=None, repr=False)
    cache_seconds: float = CACHE_SECONDS
    min_refresh_seconds: float = MIN_REFRESH_SECONDS
    clock: Callable[[], float] = time.monotonic

    _keys: dict[str, Any] = field(default_factory=dict, repr=False)
    _fetched_at: float | None = field(default=None, repr=False)

    def for_kid(self, kid: str) -> Any | None:
        """The verification key for `kid`, fetching if it is unknown.

        Returns `None` rather than raising. A key that cannot be found is a
        token that does not verify, and the caller turns that into an absent
        principal — the same outcome as any other failure, so that an attacker
        learns nothing from which one they hit.
        """
        if self._stale() or (kid and kid not in self._keys):
            self._refresh()
        return self._keys.get(kid)

    def _stale(self) -> bool:
        return self._fetched_at is None or self.clock() - self._fetched_at > self.cache_seconds

    def _refresh(self) -> None:
        """Fetch the key set, at most once per `min_refresh_seconds`."""
        now = self.clock()
        if self._fetched_at is not None and now - self._fetched_at < self.min_refresh_seconds:
            return
        if self.client is None:
            return

        # The timestamp is set before the fetch is attempted rather than after
        # it succeeds. A provider that is down would otherwise be retried on
        # every request, turning its outage into a request-rate amplifier
        # pointed at itself.
        self._fetched_at = now

        try:
            answer = self.client.get(self.jwks_url)
            if answer.status_code != 200:
                telemetry.log("auth.jwks.refused", level="warning", status=answer.status_code)
                return
            document = answer.json()
        except Exception as error:
            telemetry.log("auth.jwks.unreachable", level="warning", reason=type(error).__name__)
            return

        found = document.get("keys") if isinstance(document, Mapping) else None
        if not isinstance(found, list) or len(found) > MAX_KEYS:
            telemetry.log("auth.jwks.malformed", level="warning", count=len(found or ()))
            return

        parsed: dict[str, Any] = {}
        for entry in found:
            if not isinstance(entry, Mapping) or not entry.get("kid"):
                continue
            try:
                parsed[str(entry["kid"])] = jwt.PyJWK(dict(entry)).key
            except Exception:
                telemetry.log("auth.jwks.key-rejected", level="warning", kid=str(entry["kid"]))

        # Replaced wholesale rather than merged. A key the issuer has removed
        # is a key it has stopped trusting, and merging would keep verifying
        # tokens signed by it for as long as the process lived.
        if parsed:
            self._keys = parsed


@dataclass
class Verifier:
    """Turns a bearer token into a verified claim set, or into nothing."""

    issuer: str
    audience: str
    keys: Keys
    leeway_seconds: int = DEFAULT_LEEWAY_SECONDS
    algorithms: tuple[str, ...] = TOKEN_ALGORITHMS

    def verify(self, token: str) -> dict[str, Any] | None:
        """The claim set, if every check passes. `None` otherwise.

        One return value for every failure, deliberately. Distinguishing "no
        such key" from "expired" from "wrong audience" in the answer tells an
        attacker which of their guesses was closer; the log line says which,
        and the log line is not something they can read.
        """
        try:
            header = jwt.get_unverified_header(token)
        except Exception:
            return None

        # The header is read for `kid` only. `alg` is never taken from it: it
        # is attacker controlled, and `jwt.decode` is given the allow-list
        # instead. See `identity.TOKEN_ALGORITHMS`.
        key = self.keys.for_kid(str(header.get("kid") or ""))
        if key is None:
            telemetry.log("auth.rejected", level="warning", reason="unknown key")
            return None

        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self.algorithms),
                issuer=self.issuer,
                audience=self.audience,
                leeway=self.leeway_seconds,
                options={
                    # Named rather than left to the library's defaults, because
                    # a default that changes between versions is a control that
                    # changes without anybody deciding to.
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.InvalidTokenError as refusal:
            # The type, never the token. A rejected token is still a
            # credential, and a log line carrying one is a credential in a log
            # aggregator (threat T6).
            telemetry.log("auth.rejected", level="warning", reason=type(refusal).__name__)
            return None

        return dict(claims)


def bearer(header: str | None) -> str | None:
    """The token from an `Authorization` header, if it is a bearer one."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def install(app: FastAPI, verifier: Verifier) -> None:
    """Attach token verification to `app`.

    Installed ahead of the development principal so that, in the configuration
    where somebody has set both, the real one runs first and the development
    one overwrites it — which is loud, logged at startup, and the case that
    should never exist rather than the one to optimise.
    """

    @app.middleware("http")
    async def authenticate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Header first, cookie second. A caller that presents a bearer token
        # has chosen a credential explicitly; a cookie is attached by the
        # browser whether or not the caller meant it, so the explicit one wins
        # and a stale cookie cannot shadow a fresh token.
        token = bearer(request.headers.get("Authorization")) or request.cookies.get(SESSION_COOKIE)
        if token:
            claims = verifier.verify(token)
            if claims is not None:
                request.state.claims = claims
        return await call_next(request)


def verifier_from(settings: Any, client: Client | None = None) -> Verifier | None:
    """Build a verifier from configuration, or `None` if none is configured."""
    if not settings.oidc_issuer or not settings.oidc_audience:
        return None
    jwks_url = settings.oidc_jwks_url or f"{settings.oidc_issuer.rstrip('/')}/.well-known/jwks.json"
    return Verifier(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        leeway_seconds=min(int(settings.oidc_leeway_seconds), DEFAULT_LEEWAY_SECONDS),
        keys=Keys(jwks_url=jwks_url, client=client),
    )


def refuse_unconfigured(settings: Any, *, development: bool) -> None:
    """Refuse to start with no way to authenticate anybody. RF-01.

    The same fail-closed shape `guards.enforce_declarations` uses: a control
    plane that starts without a control is one nobody notices is missing, and
    this one presents as an API that answers 401 to everything — which reads
    like a broken deployment rather than a missing setting, so the deployment
    gets debugged instead of configured.
    """
    if development or (settings.oidc_issuer and settings.oidc_audience):
        return
    missing = [
        name
        for name, value in (
            ("DRAUPNIR_OIDC_ISSUER", settings.oidc_issuer),
            ("DRAUPNIR_OIDC_AUDIENCE", settings.oidc_audience),
        )
        if not value
    ]
    msg = (
        f"no way to authenticate a caller: {', '.join(missing)} is not set and "
        "DRAUPNIR_DEV is not enabled. Every /v1 route would answer 401, which reads "
        "as a broken deployment rather than as a missing setting -- so this refuses "
        "to start instead. Register the control plane as an OIDC client with "
        "MEGINGJORD and set the issuer and audience "
        "(docs/DEPLOYMENT.md), or set DRAUPNIR_DEV=1 on a machine with no real data."
    )
    raise AuthenticationError(msg)


__all__ = [
    "CACHE_SECONDS",
    "DEFAULT_LEEWAY_SECONDS",
    "MAX_KEYS",
    "MIN_REFRESH_SECONDS",
    "AuthenticationError",
    "Client",
    "Keys",
    "Verifier",
    "bearer",
    "install",
    "refuse_unconfigured",
    "verifier_from",
]
