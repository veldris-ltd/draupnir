"""The authorisation-code flow the console already links to. RF-03.

`SignIn.tsx` navigates to `/auth/login?return_to=…` when somebody presses
"Continue to MEGINGJORD". That path existed nowhere — not as a route, not in
the nginx configuration — so the only button on the sign-in screen was a dead
link to a 404. These are the two endpoints it wanted.

**Why a cookie rather than a token in the page.** The console is a static
bundle with no backend of its own. Giving it a token to hold means putting a
bearer credential somewhere JavaScript can read it, which makes every
cross-site scripting bug a credential disclosure. An `HttpOnly` cookie is
unreadable from script, and the console then needs no token handling at all —
it makes same-origin requests and the browser attaches the cookie.

**The cookie holds the token itself, and there is no session store.** The token
is already signed by MEGINGJORD and already verifiable, so a server-side
session table would add state to a control plane whose whole recovery story is
that it holds none (SAD 5.1) — and it would be process-local in a deployment
that runs two to four processes, which is RF-14's defect acquired deliberately.
`authentication.Verifier` verifies a cookie exactly as it verifies a header.

**PKCE, and state as a double-submit.** The verifier is generated per attempt
and kept in an `HttpOnly` cookie, so an attacker who intercepts the
authorisation code cannot exchange it without also having the browser. `state`
is a random value written to a cookie and compared against the one that comes
back, which is the CSRF defence and needs no signing key — one fewer secret to
distribute, rotate and lose.

**`return_to` is checked, not trusted.** It arrives in a query parameter and is
sent to the browser as a `Location`, which is an open-redirect if it is taken
as given. Only a same-origin path is accepted; anything else falls back to the
root, which is a worse experience for one request and not a way to phish
somebody with a veldris.internal URL.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from draupnir.api import telemetry
from draupnir.api.guards import unauthenticated
from draupnir.core.infrastructure.config import get_settings
from draupnir.svalinn import egress

router = APIRouter(tags=["operations"])

#: The one status that means the code was redeemed.
_OK: Final = 200

#: The cookie the middleware reads. Named for the product rather than
#: `session`, so that two Veldris services behind one domain do not silently
#: share one.
SESSION_COOKIE: Final = "draupnir_session"

#: Short-lived, and only alive between the redirect out and the redirect back.
STATE_COOKIE: Final = "draupnir_oidc_state"
VERIFIER_COOKIE: Final = "draupnir_oidc_verifier"
RETURN_COOKIE: Final = "draupnir_oidc_return"

#: How long the flow may take. Ten minutes is generous for a person typing a
#: password and touching a security key, and short enough that an abandoned
#: attempt is not a credential sitting in a browser overnight.
FLOW_SECONDS: Final = 600

#: How long the session cookie lives in the browser. The token's own `exp` is
#: what actually decides — this only stops the browser sending one that cannot
#: possibly still be valid.
SESSION_SECONDS: Final = 3600


def _random() -> str:
    """A value an attacker cannot guess. 32 bytes, URL-safe."""
    return secrets.token_urlsafe(32)


def challenge_for(verifier: str) -> str:
    """The S256 PKCE challenge for `verifier`. RFC 7636."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def safe_return(target: str | None) -> str:
    """A same-origin path from `target`, or the root.

    An open redirect on a sign-in endpoint is worth more to an attacker than
    most: the link is one the user was going to click anyway, and it comes from
    a host they trust. So this accepts a path and nothing else — not a URL with
    a host, not a protocol-relative `//elsewhere`, not a backslash, which some
    browsers normalise to a slash.
    """
    if not target or not target.startswith("/"):
        return "/"
    if target.startswith("//") or target.startswith("/\\") or "\\" in target[:2]:
        return "/"
    return target


def endpoints(settings: Any) -> tuple[str, str]:
    """The authorisation and token endpoints, configured or derived."""
    issuer = settings.oidc_issuer.rstrip("/")
    authorise = settings.oidc_authorization_endpoint or f"{issuer}/authorize"
    token = settings.oidc_token_endpoint or f"{issuer}/token"
    return authorise, token


@router.get("/auth/login", summary="Begin the authorisation-code flow", operation_id="beginLogin")
@unauthenticated(
    "This is where a caller goes to *become* authenticated. Requiring a principal "
    "here would mean nobody could ever obtain one."
)
async def login(request: Request, return_to: str | None = None) -> RedirectResponse:
    """Redirect to MEGINGJORD, carrying a PKCE challenge and a state value."""
    settings = get_settings()
    if not settings.oidc_issuer or not settings.oidc_client_id:
        # Not a 500. A control plane in development has no identity provider,
        # and the honest answer to "sign me in" is that there is nowhere to go.
        telemetry.log("auth.login.unconfigured", level="warning")
        return RedirectResponse("/signin?error=not-configured", status_code=303)

    authorise, _token = endpoints(settings)
    state = _random()
    verifier = _random()

    target = str(
        request.url_for("callback")
        if settings.oidc_redirect_uri == ""
        else settings.oidc_redirect_uri
    )

    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": target,
            "scope": settings.oidc_scope,
            "state": state,
            "code_challenge": challenge_for(verifier),
            "code_challenge_method": "S256",
            "audience": settings.oidc_audience,
        }
    )

    answer = RedirectResponse(f"{authorise}?{query}", status_code=303)
    secure = request.url.scheme == "https"
    for name, value in (
        (STATE_COOKIE, state),
        (VERIFIER_COOKIE, verifier),
        (RETURN_COOKIE, safe_return(return_to)),
    ):
        answer.set_cookie(
            name,
            value,
            max_age=FLOW_SECONDS,
            httponly=True,
            secure=secure,
            samesite="lax",
            path="/auth",
        )
    return answer


@router.get(
    "/auth/callback", summary="Complete the authorisation-code flow", operation_id="callback"
)
@unauthenticated(
    "The identity provider redirects the browser here with a code. There is no "
    "principal yet; establishing one is what this does."
)
async def callback(
    request: Request, code: str | None = None, state: str | None = None
) -> RedirectResponse:
    """Exchange the code for a token and set the session cookie."""
    settings = get_settings()
    presented = request.cookies.get(STATE_COOKIE)
    verifier = request.cookies.get(VERIFIER_COOKIE)
    destination = safe_return(request.cookies.get(RETURN_COOKIE))

    def refuse(reason: str) -> RedirectResponse:
        """Send the browser back to sign-in, and clear the flow cookies.

        The reason is logged and not put in the URL: an error message that
        round-trips through a query parameter is one an attacker can choose,
        and a sign-in page that renders attacker-chosen text is a phishing
        surface on the one page where trust matters most.
        """
        telemetry.log("auth.callback.refused", level="warning", reason=reason)
        answer = RedirectResponse("/signin?error=sign-in-failed", status_code=303)
        for name in (STATE_COOKIE, VERIFIER_COOKIE, RETURN_COOKIE):
            answer.delete_cookie(name, path="/auth")
        return answer

    if not code or not state or not presented or not verifier:
        return refuse("the callback is missing a code, a state or the flow cookies")

    # Constant time, because this is a secret being compared against something
    # the caller supplied.
    if not secrets.compare_digest(state, presented):
        return refuse("the state did not match the one this browser was given (CSRF)")

    exchanged = await _exchange(settings, code=code, verifier=verifier, request=request)
    if exchanged is None:
        return refuse("the identity provider did not return a token")

    answer = RedirectResponse(destination, status_code=303)
    for name in (STATE_COOKIE, VERIFIER_COOKIE, RETURN_COOKIE):
        answer.delete_cookie(name, path="/auth")
    answer.set_cookie(
        SESSION_COOKIE,
        exchanged,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    telemetry.log("auth.callback.accepted")
    return answer


@router.get("/auth/logout", summary="Discard the session", operation_id="logout")
@unauthenticated(
    "Signing out must work for a session the API can no longer verify -- an "
    "expired one, or one from a key that has rotated. Requiring a principal would "
    "mean the only sessions that could be discarded are the ones still valid."
)
async def logout() -> RedirectResponse:
    """Clear the session cookie."""
    answer = RedirectResponse("/signin", status_code=303)
    answer.delete_cookie(SESSION_COOKIE, path="/")
    return answer


async def _exchange(settings: Any, *, code: str, verifier: str, request: Request) -> str | None:
    """Redeem the authorisation code. Returns the token, or `None`.

    Returns rather than raises for the same reason the verifier does: every
    failure produces one outcome, so a caller probing the callback learns
    nothing from which one they hit.
    """
    import httpx

    _authorise, token_endpoint = endpoints(settings)
    target = str(
        request.url_for("callback")
        if settings.oidc_redirect_uri == ""
        else settings.oidc_redirect_uri
    )
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": target,
        "client_id": settings.oidc_client_id,
        "code_verifier": verifier,
    }
    # A public client with PKCE has no secret. Where one is configured -- a
    # confidential registration -- it is sent, and it is the only place in this
    # module that handles one.
    if settings.oidc_client_secret:
        form["client_secret"] = settings.oidc_client_secret

    # Decided before it is made (RF-09, threat T11). This is the one outbound
    # call in the system that carries a credential -- an authorisation code, and
    # a client secret where one is registered -- and it was the one going around
    # the allow list. `BrokeredClient` is not used here because the exchange is
    # form-encoded and asynchronous and that wrapper is neither; what matters is
    # that the destination is decided by the same broker against the same list,
    # and a refusal raises before a socket is opened.
    try:
        egress.EgressBroker().request(
            egress.Call(
                url=token_endpoint,
                purpose=egress.FEDERATION_PURPOSE,
                run_id=None,
                approving_policy=egress.FEDERATION_POLICY,
                requested_at=datetime.now(UTC),
            )
        )
    except egress.UndeclaredDestinationError as refused:
        telemetry.log("auth.exchange.undeclared", level="error", reason=str(refused))
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            answer = await client.post(token_endpoint, data=form)
    # Broad: an HTTP client raises several unrelated types for the same
    # operational fact, and every one of them means the same thing here.
    except Exception as error:
        telemetry.log("auth.exchange.unreachable", level="warning", reason=type(error).__name__)
        return None

    if answer.status_code != _OK:
        telemetry.log("auth.exchange.refused", level="warning", status=answer.status_code)
        return None

    try:
        payload = answer.json()
    except Exception:
        return None

    # The access token, because that is what the API's audience is registered
    # for. An id_token is about the user and is addressed to the client; using
    # it as a bearer credential is a common and wrong shortcut.
    found = payload.get("access_token")
    return str(found) if isinstance(found, str) and found else None


__all__ = [
    "FLOW_SECONDS",
    "RETURN_COOKIE",
    "SESSION_COOKIE",
    "SESSION_SECONDS",
    "STATE_COOKIE",
    "VERIFIER_COOKIE",
    "challenge_for",
    "endpoints",
    "router",
    "safe_return",
]
