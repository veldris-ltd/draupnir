"""Certificates for the transport tests. RF-37.

An estate's worth of TLS material, issued by a throwaway CA standing in for the
internal signing CA of Decision S9, and one certificate from a CA the estate
does not trust. The file names are the ones `docker/nginx.conf` reads under
`/etc/draupnir/tls`, so the directory can be mounted into the proxy as it is.

Elliptic curve keys, because generating them is instant and the tests issue a
fresh estate per session. Valid for a day and backdated five minutes, so a
runner whose clock is slightly behind the machine that issued them still
accepts them.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

#: The names a local server answers to.
LOCAL = ("localhost", "127.0.0.1")

#: The name the proxy verifies the API's certificate against (`proxy_ssl_name`).
API_NAME = "draupnir-api"


@dataclass(frozen=True, slots=True)
class Pair:
    """A certificate and its private key, both PEM."""

    certificate: Path
    private_key: Path


@dataclass(frozen=True, slots=True)
class Estate:
    """The transport material of one site."""

    #: The CA every certificate below except `stranger` chains to.
    ca: Path
    #: The console proxy's server certificate.
    proxy: Pair
    #: What the proxy presents to the API.
    proxy_client: Pair
    #: The API's server certificate.
    api: Pair
    #: GULLINBURSTI's site certificate, presented to MEGINGJORD.
    federation: Pair
    #: A stand-in registry's server certificate.
    megingjord: Pair
    #: A client certificate from a CA the estate does not trust.
    stranger: Pair


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _window(builder: x509.CertificateBuilder) -> x509.CertificateBuilder:
    now = datetime.now(UTC)
    return builder.not_valid_before(now - timedelta(minutes=5)).not_valid_after(
        now + timedelta(days=1)
    )


def _authority(common_name: str) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = (
        _window(x509.CertificateBuilder())
        .subject_name(_name(common_name))
        .issuer_name(_name(common_name))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return certificate, key


def _issue(
    authority: tuple[x509.Certificate, ec.EllipticCurvePrivateKey],
    common_name: str,
    *,
    server: bool,
    names: tuple[str, ...] = (),
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    issuer, issuer_key = authority
    key = ec.generate_private_key(ec.SECP256R1())
    usage = ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH
    builder = (
        _window(x509.CertificateBuilder())
        .subject_name(_name(common_name))
        .issuer_name(issuer.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
    )
    if names:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([_general_name(name) for name in names]), critical=False
        )
    return builder.sign(issuer_key, hashes.SHA256()), key


def _general_name(name: str) -> x509.GeneralName:
    try:
        return x509.IPAddress(ipaddress.ip_address(name))
    except ValueError:
        return x509.DNSName(name)


def _write(
    directory: Path, stem: str, issued: tuple[x509.Certificate, ec.EllipticCurvePrivateKey]
) -> Pair:
    certificate, key = issued
    pair = Pair(directory / f"{stem}.pem", directory / f"{stem}.key")
    pair.certificate.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    pair.private_key.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    _readable(pair.certificate, pair.private_key)
    return pair


def _readable(*paths: Path) -> None:
    # World-readable, which a deployed key must never be. The proxy container
    # runs as 65532 and the files belong to whoever runs the tests, so a 0600
    # key is one nginx cannot open -- and these keys protect nothing.
    for path in paths:
        path.chmod(0o644)


def issue_estate(directory: Path) -> Estate:
    """Issue one site's transport material into `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    authority = _authority("Veldris Internal Signing CA (test)")
    ca = directory / "internal-ca.pem"
    ca.write_bytes(authority[0].public_bytes(serialization.Encoding.PEM))
    _readable(ca)

    stranger_authority = _authority("A CA this estate does not trust")
    return Estate(
        ca=ca,
        proxy=_write(directory, "server", _issue(authority, "alviss", server=True, names=LOCAL)),
        proxy_client=_write(
            directory, "proxy-client", _issue(authority, "draupnir-web", server=False)
        ),
        api=_write(
            directory, "api", _issue(authority, API_NAME, server=True, names=(API_NAME, *LOCAL))
        ),
        federation=_write(
            directory, "federation", _issue(authority, "gullinbursti.sindri", server=False)
        ),
        megingjord=_write(
            directory, "megingjord", _issue(authority, "megingjord", server=True, names=LOCAL)
        ),
        stranger=_write(
            directory, "stranger", _issue(stranger_authority, "stranger", server=False)
        ),
    )
