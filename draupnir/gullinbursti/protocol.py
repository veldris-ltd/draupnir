"""MEGINGJORD's protocol, spoken for real. VLD-SAD-MEGINGJORD-001 section 33.

`federation.RemoteRegistry` carried bytes to a MEGINGJORD that did not exist
yet, in a shape of its own: a flat body posted to `/federation/v1/anchors`, the
signature in a header, a site called "sindri". The MEGINGJORD that was built
speaks the shared protocol (`veldris-shared-protocol`, SAD 33.1): a signed
`AnchorHead` document and its envelope posted to `/v1/sites/{uuid}/anchors`, a
signed receipt back, and a release registered at `/v1/registry/models` with
proof it was anchored. This module is GULLINBURSTI's half of that contract, and
it uses the package's types rather than restating them, which is the only way
two repositories keep agreeing about bytes (SAD 33.1, Decision M4).

**Configured by the bundle and nothing else.** MEGINGJORD's installer writes
every forge a folder, and `enrol.sh` adds the forge's keys to it at the forge
(`megingjord.toml`, `site.crt`, `site-mtls.key`, `site-signing.key`,
`enrol-request.json`, the server's CA and the published keys). `Bundle.load`
reads exactly that. The site's identity is MEGINGJORD's: a UUID and a key
identifier MEGINGJORD registered, not names this forge chose.

**A receipt is verified before it is believed.** It is checked offline against
the keys MEGINGJORD published in the bundle (the one it names, so a key
rotation does not interrupt anchoring), through the package's own verifier,
and must name this site, this sequence and this hash (AC-R1). A receipt that does not verify
is not an anchor, however the server answered.

**A failure is a finding, never an exception that reaches a run** (Decision
S8). Every answer becomes a `Receipt` with an outcome and a reason, and the
reason carries MEGINGJORD's problem type, so an operator can tell a partition
from a quarantine from a fork.
"""

from __future__ import annotations

import json
import tomllib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from shared_protocol import (
    AGENT_TOKEN,
    AnchorHead,
    Ed25519Signer,
    Keyring,
    ModelRelease,
    ProtocolError,
    SignedDocument,
    load_keyring,
    parse_digest,
    seal,
    verify_receipt,
)

from draupnir import __version__
from draupnir.core.domain.federation import (
    Anchor,
    AnchorOutcome,
    AnchorSubmission,
    Receipt,
    inspect,
)

#: The folder's one configuration file, as MEGINGJORD's installer names it.
BUNDLE_FILE: Final = "megingjord.toml"

#: What every anchor says it was sent by (SAD 12.1 `agent`). SAD 33.2 asks for
#: the shared-protocol version in the same field, so a compatibility problem is
#: diagnosable from MEGINGJORD's ledger alone.
AGENT: Final = f"gullinbursti/{__version__} {AGENT_TOKEN}"

#: MEGINGJORD's problem types, by the slug the end of their URI carries.
#: Named here because the anchor state an operator sees depends on which one.
NOT_ANCHORABLE: Final = "site-not-anchorable"
FORK_DETECTED: Final = "fork-detected"
SITE_CANNOT_RELEASE: Final = "site-cannot-release"
#: Not a problem type: the 202 MEGINGJORD answers while its signing key is
#: away. Accepted and queued there (its Decision M8), not yet countersigned.
PENDING: Final = "pending"
#: Not a problem type either: the link itself, before MEGINGJORD said anything.
UNREACHABLE: Final = "unreachable"

_NAMESPACE: Final = uuid.UUID("6f1f6c39-4b0e-4b1a-9d43-6d65676a6f72")


class BundleError(Exception):
    """The bundle is missing, incomplete or unreadable. Says what to do."""


class Response(Protocol):
    """The part of an HTTP response this needs."""

    status_code: int

    def json(self) -> Any:
        """The decoded body."""
        ...


class Client(Protocol):
    """The part of an HTTP client this needs. `BrokeredClient` in service."""

    def get(self, url: str, *, params: Mapping[str, str] | None = ...) -> Response:
        """Fetch one resource."""
        ...

    def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any] | None = ...,
        headers: Mapping[str, str] | None = ...,
    ) -> Response:
        """Submit one document."""
        ...


@dataclass(frozen=True, slots=True)
class Bundle:
    """The folder MEGINGJORD's installer wrote for this forge, with its keys."""

    folder: Path
    site_name: str
    site_id: uuid.UUID
    #: `https://megingjord.veldris.internal`. The name resolves while the
    #: WireGuard tunnel is up, or from the hosts line the bundle's README adds.
    endpoint: str
    server_ca: Path
    federation_root: Path
    client_certificate: Path
    client_key: Path
    signing_key: Path
    #: The identifier MEGINGJORD registered the signing key under, from the
    #: request `enrol.sh` wrote. Not derived: MEGINGJORD chose it.
    key_id: str
    anchor_key: Path

    @classmethod
    def load(cls, folder: Path) -> Bundle:
        """Read the bundle, or say exactly what is missing and what to do."""
        config_path = folder / BUNDLE_FILE
        if not config_path.is_file():
            msg = (
                f"{folder} holds no {BUNDLE_FILE}. Copy the folder MEGINGJORD's installer "
                "wrote for this site (/etc/megingjord/sites/<name>/ on the MEGINGJORD "
                "server) here, then run its enrol.sh."
            )
            raise BundleError(msg)
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            site_id = uuid.UUID(str(config["site_id"]))
            names = {
                key: folder / str(config[key])
                for key in (
                    "server_ca",
                    "federation_root",
                    "client_certificate",
                    "client_key",
                    "signing_key",
                    "anchor_key",
                )
            }
            site_name = str(config["site_name"])
            endpoint = str(config["endpoint"]).rstrip("/")
        except (KeyError, ValueError, tomllib.TOMLDecodeError) as error:
            msg = (
                f"{config_path} is not a MEGINGJORD bundle ({type(error).__name__}: "
                f"{error}). Copy it again from the MEGINGJORD server."
            )
            raise BundleError(msg) from error

        missing = [path.name for path in names.values() if not path.is_file()]
        request = folder / "enrol-request.json"
        if not request.is_file():
            missing.append(request.name)
        if missing:
            msg = (
                f"the bundle in {folder} is missing {', '.join(sorted(missing))}. The keys "
                "and enrol-request.json are made by running enrol.sh in that folder; "
                "site.crt is the certificate the MEGINGJORD administrator sends back."
            )
            raise BundleError(msg)
        try:
            key_id = str(json.loads(request.read_text(encoding="utf-8"))["signing_key"]["key_id"])
        except (KeyError, TypeError, ValueError) as error:
            msg = f"{request} names no signing key; run enrol.sh again in a fresh copy."
            raise BundleError(msg) from error

        return cls(
            folder=folder,
            site_name=site_name,
            site_id=site_id,
            endpoint=endpoint,
            key_id=key_id,
            **names,
        )

    def private_signing_key(self) -> Ed25519PrivateKey:
        """The forge's head-signing key. It never leaves this machine."""
        key = load_pem_private_key(self.signing_key.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            msg = f"{self.signing_key} is not an Ed25519 key; enrol.sh makes one that is."
            raise BundleError(msg)
        return key

    def anchor_keyring(self) -> Keyring:
        """Every key MEGINGJORD has published to this forge, as one keyring.

        Not only the anchor key `megingjord.toml` names. MEGINGJORD rotates its
        operational keys yearly without a ceremony (its SAD 14.2, 31.4): it
        publishes the next set beside the current one before it signs with
        it, and its bundle carries every key it has published. A receipt names
        its key, and the package's verifier uses that key and only under the
        anchor purpose, so receipts under the old key and the new both verify
        through the changeover, and a key MEGINGJORD never published still
        does not.
        """
        files = {self.anchor_key, *self.anchor_key.parent.glob("meg-*.json")}
        return load_keyring(*(path.read_bytes() for path in sorted(files)))


@dataclass(frozen=True, slots=True)
class RemoteHead:
    """What MEGINGJORD holds for this site: its state and anchored head."""

    state: str
    seq: int | None
    #: Bare hex, as this forge's ledger writes it.
    entry_hash: str | None


@dataclass(frozen=True, slots=True)
class Registration:
    """MEGINGJORD registered the release. 201 new, 200 the same record again."""

    model_id: str
    receipt_id: str
    replay: bool
    #: The estate posture MEGINGJORD stamped onto the registration (AC-I1i).
    estate_posture: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Refusal:
    """MEGINGJORD said no, or could not be asked. Its words, kept."""

    status: int
    problem: str
    detail: str

    @property
    def reason(self) -> str:
        """One sentence for an operator: who refused, and why."""
        if self.problem == UNREACHABLE:
            return self.detail
        return f"MEGINGJORD refused ({self.problem}): {self.detail}"


@dataclass
class Link:
    """GULLINBURSTI's link to MEGINGJORD. Satisfies `agent.Countersigner`."""

    bundle: Bundle
    client: Client = field(repr=False)
    signer: Ed25519Signer = field(repr=False)
    keyring: Keyring = field(repr=False)

    @classmethod
    def from_bundle(cls, bundle: Bundle, client: Client) -> Link:
        """A link speaking for the site the bundle names."""
        return cls(
            bundle=bundle,
            client=client,
            signer=Ed25519Signer(bundle.key_id, bundle.private_signing_key()),
            keyring=bundle.anchor_keyring(),
        )

    @property
    def site_id(self) -> uuid.UUID:
        """This forge, as MEGINGJORD knows it."""
        return self.bundle.site_id

    # -- reads -------------------------------------------------------------

    def head(self) -> RemoteHead | Refusal:
        """MEGINGJORD's record of this site: state and last anchored head."""
        answer = self._call("GET", f"/v1/sites/{self.site_id}/head")
        if isinstance(answer, Refusal):
            return answer
        body = answer.json()
        raw = body.get("head_hash")
        return RemoteHead(
            state=str(body.get("state") or ""),
            seq=body.get("head_seq"),
            entry_hash=str(parse_digest(raw, field="head_hash")).split(":", 1)[1] if raw else None,
        )

    # -- anchoring ---------------------------------------------------------

    def countersign(
        self, head: AnchorSubmission, *, at: datetime, countersignature: str
    ) -> Receipt:
        """Submit one head and read back what MEGINGJORD said. SAD 12."""
        del countersignature  # MEGINGJORD produces its own, as a signed receipt.
        document = AnchorHead(
            site_id=self.site_id,
            seq=head.head.seq,
            entry_hash=parse_digest(f"sha256:{head.head.entry_hash}"),
            prev_anchor_hash=(
                parse_digest(f"sha256:{head.previous_hash}") if head.previous_hash else None
            ),
            issued_at=at.astimezone(UTC).replace(microsecond=0),
            agent=AGENT,
        )
        answer = self._submit(
            f"/v1/sites/{self.site_id}/anchors",
            document.to_wire(),
            seal(document, [self.signer]).to_wire(),
            key=f"anchor/{head.head.seq}/{head.head.entry_hash}",
        )
        if isinstance(answer, Refusal):
            outcome = (
                AnchorOutcome.DIVERGED
                if answer.problem == FORK_DETECTED
                else AnchorOutcome.REJECTED
            )
            return Receipt(outcome, None, answer.reason, problem=answer.problem)

        if answer.status_code == 202:  # accepted, not yet countersigned
            return Receipt(
                AnchorOutcome.REJECTED,
                None,
                (
                    f"MEGINGJORD accepted sequence {head.head.seq} and has not countersigned "
                    "it yet: its signing key is unavailable, so it is queued there and "
                    "countersigned in order when the key returns."
                ),
                problem=PENDING,
            )
        return self._receipt(answer, head)

    def _receipt(self, answer: Response, head: AnchorSubmission) -> Receipt:
        """A receipt, believed only once it verifies offline (AC-R1)."""
        try:
            signed = SignedDocument.from_json(json.dumps(answer.json()))
            receipt = verify_receipt(signed, self.keyring).receipt
        except (ProtocolError, ValueError, TypeError) as error:
            return Receipt(
                AnchorOutcome.REJECTED,
                None,
                (
                    f"MEGINGJORD answered {answer.status_code} for sequence {head.head.seq} "
                    f"with a receipt that does not verify under the anchor key ({error}). "
                    "An anchor nobody can verify is worse than no anchor."
                ),
                problem="receipt-invalid",
            )
        expected = f"sha256:{head.head.entry_hash}"
        if (
            receipt.site.id != self.site_id
            or receipt.seq != head.head.seq
            or str(receipt.entry_hash) != expected
        ):
            return Receipt(
                AnchorOutcome.REJECTED,
                None,
                (
                    f"MEGINGJORD's receipt for sequence {head.head.seq} names "
                    f"{receipt.site.id} seq {receipt.seq} {receipt.entry_hash}; it is not a "
                    "receipt for this head."
                ),
                problem="receipt-invalid",
            )
        outcome = (
            AnchorOutcome.DUPLICATE if answer.status_code == 200 else AnchorOutcome.COUNTERSIGNED
        )
        return Receipt(
            outcome,
            Anchor(
                head=head,
                countersigned_at=receipt.anchored_at,
                # The signed receipt itself, compact: what a purchaser verifies
                # offline with the published key and nothing of MEGINGJORD's.
                countersignature=json.dumps(
                    signed.to_wire(), separators=(",", ":"), sort_keys=True
                ),
                outcome=outcome,
            ),
            "",
        )

    # -- releases ----------------------------------------------------------

    def register(self, release: ModelRelease) -> Registration | Refusal:
        """Register a release with proof it was anchored. SAD 13.1, AC-X5."""
        answer = self._submit(
            "/v1/registry/models",
            release.to_wire(),
            seal(release, [self.signer]).to_wire(),
            key=f"release/{release.manifest_sha256}",
        )
        if isinstance(answer, Refusal):
            return answer
        body = answer.json()
        return Registration(
            model_id=str(body["model_id"]),
            receipt_id=str(body["receipt_id"]),
            replay=bool(body.get("replay")),
            estate_posture=dict(body.get("estate_posture") or {}),
        )

    # -- the wire ----------------------------------------------------------

    def _submit(
        self, path: str, document: Mapping[str, Any], envelope: Mapping[str, Any], *, key: str
    ) -> Response | Refusal:
        """Post a document and its envelope, inspected first (AC-S14)."""
        inspect(_inspectable(document), name=path)
        # The same request after a timeout is the same key, so a retry is a
        # replay MEGINGJORD answers from its record, never a second anchor.
        idempotency = uuid.uuid5(_NAMESPACE, f"{self.site_id}/{key}")
        return self._call(
            "POST",
            path,
            json={"document": dict(document), "envelope": dict(envelope)},
            headers={"Idempotency-Key": str(idempotency)},
        )

    def _call(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response | Refusal:
        """One request. Anything that is not 2xx comes back as a `Refusal`."""
        url = f"{self.bundle.endpoint}{path}"
        try:
            if method == "GET":
                answer = self.client.get(url)
            else:
                answer = self.client.post(url, json=json, headers=headers)
        except Exception as error:  # every one means the link is down
            return Refusal(
                0,
                UNREACHABLE,
                f"{self.bundle.endpoint} could not be reached ({type(error).__name__}); "
                "the federation link is down.",
            )
        if 200 <= answer.status_code < 300:
            return answer
        return _refusal(answer)


def _refusal(answer: Response) -> Refusal:
    """MEGINGJORD's RFC 9457 problem, by slug and detail."""
    try:
        body = answer.json()
    except Exception:  # a body that is not JSON is still an answer
        body = {}
    if not isinstance(body, Mapping):
        body = {}
    problem = str(body.get("type") or "").rstrip("/").rsplit("/", 1)[-1] or str(answer.status_code)
    detail = str(body.get("detail") or body.get("title") or f"HTTP {answer.status_code}")
    return Refusal(answer.status_code, problem, detail)


def _inspectable(value: Any) -> Any:
    """The document as the federation inspection should read it.

    Each `sha256:<hex>` digest is reduced to its hex, and each address is split
    into its path segments. The federation inspection admits a hash and refuses an encoded run; a
    digest with its algorithm prefixed is a hash all the same, and inspecting
    it bare is what keeps the check strict for every other string.
    """
    if isinstance(value, str) and value.startswith("sha256:"):
        return value.split(":", 1)[1]
    if isinstance(value, str) and "://" in value:
        # An address, inspected a segment at a time: a release's documents are
        # addressed by its digest, which is a hash in its own segment, and
        # anything else that runs long in a segment is still refused.
        return value.split("/")
    if isinstance(value, Mapping):
        return {key: _inspectable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_inspectable(item) for item in value]
    return value


__all__ = [
    "AGENT",
    "BUNDLE_FILE",
    "FORK_DETECTED",
    "NOT_ANCHORABLE",
    "PENDING",
    "SITE_CANNOT_RELEASE",
    "UNREACHABLE",
    "Bundle",
    "BundleError",
    "Client",
    "Link",
    "Refusal",
    "Registration",
    "RemoteHead",
    "Response",
]
