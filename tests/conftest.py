"""Shared fixtures.

Integration tests are separated from every other level because they are the
only ones that need Docker. `pytest tests/unit tests/property tests/contract`
runs on a machine with nothing installed but Python.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest


@pytest.fixture
def moment() -> datetime:
    """A fixed, offset-aware instant. SAD 11E.2 forbids naive timestamps."""
    return datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


#: The issuer and audience the test session is configured with.
#:
#: RF-01 made `create_app` refuse to start with no way to authenticate anybody,
#: which is right and which every test that builds an app has to satisfy. It is
#: set here rather than in each test because it is a property of the
#: deployment, not of any one case: the API under test *is* configured to
#: authenticate, and the tests then present verified claims directly — which is
#: what they were always doing, and what `tests/contract/test_authentication.py`
#: exercises the other half of.
TEST_ISSUER = "https://megingjord.veldris.internal"
TEST_AUDIENCE = "draupnir-control-plane"


@pytest.fixture(autouse=True, scope="session")
def _configured_to_authenticate() -> Iterator[None]:
    """Give the session an identity provider, so `create_app` will start.

    No JWKS client is configured with it, so nothing is fetched and no token
    verifies. A test that wants a real verification builds its own `Verifier`
    with keys it controls.
    """
    from draupnir.core.infrastructure import config

    names = ("DRAUPNIR_OIDC_ISSUER", "DRAUPNIR_OIDC_AUDIENCE", "DRAUPNIR_OIDC_CLIENT_ID")
    previous = {name: os.environ.get(name) for name in names}
    os.environ["DRAUPNIR_OIDC_ISSUER"] = TEST_ISSUER
    os.environ["DRAUPNIR_OIDC_AUDIENCE"] = TEST_AUDIENCE
    # Registered as a client too, so the authorisation-code flow of RF-03 is
    # exercised rather than short-circuiting to "not configured".
    os.environ["DRAUPNIR_OIDC_CLIENT_ID"] = "draupnir-console"
    config.get_settings.cache_clear()
    yield
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    config.get_settings.cache_clear()


@pytest.fixture(autouse=True, scope="session")
def _signed_plugins(
    tmp_path_factory: pytest.TempPathFactory, _configured_to_authenticate: None
) -> Iterator[None]:
    """Sign the installed plug-ins, so the session verifies for real. RF-02.

    `PluginRegistry.discover` no longer defaults to a verifier that verifies
    nothing, so a session with `DRAUPNIR_DEV` unset needs a trust store and a
    signature manifest — and building them here rather than faking them is the
    point: the drivers load through `PkiVerifier`, over digests recomputed from
    the files on disk, which is the control AC-S7 describes and which nothing
    exercised before.

    The signer is `scripts/sign_artefacts.py`'s own, so a signer and a verifier
    that hashed a distribution differently would fail here rather than at a
    forge.
    """
    import base64
    import json as _json

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from draupnir.core.infrastructure import config
    from scripts.sign_artefacts import installed_plugins, sign_distributions

    root = tmp_path_factory.mktemp("pki")
    trust = root / "trust"
    trust.mkdir()

    key = ed25519.Ed25519PrivateKey.generate()
    (trust / "test-signer.pem").write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    material = base64.b64encode(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode()

    manifest = root / "plugin-signatures.json"
    manifest.write_text(
        _json.dumps(
            sign_distributions(installed_plugins(), key_material=material, key_id="test-signer")
        ),
        encoding="utf-8",
    )

    os.environ["DRAUPNIR_PLUGIN_TRUST_STORE"] = str(trust)
    os.environ["DRAUPNIR_PLUGIN_SIGNATURE_MANIFEST"] = str(manifest)
    config.get_settings.cache_clear()
    yield
    for name in ("DRAUPNIR_PLUGIN_TRUST_STORE", "DRAUPNIR_PLUGIN_SIGNATURE_MANIFEST"):
        os.environ.pop(name, None)
    config.get_settings.cache_clear()


#: The approvers the session registers keys for, and their private keys.
#:
#: RF-06 made an approval's signature something that has to verify against a
#: registered key rather than merely be a non-empty string. Every test that
#: decides a gate therefore needs a real key and a real signature, and
#: `sign_decision` below is what produces one.
APPROVER_KEYS: dict[str, object] = {}


@pytest.fixture(autouse=True, scope="session")
def _registered_approvers(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Register approver keys for the session. RF-06.

    A real directory of real PEMs, so the verification path is exercised rather
    than bypassed — the same arrangement as the plug-in trust store, and for
    the same reason: a control the tests step around is a control the tests do
    not cover.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from draupnir.core.infrastructure import config

    store = tmp_path_factory.mktemp("approvers")
    for subject in ("akuma", "dev@veldris.internal", "approver@veldris.internal"):
        key = ed25519.Ed25519PrivateKey.generate()
        APPROVER_KEYS[subject] = key
        (store / f"{subject}.pem").write_bytes(
            key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )

    os.environ["DRAUPNIR_APPROVER_KEY_STORE"] = str(store)
    config.get_settings.cache_clear()
    yield
    os.environ.pop("DRAUPNIR_APPROVER_KEY_STORE", None)
    config.get_settings.cache_clear()


def sign_decision(
    *,
    approver: str,
    subject_id: UUID,
    decided_at: datetime,
    sole_approver_exception: bool = False,
    approval_id: UUID | None = None,
) -> str:
    """A real signature over a real decision. RF-06.

    The payload is `Approval.signing_payload()` — the same bytes the API
    verifies — so a test that signs with this is exercising the control rather
    than stepping around it. It includes the sole-approver exception, which is
    the property the whole arrangement exists for: suppressing the flag
    invalidates the signature.
    """
    from draupnir.core.domain.identifiers import new_id
    from draupnir.gleipnir.approvals import Approval, Decision

    key = APPROVER_KEYS[approver]
    payload = Approval(
        id=approval_id or new_id(),
        subject_id=subject_id,
        approver=approver,
        submitter="",
        decision=Decision.APPROVED,
        policy_version="gleipnir/2026.01",
        decided_at=decided_at,
        signature="",
        sole_approver_exception=sole_approver_exception,
    ).signing_payload()
    signature: bytes = key.sign(payload)  # type: ignore[attr-defined]
    return signature.hex()
