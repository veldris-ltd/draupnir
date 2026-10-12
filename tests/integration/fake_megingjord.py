"""A MEGINGJORD for the integration stage: real TLS, real certificates, answers set per test.

The API under test is another process, so its link to MEGINGJORD is the real
one -- the bundle read from disk, the mutually authenticated client, the egress
broker, the shared protocol -- and what answers it here is an HTTPS server that
requires the site's certificate, as MEGINGJORD's TLS front does. It answers
registration as a test tells it to, and keeps every request.

The one thing that is not real is the name. `megingjord.veldris.internal` is the
only MEGINGJORD host the egress allow list admits, and a test machine does not
resolve it, so the API process is started with `sitecustomize.py` from
`SHIM` on its path, which resolves that one name to this server's address. The
certificate is still checked against the name, as it is in service.
"""

from __future__ import annotations

import json
import ssl
import threading
from collections.abc import Generator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.x509.oid import NameOID
from shared_protocol import (
    AnchorReceipt,
    Ed25519Signer,
    KeyPurpose,
    MLDSA65Signer,
    PublishedKey,
    ReceiptSite,
    parse_digest,
    seal,
)

HOST = "megingjord.veldris.internal"
SITE_ID = "06aca525-0e73-74a8-8000-421f4b681171"
SITE_KEY = "site-sindri-2026-10"
ANCHOR_KEY = "meg-anchor-2026-10"
#: A posture as MEGINGJORD's installer records it (its `core/posture.py`).
POSTURE: Mapping[str, Any] = {
    "v": 1,
    "recorded": True,
    "profile": "Guided: the root key was made on an offline computer.",
    "root_key": "The root key is not on the server; two recovery slips hold it.",
    "intermediate_key": "In a software token on the encrypted data volume.",
    "operational_keys": "In a software token on the encrypted data volume.",
    "storage": "Encrypted (LUKS2); unlocked by hand after each restart.",
    "backups": "Encrypted, local; last restore test passed.",
    "release_signature": "Verified against the stamped release signers.",
    "installed_at": "2026-10-12T09:00:00Z",
}
#: Put on the API process's path: resolves MEGINGJORD's name to this server.
SHIM = Path(__file__).parent / "megingjord_shim"


def _anchor_signers() -> list[Ed25519Signer | MLDSA65Signer]:
    return [
        Ed25519Signer.from_seed(ANCHOR_KEY, b"\x01" * 32),
        MLDSA65Signer.from_seed(ANCHOR_KEY, b"\x02" * 32),
    ]


def receipt(seq: int, entry_hash: str) -> str:
    """MEGINGJORD's signed receipt for one head, as the anchor duty records it."""
    document = AnchorReceipt(
        site=ReceiptSite(_uuid(SITE_ID), "sindri", 0),
        seq=seq,
        entry_hash=parse_digest(f"sha256:{entry_hash}"),
        anchored_at=datetime(2026, 3, 2, 9, 5, tzinfo=UTC),
        checkpoint_seq=0,
        key_id=ANCHOR_KEY,
        algorithms=("ed25519", "ml-dsa-65"),
    )
    signed = {
        "document": document.to_wire(),
        "envelope": seal(document, _anchor_signers()).to_wire(),
    }
    return json.dumps(signed, separators=(",", ":"), sort_keys=True)


def _uuid(value: str) -> Any:
    import uuid

    return uuid.UUID(value)


def _certificate(
    subject: str, key: Any, *, issuer: tuple[str, Any] | None = None, dns: str | None = None
) -> bytes:
    now = datetime.now(UTC)
    issuer_name, issuer_key = issuer or (subject, key)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=issuer is None, path_length=None), critical=True)
    )
    if dns:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(dns)]), critical=False
        )
    return bytes(builder.sign(issuer_key, hashes.SHA256()).public_bytes(Encoding.PEM))


def _pem(key: Any) -> bytes:
    return bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))


@dataclass
class FakeMegingjord:
    """The server, its bundle for the site, and what it has been asked."""

    bundle: Path
    port: int = 0
    #: What `POST /v1/registry/models` answers: a status and a body, or None
    #: to close the connection unanswered, as a severed link does.
    registration: tuple[int, Mapping[str, Any]] | None = field(
        default_factory=lambda: (
            201,
            {"model_id": "m-1", "receipt_id": "r-1", "replay": False, "estate_posture": POSTURE},
        )
    )
    requests: list[tuple[str, str, Any]] = field(default_factory=list)

    def refuse(self, slug: str, status: int, detail: str) -> None:
        """Answer registration with MEGINGJORD's problem."""
        self.registration = (
            status,
            {
                "type": f"https://veldris.internal/problems/megingjord/{slug}",
                "title": slug,
                "status": status,
                "detail": detail,
            },
        )

    def registered(self) -> list[Any]:
        """Every release record MEGINGJORD was asked to register."""
        return [body for method, path, body in self.requests if path == "/v1/registry/models"]

    @property
    def environment(self) -> dict[str, str]:
        """What the API process needs to reach this server as MEGINGJORD."""
        return {
            "DRAUPNIR_MEGINGJORD_BUNDLE": str(self.bundle),
            "DRAUPNIR_PUBLIC_ORIGIN": "https://alviss.sindri.veldris.internal",
            "DRAUPNIR_TEST_MEGINGJORD_ADDRESS": "127.0.0.1",
        }


def start(folder: Path) -> Generator[FakeMegingjord, None, None]:
    """Write the bundle into `folder`, start the server, yield it, stop it."""
    console_ca = ec.generate_private_key(ec.SECP256R1())
    intermediate = ec.generate_private_key(ec.SECP256R1())
    server_key = ec.generate_private_key(ec.SECP256R1())
    site_key = ec.generate_private_key(ec.SECP256R1())

    bundle = folder / "megingjord"
    (bundle / "keys").mkdir(parents=True)
    server = folder / "server"
    server.mkdir()

    (bundle / "console-ca.crt").write_bytes(_certificate("MEGINGJORD console CA", console_ca))
    (bundle / "root.crt").write_bytes(_certificate("federation intermediate", intermediate))
    (bundle / "site.crt").write_bytes(
        _certificate(
            "sindri.veldris.internal",
            site_key,
            issuer=("federation intermediate", intermediate),
            dns="sindri.veldris.internal",
        )
    )
    (bundle / "site-mtls.key").write_bytes(_pem(site_key))
    (bundle / "site-signing.key").write_bytes(_pem(Ed25519PrivateKey.generate()))
    (bundle / "enrol-request.json").write_text(
        json.dumps({"csr": "...", "signing_key": {"key_id": SITE_KEY}})
    )
    published = PublishedKey(
        ANCHOR_KEY, KeyPurpose.ANCHOR, tuple(s.public_key() for s in _anchor_signers())
    )
    (bundle / "keys" / f"{ANCHOR_KEY}.json").write_text(json.dumps(published.to_wire()))

    (server / "server.crt").write_bytes(
        _certificate(HOST, server_key, issuer=("MEGINGJORD console CA", console_ca), dns=HOST)
    )
    (server / "server.key").write_bytes(_pem(server_key))

    fake = FakeMegingjord(bundle=bundle)

    class Answering(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            del args

        def _answer(self, status: int, body: Mapping[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def get(self) -> None:
            fake.requests.append(("GET", self.path, None))
            if self.path == "/v1/protocol":
                self._answer(200, {"majors": [1], "current": 1})
            else:
                self._answer(404, {"title": "not here"})

        def post(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            fake.requests.append(("POST", self.path, body))
            if self.path == "/v1/registry/models":
                if fake.registration is None:
                    self.close_connection = True
                    return
                self._answer(*fake.registration)
            else:
                self._answer(404, {"title": "not here"})

    # Under the names `BaseHTTPRequestHandler` dispatches to, which are the
    # standard library's spelling rather than this file's.
    handler = type("Handler", (Answering,), {"do_GET": Answering.get, "do_POST": Answering.post})

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(server / "server.crt", server / "server.key")
    # The site's certificate is required, as MEGINGJORD's TLS front requires it.
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(bundle / "root.crt")
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    fake.port = httpd.server_address[1]
    # The endpoint names the port: the allow list admits the host on any port.
    (bundle / "megingjord.toml").write_text(
        f"""site_name = "sindri"
site_id = "{SITE_ID}"
ordinal = 0
endpoint = "https://{HOST}:{fake.port}"
endpoint_address = "127.0.0.1"
server_ca = "console-ca.crt"
federation_root = "root.crt"
wireguard = "wg0.conf"
anchor_key = "keys/{ANCHOR_KEY}.json"
policy_key = "keys/meg-policy-2026-10.json"
checkpoint_key = "keys/meg-ckpt-2026-10.json"
client_key = "site-mtls.key"
client_certificate = "site.crt"
signing_key = "site-signing.key"
"""
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake
    finally:
        httpd.shutdown()
        httpd.server_close()
