"""Starting the transport's ends for a test. RF-37.

Shared by the contract stage, which starts each end alone, and the integration
stage, which starts them together. The proxy runs in the nginx image
`docker/web.Dockerfile` builds on, read from that file, with the repository's
`docker/nginx.conf`; the API runs under `draupnir.api.serve` in a thread.
"""

from __future__ import annotations

import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from tests.tls import Estate

ROOT = Path(__file__).resolve().parents[1]

#: The console proxy's configuration, as the web image ships it.
NGINX_CONFIGURATION = ROOT / "docker" / "nginx.conf"

#: How long a container or a server gets to start answering.
STARTUP_SECONDS = 60


def base_image() -> str:
    """The nginx image `docker/web.Dockerfile` builds on, read from the file.

    Read rather than restated, so the tests start the image the console ships
    on, and a change of base in the Dockerfile is a change here too.
    """
    dockerfile = (ROOT / "docker" / "web.Dockerfile").read_text(encoding="utf-8")
    image = re.search(r"^ARG NGINX_IMAGE=(\S+)$", dockerfile, flags=re.MULTILINE)
    tag = re.search(r"^FROM \$\{NGINX_IMAGE\}:(\S+)", dockerfile, flags=re.MULTILINE)
    assert image and tag, "web.Dockerfile no longer names its nginx base image"
    return f"{image.group(1)}:{tag.group(1)}"


def handshake(host: str, port: int, ca: Path, version: ssl.TLSVersion) -> str:
    """Complete a handshake at exactly one protocol version, and name it."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = version
    context.maximum_version = version
    context.load_verify_locations(cafile=str(ca))
    with (
        socket.create_connection((host, port), timeout=5) as raw,
        context.wrap_socket(raw, server_hostname="localhost") as tls,
    ):
        return str(tls.version())


def console_html(directory: Path) -> Path:
    """A one-page console, readable by the proxy's unprivileged user."""
    directory.mkdir(parents=True, exist_ok=True)
    page = directory / "index.html"
    page.write_text("<!doctype html><title>console</title>\n", encoding="utf-8")
    directory.chmod(0o755)
    page.chmod(0o644)
    return directory


def proxy_container(
    estate: Estate,
    html: Path,
    *,
    configuration: Path = NGINX_CONFIGURATION,
    with_tls: bool = True,
    **run: Any,
) -> Any:
    """The console proxy, configured and not yet started."""
    from testcontainers.core.container import DockerContainer

    container = (
        DockerContainer(base_image())
        .with_kwargs(entrypoint=["/usr/sbin/nginx"], **run)
        .with_command(["-g", "daemon off;"])
        .with_volume_mapping(str(configuration), "/etc/nginx/nginx.conf", "ro")
        .with_volume_mapping(str(html), "/usr/share/nginx/html", "ro")
        .with_exposed_ports(8443)
    )
    if with_tls:
        estate.ca.parent.chmod(0o755)
        container = container.with_volume_mapping(str(estate.ca.parent), "/etc/draupnir/tls", "ro")
    return container


@contextmanager
def running_proxy(
    estate: Estate, html: Path, **options: Any
) -> Iterator[tuple[str, int, Callable[[], str]]]:
    """Start the proxy and yield where it answers TLS 1.3, and its log.

    The log is yielded so a failing assertion can carry nginx's own account of
    what it refused, which is the only place a 502's cause is written.
    """
    container = proxy_container(estate, html, **options)
    container.start()

    def logs() -> str:
        stdout, stderr = container.get_logs()
        return f"{stdout.decode(errors='replace')}{stderr.decode(errors='replace')}"

    try:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(8443))
        deadline = time.monotonic() + STARTUP_SECONDS
        while True:
            try:
                handshake(host, port, estate.ca, ssl.TLSVersion.TLSv1_3)
                break
            except (OSError, ssl.SSLError) as error:
                if time.monotonic() > deadline:
                    pytest.fail(
                        f"the proxy did not answer TLS within {STARTUP_SECONDS}s ({error}):\n"
                        f"{logs()}"
                    )
                time.sleep(0.5)
        yield host, port, logs
    finally:
        container.stop()


@contextmanager
def serving(config: uvicorn.Config) -> Iterator[int]:
    """Run a server in a thread and yield the port it bound."""
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTUP_SECONDS
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            pytest.fail("the server did not start")
        time.sleep(0.05)
    try:
        yield int(server.servers[0].sockets[0].getsockname()[1])
    finally:
        server.should_exit = True
        thread.join(timeout=10)
