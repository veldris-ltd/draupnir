"""A development principal, and nothing more.

The API resolves its caller from `request.state.claims`, which the OIDC
middleware sets after verifying a token against MEGINGJORD's JWKS. On a
developer's machine there is no MEGINGJORD, so without this every request to a
running stack is 401 and the console can read nothing -- which makes the four
journeys of AC-U1 unrunnable outside a full identity deployment.

This fills that gap in the narrowest way available, and the shape is copied
deliberately from the plug-in loader's `DRAUPNIR_DEV` escape hatch, because the
two are the same kind of concession and should look the same:

  * **Off unless `DRAUPNIR_DEV=1`.** Not "off in production", which depends on
    somebody having set an environment correctly. Off by default, everywhere.
  * **Loud.** It logs a warning naming itself on every startup that installs
    it, for the same reason the loader does: a control that is quietly absent
    is a control nobody notices is absent.
  * **It verifies nothing and claims nothing.** It does not accept a token, it
    does not read an `Authorization` header, and it cannot be persuaded to
    trust one. It sets a fixed claim set from the environment. There is
    therefore no code path here that could be reached by a request, which is
    the property that makes it safe to ship: an attacker cannot enable it by
    sending anything.

The roles it grants are configurable because the journeys need different ones
-- a curator registers a source, an approver signs -- and a single all-powerful
development principal would let a screen pass its journey while being wrong
about authorisation.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any, Final

from fastapi import FastAPI, Request, Response

from draupnir.api import telemetry

#: The claim set a development principal presents. `amr` carries `hwk` because
#: an approver must have authenticated with a hardware factor (SAD 9.4), and a
#: development principal that could not approve would make J3 untestable.
DEFAULT_ROLES: Final = "viewer,curator,operator,approver,admin"


def enabled(environ: dict[str, str] | None = None) -> bool:
    """Whether the development principal is switched on."""
    source = environ if environ is not None else dict(os.environ)
    return source.get("DRAUPNIR_DEV", "").strip() in {"1", "true", "TRUE", "yes"}


def claims(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """The claim set to present, from the environment."""
    source = environ if environ is not None else dict(os.environ)
    roles = [
        role.strip()
        for role in source.get("DRAUPNIR_DEV_ROLES", DEFAULT_ROLES).split(",")
        if role.strip()
    ]
    return {
        "sub": source.get("DRAUPNIR_DEV_SUBJECT", "dev@veldris.internal"),
        "iss": "https://megingjord.veldris.internal",
        "roles": roles,
        "amr": ["pwd", "hwk"],
        "site_id": source.get("DRAUPNIR_SITE_ID", source.get("DRAUPNIR_DEV_SITE", "sindri")),
    }


def install(app: FastAPI) -> bool:
    """Attach the development principal, if it is switched on.

    Returns whether it was attached, so a caller can say so rather than
    guessing.
    """
    if not enabled():
        return False

    presented = claims()
    telemetry.log(
        "auth.development-principal",
        level="warning",
        subject=presented["sub"],
        roles=",".join(presented["roles"]),
        siteId=presented["site_id"],
        detail=(
            "every request is treated as this principal because DRAUPNIR_DEV=1. "
            "No token is verified. Authentication is a control, not a convention "
            "(SAD 9.3): this must never be set where real data is held."
        ),
    )

    @app.middleware("http")
    async def development_principal(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.claims = presented
        return await call_next(request)

    return True
