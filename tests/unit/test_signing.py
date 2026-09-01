"""Detached signatures over a chain head, SAD 11A.3.

What matters is not that a signature verifies, but that it stops verifying the
moment anything it covers changes. A signature over a head whose sequence
number can be edited without detection would let a forge under-report how far
its chain had run, which is exactly the divergence anchoring exists to catch.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from draupnir.core.domain.ledger import ChainHead
from draupnir.svalinn.signing import (
    SCHEMA,
    SignedChainHead,
    SigningError,
    generate_key_pair,
    key_id,
    load_private_key,
    load_public_key,
    sign_chain_head,
    verify_chain_head,
)

HEAD = ChainHead(site_id="sindri", seq=211, entry_hash="a" * 64)


@pytest.fixture(scope="module")
def keys() -> tuple[bytes, bytes]:
    return generate_key_pair()


def test_a_signature_verifies_under_its_own_key(keys: tuple[bytes, bytes]) -> None:
    private, public = keys
    signed = sign_chain_head(HEAD, load_private_key(private))
    assert verify_chain_head(signed, load_public_key(public))


def test_a_signature_does_not_verify_under_another_key(keys: tuple[bytes, bytes]) -> None:
    private, _ = keys
    _, other_public = generate_key_pair()
    signed = sign_chain_head(HEAD, load_private_key(private))
    assert not verify_chain_head(signed, load_public_key(other_public))


@pytest.mark.parametrize(
    "change",
    [
        {"seq": 210},
        {"seq": 212},
        {"entry_hash": "b" * 64},
        {"site_id": "brokkr"},
    ],
)
def test_editing_any_covered_field_breaks_the_signature(
    keys: tuple[bytes, bytes], change: dict[str, object]
) -> None:
    private, public = keys
    signed = sign_chain_head(HEAD, load_private_key(private))
    tampered = replace(signed, head=replace(HEAD, **change))  # type: ignore[arg-type]
    assert not verify_chain_head(tampered, load_public_key(public))


def test_a_signature_from_a_different_schema_is_refused(keys: tuple[bytes, bytes]) -> None:
    private, public = keys
    signed = sign_chain_head(HEAD, load_private_key(private))
    assert not verify_chain_head(
        replace(signed, schema="draupnir/chain-head/v0"), load_public_key(public)
    )


def test_a_mismatched_key_identity_is_refused(keys: tuple[bytes, bytes]) -> None:
    private, public = keys
    signed = sign_chain_head(HEAD, load_private_key(private))
    assert not verify_chain_head(replace(signed, key_id="not-this-key"), load_public_key(public))


def test_a_malformed_signature_is_refused_rather_than_raising(
    keys: tuple[bytes, bytes],
) -> None:
    private, public = keys
    signed = sign_chain_head(HEAD, load_private_key(private))
    assert not verify_chain_head(
        replace(signed, signature="!!not base64!!"), load_public_key(public)
    )


def test_the_key_names_itself(keys: tuple[bytes, bytes]) -> None:
    private, public = keys
    identity = key_id(load_public_key(public))
    assert sign_chain_head(HEAD, load_private_key(private)).key_id == identity
    # Nothing maintains a mapping, so two keys cannot collide on a name.
    _, other = generate_key_pair()
    assert key_id(load_public_key(other)) != identity


def test_the_submission_carries_hashes_and_nothing_else(keys: tuple[bytes, bytes]) -> None:
    private, _ = keys
    submission = sign_chain_head(HEAD, load_private_key(private)).as_submission()
    assert set(submission) == {
        "schema",
        "site_id",
        "seq",
        "entry_hash",
        "key_id",
        "signature",
    }
    assert submission["schema"] == SCHEMA
    assert submission["seq"] == 211


def test_the_signed_payload_is_the_three_fields_only() -> None:
    # A verifier reconstructs the payload from the three values. If the payload
    # ever included, say, a timestamp, it could not.
    assert HEAD.signing_payload() == (
        b'{"entry_hash":"' + b"a" * 64 + b'","seq":211,"site_id":"sindri"}'
    )


def test_the_head_converts_to_the_anchor_pair() -> None:
    assert HEAD.as_anchor() == (211, "a" * 64)


def test_a_key_of_the_wrong_kind_is_refused() -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    rsa_pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    with pytest.raises(SigningError, match="Ed25519"):
        load_private_key(rsa_pem)


def test_unloadable_key_material_is_refused() -> None:
    with pytest.raises(SigningError):
        load_private_key(b"not a key")
    with pytest.raises(SigningError):
        load_public_key(b"not a key")


def test_a_signed_head_is_immutable() -> None:
    signed = SignedChainHead(head=HEAD, signature="x", key_id="y")
    with pytest.raises(AttributeError):
        signed.signature = "z"  # type: ignore[misc]
