"""Transport security, started for real. SAD 9.5, RF-37.

Each end started alone, rather than its configuration read:

- **The console proxy.** `docker/nginx.conf` as the web image ships it, in the
  nginx image the web image is built on, with a throwaway CA's certificates
  mounted where the configuration reads them.
- **The API.** Served by `draupnir.api.serve`, which is the API image's
  command, with the same CA.

The integration stage starts the two together (`tests/integration/
test_transport.py`). The proxy tests need Docker, as that stage's PostgreSQL
does. They do not skip without it: a transport gate that passed on a runner
with no container runtime would pass by not running.
"""

from __future__ import annotations

import ssl
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from draupnir.api import serve
from draupnir.core.infrastructure.config import Settings
from draupnir.svalinn import transport
from tests.tls import Estate, Pair, issue_estate
from tests.transport_harness import (
    STARTUP_SECONDS,
    console_html,
    handshake,
    proxy_container,
    running_proxy,
    serving,
)

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def estate(tmp_path_factory: pytest.TempPathFactory) -> Estate:
    return issue_estate(tmp_path_factory.mktemp("tls"))


@pytest.fixture(scope="module")
def html(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return console_html(tmp_path_factory.mktemp("html"))


@pytest.fixture(scope="module")
def proxy(estate: Estate, html: Path) -> Iterator[tuple[str, int]]:
    with running_proxy(estate, html) as (host, port, _logs):
        yield host, port


# ---------------------------------------------------------------------------
# The console proxy
# ---------------------------------------------------------------------------


def test_the_proxy_terminates_tls_1_3(proxy: tuple[str, int], estate: Estate) -> None:
    assert handshake(*proxy, estate.ca, ssl.TLSVersion.TLSv1_3) == "TLSv1.3"


def test_the_proxy_refuses_a_tls_1_2_handshake(proxy: tuple[str, int], estate: Estate) -> None:
    """SAD 9.5 is TLS 1.3 *only*. nginx's default would have completed this."""
    with pytest.raises(ssl.SSLError):
        handshake(*proxy, estate.ca, ssl.TLSVersion.TLSv1_2)


def test_the_console_is_served_over_it_with_hsts(proxy: tuple[str, int], estate: Estate) -> None:
    _host, port = proxy
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_verify_locations(cafile=str(estate.ca))

    response = httpx.get(f"https://localhost:{port}/", verify=context, timeout=10)

    assert response.status_code == 200
    assert "Strict-Transport-Security" in response.headers


def test_the_proxy_does_not_start_without_its_certificates(estate: Estate, html: Path) -> None:
    """The refusal is nginx's own: there is no plain listener to fall back to."""
    container = proxy_container(estate, html, with_tls=False)
    container.start()
    try:
        wrapped = container.get_wrapped_container()
        deadline = time.monotonic() + STARTUP_SECONDS
        while wrapped.status != "exited" and time.monotonic() < deadline:
            time.sleep(0.5)
            wrapped.reload()
        _stdout, stderr = container.get_logs()
        assert wrapped.status == "exited", "nginx started with no certificate to terminate with"
        assert b"/etc/draupnir/tls/" in stderr
    finally:
        container.stop()


def test_the_repository_configuration_declares_what_the_proxy_does() -> None:
    """The inventory reads the file these tests start, so the two cannot differ."""
    declared = transport.proxy_declaration()

    assert declared.terminates_tls13_only, declared.reasons
    assert declared.upstream_mtls, declared.reasons


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


def _application() -> FastAPI:
    app = FastAPI()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _settings(estate: Estate, **changes: Any) -> Settings:
    return Settings().model_copy(
        update={
            "api_tls_certificate": str(estate.api.certificate),
            "api_tls_private_key": str(estate.api.private_key),
            "internal_ca": str(estate.ca),
            "api_host": "127.0.0.1",
            "api_port": 0,
            **changes,
        }
    )


def _client(estate: Estate, presenting: Pair | None, version: ssl.TLSVersion) -> httpx.Client:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = version
    context.maximum_version = version
    context.load_verify_locations(cafile=str(estate.ca))
    if presenting is not None:
        context.load_cert_chain(str(presenting.certificate), str(presenting.private_key))
    return httpx.Client(verify=context, timeout=10)


@pytest.fixture(scope="module")
def api(estate: Estate) -> Iterator[int]:
    with serving(serve.configure(_settings(estate), app=_application(), environ={})) as port:
        yield port


def test_the_api_serves_a_client_presenting_the_estate_certificate(
    api: int, estate: Estate
) -> None:
    with _client(estate, estate.proxy_client, ssl.TLSVersion.TLSv1_3) as client:
        response = client.get(f"https://127.0.0.1:{api}/healthz")

    assert response.status_code == 200


def test_the_api_refuses_a_connection_that_presents_no_client_certificate(
    api: int, estate: Estate
) -> None:
    """SAD 9.5's mTLS, from the API's side. The request never reaches a route."""
    with (
        _client(estate, None, ssl.TLSVersion.TLSv1_3) as client,
        pytest.raises(httpx.TransportError),
    ):
        client.get(f"https://127.0.0.1:{api}/healthz")


def test_the_api_refuses_a_certificate_from_another_ca(api: int, estate: Estate) -> None:
    with (
        _client(estate, estate.stranger, ssl.TLSVersion.TLSv1_3) as client,
        pytest.raises(httpx.TransportError),
    ):
        client.get(f"https://127.0.0.1:{api}/healthz")


def test_the_api_refuses_tls_1_2(api: int, estate: Estate) -> None:
    with (
        _client(estate, estate.proxy_client, ssl.TLSVersion.TLSv1_2) as client,
        pytest.raises(httpx.TransportError),
    ):
        client.get(f"https://127.0.0.1:{api}/healthz")


def test_without_certificates_the_api_refuses_to_start(estate: Estate) -> None:
    unconfigured = _settings(estate, api_tls_certificate="", api_tls_private_key="", internal_ca="")

    with pytest.raises(serve.ServeError, match="DRAUPNIR_API_TLS_CERTIFICATE"):
        serve.configure(unconfigured, app=_application(), environ={})


def test_a_development_machine_may_serve_plain_http(estate: Estate) -> None:
    unconfigured = _settings(estate, api_tls_certificate="", api_tls_private_key="", internal_ca="")

    config = serve.configure(unconfigured, app=_application(), environ={"DRAUPNIR_DEV": "1"})

    assert not config.is_ssl


def test_half_a_configuration_is_refused_even_on_a_development_machine(estate: Estate) -> None:
    partial = _settings(estate, api_tls_private_key="")

    with pytest.raises(serve.ServeError, match="half configured"):
        serve.configure(partial, app=_application(), environ={"DRAUPNIR_DEV": "1"})


def test_an_unreadable_certificate_is_refused_by_name(estate: Estate, tmp_path: Path) -> None:
    absent = str(tmp_path / "absent.pem")

    with pytest.raises(serve.ServeError, match=r"absent\.pem"):
        serve.configure(
            _settings(estate, api_tls_certificate=absent), app=_application(), environ={}
        )


# ---------------------------------------------------------------------------
# The check install.sh runs in the API image
# ---------------------------------------------------------------------------


def test_the_installer_s_material_check_accepts_what_the_ca_issued(estate: Estate) -> None:
    """`python -m draupnir.svalinn.transport`, as `install.sh --check` runs it."""
    for role, pair in (("server", estate.api), ("client", estate.federation)):
        arguments = [role, str(pair.certificate), str(pair.private_key), str(estate.ca)]
        assert transport.main(arguments) == 0


def test_the_installer_s_material_check_refuses_a_certificate_another_ca_issued(
    estate: Estate,
) -> None:
    """A context loads any certificate it is given, so issuance is checked apart."""
    with pytest.raises(transport.TransportError, match="not issued by the internal CA"):
        transport.verify_material(
            role="client",
            certificate=str(estate.stranger.certificate),
            private_key=str(estate.stranger.private_key),
            ca=str(estate.ca),
        )


def test_the_installer_s_material_check_refuses_a_key_that_is_not_the_certificate_s(
    estate: Estate,
) -> None:
    with pytest.raises(transport.TransportError, match="certificate and key"):
        transport.verify_material(
            role="server",
            certificate=str(estate.api.certificate),
            private_key=str(estate.proxy.private_key),
            ca=str(estate.ca),
        )
