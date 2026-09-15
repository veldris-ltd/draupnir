"""The approver's signing agent: where an approval is signed. RF-40.

S13's "Sign and approve" sent `signature: 'console-session'`, a string no key
produced, and no `decidedAt`, so the API refused every approval from the
console. The signature has to come from where the approver's key is, and a
browser cannot sign arbitrary bytes with one. This is the other end: a small
process on the approver's own machine, holding their Ed25519 key, which the
console asks to sign.

**It signs what it builds, not what it is sent.** The request names the fields
of an approval -- the subject, the approver, the policy version and the sole
approver flag the queue row carries. The agent builds `Approval.signing_payload()`
from them, the function the API verifies against, and dates it with the instant
it signs. It never signs bytes a caller supplies, so a page that reached it
could obtain a signature over an approval and over nothing else. It signs only
for the approver it was started for, and only `approved`: a rejection needs no
signature.

**It answers the console and nothing else.** It listens on the loopback
interface, and every request must come from an origin it was started with. The
answers carry the CORS headers the console's origin needs, including
`Access-Control-Allow-Private-Network` for the preflight a browser sends before
a page served from the estate reaches a loopback address.

Started with the approver's key, their subject, and each console origin:

    python -m draupnir.gleipnir.signing_agent
        --key ~/.config/draupnir/approver.key
        --approver akuma@veldris.internal
        --origin https://alviss.sindri.veldris.internal:8443
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final
from uuid import UUID, uuid4

import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from draupnir.gleipnir.approvals import Approval, Decision

logger = structlog.get_logger(__name__)

#: Where the console looks for the agent. Loopback only, and a port the console
#: and the console's content security policy both name.
DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 47920

IDENTITY_PATH: Final = "/v1/identity"
SIGN_PATH: Final = "/v1/sign-approval"

#: The largest request body the agent reads. An approval's fields are a few
#: hundred bytes; anything larger is not one.
MAX_BODY: Final = 8192


class AgentError(Exception):
    """Raised when the agent cannot start or cannot sign what it was asked to."""


@dataclass(frozen=True, slots=True)
class Agent:
    """One approver's key, and the origins allowed to ask for a signature."""

    key: Ed25519PrivateKey
    approver: str
    origins: frozenset[str]
    #: Injected so a test can fix the instant signed over.
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    @property
    def key_id(self) -> str:
        """A short fingerprint of the public key, for the console to show."""
        raw = self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return hashlib.sha256(raw).hexdigest()[:16]

    def identity(self) -> dict[str, str]:
        """Whose key this is. What the console checks before offering approval."""
        return {"approver": self.approver, "keyId": self.key_id}

    def sign(self, fields: dict[str, Any]) -> dict[str, str]:
        """Sign an approval built from these fields, dated now.

        Refuses anything but an approval, for this agent's approver, with the
        fields the payload needs. The payload is `Approval.signing_payload()`,
        so what is signed is exactly what the API verifies.
        """
        if fields.get("decision", "approved") != "approved":
            msg = "the agent signs approvals only; a rejection needs no signature"
            raise AgentError(msg)
        approver = str(fields.get("approver") or "")
        if approver != self.approver:
            msg = (
                f"this agent holds {self.approver}'s key and was asked to sign for "
                f"{approver or 'nobody'}. It signs only for its own approver."
            )
            raise AgentError(msg)
        try:
            subject = UUID(str(fields["subject"]))
            policy_version = str(fields["policyVersion"])
            exception = fields["soleApproverException"]
        except (KeyError, ValueError) as error:
            msg = f"the approval is missing or mistypes a field it needs: {error}"
            raise AgentError(msg) from error
        if not isinstance(exception, bool) or not policy_version:
            msg = "soleApproverException must be true or false, and policyVersion must be named"
            raise AgentError(msg)

        decided_at = self.clock()
        payload = Approval(
            id=uuid4(),
            subject_id=subject,
            approver=approver,
            decision=Decision.APPROVED,
            signature="",
            policy_version=policy_version,
            decided_at=decided_at,
            sole_approver_exception=exception,
        ).signing_payload()
        signature = self.key.sign(payload).hex()
        logger.info(
            "signing-agent.signed",
            approver=approver,
            subject=str(subject),
            soleApproverException=exception,
            decidedAt=decided_at.isoformat(),
        )
        return {"signature": signature, "decidedAt": decided_at.isoformat()}


def load(key_path: Path, approver: str, origins: Iterable[str]) -> Agent:
    """Build an agent from a PKCS#8 PEM key, refusing a key it cannot use."""
    allowed = frozenset(origin.rstrip("/") for origin in origins if origin.strip())
    if not allowed:
        msg = (
            "no --origin was given. The agent answers only the console origins it is "
            "started with, and with none it would answer nothing."
        )
        raise AgentError(msg)
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as error:
        msg = f"the key at {key_path} could not be read: {error}"
        raise AgentError(msg) from error
    if not isinstance(key, Ed25519PrivateKey):
        msg = f"the key at {key_path} is a {type(key).__name__}; approvals are signed with Ed25519"
        raise AgentError(msg)
    return Agent(key=key, approver=approver, origins=allowed)


def handler_for(agent: Agent) -> type[BaseHTTPRequestHandler]:
    """The request handler, bound to one agent."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "draupnir-signing-agent"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 -- the base signature
            del format, args

        def _origin(self) -> str | None:
            origin = self.headers.get("Origin", "").rstrip("/")
            return origin if origin in agent.origins else None

        def _answer(
            self, status: int, body: dict[str, Any], origin: str | None, **extra: str
        ) -> None:
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            if origin is not None:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            for name, value in extra.items():
                self.send_header(name.replace("_", "-"), value)
            self.end_headers()
            self.wfile.write(encoded)

        def _refuse_origin(self) -> None:
            self._answer(
                403,
                {"error": "this agent answers only the console origins it was started with"},
                None,
            )

        def do_OPTIONS(self) -> None:
            origin = self._origin()
            if origin is None:
                self._refuse_origin()
                return
            # 204 and no body: a preflight answer carries headers only.
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()

        def do_GET(self) -> None:
            origin = self._origin()
            if origin is None:
                self._refuse_origin()
                return
            if self.path != IDENTITY_PATH:
                self._answer(404, {"error": f"no such path; ask {IDENTITY_PATH}"}, origin)
                return
            self._answer(200, agent.identity(), origin)

        def do_POST(self) -> None:
            origin = self._origin()
            if origin is None:
                self._refuse_origin()
                return
            if self.path != SIGN_PATH:
                self._answer(404, {"error": f"no such path; post to {SIGN_PATH}"}, origin)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                self._answer(400, {"error": "the body is not an approval's fields"}, origin)
                return
            try:
                fields = json.loads(self.rfile.read(length))
                if not isinstance(fields, dict):
                    raise AgentError("the body is not an object")
                signed = agent.sign(fields)
            except (ValueError, AgentError) as refused:
                self._answer(422, {"error": str(refused)}, origin)
                return
            self._answer(200, signed, origin)

    return Handler


def serve(
    agent: Agent, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> ThreadingHTTPServer:
    """A server for this agent, bound and not yet serving."""
    return ThreadingHTTPServer((host, port), handler_for(agent))


def main(argv: list[str] | None = None) -> int:
    """Start the agent, or say why not."""
    parser = argparse.ArgumentParser(
        description="Sign DRAUPNIR approvals with this approver's key."
    )
    parser.add_argument("--key", type=Path, required=True, help="Ed25519 private key, PKCS#8 PEM.")
    parser.add_argument("--approver", required=True, help="The subject the key belongs to.")
    parser.add_argument(
        "--origin",
        action="append",
        default=[],
        help="A console origin allowed to ask for a signature. Repeatable.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    arguments = parser.parse_args(argv)

    try:
        agent = load(arguments.key, arguments.approver, arguments.origin)
    except AgentError as error:
        sys.stderr.write(f"signing-agent: {error}\n")
        return 2

    server = serve(agent, port=arguments.port)
    logger.info(
        "signing-agent.listening",
        approver=agent.approver,
        keyId=agent.key_id,
        address=f"http://{DEFAULT_HOST}:{arguments.port}",
        origins=sorted(agent.origins),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
