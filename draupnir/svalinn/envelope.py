"""The signature envelope: versioned, and carrying more than one algorithm.

Decision S10: "The signature envelope is crypto agile from Release 1 ...
Designing the envelope to carry more than one signature now costs a schema
field. Retrofitting it later means re-signing every historical release."

AC-S17 is the test of it: "The signature envelope carries two signatures of
different algorithms in a test case, and verification succeeds against either."

Three consequences shape what follows.

**Signatures are a list, from the first version.** Not an optional second
field, not a `signature` plus a `signatures`. One list, which happens to have
one element today and two during a migration. A format that special-cases the
single-signature shape is a format that changes when a second algorithm
arrives, which is the change this exists to avoid.

**Verification succeeds on any one signature, and reports which.** During a
migration, a verifier that has ML-DSA and a verifier that has only Ed25519 must
both be able to verify the same envelope. Requiring all signatures to verify
would make an envelope unverifiable by the older party, which is the thing that
stops a migration from being incremental.

The corollary is that a verifier states the algorithms it will accept. "Any
signature verifies" would let an envelope be accepted on an algorithm that has
since been withdrawn, and the withdrawal is the moment that matters.

**The payload is canonical bytes, hashed once.** Each algorithm signs the same
digest, so a caller cannot produce two signatures over subtly different
serialisations of the same object and have both verify.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

#: The envelope schema. Versioned in the name so that a change is a new schema
#: rather than a field appearing in an old one.
SCHEMA: Final = "draupnir/signature-envelope/v1"


class Algorithm(StrEnum):
    """Signature algorithms. SAD 9.5, Release 1 column and the migration column."""

    #: Release 1, artefact and approval signing.
    ED25519 = "ed25519"
    #: Release 1 alternative, where an HSM speaks ECDSA and not EdDSA.
    ECDSA_P384 = "ecdsa-p384"
    #: The post-quantum addition. Declared now so the envelope, the inventory
    #: and the verifier all have somewhere to put it; no implementation is
    #: shipped, and `SUPPORTED` says so.
    ML_DSA_65 = "ml-dsa-65"


#: What this build can actually verify. ML-DSA is in the vocabulary and not in
#: this set, which is the honest statement: the envelope is ready and the
#: library estate is not (SAD 9.5, "once the library estate supports it").
SUPPORTED: Final[frozenset[Algorithm]] = frozenset({Algorithm.ED25519, Algorithm.ECDSA_P384})

#: The digest every algorithm signs. SAD 9.5: SHA-256 for artefact manifests
#: and ledger chaining.
DIGEST = "sha-256"


class EnvelopeError(Exception):
    """Raised when an envelope cannot be built or trusted."""


class NoAcceptableSignatureError(EnvelopeError):
    """Raised when nothing in the envelope verifies against an accepted algorithm.

    Names both what was offered and what was accepted, because during a
    migration the interesting failure is an envelope signed only with an
    algorithm this verifier does not hold -- which is a deployment problem, not
    a tampering one, and the message should not suggest otherwise.
    """

    def __init__(self, offered: Iterable[str], accepted: Iterable[str], reason: str = "") -> None:
        """Name the algorithms on both sides."""
        self.offered = tuple(sorted(offered))
        self.accepted = tuple(sorted(accepted))
        detail = f" {reason}" if reason else ""
        super().__init__(
            f"no signature on this envelope verified. It carries "
            f"{', '.join(self.offered) or 'no signatures'}; this verifier accepts "
            f"{', '.join(self.accepted) or 'nothing'}.{detail} If the algorithms do not "
            "overlap this is a deployment problem rather than a tampering one: the "
            "envelope is crypto agile so that both sides of a migration can verify "
            "the same artefact (Decision S10)."
        )


@runtime_checkable
class Signer(Protocol):
    """Something that can sign a digest with one algorithm."""

    algorithm: Algorithm
    key_id: str

    def sign(self, digest: bytes) -> bytes:
        """Return a signature over `digest`."""
        ...


@runtime_checkable
class Verifier(Protocol):
    """Something that can verify a signature under one algorithm."""

    algorithm: Algorithm

    def verify(self, digest: bytes, signature: bytes, key_id: str) -> bool:
        """Return whether `signature` is valid over `digest` for `key_id`."""
        ...


@dataclass(frozen=True, slots=True)
class Signature:
    """One signature, naming the algorithm and the key that produced it."""

    algorithm: Algorithm
    key_id: str
    #: Hex, so the envelope is JSON and diffable. Base64 saves a third of the
    #: bytes and costs a reader the ability to compare two envelopes by eye.
    value: str
    signed_at: datetime

    def __post_init__(self) -> None:
        """Refuse a signature that cannot be attributed."""
        if not self.key_id:
            msg = (
                "a signature names the key that produced it. A signature with no key "
                "identifier cannot be checked against a revocation, which is the one "
                "question asked of an old signature."
            )
            raise EnvelopeError(msg)
        if self.signed_at.tzinfo is None:
            msg = "signature timestamps carry an explicit offset (SAD 11E.2)"
            raise EnvelopeError(msg)

    def as_payload(self) -> dict[str, Any]:
        """The wire shape."""
        return {
            "algorithm": str(self.algorithm),
            "keyId": self.key_id,
            "value": self.value,
            "signedAt": self.signed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class Envelope:
    """A signed statement, carrying one or more signatures over one digest."""

    #: What was signed, canonicalised. Held so a verifier can recompute the
    #: digest rather than trusting the one in the envelope.
    payload: Mapping[str, Any]
    signatures: tuple[Signature, ...]
    schema: str = SCHEMA
    digest_algorithm: str = DIGEST

    def __post_init__(self) -> None:
        """Refuse an envelope with nothing to verify."""
        if not self.signatures:
            msg = (
                "an envelope with no signatures is a payload. Build it with `seal`, "
                "which requires at least one signer."
            )
            raise EnvelopeError(msg)
        algorithms = [item.algorithm for item in self.signatures]
        if len(set(algorithms)) != len(algorithms):
            msg = (
                "the envelope carries two signatures of the same algorithm. That is "
                "either the same signature twice or two keys of one algorithm, and "
                "neither adds anything a verifier can use."
            )
            raise EnvelopeError(msg)

    @property
    def algorithms(self) -> tuple[Algorithm, ...]:
        """Every algorithm this envelope is signed under."""
        return tuple(sorted(item.algorithm for item in self.signatures))

    def canonical(self) -> bytes:
        """The bytes every signature is over."""
        return json.dumps(
            dict(self.payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def digest(self) -> bytes:
        """The digest every algorithm signs. Recomputed, never read."""
        return hashlib.sha256(self.canonical()).digest()

    def signature_for(self, algorithm: Algorithm) -> Signature | None:
        """The signature under one algorithm, if the envelope carries one."""
        for item in self.signatures:
            if item.algorithm is algorithm:
                return item
        return None

    def as_payload(self) -> dict[str, Any]:
        """The published shape."""
        return {
            "schema": self.schema,
            "digestAlgorithm": self.digest_algorithm,
            "payload": dict(self.payload),
            "signatures": [item.as_payload() for item in self.signatures],
        }

    def to_json(self) -> str:
        """The envelope as an artefact."""
        return json.dumps(self.as_payload(), indent=2, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_payload(cls, data: Mapping[str, Any]) -> Envelope:
        """Rebuild an envelope from its published form."""
        if data.get("schema") != SCHEMA:
            msg = (
                f"this build reads {SCHEMA} and the envelope declares "
                f"{data.get('schema')!r}. A schema it does not know is refused rather "
                "than read optimistically."
            )
            raise EnvelopeError(msg)
        return cls(
            payload=dict(data["payload"]),
            signatures=tuple(
                Signature(
                    algorithm=Algorithm(item["algorithm"]),
                    key_id=str(item["keyId"]),
                    value=str(item["value"]),
                    signed_at=datetime.fromisoformat(str(item["signedAt"])),
                )
                for item in data.get("signatures", ())
            ),
            schema=str(data["schema"]),
            digest_algorithm=str(data.get("digestAlgorithm", DIGEST)),
        )


@dataclass(frozen=True, slots=True)
class Verified:
    """The result of verifying an envelope: which signature carried it."""

    algorithm: Algorithm
    key_id: str
    #: Every algorithm that verified, not only the first. During a migration
    #: this is how you establish that the new algorithm is actually working
    #: before the old one is withdrawn.
    verified_by: tuple[Algorithm, ...] = field(default_factory=tuple)

    def as_payload(self) -> dict[str, Any]:
        """The wire shape."""
        return {
            "algorithm": str(self.algorithm),
            "keyId": self.key_id,
            "verifiedBy": [str(item) for item in self.verified_by],
        }


def seal(payload: Mapping[str, Any], signers: Sequence[Signer], *, signed_at: datetime) -> Envelope:
    """Sign a payload with every signer given. One or several algorithms.

    Every signer signs the same digest of the same canonical bytes, so two
    signatures cannot be over subtly different serialisations of one object.
    """
    if not signers:
        msg = "sealing needs at least one signer"
        raise EnvelopeError(msg)

    canonical = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).digest()

    return Envelope(
        payload=dict(payload),
        signatures=tuple(
            Signature(
                algorithm=signer.algorithm,
                key_id=signer.key_id,
                value=signer.sign(digest).hex(),
                signed_at=signed_at,
            )
            for signer in signers
        ),
    )


def verify(
    envelope: Envelope,
    verifiers: Sequence[Verifier],
    *,
    accepted: Iterable[Algorithm] | None = None,
) -> Verified:
    """Verify an envelope. Any one accepted signature suffices. AC-S17.

    Any one, because a verifier holding only the older algorithm must still be
    able to verify an envelope that also carries the newer one -- otherwise a
    migration cannot be incremental. Not *any* signature, though: the caller
    states which algorithms it accepts, so an envelope signed only under an
    algorithm that has since been withdrawn does not verify.
    """
    allowed = frozenset(accepted) if accepted is not None else SUPPORTED
    digest = envelope.digest()
    passed: list[Algorithm] = []
    first: Signature | None = None

    for verifier in verifiers:
        if verifier.algorithm not in allowed:
            continue
        signature = envelope.signature_for(verifier.algorithm)
        if signature is None:
            continue
        if verifier.verify(digest, bytes.fromhex(signature.value), signature.key_id):
            passed.append(verifier.algorithm)
            first = first or signature

    if first is None:
        raise NoAcceptableSignatureError(
            (str(item) for item in envelope.algorithms), (str(item) for item in allowed)
        )

    return Verified(
        algorithm=first.algorithm, key_id=first.key_id, verified_by=tuple(sorted(passed))
    )
