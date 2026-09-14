"""Transport security: TLS 1.3 only, and both ends authenticated. SAD 9.5, RF-37.

SAD 9.5 says "TLS 1.3 only", with mTLS between control plane components and
between GULLINBURSTI and MEGINGJORD. Until RF-37 nothing terminated TLS at all:
the console proxy and the API both listened in plain HTTP, `install.sh --check`
insisted on a certificate nobody served, and the cryptographic inventory's TLS
row had to be corrected twice for describing controls that did not exist.

This module is the one place the policy is written down, in two forms:

- **Contexts** for the Python ends. `server_context` is what the API serves
  with: TLS 1.3 and nothing else, and a client certificate from the internal CA
  or no connection. `client_context` is what GULLINBURSTI calls MEGINGJORD
  with: TLS 1.3, the server verified against the same CA, and the site's own
  certificate presented.
- **A reading of the proxy's configuration.** nginx is not Python, so its half
  of the policy lives in `docker/nginx.conf`. `declared_by` reads what that file
  declares, and the inventory's TLS row is derived from it -- so the row says
  "in use" because the configuration terminates TLS 1.3, not because a
  certificate path happens to be set (RF-30's correction, and this finding's
  prompt).

The minimum and maximum version are both pinned. Pinning only the minimum
would admit whatever a later OpenSSL adds above 1.3 before anyone decided to,
and "TLS 1.3 only" is a statement about the ceiling as well as the floor.
"""

from __future__ import annotations

import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: The one protocol version SAD 9.5 admits.
TLS13: Final = ssl.TLSVersion.TLSv1_3

#: The console proxy's configuration, as the repository holds it. The web image
#: copies this file to `/etc/nginx/nginx.conf`, so it is the configuration a
#: deployment runs rather than a description of one.
PROXY_CONFIGURATION: Final = Path(__file__).resolve().parents[2] / "docker" / "nginx.conf"


class TransportError(Exception):
    """Raised when the material for a TLS endpoint cannot be loaded."""


def _pinned(context: ssl.SSLContext) -> ssl.SSLContext:
    """Admit TLS 1.3 and nothing either side of it."""
    context.minimum_version = TLS13
    context.maximum_version = TLS13
    return context


def _load(action: Callable[[], object], what: str, *paths: str) -> None:
    """Load TLS material, or raise naming the files and what they were for."""
    try:
        action()
    except (OSError, ssl.SSLError) as error:
        msg = f"{what} could not be loaded from {', '.join(paths)}: {error}"
        raise TransportError(msg) from error


def server_context(*, certificate: str, private_key: str, client_ca: str) -> ssl.SSLContext:
    """A server that speaks TLS 1.3 only and refuses a client with no certificate.

    `CERT_REQUIRED` rather than `CERT_OPTIONAL`: an optional client certificate
    is a server that authenticates the clients that choose to be authenticated,
    which is none of the ones that matter.
    """
    context = _pinned(ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER))
    _load(
        lambda: context.load_cert_chain(certificate, private_key),
        "the server certificate and key",
        certificate,
        private_key,
    )
    _load(
        lambda: context.load_verify_locations(cafile=client_ca),
        "the CA client certificates are verified against",
        client_ca,
    )
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def client_context(*, certificate: str, private_key: str, server_ca: str) -> ssl.SSLContext:
    """A client that speaks TLS 1.3 only, verifies the server, and presents itself.

    `PROTOCOL_TLS_CLIENT` checks the host name and requires a server
    certificate by default. Neither is relaxed: a client that presents its own
    certificate to a server it has not verified has authenticated itself to
    whoever answered.
    """
    context = _pinned(ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    _load(
        lambda: context.load_verify_locations(cafile=server_ca),
        "the CA the server is verified against",
        server_ca,
    )
    _load(
        lambda: context.load_cert_chain(certificate, private_key),
        "the client certificate and key",
        certificate,
        private_key,
    )
    return context


# ---------------------------------------------------------------------------
# What the proxy configuration declares
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProxyDeclaration:
    """What a proxy configuration says about its transport."""

    #: Every listener is TLS, and TLS 1.3 is the only protocol named.
    terminates_tls13_only: bool
    #: Every upstream is HTTPS, verified against a CA, over TLS 1.3, with a
    #: client certificate presented.
    upstream_mtls: bool
    #: Why either is false, in words an inventory can carry.
    reasons: tuple[str, ...] = ()


def _code(text: str) -> str:
    """The configuration without its comments."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _directives(code: str, name: str) -> list[str]:
    """The values of every occurrence of one directive."""
    pattern = rf"^\s*{re.escape(name)}\s+([^;]*);"
    return [value.strip() for value in re.findall(pattern, code, flags=re.MULTILINE)]


def _only_tls13(values: list[str]) -> bool:
    return bool(values) and all(value.split() == ["TLSv1.3"] for value in values)


def _resolved(code: str, target: str) -> str:
    """A `proxy_pass` target, with a `map` variable replaced by its default."""
    if not target.startswith("$"):
        return target
    block = re.search(rf"map\s+\S+\s+{re.escape(target)}\s*\{{(.*?)\}}", code, flags=re.DOTALL)
    default = re.search(r'default\s+"?([^";\s]+)"?\s*;', block.group(1)) if block else None
    return default.group(1) if default else target


def declared_by(text: str) -> ProxyDeclaration:
    """Read an nginx configuration's transport, refusing to infer anything.

    Every listener has to be TLS. A second listener in plain HTTP beside a TLS
    one is not "TLS 1.3 only", however it is meant to be used, and nginx's
    default `ssl_protocols` admits TLS 1.2, so a configuration that names no
    protocol does not terminate TLS 1.3 only either.
    """
    code = _code(text)
    reasons: list[str] = []

    listens = _directives(code, "listen")
    plain = [value for value in listens if "ssl" not in value.split()]
    if not listens:
        reasons.append("the proxy configuration declares no listener")
    if plain:
        reasons.append(f"the proxy listens without TLS on {', '.join(plain)}")
    protocols = _directives(code, "ssl_protocols")
    if not protocols:
        reasons.append("the proxy names no ssl_protocols, and nginx's default admits TLS 1.2")
    elif not _only_tls13(protocols):
        reasons.append(f"the proxy admits {'; '.join(protocols)}, not TLS 1.3 only")
    if not _directives(code, "ssl_certificate") or not _directives(code, "ssl_certificate_key"):
        reasons.append("the proxy names no certificate to terminate TLS with")
    terminates = not reasons

    upstream: list[str] = []
    targets = [_resolved(code, value) for value in _directives(code, "proxy_pass")]
    plaintext = sorted({target for target in targets if not target.startswith("https://")})
    if plaintext:
        upstream.append(f"the proxy passes to {', '.join(plaintext)} without TLS")
    for required in (
        "proxy_ssl_certificate",
        "proxy_ssl_certificate_key",
        "proxy_ssl_trusted_certificate",
    ):
        if not _directives(code, required):
            upstream.append(f"the proxy names no {required}")
    if _directives(code, "proxy_ssl_verify") != ["on"]:
        upstream.append("the proxy does not verify the API's certificate")
    if not _only_tls13(_directives(code, "proxy_ssl_protocols")):
        upstream.append("the proxy does not restrict its upstream to TLS 1.3")

    return ProxyDeclaration(
        terminates_tls13_only=terminates,
        upstream_mtls=bool(targets) and not upstream,
        reasons=(*reasons, *upstream),
    )


def proxy_declaration(path: Path | None = None) -> ProxyDeclaration:
    """What the console proxy's configuration declares. The inventory's source.

    A configuration that cannot be read declares nothing, and says why: an
    inventory generated where the file is absent must not report a transport
    it could not see.
    """
    source = path if path is not None else PROXY_CONFIGURATION
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as error:
        return ProxyDeclaration(
            terminates_tls13_only=False,
            upstream_mtls=False,
            reasons=(f"the proxy configuration at {source} could not be read: {error}",),
        )
    return declared_by(text)


# ---------------------------------------------------------------------------
# Checking material before a process loads it
# ---------------------------------------------------------------------------


def verify_material(*, role: str, certificate: str, private_key: str, ca: str) -> str:
    """Load a Python end's material as that end does, and check who issued it.

    What `install.sh --check` runs, in the API image and as the user the unit
    runs as (RF-37). A host-side `openssl` reads the files as the service
    account, which under rootless podman is not who reads them in deployment,
    so a key readable on the host and unreadable in the container would pass.

    Loading the context proves the certificate is the one for its key and the
    CA can be read. It does not prove the CA issued the certificate -- a server
    context loads whatever certificate it is given -- so that is checked
    directly. Directly issued: the internal CA of Decision S9 issues these
    certificates itself, and a chain through an intermediate is refused here
    rather than half checked.
    """
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature

    if role == "server":
        server_context(certificate=certificate, private_key=private_key, client_ca=ca)
    elif role == "client":
        client_context(certificate=certificate, private_key=private_key, server_ca=ca)
    else:
        msg = f"the role is 'server' or 'client', not {role!r}"
        raise TransportError(msg)

    try:
        issued = x509.load_pem_x509_certificate(Path(certificate).read_bytes())
        authority = x509.load_pem_x509_certificate(Path(ca).read_bytes())
        issued.verify_directly_issued_by(authority)
    except (OSError, ValueError, TypeError, InvalidSignature) as error:
        reason = str(error) or type(error).__name__
        msg = f"{certificate} was not issued by the internal CA at {ca}: {reason}"
        raise TransportError(msg) from error
    return f"{certificate} is the certificate for {private_key}, and the internal CA issued it"


def main(argv: list[str] | None = None) -> int:
    """`python -m draupnir.svalinn.transport server|client CERTIFICATE KEY CA`."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Load TLS material as a DRAUPNIR end does.")
    parser.add_argument("role", choices=("server", "client"))
    parser.add_argument("certificate")
    parser.add_argument("private_key")
    parser.add_argument("ca")
    arguments = parser.parse_args(argv)
    try:
        said = verify_material(
            role=arguments.role,
            certificate=arguments.certificate,
            private_key=arguments.private_key,
            ca=arguments.ca,
        )
    except TransportError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(f"{said}\n")
    return 0


__all__ = [
    "PROXY_CONFIGURATION",
    "TLS13",
    "ProxyDeclaration",
    "TransportError",
    "client_context",
    "declared_by",
    "main",
    "proxy_declaration",
    "server_context",
    "verify_material",
]


if __name__ == "__main__":
    raise SystemExit(main())
