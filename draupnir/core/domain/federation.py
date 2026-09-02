"""The federation wire vocabulary: hashes and policy, and nothing else. Ever.

Threat T13 is a compromised federation registry affecting every forge, and the
mitigation is a limit on what MEGINGJORD is able to hold: "MEGINGJORD holds
hashes and policy, never corpora or weights. Forge ledgers are independently
verifiable without it."

AC-S14 tests it by inspection of the wire format: "No corpus or weight content
is present in any federation payload, verified by inspection of the wire
format."

A rule enforced by inspection is a rule that decays, so it is enforced by
construction instead. Every federation payload is built through `sealed`, which
walks the finished structure and refuses anything that is not a hash, an
identifier, a timestamp, a number or a short piece of declared metadata. A
1 KB base64 string is refused. A bytes value is refused. A string carrying a
tensor header marker is refused.

The check is on the *serialised* structure rather than on declared types,
because the way corpus content would actually arrive is embedded in a field
declared as a string -- a "label", an "excerpt", a "reason" -- and a check on
declared types would pass it.

This lives in the core rather than in MEGINGJORD because both tiers speak it.
GULLINBURSTI builds these payloads and MEGINGJORD consumes them, and the two
are independent siblings that cannot import each other; a wire format defined
in one and redefined in the other is a wire format with two definitions, which
is the ordinary way a boundary control develops a gap.

It is also deliberately paranoid about a component Veldris controls. Section
16A accepts custody concentration at Site 0; what makes that acceptable is that
the concentrated component cannot hold the thing whose disclosure would matter.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from draupnir.core.domain.ledger import ChainHead

SCHEMA: Final = "draupnir/federation/v1"

#: The longest a string field may be. Long enough for a URI, a policy version
#: or a site name; far too short for a document, a log excerpt or a base64
#: blob. A federation payload has no legitimate use for more.
MAX_STRING = 512

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
#: Base64-ish runs. A field carrying one is carrying content, whatever it is.
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{64,}={0,2}")
#: The safetensors header key, and the GGUF and pickle magics, as text.
_WEIGHT_MARKERS: Final[tuple[str, ...]] = ("__metadata__", "GGUF", "PK\x03\x04", "\x80\x02")


class FederationPayloadError(Exception):
    """Raised when a payload may not cross the federation boundary."""


class ContentLeakError(FederationPayloadError):
    """Raised when a payload carries something that is not a hash or a name.

    The exception AC-S14 exists for. Names the field path rather than the
    value: quoting the leaked content into an exception message would put it in
    a log, which is where it was going anyway.
    """

    def __init__(self, path: str, why: str) -> None:
        """Name where in the payload, and why it was refused."""
        self.path = path
        self.why = why
        super().__init__(
            f"the federation payload field {path!r} may not cross the boundary: {why}. "
            "MEGINGJORD holds hashes and policy, never corpora or weights (threat T13, "
            "AC-S14). Corpora and weights never leave their site."
        )


class FederationRecordError(Exception):
    """Raised when a federation record cannot be built."""


def _inspect(value: Any, path: str) -> None:
    """Walk one value, refusing anything that is not a hash, name or number."""
    if value is None or isinstance(value, (bool, int, float)):
        return

    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ContentLeakError(path, "it is raw bytes")

    if isinstance(value, str):
        if len(value) > MAX_STRING:
            raise ContentLeakError(
                path, f"it is {len(value)} characters, over the {MAX_STRING} limit"
            )
        if _BASE64_RUN.search(value) and not _HEX64.match(value):
            raise ContentLeakError(path, "it contains an encoded run, which is content")
        for marker in _WEIGHT_MARKERS:
            if marker in value:
                raise ContentLeakError(path, f"it contains the marker {marker!r}")
        return

    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContentLeakError(f"{path}.{key!r}", "the key is not a name")
            _inspect(item, f"{path}.{key}")
        return

    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _inspect(item, f"{path}[{index}]")
        return

    raise ContentLeakError(
        path, f"it is a {type(value).__name__}, which the format has no shape for"
    )


def inspect(payload: Mapping[str, Any], *, name: str = "payload") -> None:
    """Raise if anything in `payload` is corpus or weight content. AC-S14."""
    _inspect(dict(payload), name)


def sealed(payload: Mapping[str, Any], *, name: str = "payload") -> dict[str, Any]:
    """Return the payload, having refused it if it carries content.

    Everything that crosses the federation boundary goes through here. Making
    it the constructor rather than a checker means a payload that skipped the
    check is a payload that was never built.
    """
    inspect(payload, name=name)
    return dict(payload)


def is_hash(value: str) -> bool:
    """Whether a string is a SHA-256 in the form the federation uses."""
    return bool(_HEX64.match(value))


def to_wire(payload: Mapping[str, Any]) -> bytes:
    """Serialise a sealed payload for transmission.

    Seals first. There is no path that serialises an unchecked payload, which
    is what makes AC-S14 a property of the code rather than of the review.
    """
    return json.dumps(
        sealed(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class Inspection:
    """What an inspection of a wire capture found. For the evidence pack."""

    payloads: int
    fields: int
    refused: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        """Whether every payload carried only hashes, names and numbers."""
        return not self.refused

    def as_payload(self) -> dict[str, Any]:
        """The report, for AC-S14's evidence."""
        return {
            "payloadsInspected": self.payloads,
            "fieldsInspected": self.fields,
            "clean": self.clean,
            "refused": list(self.refused),
        }


def _count(value: Any) -> int:
    """How many leaf fields a structure has."""
    if isinstance(value, Mapping):
        return sum(_count(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_count(item) for item in value)
    return 1


def inspect_capture(payloads: Sequence[Mapping[str, Any]]) -> Inspection:
    """Inspect a captured set of federation payloads. AC-S14's verification.

    Reports rather than raising, because this runs over a capture after the
    fact and the useful output is every problem rather than the first one.
    """
    refused: list[str] = []
    fields = 0

    for index, payload in enumerate(payloads):
        fields += _count(payload)
        try:
            inspect(payload, name=f"payload[{index}]")
        except ContentLeakError as leak:
            refused.append(f"{leak.path}: {leak.why}")

    return Inspection(payloads=len(payloads), fields=fields, refused=tuple(refused))


def hashes_in(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Every SHA-256 in a payload. What the federation actually carries."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str) and is_hash(value):
            found.append(value)
        elif isinstance(value, Mapping):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(payload)
    return tuple(sorted(set(found)))


def declared_fields(payloads: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """Every field name that appears across payloads, for documentation."""
    names: set[str] = set()

    def walk(value: Any, prefix: str = "") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                names.add(f"{prefix}{key}")
                walk(item, f"{prefix}{key}.")
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item, prefix)

    for payload in payloads:
        walk(payload)
    return tuple(sorted(names))


# ---------------------------------------------------------------------------
# What crosses the boundary. GULLINBURSTI builds these; MEGINGJORD holds them.
# ---------------------------------------------------------------------------


class AnchorOutcome(StrEnum):
    """What happened to a submitted head."""

    COUNTERSIGNED = "countersigned"
    #: Already held, identically. Resubmission after a partition is normal.
    DUPLICATE = "duplicate"
    DIVERGED = "diverged"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class AnchorSubmission:
    """A chain head offered for anchoring, with its verification envelope.

    The head itself is `ledger.ChainHead` -- site, sequence and entry hash,
    which is exactly what SAD 11A.3 says travels. It is reused rather than
    redefined: two classes for one concept is how a signature ends up covering
    different bytes at each end.

    What this adds is the anchoring envelope: the previous hash, so continuity
    is checkable from hashes alone; when it was submitted; and the site's
    signature, produced by `svalinn.signing.sign_chain_head` over the head's
    own canonical bytes.
    """

    head: ChainHead
    #: The head this one follows, so continuity is checkable from hashes alone.
    previous_hash: str | None
    submitted_at: datetime
    #: The site's signature over `head.signing_payload()`. An unsigned
    #: submission is not a claim about a chain, it is a packet.
    signature: str = ""
    key_id: str = ""

    def __post_init__(self) -> None:
        """Refuse a submission that cannot be reasoned about."""
        if self.head.seq < 1:
            msg = f"a ledger sequence starts at 1, not {self.head.seq}"
            raise FederationRecordError(msg)
        if not is_hash(self.head.entry_hash):
            msg = f"{self.head.entry_hash!r} is not a SHA-256; an anchor is a hash"
            raise FederationRecordError(msg)
        if self.previous_hash is not None and not is_hash(self.previous_hash):
            msg = f"{self.previous_hash!r} is not a SHA-256"
            raise FederationRecordError(msg)
        if self.submitted_at.tzinfo is None:
            msg = "anchor timestamps carry an explicit offset (SAD 11E.2)"
            raise FederationRecordError(msg)

    @property
    def site_id(self) -> str:
        """The site whose chain this is."""
        return self.head.site_id

    @property
    def seq(self) -> int:
        """The ledger sequence being anchored."""
        return self.head.seq

    @property
    def entry_hash(self) -> str:
        """The entry hash at that sequence."""
        return self.head.entry_hash

    def signing_payload(self) -> bytes:
        """The exact bytes the site's signature covers.

        The ledger's, not a re-rendering. A verifier years later reconstructs
        them from the three values without reproducing anyone's formatting.
        """
        return self.head.signing_payload()

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, sealed."""
        return sealed(
            {
                "siteId": self.site_id,
                "seq": self.seq,
                "entryHash": self.entry_hash,
                "previousHash": self.previous_hash,
                "submittedAt": self.submitted_at.isoformat(),
                "signature": self.signature,
                "keyId": self.key_id,
            },
            name="anchorSubmission",
        )


@dataclass(frozen=True, slots=True)
class Anchor:
    """A countersigned head. What the registry holds for a site."""

    head: AnchorSubmission
    countersigned_at: datetime
    countersignature: str
    outcome: AnchorOutcome = AnchorOutcome.COUNTERSIGNED

    @property
    def site_id(self) -> str:
        """The site this anchor belongs to."""
        return self.head.site_id

    @property
    def seq(self) -> int:
        """The sequence anchored."""
        return self.head.seq

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, sealed."""
        return sealed(
            {
                "head": self.head.as_payload(),
                "countersignedAt": self.countersigned_at.isoformat(),
                "countersignature": self.countersignature,
                "outcome": str(self.outcome),
            },
            name="anchor",
        )


@dataclass(frozen=True, slots=True)
class Receipt:
    """What the registry returns for a submitted head."""

    outcome: AnchorOutcome
    anchor: Anchor | None
    reason: str

    @property
    def accepted(self) -> bool:
        """Whether the head was countersigned or already held."""
        return self.outcome in {AnchorOutcome.COUNTERSIGNED, AnchorOutcome.DUPLICATE}

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, sealed."""
        return sealed(
            {
                "outcome": str(self.outcome),
                "accepted": self.accepted,
                "reason": self.reason,
                "anchor": self.anchor.as_payload() if self.anchor else None,
            },
            name="receipt",
        )


@dataclass(frozen=True, slots=True)
class Site:
    """One forge in the Forge Matrix. SAD 11A.0: a site, never a node."""

    site_id: str
    #: Ordinal. Sindri is Site 0, the first and currently only member.
    ordinal: int
    #: `<forge>.veldris.internal`. The host part belongs to an appliance.
    fqdn: str
    #: The key this site signs chain heads with. The trust anchor for anchoring.
    signing_key_id: str
    registered_at: datetime
    #: Jurisdictions whose residency-constrained corpora may be held here.
    permitted_residency: tuple[str, ...] = ()
    active: bool = True

    def __post_init__(self) -> None:
        """Refuse a site that cannot anchor."""
        if not self.signing_key_id:
            msg = (
                f"site {self.site_id} declares no signing key. A site that cannot sign "
                "a chain head cannot anchor, and a site that cannot anchor cannot "
                "release (SAD 11A.3)."
            )
            raise FederationRecordError(msg)
        if self.registered_at.tzinfo is None:
            msg = "registry timestamps carry an explicit offset (SAD 11E.2)"
            raise FederationRecordError(msg)

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, sealed."""
        return sealed(
            {
                "siteId": self.site_id,
                "ordinal": self.ordinal,
                "fqdn": self.fqdn,
                "signingKeyId": self.signing_key_id,
                "registeredAt": self.registered_at.isoformat(),
                "permittedResidency": list(self.permitted_residency),
                "active": self.active,
            },
            name="site",
        )


@dataclass(frozen=True, slots=True)
class PolicyBundle:
    """A policy at a version, with the digest a forge verifies it against."""

    name: str
    version: str
    #: The digest of the policy document. The document is fetched separately;
    #: what the federation distributes is the version and the hash.
    digest: str
    issued_at: datetime
    #: The registry's signature, so a forge can tell a policy from something
    #: that arrived over the same connection.
    signature: str = ""

    def __post_init__(self) -> None:
        """Refuse a bundle a forge could not verify."""
        if not is_hash(self.digest):
            msg = f"policy {self.name}/{self.version} declares {self.digest!r}, not a SHA-256"
            raise FederationRecordError(msg)
        if self.issued_at.tzinfo is None:
            msg = "policy timestamps carry an explicit offset (SAD 11E.2)"
            raise FederationRecordError(msg)

    @property
    def key(self) -> str:
        """`name/version`, which is what a decision records."""
        return f"{self.name}/{self.version}"

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, sealed."""
        return sealed(
            {
                "name": self.name,
                "version": self.version,
                "digest": self.digest,
                "issuedAt": self.issued_at.isoformat(),
                "signature": self.signature,
            },
            name="policyBundle",
        )


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    """Release metadata pushed by a site. Hashes and names, never weights."""

    site_id: str
    model: str
    artefact_sha256: str
    released_at: datetime
    #: The ledger sequence the release was recorded at, so the registry can
    #: check it against a countersigned anchor.
    seq: int
    #: The digests of the release package artefacts, not the artefacts.
    manifest: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse a record that names something other than a hash."""
        if not is_hash(self.artefact_sha256):
            msg = f"{self.artefact_sha256!r} is not a SHA-256"
            raise FederationRecordError(msg)
        for name, digest in self.manifest.items():
            if not is_hash(digest):
                msg = f"the manifest entry {name!r} is {digest!r}, not a SHA-256"
                raise FederationRecordError(msg)

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, sealed."""
        return sealed(
            {
                "siteId": self.site_id,
                "model": self.model,
                "artefactSha256": self.artefact_sha256,
                "releasedAt": self.released_at.isoformat(),
                "seq": self.seq,
                "manifest": dict(sorted(self.manifest.items())),
            },
            name="releaseRecord",
        )


@dataclass(frozen=True, slots=True)
class Capacity:
    """A site's reported capacity and health. Numbers only."""

    site_id: str
    reported_at: datetime
    appliances_total: int
    appliances_available: int
    queued_runs: int = 0
    running_runs: int = 0

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, sealed."""
        return sealed(
            {
                "siteId": self.site_id,
                "reportedAt": self.reported_at.isoformat(),
                "appliancesTotal": self.appliances_total,
                "appliancesAvailable": self.appliances_available,
                "queuedRuns": self.queued_runs,
                "runningRuns": self.running_runs,
            },
            name="capacity",
        )
