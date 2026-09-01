"""Identifier generation.

SAD 11E.2: identifiers are UUIDv7, so they sort by creation time without
exposing a sequence.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import uuid6


def new_id() -> UUID:
    """Return a fresh UUIDv7."""
    return uuid6.uuid7()


def id_at(moment: datetime, entropy: bytes) -> UUID:
    """Build a UUIDv7 from an explicit instant and explicit entropy.

    `new_id` is what production uses. This exists so that a seed script or a
    property test can produce identifiers that still sort by creation time but
    are byte-for-byte reproducible, which is what makes a seeded dataset
    comparable across machines.

    Layout per RFC 9562 section 5.7: 48 bits of Unix milliseconds, 4 bits of
    version, 12 bits of entropy, 2 bits of variant, 62 bits of entropy.
    """
    if moment.tzinfo is None:
        msg = "identifier timestamps carry an explicit offset (SAD 11E.2)"
        raise ValueError(msg)
    if len(entropy) < 10:
        msg = "id_at needs at least 10 bytes of entropy"
        raise ValueError(msg)

    milliseconds = int(moment.timestamp() * 1000) & ((1 << 48) - 1)
    bits = int.from_bytes(entropy[:10], "big")
    rand_a = (bits >> 62) & 0xFFF
    rand_b = bits & ((1 << 62) - 1)
    value = (milliseconds << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return UUID(int=value)
