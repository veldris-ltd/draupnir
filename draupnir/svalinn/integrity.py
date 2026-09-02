"""Load-time integrity: the run does not start if the weights changed.

Threat T1 is poisoned or substituted base weights, and AC-S1 is the test:
"Altering one byte of a base weight file causes the next load to fail with a
hash mismatch and a ledger entry, and the run does not start."

Three clauses, and each is a separate design decision.

**The next load.** Not the next ingest, not a nightly sweep. Verification
happens at the point of use, every time, because a check that runs at
acquisition establishes what the bytes were then and says nothing about what
they are now. A file on a shared vault is mutable by everything that can write
to the vault.

**A ledger entry.** The refusal is recorded whether or not anybody is watching.
A tamper that is refused and not recorded is a tamper nobody investigates, and
the second attempt looks like the first.

**The run does not start.** Not "starts and fails later", not "starts with a
warning". `verify_before_load` raises, and the caller has not yet consumed an
allocation.

The specification already carries what is needed: `base.expectSha256` in SAD
6.2 is the hash the run expects to find. This module is what makes that field
load-bearing rather than documentation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

CHUNK: Final = 1024 * 1024


class IntegrityError(Exception):
    """Raised when an artefact is not what the specification expects."""


class HashMismatchError(IntegrityError):
    """Raised when an artefact's bytes differ from what was recorded. AC-S1.

    Deliberately does not speculate about cause. A mismatch is a mismatch; it
    may be tampering, a partial write, or a legitimate rebuild nobody recorded,
    and a message that guessed would send the reader to the wrong place.
    """

    def __init__(self, artefact: str, expected: str, observed: str) -> None:
        """Name the artefact and both hashes."""
        self.artefact = artefact
        self.expected = expected
        self.observed = observed
        super().__init__(
            f"{artefact} does not match the hash the specification expects. Expected "
            f"{expected}, found {observed}. The run does not start (threat T1, AC-S1). "
            "This is a mismatch, not a diagnosis: it may be tampering, a partial "
            "write, or a rebuild nobody recorded. The ledger entry for this refusal "
            "carries both hashes."
        )


class UnverifiableArtefactError(IntegrityError):
    """Raised when a specification names no expected hash.

    A specification with no `expectSha256` cannot be verified, and treating
    that as "verified" is how the control is lost -- silently, for the runs
    that happen to omit the field.
    """

    def __init__(self, artefact: str) -> None:
        """Name the artefact that cannot be checked."""
        self.artefact = artefact
        super().__init__(
            f"the specification names {artefact} with no expected SHA-256, so the load "
            "cannot be verified. An unverifiable load is refused rather than treated "
            "as verified: SAD 6.2 carries expectSha256 precisely so that a replay "
            "resolving to different bytes is a failure and not a silent upgrade."
        )


def hash_file(path: Path, *, chunk: int = CHUNK) -> str:
    """SHA-256 of a file, read in chunks and never held whole."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_tree(root: Path, *, chunk: int = CHUNK) -> str:
    """SHA-256 over a directory: every relative path with its content hash.

    Paths are included, so renaming a shard changes the digest. Renaming shards
    changes which weights load, and a digest that did not notice would verify a
    model that is not the model.
    """
    entries = sorted(
        (str(item.relative_to(root)).replace("\\", "/"), hash_file(item, chunk=chunk))
        for item in root.rglob("*")
        if item.is_file()
    )
    if not entries:
        msg = f"{root} contains no files; there is nothing to verify"
        raise IntegrityError(msg)
    import json

    canonical = json.dumps(entries, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def digest_of(path: Path, *, chunk: int = CHUNK) -> str:
    """The digest of a file or a directory of shards."""
    return hash_tree(path, chunk=chunk) if path.is_dir() else hash_file(path, chunk=chunk)


@dataclass(frozen=True, slots=True)
class Refusal:
    """The ledger entry AC-S1 requires a refused load to write."""

    artefact: str
    expected: str
    observed: str
    run_id: str | None
    refused_at: datetime

    def as_payload(self) -> dict[str, Any]:
        """The ledger payload for a refused load."""
        return {
            "event": "integrity.refused",
            "artefact": self.artefact,
            "expectedSha256": self.expected,
            "observedSha256": self.observed,
            "runId": self.run_id,
            "refusedAt": self.refused_at.isoformat(),
            "threat": "T1",
            "outcome": "the run did not start",
        }


@dataclass(frozen=True, slots=True)
class Verification:
    """The result of verifying one artefact before it is loaded."""

    artefact: str
    expected: str
    observed: str
    verified_at: datetime
    run_id: str | None = None

    @property
    def matches(self) -> bool:
        """Whether the bytes are what the specification expects."""
        return self.expected == self.observed

    def refusal(self) -> Refusal:
        """The ledger entry for a mismatch."""
        return Refusal(
            artefact=self.artefact,
            expected=self.expected,
            observed=self.observed,
            run_id=self.run_id,
            refused_at=self.verified_at,
        )

    def as_payload(self) -> dict[str, Any]:
        """The ledger payload for a successful verification."""
        return {
            "event": "integrity.verified",
            "artefact": self.artefact,
            "sha256": self.observed,
            "runId": self.run_id,
            "verifiedAt": self.verified_at.isoformat(),
        }


def verify(
    path: Path, *, artefact: str, expected: str | None, at: datetime, run_id: str | None = None
) -> Verification:
    """Hash what is on disk and compare it. Returns; does not raise.

    Separate from `verify_before_load` so that a caller which must record the
    refusal can do so before the exception propagates.
    """
    if not expected:
        raise UnverifiableArtefactError(artefact)
    return Verification(
        artefact=artefact,
        expected=expected,
        observed=digest_of(path),
        verified_at=at,
        run_id=run_id,
    )


def verify_before_load(
    path: Path, *, artefact: str, expected: str | None, at: datetime, run_id: str | None = None
) -> Verification:
    """Verify, and refuse the load on mismatch. AC-S1.

    Named for when it runs. `verify_artefact` would invite being called
    somewhere convenient, and the timing is the control: this is the last
    moment before an allocation is consumed.
    """
    result = verify(path, artefact=artefact, expected=expected, at=at, run_id=run_id)
    if not result.matches:
        raise HashMismatchError(artefact, result.expected, result.observed)
    return result


def verify_inputs(
    inputs: Mapping[str, tuple[Path, str | None]], *, at: datetime, run_id: str | None = None
) -> tuple[Verification, ...]:
    """Verify every input a run declares, refusing on the first mismatch.

    Every input, not only the base model. A poisoned corpus is the same threat
    with a different artefact kind, and SAD 6.2 records an expected hash for
    the dataset as well.
    """
    results: list[Verification] = []
    for artefact, (path, expected) in sorted(inputs.items()):
        results.append(
            verify_before_load(path, artefact=artefact, expected=expected, at=at, run_id=run_id)
        )
    return tuple(results)


def unverifiable(inputs: Iterable[tuple[str, str | None]]) -> tuple[str, ...]:
    """Every declared input carrying no expected hash. For submission checks."""
    return tuple(sorted(name for name, expected in inputs if not expected))
