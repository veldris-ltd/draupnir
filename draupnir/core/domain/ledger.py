"""The append only, hash chained audit ledger.

SAD 7.1: one chain per site, `entry_hash = H(prev_hash || canonical(payload))`.

This module is pure. It computes and verifies a chain; it does not know where
the chain is stored. The repository in the infrastructure layer persists it and
a database trigger, not this code, is what makes the table refuse UPDATE and
DELETE (SAD 11C).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from draupnir.core.domain.identifiers import new_id

#: The prev_hash of the first entry in a site chain.
GENESIS_HASH = "0" * 64

JsonValue = Any


def canonical(payload: JsonValue) -> bytes:
    """Serialise `payload` to the one byte string the chain hashes.

    Keys are sorted, separators are tight, non-finite floats are refused, and
    the result is UTF-8. Two structurally equal payloads therefore always
    produce the same bytes, which is what makes a chain verifiable on a
    different machine years later.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def compute_entry_hash(prev_hash: str, payload: JsonValue) -> str:
    """Return `H(prev_hash || canonical(payload))` as lowercase hex."""
    digest = hashlib.sha256()
    digest.update(bytes.fromhex(prev_hash))
    digest.update(canonical(payload))
    return digest.hexdigest()


class LedgerChainError(Exception):
    """Raised when a chain fails verification."""


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One append only record in a site's chain. Attributes per SAD 7.1."""

    id: UUID
    site_id: str
    seq: int
    prev_hash: str
    entry_hash: str
    ts: datetime
    actor: str
    subject_type: str
    subject_id: str
    transition: str
    payload: JsonValue

    def recompute(self) -> str:
        """Return the hash this entry should carry given its own payload."""
        return compute_entry_hash(self.prev_hash, self.payload)


def append(
    *,
    previous: LedgerEntry | None,
    site_id: str,
    ts: datetime,
    actor: str,
    subject_type: str,
    subject_id: str,
    transition: str,
    payload: JsonValue,
) -> LedgerEntry:
    """Build the next entry in a site chain.

    Passing `previous=None` starts the chain at seq 1 with the genesis hash.
    """
    if ts.tzinfo is None:
        msg = "ledger timestamps carry an explicit offset (SAD 11E.2)"
        raise ValueError(msg)
    if previous is not None and previous.site_id != site_id:
        msg = f"chain is per site: cannot append {site_id} onto {previous.site_id}"
        raise LedgerChainError(msg)

    prev_hash = GENESIS_HASH if previous is None else previous.entry_hash
    seq = 1 if previous is None else previous.seq + 1
    return LedgerEntry(
        id=new_id(),
        site_id=site_id,
        seq=seq,
        prev_hash=prev_hash,
        entry_hash=compute_entry_hash(prev_hash, payload),
        ts=ts,
        actor=actor,
        subject_type=subject_type,
        subject_id=subject_id,
        transition=transition,
        payload=payload,
    )


#: What GULLINBURSTI submits to MEGINGJORD: the sequence number and the entry
#: hash of the chain head at the time of anchoring (SAD 11A.3).
Anchor = tuple[int, str]


def verify_chain(entries: Sequence[LedgerEntry], anchor: Anchor | None = None) -> None:
    """Raise `LedgerChainError` unless `entries` form one intact site chain.

    Checks, in order: a single site, contiguous sequence numbers from 1, each
    `prev_hash` equal to its predecessor's `entry_hash`, and each `entry_hash`
    equal to the hash recomputed from the stored payload.

    A chain verifies what it contains, and cannot by itself detect that entries
    were removed from its end: entries 1 to n-1 of a valid chain are themselves
    a valid chain. Truncation is caught by comparing the head against a
    countersigned anchor, which is why anchoring exists (SAD 11A.3) and why
    "the forge enters read only mode" on divergence (SAD 11A.4). Pass `anchor`
    to make that comparison here.
    """
    if not entries and anchor is None:
        return

    if entries:
        site_ids = {entry.site_id for entry in entries}
        if len(site_ids) != 1:
            msg = f"expected one site chain, found {sorted(site_ids)}"
            raise LedgerChainError(msg)

    expected_prev = GENESIS_HASH
    for index, entry in enumerate(entries, start=1):
        if entry.seq != index:
            msg = f"sequence break at position {index}: seq is {entry.seq}"
            raise LedgerChainError(msg)
        if entry.prev_hash != expected_prev:
            msg = f"chain break at seq {entry.seq}: prev_hash does not match seq {entry.seq - 1}"
            raise LedgerChainError(msg)
        if entry.entry_hash != entry.recompute():
            msg = f"payload at seq {entry.seq} does not hash to its recorded entry_hash"
            raise LedgerChainError(msg)
        expected_prev = entry.entry_hash

    if anchor is not None:
        verify_anchor(entries, anchor)


def verify_anchor(entries: Sequence[LedgerEntry], anchor: Anchor) -> None:
    """Raise unless the chain still contains the anchored head.

    An anchored entry that the chain no longer reaches, or reaches with a
    different hash, is divergence: the forge has rewritten history since the
    federation countersigned it.
    """
    anchored_seq, anchored_hash = anchor
    if anchored_seq <= 0:
        msg = f"an anchor names a positive sequence number, not {anchored_seq}"
        raise LedgerChainError(msg)
    if len(entries) < anchored_seq:
        msg = (
            f"chain is truncated: it ends at seq {len(entries)} but seq {anchored_seq} is anchored"
        )
        raise LedgerChainError(msg)
    if entries[anchored_seq - 1].entry_hash != anchored_hash:
        msg = f"divergence at seq {anchored_seq}: the entry no longer matches the anchor"
        raise LedgerChainError(msg)


def head(entries: Iterable[LedgerEntry]) -> LedgerEntry | None:
    """Return the highest-sequence entry, which is the anchorable head."""
    return max(entries, key=lambda entry: entry.seq, default=None)


def anchor_of(entries: Sequence[LedgerEntry]) -> Anchor | None:
    """Return the anchor GULLINBURSTI would submit for this chain."""
    latest = head(entries)
    return None if latest is None else (latest.seq, latest.entry_hash)
