"""Serve the API over mutual TLS. SAD 9.5, RF-37.

The image ran `uvicorn draupnir.api.app:app` in plain HTTP, and the proxy in
front of it passed every request -- bearer tokens and session cookies included
-- across that hop in the clear. SAD 9.5 asks for mTLS between control plane
components, so the API serves TLS 1.3 only and refuses a connection that
presents no certificate from the internal CA. The console proxy presents one;
nothing else reaches the API by being on the same host.

**No certificate is a refusal, not a fallback.** An API that served plain HTTP
whenever its certificates were missing would be one mistyped path away from the
state RF-37 found, and it would start without complaint. `DRAUPNIR_DEV=1` is
the escape, the same one the plug-in loader and authentication use, for a
development machine with no CA. A *partial* configuration is refused even
there: a certificate without its key is a mistake, never a choice.

Run as `python -m draupnir.api.serve`, which is the API image's command. The
development stack and the journeys run `uvicorn` against the application
directly, as they always have.
"""

from __future__ import annotations

import ssl
import sys
from collections.abc import Callable, Mapping
from typing import Any, Final

import structlog
import uvicorn

from draupnir.core import plugins
from draupnir.core.infrastructure.config import Settings, get_settings
from draupnir.svalinn import transport

logger = structlog.get_logger(__name__)

#: The application, by import string, so uvicorn loads it the way it always has.
APP: Final = "draupnir.api.app:app"

#: The settings a TLS API needs: all of them, or none.
MATERIAL: Final = ("api_tls_certificate", "api_tls_private_key", "internal_ca")


class ServeError(Exception):
    """Raised when the API cannot be served as configured."""


def _variable(name: str) -> str:
    return f"DRAUPNIR_{name.upper()}"


def configure(
    settings: Settings | None = None,
    *,
    app: Any = APP,
    environ: Mapping[str, str] | None = None,
) -> uvicorn.Config:
    """The server configuration, or a refusal naming what is missing.

    The context is built here rather than when uvicorn starts, so an unreadable
    certificate is reported before the port is bound and in words that name
    the file -- not as a traceback from inside the event loop.
    """
    current = settings if settings is not None else get_settings()
    missing = [_variable(name) for name in MATERIAL if not getattr(current, name)]

    if not missing:
        try:
            context = transport.server_context(
                certificate=current.api_tls_certificate,
                private_key=current.api_tls_private_key,
                client_ca=current.internal_ca,
            )
        except transport.TransportError as error:
            raise ServeError(str(error)) from error

        def factory(
            _config: uvicorn.Config, _default: Callable[[], ssl.SSLContext]
        ) -> ssl.SSLContext:
            return context

        return uvicorn.Config(
            app, host=current.api_host, port=current.api_port, ssl_context_factory=factory
        )

    if len(missing) < len(MATERIAL):
        msg = (
            f"the API's TLS material is half configured: {', '.join(missing)} is not set. "
            "A certificate, its key and the internal CA are needed together, and a partial "
            "configuration is refused even with DRAUPNIR_DEV set, because it is a mistake "
            "rather than a choice."
        )
        raise ServeError(msg)

    if plugins.developer_mode(environ):
        logger.warning(
            "api.serve.plaintext",
            reason="DRAUPNIR_DEV is set and no TLS material is configured",
        )
        return uvicorn.Config(app, host=current.api_host, port=current.api_port)

    msg = (
        f"no TLS material is configured for the API: {', '.join(missing)} is not set. "
        "SAD 9.5 requires mTLS between control plane components, so the API does not "
        "serve plain HTTP. Issue the API a certificate from the internal CA "
        "(docs/runbook.md, Certificates), or set DRAUPNIR_DEV=1 on a machine with no real "
        "data."
    )
    raise ServeError(msg)


def main() -> int:
    """Serve the API, or say why not and exit non-zero."""
    try:
        config = configure()
    except ServeError as error:
        sys.stderr.write(f"draupnir-api: {error}\n")
        return 2
    uvicorn.Server(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
