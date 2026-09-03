"""Run identity: what makes two submissions of the same work the same work.

AC-F1 states it exactly: a specification submitted through `draupnirctl` and
through the console must produce "an identical run identity, being the hash of
the specification plus its input artefact hashes".

Two things follow from that sentence, and both are load bearing.

**The identity is not the run identifier.** SAD 11E.2 makes identifiers UUIDv7
so that they sort by creation time, and a digest does not sort by anything.
Deriving the identifier from the digest would trade a property the whole system
relies on for one this criterion does not ask for. So a run has both: an
identifier that says *when it was created*, and an identity that says *what it
is*. Submitting the same specification twice is two runs -- a deliberate
re-run -- with one identity between them, which is precisely the relationship
an operator wants when comparing them.

**The input artefact hashes are part of it.** A specification that names
`cim-000-base` and nothing else is not reproducible: `cim-000-base` is a moving
target unless the digest it resolved to at submission time is recorded. Two
submissions naming the same base at different times are the same specification
and different work, and the identity has to say so or it is not an identity.

The canonicalisation is the ledger's, not a second one. A payload that hashes
differently here and in the ledger would make the identity unverifiable from
the record, which is the only place it will ever be checked from.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from draupnir.core.domain.ledger import canonical

#: A lowercase SHA-256 digest, with or without the `sha256:` prefix a
#: specification writes and an artefact URI carries.
_DIGEST: Final = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")


class InputHashError(ValueError):
    """An input artefact hash is missing or is not a SHA-256 digest."""


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """What a run is, independent of when it was submitted."""

    #: SHA-256 of the canonical specification (SAD 6.2).
    spec_hash: str
    #: The resolved digests of every artefact the specification consumes,
    #: sorted, so that the order a client happens to list them in cannot
    #: change the identity.
    input_artefact_sha256: tuple[str, ...]
    #: SHA-256 over the pair. This is the value AC-F1 compares.
    digest: str

    def as_payload(self) -> dict[str, object]:
        """The identity as it is written to the ledger."""
        return {
            "spec_hash": self.spec_hash,
            "input_artefact_sha256": list(self.input_artefact_sha256),
            "run_identity": self.digest,
        }


def normalise_digest(value: str) -> str:
    """Return the bare lowercase hex of a SHA-256 digest.

    Accepts `sha256:` prefixed and bare forms because a specification writes
    one and an artefact URI carries the other, and an identity that changed
    with the spelling would be no identity at all.
    """
    match = _DIGEST.match(value.strip().lower())
    if match is None:
        msg = f"not a SHA-256 digest: {value!r}"
        raise InputHashError(msg)
    return match.group(1)


def run_identity(spec_hash: str, input_artefact_sha256: Iterable[str]) -> RunIdentity:
    """Compute the run identity of a specification and its resolved inputs.

    Raises `InputHashError` rather than hashing whatever it was given. An
    identity computed over an unresolved reference is worse than no identity:
    it looks reproducible and is not.
    """
    spec = normalise_digest(spec_hash)
    inputs = tuple(sorted({normalise_digest(value) for value in input_artefact_sha256}))
    payload = {"spec_hash": spec, "input_artefact_sha256": list(inputs)}
    digest = hashlib.sha256(canonical(payload)).hexdigest()
    return RunIdentity(spec_hash=spec, input_artefact_sha256=inputs, digest=digest)
