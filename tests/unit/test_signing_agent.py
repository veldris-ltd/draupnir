"""The approver's signing agent. RF-40.

The console signed approvals with a placeholder, so none could verify. The agent
signs on the approver's machine, with their key, over the bytes the API checks.
These tests hold it to that: what it signs verifies the way `decideGate`
verifies it, it signs nothing but an approval for its own approver, and it
answers no origin it was not started with.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from draupnir.gleipnir import signing_agent
from draupnir.gleipnir.approvals import Approval, Decision
from draupnir.svalinn import signing

AT = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
SUBJECT = UUID("019cf270-ba80-76c9-84ca-7374e16c7631")
ORIGIN = "https://alviss.sindri.veldris.internal:8443"
POLICY = "gleipnir/2026.01"


def _agent(key: ed25519.Ed25519PrivateKey) -> signing_agent.Agent:
    return signing_agent.Agent(
        key=key, approver="akuma", origins=frozenset({ORIGIN}), clock=lambda: AT
    )


def _fields(**changes: Any) -> dict[str, Any]:
    return {
        "subject": str(SUBJECT),
        "approver": "akuma",
        "policyVersion": POLICY,
        "soleApproverException": True,
        **changes,
    }


def _payload(decided_at: str, *, exception: bool) -> bytes:
    """The bytes `decideGate` verifies, built the way it builds them."""
    return Approval(
        id=uuid4(),
        subject_id=SUBJECT,
        approver="akuma",
        decision=Decision.APPROVED,
        signature="",
        policy_version=POLICY,
        decided_at=datetime.fromisoformat(decided_at),
        sole_approver_exception=exception,
    ).signing_payload()


# ---------------------------------------------------------------------------
# What it signs
# ---------------------------------------------------------------------------


def test_what_the_agent_signs_verifies_as_the_api_verifies_it() -> None:
    key = ed25519.Ed25519PrivateKey.generate()

    signed = _agent(key).sign(_fields())

    assert signed["decidedAt"] == AT.isoformat()
    assert signing.verify_approval(
        _payload(signed["decidedAt"], exception=True), signed["signature"], key.public_key()
    )


def test_the_signature_does_not_survive_clearing_the_exception() -> None:
    """The flag is inside the signed bytes, which is what makes it undeniable."""
    key = ed25519.Ed25519PrivateKey.generate()

    signed = _agent(key).sign(_fields())

    assert not signing.verify_approval(
        _payload(signed["decidedAt"], exception=False), signed["signature"], key.public_key()
    )


@pytest.mark.parametrize(
    ("changes", "refusal"),
    [
        ({"approver": "somebody-else"}, "only for its own approver"),
        ({"decision": "rejected"}, "approvals only"),
        ({"policyVersion": ""}, "policyVersion must be named"),
        ({"soleApproverException": "yes"}, "must be true or false"),
        ({"subject": "not-a-uuid"}, "mistypes a field"),
    ],
)
def test_the_agent_signs_nothing_but_its_own_approver_s_approval(
    changes: dict[str, Any], refusal: str
) -> None:
    with pytest.raises(signing_agent.AgentError, match=refusal):
        _agent(ed25519.Ed25519PrivateKey.generate()).sign(_fields(**changes))


# ---------------------------------------------------------------------------
# Starting it
# ---------------------------------------------------------------------------


def _pem(key: Any, path: Path) -> Path:
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return path


def test_an_agent_with_no_origin_is_refused(tmp_path: Path) -> None:
    key = _pem(ed25519.Ed25519PrivateKey.generate(), tmp_path / "approver.key")

    with pytest.raises(signing_agent.AgentError, match="no --origin"):
        signing_agent.load(key, "akuma", [])


def test_a_key_that_is_not_ed25519_is_refused(tmp_path: Path) -> None:
    key = _pem(ec.generate_private_key(ec.SECP256R1()), tmp_path / "approver.key")

    with pytest.raises(signing_agent.AgentError, match="Ed25519"):
        signing_agent.load(key, "akuma", [ORIGIN])


def test_an_origin_is_matched_without_its_trailing_slash(tmp_path: Path) -> None:
    key = _pem(ed25519.Ed25519PrivateKey.generate(), tmp_path / "approver.key")

    agent = signing_agent.load(key, "akuma", [f"{ORIGIN}/"])

    assert agent.origins == frozenset({ORIGIN})


# ---------------------------------------------------------------------------
# Over HTTP, as the console reaches it
# ---------------------------------------------------------------------------


@pytest.fixture
def served() -> Iterator[tuple[str, ed25519.Ed25519PrivateKey]]:
    key = ed25519.Ed25519PrivateKey.generate()
    server = signing_agent.serve(_agent(key), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", key
    finally:
        server.shutdown()
        server.server_close()


def _request(
    url: str, *, method: str = "GET", origin: str = ORIGIN, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, str], bytes]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Origin": origin}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, method=method, headers=headers)  # noqa: S310 -- loopback
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 -- loopback
            return response.status, dict(response.headers), response.read()
    except HTTPError as refused:
        return refused.code, dict(refused.headers), refused.read()


def test_the_agent_names_its_approver_to_the_console(
    served: tuple[str, ed25519.Ed25519PrivateKey],
) -> None:
    base, _key = served

    status, headers, body = _request(f"{base}{signing_agent.IDENTITY_PATH}")

    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == ORIGIN
    assert json.loads(body)["approver"] == "akuma"


def test_another_origin_is_refused_and_given_no_cors_grant(
    served: tuple[str, ed25519.Ed25519PrivateKey],
) -> None:
    base, _key = served

    status, headers, _body = _request(
        f"{base}{signing_agent.SIGN_PATH}",
        method="POST",
        origin="https://elsewhere",
        body=_fields(),
    )

    assert status == 403
    assert "Access-Control-Allow-Origin" not in headers


def test_the_preflight_grants_the_console_private_network_access(
    served: tuple[str, ed25519.Ed25519PrivateKey],
) -> None:
    base, _key = served

    status, headers, body = _request(f"{base}{signing_agent.SIGN_PATH}", method="OPTIONS")

    assert status == 204
    assert body == b""
    assert headers["Access-Control-Allow-Private-Network"] == "true"
    assert "POST" in headers["Access-Control-Allow-Methods"]


def test_a_signature_asked_for_over_http_verifies(
    served: tuple[str, ed25519.Ed25519PrivateKey],
) -> None:
    base, key = served

    status, _headers, body = _request(
        f"{base}{signing_agent.SIGN_PATH}", method="POST", body=_fields()
    )
    signed = json.loads(body)

    assert status == 200
    assert signing.verify_approval(
        _payload(signed["decidedAt"], exception=True), signed["signature"], key.public_key()
    )


def test_a_refused_signature_is_a_422_with_the_reason(
    served: tuple[str, ed25519.Ed25519PrivateKey],
) -> None:
    base, _key = served

    status, _headers, body = _request(
        f"{base}{signing_agent.SIGN_PATH}", method="POST", body=_fields(approver="mallory")
    )

    assert status == 422
    assert "its own approver" in json.loads(body)["error"]
