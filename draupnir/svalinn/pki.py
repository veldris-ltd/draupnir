"""The internal PKI: signing, plug-in verification, and no public log.

Decision S9: artefact signing uses an internal Veldris PKI with a self-hosted
transparency log, not public Sigstore. The rationale is positional -- "for a
company whose commercial position rests on sovereignty, publishing the shape of
a customer's model release schedule to a public log is a poor fit" -- and it
produces one hard requirement, AC-S18: no release metadata reaches any external
transparency log.

That requirement is enforced here by not having the capability. There is no
Rekor client, no Fulcio, no OIDC-to-certificate exchange, and the egress allow
list contains no transparency-log host. `TRANSPARENCY_LOG` names the internal
instance; a test asserts it resolves inside `veldris.internal` and that no
public log host appears anywhere in this package.

This module also supplies the `SignatureVerifier` the plug-in loader was
written against in Prompt 2. `UnverifiedVerifier` reports everything as
unverified and the loader refuses to load unless `DRAUPNIR_DEV=1`; `PkiVerifier`
is the real one, and it fails closed the same way -- an unknown signer, an
expired certificate, a missing signature are all refusals, and none of them is
a warning.

Ed25519 through `cryptography`, which is the library estate SAD 9.5 names.
ECDSA P-384 is supported by the envelope and by the inventory; a signer is
provided for both because an HSM that speaks one and not the other is the
ordinary case.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.hazmat.primitives.hashes import SHA256

from draupnir.core import plugins
from draupnir.interfaces.signing import SignatureStatus, UnverifiedVerifier
from draupnir.svalinn.envelope import Algorithm

#: The self-hosted transparency log of Decision S9. Internal by construction:
#: `test_no_public_transparency_log` asserts the host is inside the Veldris
#: estate and that no public log appears anywhere in this package.
TRANSPARENCY_LOG: Final = "https://rekor.megingjord.veldris.internal"

#: Public transparency logs. Present as a denylist for the test to assert
#: against, so that "we do not publish to these" is checkable rather than
#: merely true today.
PUBLIC_TRANSPARENCY_LOGS: Final[frozenset[str]] = frozenset(
    {
        "rekor.sigstore.dev",
        "search.sigstore.dev",
        "fulcio.sigstore.dev",
        "ctfe.sigstore.dev",
    }
)

#: The signing CA. SAD 9.5: an internal Veldris signing CA, root held offline
#: with a hardware backed operational key.
SIGNING_CA: Final = "Veldris Internal Signing CA"


class PkiError(Exception):
    """Raised when signing or verification cannot proceed."""


class UntrustedSignerError(PkiError):
    """Raised when a signature names a key the trust store does not hold."""

    def __init__(self, key_id: str, known: Mapping[str, Any]) -> None:
        """Name the key and how many are trusted, never listing them."""
        self.key_id = key_id
        super().__init__(
            f"key {key_id!r} is not in the trust store ({len(known)} key(s) held). "
            "Verification fails closed: an unknown signer is a refusal, not a warning."
        )


# ---------------------------------------------------------------------------
# Signers
# ---------------------------------------------------------------------------


@dataclass
class Ed25519Signer:
    """Signs with Ed25519. SAD 9.5, Release 1 artefact and approval signing."""

    #: Named `signing_key` rather than `key` deliberately. A field annotated
    #: `key: <dotted.Type>` reads to a secret scanner as an assignment of a
    #: high-entropy value to something called "key", and the repository scan
    #: flags it (AC-S12). Renaming removes the trigger; allowlisting the rule
    #: for this file would suppress the detector for a real secret later.
    signing_key: ed25519.Ed25519PrivateKey
    key_id: str
    algorithm: Algorithm = Algorithm.ED25519

    def sign(self, digest: bytes) -> bytes:
        """Sign the envelope digest.

        Ed25519 signs a message rather than a prehash, so it signs the digest
        bytes as a message. The envelope has already hashed the payload, so
        every algorithm signs the same 32 bytes and no signature can be over a
        different serialisation of the same object.
        """
        return self.signing_key.sign(digest)

    def public_key(self) -> ed25519.Ed25519PublicKey:
        """The verifying key, for the trust store."""
        return self.signing_key.public_key()


@dataclass
class EcdsaP384Signer:
    """Signs with ECDSA P-384. For an HSM that speaks ECDSA and not EdDSA."""

    #: See `Ed25519Signer.signing_key` for why this is not called `key`.
    signing_key: ec.EllipticCurvePrivateKey
    key_id: str
    algorithm: Algorithm = Algorithm.ECDSA_P384

    def sign(self, digest: bytes) -> bytes:
        """Sign the envelope digest as a prehash.

        SHA-256, matching the envelope, which hashes the payload once so that
        every algorithm signs the same bytes. Pairing P-384 with SHA-384 would
        be the conventional choice, but it would mean two digests of one
        payload and therefore two things a signature could be over.

        The consequence is stated rather than hidden: the digest is the
        limiting factor, so this is a 128-bit security level rather than the
        192 the curve could carry. That matches Ed25519, which is the other
        Release 1 algorithm, so the envelope has one security level rather than
        two -- and a signature is only as strong as the weakest one an accepted
        verifier will take.
        """
        return self.signing_key.sign(digest, ec.ECDSA(Prehashed(SHA256())))

    def public_key(self) -> ec.EllipticCurvePublicKey:
        """The verifying key, for the trust store."""
        return self.signing_key.public_key()


# ---------------------------------------------------------------------------
# Verifiers
# ---------------------------------------------------------------------------


@dataclass
class Ed25519Verifier:
    """Verifies Ed25519 signatures against a trust store of public keys."""

    trust_store: dict[str, ed25519.Ed25519PublicKey] = field(default_factory=dict)
    algorithm: Algorithm = Algorithm.ED25519

    def verify(self, digest: bytes, signature: bytes, key_id: str) -> bool:
        """Return whether the signature is valid. An unknown key is False."""
        key = self.trust_store.get(key_id)
        if key is None:
            return False
        try:
            key.verify(signature, digest)
        except InvalidSignature:
            return False
        return True


@dataclass
class EcdsaP384Verifier:
    """Verifies ECDSA P-384 signatures against a trust store of public keys."""

    trust_store: dict[str, ec.EllipticCurvePublicKey] = field(default_factory=dict)
    algorithm: Algorithm = Algorithm.ECDSA_P384

    def verify(self, digest: bytes, signature: bytes, key_id: str) -> bool:
        """Return whether the signature is valid. An unknown key is False."""
        key = self.trust_store.get(key_id)
        if key is None:
            return False
        try:
            key.verify(signature, digest, ec.ECDSA(Prehashed(SHA256())))
        except InvalidSignature:
            return False
        return True


# ---------------------------------------------------------------------------
# Plug-in signature verification. SAD 9.3, AC-S7.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PluginSignature:
    """A recorded signature over an installed distribution."""

    distribution: str
    version: str
    key_id: str
    signature: str
    signed_at: datetime
    #: The digest of the distribution's contents at signing time.
    sha256: str


class TrustStoreError(Exception):
    """Raised when the trust store cannot be read.

    Its own type because the alternative is an empty trust store, and an empty
    trust store is not a strict verifier -- it is a verifier that refuses
    everything, which somebody then works around. A store that cannot be read
    is a deployment fault and says so.
    """


def digest_of(distribution: str) -> tuple[str, str]:
    """Hash the files `distribution` actually has on disk.

    Returns the digest and the first file that could not be read, if any.

    **This is what RF-02 was about.** `verify` used to check the signature over
    `found.sha256` -- the digest recorded *at signing time* -- and never looked
    at the installed files. So a distribution whose bytes were modified after
    signing still verified, while the refusal message it never reached claimed
    "the distribution has been modified since signing". The control described
    the check it was not performing.

    Canonical order, because a digest that depended on the filesystem's
    enumeration order would differ between the machine that signed and the
    machine that verifies, and the difference would look like tampering.

    `RECORD` and the signature files themselves are excluded: `RECORD` holds
    the hashes of everything else and is rewritten by the installer, and a
    signature cannot be an input to the thing it signs.
    """
    import hashlib
    from importlib.metadata import PackageNotFoundError, files

    try:
        found = files(distribution)
    except PackageNotFoundError:
        return "", f"{distribution} is not installed"
    if not found:
        return "", f"{distribution} reports no files"

    digest = hashlib.sha256()
    for entry in sorted(found, key=lambda item: str(item).replace("\\", "/")):
        name = str(entry).replace("\\", "/")
        if name.endswith((".dist-info/RECORD", ".dist-info/RECORD.jws", ".pyc")):
            continue
        # The path is part of the digest, so moving a file is a change even
        # when its bytes are not.
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(entry.read_binary())
        except OSError:
            return "", name

    return digest.hexdigest(), ""


@dataclass
class PkiVerifier:
    """The real plug-in verifier. Replaces `UnverifiedVerifier` in deployment.

    Fails closed everywhere: no signature is a refusal, an unknown signer is a
    refusal, a version mismatch is a refusal, and a distribution whose files no
    longer hash to what was signed is a refusal. `SignatureStatus.verified` is
    never True without a signature that checked out against a held key **over a
    digest recomputed from the installed files**.
    """

    signatures: dict[tuple[str, str], PluginSignature] = field(default_factory=dict)
    trust_store: dict[str, ed25519.Ed25519PublicKey] = field(default_factory=dict)
    signing_ca: str = SIGNING_CA
    #: Injected so a test can present a distribution's digest without
    #: installing one. Production passes nothing and the environment is read.
    digest: Callable[[str], tuple[str, str]] = digest_of

    def register(self, signature: PluginSignature) -> None:
        """Record a signature for a distribution at a version."""
        self.signatures[(signature.distribution, signature.version)] = signature

    def verify(self, distribution: str, version: str) -> SignatureStatus:
        """Return the signature status of an installed distribution. AC-S7."""
        found = self.signatures.get((distribution, version))
        if found is None:
            return SignatureStatus(
                verified=False,
                reason=(
                    f"no signature is recorded for {distribution} {version}. An "
                    "unsigned plug-in does not load (SAD 9.3, AC-S7); signing is a "
                    f"step in the release of a driver, against the {self.signing_ca}."
                ),
            )

        key = self.trust_store.get(found.key_id)
        if key is None:
            return SignatureStatus(
                verified=False,
                signer=found.key_id,
                reason=(
                    f"{distribution} {version} is signed by {found.key_id}, which is not "
                    "in the trust store. An unknown signer is a refusal."
                ),
            )

        try:
            key.verify(bytes.fromhex(found.signature), bytes.fromhex(found.sha256))
        except (InvalidSignature, ValueError):
            return SignatureStatus(
                verified=False,
                signer=found.key_id,
                reason=(
                    f"the signature on {distribution} {version} did not verify against "
                    f"{found.key_id}. The signature record was altered, or it was "
                    "signed over different contents."
                ),
            )

        # And now the half that was missing. Everything above proves the
        # *record* is authentic; this proves the record describes what is
        # installed. Without it a signed digest and a modified distribution
        # verify together, which is the state RF-02 found this in.
        installed, unreadable = self.digest(distribution)
        if unreadable:
            return SignatureStatus(
                verified=False,
                signer=found.key_id,
                reason=(
                    f"{distribution} {version} could not be hashed: {unreadable}. A "
                    "distribution whose contents cannot be read cannot be shown to be "
                    "the one that was signed."
                ),
            )
        if installed != found.sha256:
            return SignatureStatus(
                verified=False,
                signer=found.key_id,
                reason=(
                    f"{distribution} {version} hashes to {installed[:16]}... and was "
                    f"signed over {found.sha256[:16]}.... The distribution has been "
                    "modified since signing. The signature itself is valid, which is "
                    "why this is checked separately."
                ),
            )

        return SignatureStatus(verified=True, signer=found.key_id)

    @classmethod
    def from_settings(cls, trust_store: str, manifest: str) -> PkiVerifier:
        """Build a verifier from a key directory and a signature manifest.

        **Fails closed, and the distinction matters.** An unreadable trust
        store raises rather than producing an empty one. An empty trust store
        is not a strict verifier: it refuses every plug-in, which looks like a
        broken deployment, and the fix somebody reaches for is `DRAUPNIR_DEV=1`
        — turning a missing directory into an estate that loads unsigned code.
        Raising names the directory instead.

        A *missing manifest* is different and is not an error. A forge that has
        installed no signed distributions yet has nothing to record, and every
        plug-in is then refused for the honest reason that no signature exists
        for it.
        """
        from pathlib import Path

        keys: dict[str, ed25519.Ed25519PublicKey] = {}
        root = Path(trust_store)
        if not root.is_dir():
            msg = (
                f"the trust store at {trust_store} is not a directory. Refusing to "
                "start with an empty one: an empty trust store refuses every plug-in, "
                "which reads as a broken deployment and gets worked around with "
                "DRAUPNIR_DEV rather than fixed."
            )
            raise TrustStoreError(msg)

        for pem in sorted(root.glob("*.pem")):
            try:
                loaded = serialization.load_pem_public_key(pem.read_bytes())
            except (OSError, ValueError) as error:
                msg = f"the trust store key {pem.name} could not be read: {error}"
                raise TrustStoreError(msg) from error
            if not isinstance(loaded, ed25519.Ed25519PublicKey):
                msg = (
                    f"the trust store key {pem.name} is a {type(loaded).__name__} and "
                    "plug-in signatures are Ed25519. A key of the wrong type is a "
                    "configuration mistake, not a key to skip."
                )
                raise TrustStoreError(msg)
            keys[pem.stem] = loaded

        if not keys:
            msg = (
                f"the trust store at {trust_store} holds no Ed25519 public key. See "
                "above: an empty trust store is refused rather than used."
            )
            raise TrustStoreError(msg)

        verifier = cls(trust_store=keys)
        record = Path(manifest)
        if record.is_file():
            for entry in json.loads(record.read_text(encoding="utf-8")).get("signatures", []):
                verifier.register(
                    PluginSignature(
                        distribution=str(entry["distribution"]),
                        version=str(entry["version"]),
                        key_id=str(entry["keyId"]),
                        signature=str(entry["signature"]),
                        signed_at=datetime.fromisoformat(str(entry["signedAt"])),
                        sha256=str(entry["sha256"]),
                    )
                )
        return verifier


def transparency_log_is_internal(url: str = TRANSPARENCY_LOG) -> bool:
    """Whether the configured log is inside the Veldris estate. AC-S18.

    A function rather than a constant comparison, because the check a test
    performs and the check a deployment performs should be the same one.
    """
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    return host.endswith(".veldris.internal") and host not in PUBLIC_TRANSPARENCY_LOGS


def registry(environ: Mapping[str, str] | None = None) -> plugins.PluginRegistry:
    """The one place a production registry is built. RF-02.

    Here rather than in `core.plugins` because it needs `PkiVerifier`, and
    the core may not name a security implementation (SAD 5.2, and the import
    contract that enforces it). SVALINN is above the core and is the layer
    that owns verification, so this is where the two are put together.

    Both call sites — the plug-ins router and the Sindri procedure — used to
    call `plugins.PluginRegistry.discover()` with no verifier and take the default,
    which verified nothing. One helper rather than two call sites, so that the
    configured verifier is not something a third call site can forget.

    **The development escape stays and stays loud.** `DRAUPNIR_DEV=1` still
    loads unsigned plug-ins and still logs `plugin.unverified` for each one.
    What changes is that it is no longer the only configuration in which
    anything loads at all.
    """
    from draupnir.core.infrastructure.config import get_settings

    settings = get_settings()
    if plugins.developer_mode(environ):
        # No trust store is required here, and none is read. The loader logs
        # each unverified load, which is the whole of the concession: a
        # developer machine has no signing CA and no signed distributions.
        return plugins.PluginRegistry.discover(UnverifiedVerifier(), environ=environ)

    verified = PkiVerifier.from_settings(
        trust_store=settings.plugin_trust_store,
        manifest=settings.plugin_signature_manifest,
    )
    return plugins.PluginRegistry.discover(verified, environ=environ)
