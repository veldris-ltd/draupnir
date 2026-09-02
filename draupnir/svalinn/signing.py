"""Detached signatures over a site's chain head.

SAD 11A.3: GULLINBURSTI submits the head -- the sequence number and entry hash
-- and MEGINGJORD countersigns. This module produces the signature that
accompanies the submission, so that the receiving tier can establish the head
came from the forge that claims it before it countersigns anything.

The head signed here is `ledger.ChainHead`, and it is the same object
`federation.AnchorSubmission` wraps for transmission: the submission adds the
previous hash, a timestamp and this signature, and delegates
`signing_payload()` straight through. One definition of what is signed, so the
bytes cannot differ between the two ends.

Signing lives in SVALINN because SAD 5.2 gives it artefact signing and the
plug-in signature trust root, and because the core must not know what a key
is. The core produces the bytes (`ChainHead.signing_payload`); this decides
what to do with them.

Ed25519, per SAD 9.5's preference for small deterministic signatures over an
internal PKI, with a Rekor transparency log rather than public Sigstore
(Decision S9). Submission to that log is the release stage's job, not this
module's: here the output is evidence, not a publication.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

from draupnir.core.domain.ledger import ChainHead

#: The schema string carried with a signature, so a verifier years from now
#: knows what the bytes were and can reject a payload built a different way.
SCHEMA = "draupnir/chain-head/v1"


class SigningError(Exception):
    """Raised when a key cannot be loaded or is of the wrong kind."""


@dataclass(frozen=True, slots=True)
class SignedChainHead:
    """A chain head with a detached signature and the key that made it."""

    head: ChainHead
    #: Base64 of the raw 64-byte Ed25519 signature.
    signature: str
    #: Identifier of the signing key, so a verifier can find the public half.
    key_id: str
    schema: str = SCHEMA

    def as_submission(self) -> dict[str, str | int]:
        """The document GULLINBURSTI sends to MEGINGJORD.

        Hashes and metadata only. No corpus, no weights, no payload: the
        federation holds hashes, so a forge's content never leaves it
        (SAD 11A.3).
        """
        return {
            "schema": self.schema,
            "site_id": self.head.site_id,
            "seq": self.head.seq,
            "entry_hash": self.head.entry_hash,
            "key_id": self.key_id,
            "signature": self.signature,
        }


def load_private_key(pem: bytes) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from PKCS#8 PEM."""
    try:
        key = load_pem_private_key(pem, password=None)
    except Exception as error:
        msg = "the signing key could not be loaded"
        raise SigningError(msg) from error
    if not isinstance(key, Ed25519PrivateKey):
        msg = f"expected an Ed25519 private key, got {type(key).__name__}"
        raise SigningError(msg)
    return key


def load_public_key(pem: bytes) -> Ed25519PublicKey:
    """Load an Ed25519 public key from PEM."""
    try:
        key = load_pem_public_key(pem)
    except Exception as error:
        msg = "the verifying key could not be loaded"
        raise SigningError(msg) from error
    if not isinstance(key, Ed25519PublicKey):
        msg = f"expected an Ed25519 public key, got {type(key).__name__}"
        raise SigningError(msg)
    return key


def key_id(public_key: Ed25519PublicKey) -> str:
    """Return a stable identifier for a key: base64 of its raw public bytes.

    The key names itself. Nothing has to maintain a mapping, and two forges
    cannot collide on an identifier they did not choose.
    """
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def sign_chain_head(head: ChainHead, private_key: Ed25519PrivateKey) -> SignedChainHead:
    """Sign a chain head, returning the detached signature and key identity."""
    signature = private_key.sign(head.signing_payload())
    return SignedChainHead(
        head=head,
        signature=base64.b64encode(signature).decode("ascii"),
        key_id=key_id(private_key.public_key()),
    )


def verify_chain_head(signed: SignedChainHead, public_key: Ed25519PublicKey) -> bool:
    """Return whether the signature covers this head under this key."""
    if signed.schema != SCHEMA:
        return False
    if key_id(public_key) != signed.key_id:
        return False
    try:
        public_key.verify(base64.b64decode(signed.signature), signed.head.signing_payload())
    except (InvalidSignature, ValueError):
        return False
    return True


def generate_key_pair() -> tuple[bytes, bytes]:
    """Return a fresh (private PEM, public PEM) pair.

    For development and tests. Production keys come from the internal Veldris
    PKI and are brokered by SVALINN, never generated in process.
    """
    private = Ed25519PrivateKey.generate()
    return (
        private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
        private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo),
    )
