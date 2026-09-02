"""The cryptographic inventory, produced as a build artefact.

AC-S16: "The cryptographic inventory lists every algorithm, key length and
module in use, and each entry maps to NCSC guidance or an ISO/IEC standard."

SAD 9.5 explains why this document is the deliverable rather than a
certificate. NCSC operates guidance, not a validation scheme comparable to
CMVP: "There is no certificate to obtain. The claim Veldris can make and
evidence is conformance to NCSC recommended algorithms and configurations, and
use of ISO/IEC 19790 conformant modules where a conformant build is available,
demonstrated by configuration evidence and a documented cryptographic
inventory."

So the inventory is the evidence, and it is generated from the constants the
system actually uses rather than transcribed. An inventory maintained by hand
is a description of what somebody believed the system did at the time they last
looked, which is precisely the failure Decision S11 identifies for compliance
documents and it applies here for the same reason.

Every entry carries a reference. An inventory row without one is a row that
cannot be assessed, and `validate` refuses to emit an inventory containing one.
"""

from __future__ import annotations

import json
import platform
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from draupnir.svalinn.envelope import SUPPORTED, Algorithm

SCHEMA: Final = "draupnir/crypto-inventory/v1"


class InventoryError(Exception):
    """Raised when the inventory cannot be produced or is incomplete."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One algorithm in use, and the guidance it maps to."""

    purpose: str
    algorithm: str
    #: Bits. `None` where the concept does not apply, e.g. a hash's output is
    #: recorded in `digest_bits` on the row that has one.
    key_bits: int | None
    module: str
    #: NCSC guidance or an ISO/IEC standard. AC-S16 requires every row to have
    #: one, and `validate` refuses an inventory where a row does not.
    reference: str
    #: What replaces this after post-quantum migration. SAD 9.5's third column,
    #: carried in the inventory so the migration plan and the inventory are one
    #: document rather than two that disagree.
    migration: str = "unchanged"
    #: False where the algorithm is declared but not implemented in this build.
    in_use: bool = True
    notes: str = ""

    def __post_init__(self) -> None:
        """Refuse a row that cannot be assessed."""
        if not self.reference:
            msg = (
                f"the inventory row for {self.algorithm} ({self.purpose}) cites no "
                "guidance. AC-S16 requires every entry to map to NCSC guidance or an "
                "ISO/IEC standard; a row with no reference cannot be assessed and "
                "would be the one an auditor asks about."
            )
            raise InventoryError(msg)

    def as_payload(self) -> dict[str, Any]:
        """The wire shape."""
        return {
            "purpose": self.purpose,
            "algorithm": self.algorithm,
            "keyBits": self.key_bits,
            "module": self.module,
            "reference": self.reference,
            "postQuantumMigration": self.migration,
            "inUse": self.in_use,
            "notes": self.notes or None,
        }


def _normalise(name: str) -> str:
    """Compare algorithm names without punctuation.

    `ECDSA P-384` in the inventory and `ecdsa-p384` in the envelope are one
    algorithm written two ways, and a comparison that treated them as two would
    fail every build. A comparison that stripped only hyphens would treat them
    as two, which is how this was written the first time.
    """
    return "".join(character for character in name.lower() if character.isalnum())


def _cryptography_version() -> str:
    """The version of the `cryptography` module actually loaded."""
    try:
        from cryptography import __version__ as version
    except ImportError:  # pragma: no cover -- a dependency of this package
        return "unavailable"
    return f"cryptography {version}"


def entries() -> tuple[Entry, ...]:
    """The inventory rows, from what this build uses. SAD 9.5, transcribed.

    Read from the module constants where one exists, so that adding an
    algorithm to the envelope and forgetting the inventory is caught by
    `validate` rather than by an auditor.
    """
    module = _cryptography_version()
    openssl = f"OpenSSL via {module}"

    return (
        Entry(
            purpose="Transport",
            algorithm="TLS 1.3",
            key_bits=None,
            module=openssl,
            reference="NCSC TLS guidance; ISO/IEC 18033",
            migration="Hybrid X25519 with ML-KEM once the library estate supports it",
            notes="TLS 1.3 only. mTLS between control plane components and between "
            "GULLINBURSTI and MEGINGJORD.",
        ),
        Entry(
            purpose="Hashing, artefact manifests and ledger chaining",
            algorithm="SHA-256",
            key_bits=256,
            module=f"hashlib on {platform.python_implementation()}",
            reference="ISO/IEC 10118-3; NCSC recommended",
            migration="unchanged; SHA-2 at this length remains suitable",
        ),
        Entry(
            purpose="Hashing, where a longer digest is warranted",
            algorithm="SHA-384",
            key_bits=384,
            module=f"hashlib on {platform.python_implementation()}",
            reference="ISO/IEC 10118-3; NCSC recommended",
            migration="unchanged",
        ),
        Entry(
            purpose="Artefact and approval signing",
            algorithm="Ed25519",
            key_bits=256,
            module=module,
            reference="ISO/IEC 14888-3; NCSC recommended",
            migration="Hybrid classical with ML-DSA",
            in_use=Algorithm.ED25519 in SUPPORTED,
            notes="Against the internal Veldris signing CA (Decision S9).",
        ),
        Entry(
            purpose="Artefact and approval signing, HSM alternative",
            algorithm="ECDSA P-384",
            key_bits=384,
            module=module,
            reference="ISO/IEC 14888-3; FIPS 186-5 curve, NCSC recommended",
            migration="Hybrid classical with ML-DSA",
            in_use=Algorithm.ECDSA_P384 in SUPPORTED,
            notes="Signs the envelope's SHA-256 digest, so the effective security "
            "level is 128-bit rather than the 192 the curve could carry. That is "
            "deliberate: the envelope hashes once so every algorithm signs the same "
            "bytes, and it matches Ed25519, so the envelope has one security level "
            "rather than two.",
        ),
        Entry(
            purpose="Artefact and approval signing, post-quantum",
            algorithm="ML-DSA-65",
            key_bits=None,
            module="not implemented in this build",
            reference="FIPS 204; NCSC post-quantum migration guidance",
            migration="target algorithm",
            in_use=Algorithm.ML_DSA_65 in SUPPORTED,
            notes="Declared in the signature envelope so that adding it is not a "
            "format change (Decision S10). No implementation ships in Release 1: the "
            "envelope is ready and the library estate is not.",
        ),
        Entry(
            purpose="Data at rest",
            algorithm="AES-256",
            key_bits=256,
            module="Self-encrypting drive; FileVault; APFS encryption",
            reference="ISO/IEC 18033-3; NCSC recommended",
            migration="unchanged",
            notes="Appliance SEDs, FileVault on Apple silicon, APFS on the HODD vault.",
        ),
        Entry(
            purpose="Brokered token generation",
            algorithm="CSPRNG, 128-bit tokens",
            key_bits=128,
            module="secrets.token_urlsafe",
            reference="ISO/IEC 18031; NCSC random number guidance",
            migration="unchanged",
        ),
    )


@dataclass(frozen=True, slots=True)
class Inventory:
    """The cryptographic inventory. A build artefact, not a document."""

    generated_at: datetime
    rows: tuple[Entry, ...] = field(default_factory=entries)
    #: Restated here because the inventory is what an auditor reads first, and
    #: the distinction between guidance and a validation scheme is the thing
    #: they most need to see stated plainly (SAD 9.5).
    assurance_position: str = (
        "NCSC operates guidance, not a validation scheme comparable to CMVP. There is "
        "no certificate to obtain. The claim made here is conformance to NCSC "
        "recommended algorithms and configurations, and use of ISO/IEC 19790 "
        "conformant modules where a conformant build is available, evidenced by "
        "configuration and by this inventory. This is a weaker assertion than 'FIPS "
        "validated' and is presented as what it is."
    )

    @property
    def in_use(self) -> tuple[Entry, ...]:
        """Rows for algorithms this build actually implements."""
        return tuple(item for item in self.rows if item.in_use)

    @property
    def declared_not_implemented(self) -> tuple[Entry, ...]:
        """Rows present for crypto agility, not yet in use."""
        return tuple(item for item in self.rows if not item.in_use)

    def validate(self) -> None:
        """Raise unless every row can be assessed. AC-S16.

        Also checks the reverse direction: every algorithm the envelope
        supports has a row. An algorithm in the code and not in the inventory
        is the failure this document exists to prevent.
        """
        unreferenced = [item.algorithm for item in self.rows if not item.reference]
        if unreferenced:
            msg = f"inventory rows with no guidance reference: {', '.join(unreferenced)}"
            raise InventoryError(msg)

        listed = {_normalise(item.algorithm) for item in self.rows}
        missing = [str(item) for item in Algorithm if _normalise(str(item)) not in listed]
        if missing:
            msg = (
                f"the signature envelope knows {', '.join(missing)} and the inventory "
                "does not list it. An algorithm in the code and not in the inventory is "
                "exactly what AC-S16 exists to catch."
            )
            raise InventoryError(msg)

    def as_payload(self) -> dict[str, Any]:
        """The published inventory."""
        return {
            "schema": SCHEMA,
            "generatedAt": self.generated_at.isoformat(),
            "assurancePosition": self.assurance_position,
            "transportPolicy": "TLS 1.3 only",
            "transparencyLog": "self-hosted, internal (Decision S9)",
            "entries": [item.as_payload() for item in self.rows],
        }

    def to_json(self) -> str:
        """The inventory as a build artefact."""
        return json.dumps(self.as_payload(), indent=2, sort_keys=True, ensure_ascii=False)

    def to_markdown(self) -> str:
        """The inventory as a table, for the evidence pack."""
        lines = [
            "# Cryptographic inventory",
            "",
            f"Generated {self.generated_at.isoformat()}.",
            "",
            f"> {self.assurance_position}",
            "",
            "| Purpose | Algorithm | Key bits | Module | Reference | Migration | In use |",
            "|---|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {item.purpose} | {item.algorithm} | {item.key_bits or '-'} | "
            f"{item.module} | {item.reference} | {item.migration} | "
            f"{'yes' if item.in_use else 'declared'} |"
            for item in self.rows
        ]
        return "\n".join(lines) + "\n"


def build(generated_at: datetime, rows: Iterable[Entry] | None = None) -> Inventory:
    """Produce and validate the inventory. What the build stage calls."""
    inventory = Inventory(
        generated_at=generated_at, rows=tuple(rows) if rows is not None else entries()
    )
    inventory.validate()
    return inventory
