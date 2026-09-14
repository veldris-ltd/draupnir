"""Both mutually authenticated hops, end to end. SAD 9.5, RF-37.

The contract stage starts each end alone. This starts them together:

- the console proxy, in its base image with `docker/nginx.conf`, passing a
  request to the API served by `draupnir.api.serve` -- which answers only
  because the proxy presented its certificate;
- GULLINBURSTI's client, built by `transport.client_context` as the worker
  builds it, presenting its site certificate to a stand-in MEGINGJORD that
  requires one.
"""

from __future__ import annotations

import ssl
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from draupnir.api import serve
from draupnir.core.infrastructure.config import Settings
from draupnir.svalinn import transport
from tests.tls import Estate, issue_estate
from tests.transport_harness import NGINX_CONFIGURATION, console_html, running_proxy, serving

#: How `docker/nginx.conf` names the API in each proxied location.
UPSTREAM = "proxy_pass $draupnir_api;"


@pytest.fixture(scope="module")
def estate(tmp_path_factory: pytest.TempPathFactory) -> Estate:
    return issue_estate(tmp_path_factory.mktemp("tls"))


@pytest.fixture(scope="module")
def html(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return console_html(tmp_path_factory.mktemp("html"))


def _application(name: str) -> FastAPI:
    app = FastAPI()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "servedBy": name}

    return app


@pytest.fixture(scope="module")
def api(estate: Estate) -> Iterator[int]:
    """The API over mTLS, on every interface so a container can reach it."""
    settings = Settings().model_copy(
        update={
            "api_tls_certificate": str(estate.api.certificate),
            "api_tls_private_key": str(estate.api.private_key),
            "internal_ca": str(estate.ca),
            "api_host": "0.0.0.0",  # noqa: S104 -- the proxy container reaches the host
            "api_port": 0,
        }
    )
    with serving(serve.configure(settings, app=_application("the api"), environ={})) as port:
        yield port


def _configuration(tmp_path: Path, api_port: int, *, drop: tuple[str, ...] = ()) -> Path:
    """`docker/nginx.conf`, pointed at the test's API and otherwise unchanged.

    The upstream is written into each `proxy_pass` rather than into the `map`
    the file uses. A `proxy_pass` naming a variable is resolved at request
    time, and nginx resolves a host name then only through a `resolver`, which
    the deployed file does not need: its upstream is an address. The host the
    container reaches the test through is a name, so it is given literally,
    which nginx resolves once at start.
    """
    text = NGINX_CONFIGURATION.read_text(encoding="utf-8")
    assert UPSTREAM in text, "docker/nginx.conf no longer names the upstream this replaces"
    text = text.replace(UPSTREAM, f"proxy_pass https://host.docker.internal:{api_port};")
    text = "\n".join(line for line in text.splitlines() if not line.strip().startswith(drop))
    path = tmp_path / "nginx.conf"
    path.write_text(text + "\n", encoding="utf-8")
    path.chmod(0o644)
    return path


def _verifying(estate: Estate) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_verify_locations(cafile=str(estate.ca))
    return context


def test_the_proxy_reaches_the_api_over_mtls(
    estate: Estate, html: Path, api: int, tmp_path: Path
) -> None:
    configuration = _configuration(tmp_path, api)
    assert transport.proxy_declaration(configuration).upstream_mtls

    with running_proxy(
        estate,
        html,
        configuration=configuration,
        extra_hosts={"host.docker.internal": "host-gateway"},
    ) as (_host, port, logs):
        response = httpx.get(
            f"https://localhost:{port}/healthz", verify=_verifying(estate), timeout=15
        )
        assert response.status_code == 200, f"{response.text}\n{logs()}"

    assert response.json()["servedBy"] == "the api"


def test_a_proxy_that_presents_no_certificate_is_refused_by_the_api(
    estate: Estate, html: Path, api: int, tmp_path: Path
) -> None:
    """The same proxy, without its client certificate: the API ends the connection.

    Asserted on the proxy's log as well as on the 502, because a 502 alone is
    also what an unreachable API produces, and that would pass this test
    without the API having refused anything. Under TLS 1.3 the server's
    certificate-required alert arrives after the client's side of the
    handshake has finished, so nginx records it as the upstream closing the
    connection before a response -- naming the upstream it reached. The test
    above, against the same API with the certificate presented, is what makes
    that close a refusal rather than a fault.
    """
    configuration = _configuration(
        tmp_path, api, drop=("proxy_ssl_certificate ", "proxy_ssl_certificate_key ")
    )
    assert not transport.proxy_declaration(configuration).upstream_mtls

    with running_proxy(
        estate,
        html,
        configuration=configuration,
        extra_hosts={"host.docker.internal": "host-gateway"},
    ) as (_host, port, logs):
        response = httpx.get(
            f"https://localhost:{port}/healthz", verify=_verifying(estate), timeout=15
        )
        log = logs()

    assert response.status_code == 502
    assert f":{api}/healthz" in log, f"the proxy never reached the API:\n{log}"
    for unreachable in ("connect() failed", "could not be resolved", "timed out"):
        assert unreachable not in log, f"the 502 was the API being unreachable:\n{log}"
    assert "upstream prematurely closed connection" in log or "SSL" in log, (
        f"the 502 was not the API ending the connection:\n{log}"
    )


@pytest.fixture(scope="module")
def megingjord(estate: Estate) -> Iterator[int]:
    """A stand-in registry that requires a client certificate from the estate's CA."""
    context = transport.server_context(
        certificate=str(estate.megingjord.certificate),
        private_key=str(estate.megingjord.private_key),
        client_ca=str(estate.ca),
    )
    config = uvicorn.Config(
        _application("megingjord"),
        host="127.0.0.1",
        port=0,
        ssl_context_factory=lambda _config, _default: context,
    )
    with serving(config) as port:
        yield port


def test_gullinbursti_presents_its_site_certificate_to_megingjord(
    estate: Estate, megingjord: int
) -> None:
    context = transport.client_context(
        certificate=str(estate.federation.certificate),
        private_key=str(estate.federation.private_key),
        server_ca=str(estate.ca),
    )

    with httpx.Client(verify=context, timeout=15) as client:
        response = client.get(f"https://127.0.0.1:{megingjord}/healthz")

    assert response.status_code == 200
    assert response.json()["servedBy"] == "megingjord"


def test_megingjord_refuses_a_forge_that_presents_no_certificate(
    estate: Estate, megingjord: int
) -> None:
    with (
        httpx.Client(verify=_verifying(estate), timeout=15) as client,
        pytest.raises(httpx.TransportError),
    ):
        client.get(f"https://127.0.0.1:{megingjord}/healthz")
