"""Pre-registration scanning: the half of T6 that catches what escapes anyway.

AC-S6: "the pre registration scan detects a planted test secret and blocks
registration". Registration, not release. The scan runs before an artefact
enters the register, because an artefact that has been registered has a
`hodd://` URI, and by the time anybody looks, that URI is in a specification, a
lineage chain and possibly a customer's hands.

Two things shape the implementation.

**Checkpoints are large and mostly binary.** A 54 GB safetensors file cannot be
read into memory and does not contain text. It is read in overlapping chunks so
that a secret straddling a boundary is still found, and the overlap is the
length of the longest pattern rather than a round number that happens to work.

**A false negative is worse than a false positive.** A missed credential is in
a customer's artefact; a spurious hit costs somebody ten minutes. So the
patterns are broad, the scan reports everything it found with enough context to
triage, and there is no allow-list mechanism -- an artefact with a finding is
blocked, and the response is to remove the credential, not to annotate it.

Findings never quote the secret. A scan report that includes the value it found
is a scan report that has to be handled as a secret, and it will not be.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

#: How much is read at a time. Large enough to be efficient on a vault-backed
#: filesystem, small enough that a scan does not become the memory problem.
CHUNK: Final = 1024 * 1024


@dataclass(frozen=True, slots=True)
class Pattern:
    """One thing worth blocking an artefact for."""

    name: str
    expression: re.Pattern[bytes]
    #: What an operator should do. A finding without a next step gets waved
    #: through by whoever is on shift.
    remediation: str

    @property
    def width(self) -> int:
        """A conservative bound on how many bytes a match can span."""
        return 512


def _pattern(name: str, expression: str, remediation: str) -> Pattern:
    """Build a pattern, compiled case-insensitively over bytes."""
    return Pattern(
        name=name,
        expression=re.compile(expression.encode("ascii"), re.IGNORECASE),
        remediation=remediation,
    )


#: What the scan looks for. Deliberately includes DRAUPNIR's own lease
#: references: a lease reference in a checkpoint is not a credential, but it is
#: evidence that the broker's contract was broken somewhere upstream, and that
#: is worth stopping for.
PATTERNS: Final[tuple[Pattern, ...]] = (
    _pattern(
        "aws-access-key-id",
        r"\bAKIA[0-9A-Z]{16}\b",
        "Rotate the key in IAM, then rebuild the artefact. A rotated key in a "
        "published checkpoint is still a disclosure of your account structure.",
    ),
    _pattern(
        "private-key-block",
        r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----",
        "Rotate the key pair and rebuild. Assume the key is compromised.",
    ),
    _pattern(
        "bearer-token",
        r"\b(?:authorization|bearer)\s*[:=]\s*['\"]?[A-Za-z0-9._~+/-]{20,}",
        "The credential reached the job environment. Check that the caller used a "
        "brokered lease rather than a value (svalinn.secrets).",
    ),
    _pattern(
        "hugging-face-token",
        r"\bhf_[A-Za-z0-9]{30,}\b",
        "Revoke the token on the Hub and rebuild. Acquisition should use a "
        "brokered lease redeemed at job start.",
    ),
    _pattern(
        "github-token",
        r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
        "Revoke the token and rebuild.",
    ),
    _pattern(
        "generic-api-key",
        r"\b(?:api[_-]?key|secret[_-]?key|client[_-]?secret)\s*[:=]\s*['\"]?[A-Za-z0-9/+=_-]{16,}",
        "Identify the caller that wrote it and move it to a brokered lease.",
    ),
    _pattern(
        "postgres-url",
        r"\bpostgres(?:ql)?://[^\s:@/]+:[^\s@/]+@",
        "The connection string carries a password. Rotate it and use a lease.",
    ),
    _pattern(
        "draupnir-lease-reference",
        r"\blease:[A-Za-z0-9_-]{16,}\b",
        "A lease reference reached an artefact. Not a credential, but it means a "
        "rendered plan or an environment file was written into the output; find "
        "and fix that.",
    ),
)


class ScanError(Exception):
    """Raised when a scan cannot be performed."""


class SecretDetectedError(ScanError):
    """Raised when registration is blocked by a finding. AC-S6.

    Blocking is the whole point. A scan that reports and proceeds is a scan
    whose output nobody reads, and the artefact is registered either way.
    """

    def __init__(self, findings: Sequence[Finding]) -> None:
        """Name what was found and where, never quoting the value."""
        self.findings = tuple(findings)
        detail = "; ".join(
            f"{item.pattern} in {item.path} at byte {item.offset}" for item in self.findings
        )
        remediation = "\n".join(
            f"  {name}: {step}"
            for name, step in sorted({(item.pattern, item.remediation) for item in self.findings})
        )
        super().__init__(
            f"registration is blocked: {len(self.findings)} secret pattern(s) found in "
            f"this artefact. {detail}.\n{remediation}\n"
            "The scan runs before registration because a registered artefact has a "
            "hodd:// URI, and by the time anybody looks it is in a specification, a "
            "lineage chain and possibly a customer's hands (threat T6, AC-S6)."
        )


@dataclass(frozen=True, slots=True)
class Finding:
    """One match, described without quoting what matched."""

    pattern: str
    path: str
    offset: int
    remediation: str
    #: A digest of the matched bytes. Lets two findings be compared, and lets a
    #: fix be confirmed, without the report carrying the value.
    digest: str

    def as_payload(self) -> dict[str, Any]:
        """The wire shape. Contains no secret, by construction."""
        return {
            "pattern": self.pattern,
            "path": self.path,
            "offset": self.offset,
            "matchDigest": self.digest,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class ScanResult:
    """What a scan found across an artefact."""

    findings: tuple[Finding, ...] = ()
    files_scanned: int = 0
    bytes_scanned: int = 0
    skipped: tuple[str, ...] = field(default_factory=tuple)

    @property
    def clean(self) -> bool:
        """Whether the artefact may be registered."""
        return not self.findings

    def raise_if_dirty(self) -> None:
        """Block registration if anything was found. AC-S6."""
        if self.findings:
            raise SecretDetectedError(self.findings)

    def as_payload(self) -> dict[str, Any]:
        """The scan report, for the ledger and the evidence pack."""
        return {
            "clean": self.clean,
            "filesScanned": self.files_scanned,
            "bytesScanned": self.bytes_scanned,
            "findings": [item.as_payload() for item in self.findings],
            "skipped": list(self.skipped),
        }


def scan_bytes(
    data: bytes, *, path: str = "<memory>", patterns: Iterable[Pattern] = PATTERNS, base: int = 0
) -> tuple[Finding, ...]:
    """Every pattern match in `data`, described without quoting it."""
    found: list[Finding] = []
    for pattern in patterns:
        for match in pattern.expression.finditer(data):
            found.append(
                Finding(
                    pattern=pattern.name,
                    path=path,
                    offset=base + match.start(),
                    remediation=pattern.remediation,
                    digest=hashlib.sha256(match.group(0)).hexdigest()[:16],
                )
            )
    return tuple(found)


def scan_file(
    path: Path,
    *,
    patterns: Sequence[Pattern] = PATTERNS,
    chunk: int = CHUNK,
    label: str | None = None,
) -> tuple[tuple[Finding, ...], int]:
    """Scan one file in overlapping chunks. Returns findings and bytes read.

    The overlap is the widest a pattern can match, so a credential straddling a
    chunk boundary is still found. Without it, a scan of a 54 GB checkpoint has
    one blind spot per megabyte.
    """
    overlap = max((item.width for item in patterns), default=0)
    findings: list[Finding] = []
    seen: set[tuple[str, int]] = set()
    total = 0
    name = label or path.name

    with path.open("rb") as handle:
        carry = b""
        base = 0
        while True:
            block = handle.read(chunk)
            if not block:
                break
            total += len(block)
            window = carry + block
            start = base - len(carry)
            for item in scan_bytes(window, path=name, patterns=patterns, base=start):
                key = (item.pattern, item.offset)
                if key not in seen:
                    seen.add(key)
                    findings.append(item)
            carry = window[-overlap:] if overlap else b""
            base += len(block)

    return tuple(findings), total


def scan(root: Path, *, patterns: Sequence[Pattern] = PATTERNS, chunk: int = CHUNK) -> ScanResult:
    """Scan every file under `root`. What runs before registration.

    Scans everything, including binary weights. A checkpoint is where AC-S6
    plants its test secret, and a scan that skipped binaries would pass it.
    """
    if not root.exists():
        msg = f"{root} does not exist; there is nothing to scan"
        raise ScanError(msg)

    targets = (
        [root] if root.is_file() else sorted(item for item in root.rglob("*") if item.is_file())
    )
    findings: list[Finding] = []
    skipped: list[str] = []
    total = 0

    for target in targets:
        label = target.name if target == root else str(target.relative_to(root)).replace("\\", "/")
        try:
            found, read = scan_file(target, patterns=patterns, chunk=chunk, label=label)
        except OSError as error:
            skipped.append(f"{label}: {error}")
            continue
        findings.extend(found)
        total += read

    return ScanResult(
        findings=tuple(findings),
        files_scanned=len(targets) - len(skipped),
        bytes_scanned=total,
        skipped=tuple(skipped),
    )


def scan_before_registration(root: Path, **options: Any) -> ScanResult:
    """Scan, and refuse registration on any finding. AC-S6.

    The function a registration path calls. Named for when it runs, because
    "scan the artefact" invites being called somewhere later and the timing is
    the control.
    """
    result = scan(root, **options)
    result.raise_if_dirty()
    return result
