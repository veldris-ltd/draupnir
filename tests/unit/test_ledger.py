"""Ledger chain unit tests. The invariants themselves are in tests/property."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest

from draupnir.core.domain.ledger import (
    GENESIS_HASH,
    LedgerChainError,
    LedgerEntry,
    anchor_of,
    append,
    canonical,
    compute_entry_hash,
    head,
    verify_chain,
)


def _append(
    previous: LedgerEntry | None,
    moment: datetime,
    *,
    site_id: str = "sindri",
    payload: dict[str, Any] | None = None,
) -> LedgerEntry:
    return append(
        previous=previous,
        site_id=site_id,
        ts=moment,
        actor="curator@veldris.internal",
        subject_type="run",
        subject_id="cim-gbr-v0.1",
        transition="QUEUED->TRAINING",
        payload=payload if payload is not None else {"scheduler_job_id": "421337"},
    )


def test_canonical_is_key_order_independent() -> None:
    assert canonical({"b": 1, "a": 2}) == canonical({"a": 2, "b": 1})


def test_canonical_refuses_non_finite_floats() -> None:
    with pytest.raises(ValueError, match="Out of range"):
        canonical({"loss": float("nan")})


def test_first_entry_starts_at_genesis(moment: datetime) -> None:
    entry = _append(None, moment)
    assert entry.seq == 1
    assert entry.prev_hash == GENESIS_HASH
    assert entry.entry_hash == compute_entry_hash(GENESIS_HASH, entry.payload)


def test_chain_links_and_verifies(moment: datetime) -> None:
    first = _append(None, moment)
    second = _append(first, moment + timedelta(minutes=5), payload={"steps": 1200})
    third = _append(second, moment + timedelta(minutes=9), payload={"gate": "E1"})

    assert second.prev_hash == first.entry_hash
    assert third.seq == 3
    verify_chain([first, second, third])


def test_naive_timestamps_are_refused() -> None:
    with pytest.raises(ValueError, match="explicit offset"):
        _append(None, datetime(2026, 3, 2, 9, 0))  # noqa: DTZ001 -- that is the point


def test_a_chain_cannot_span_two_sites(moment: datetime) -> None:
    first = _append(None, moment)
    with pytest.raises(LedgerChainError, match="per site"):
        _append(first, moment + timedelta(minutes=1), site_id="brokkr")


def test_verification_catches_a_tampered_payload(moment: datetime) -> None:
    first = _append(None, moment)
    second = _append(first, moment + timedelta(minutes=5))
    tampered = replace(second, payload={"scheduler_job_id": "000000"})

    with pytest.raises(LedgerChainError, match="does not hash"):
        verify_chain([first, tampered])


def test_verification_catches_a_removed_entry(moment: datetime) -> None:
    entries: list[LedgerEntry] = []
    previous: LedgerEntry | None = None
    for index in range(4):
        previous = _append(previous, moment + timedelta(minutes=index), payload={"i": index})
        entries.append(previous)

    with pytest.raises(LedgerChainError, match="sequence break"):
        verify_chain([entries[0], entries[2], entries[3]])


def test_verification_catches_a_reordered_chain(moment: datetime) -> None:
    first = _append(None, moment)
    second = _append(first, moment + timedelta(minutes=1), payload={"a": 1})
    third = _append(second, moment + timedelta(minutes=2), payload={"b": 2})

    with pytest.raises(LedgerChainError):
        verify_chain([first, third, second])


def test_empty_chain_verifies() -> None:
    verify_chain([])


def test_head_is_the_anchorable_entry(moment: datetime) -> None:
    first = _append(None, moment)
    second = _append(first, moment + timedelta(minutes=1), payload={"a": 1})
    assert head([first, second]) is second
    assert head([]) is None


def test_entry_hash_is_stable_across_processes(moment: datetime) -> None:
    # A chain has to verify years later on a different machine, so the digest
    # is asserted against a literal rather than against a recomputation.
    expected = compute_entry_hash(GENESIS_HASH, {"scheduler_job_id": "421337"})
    assert expected == "32f407685170e18eb12f2ecde067c70747bd9df5cc3819623e5a4523505960e5"


def test_an_anchor_names_the_head(moment: datetime) -> None:
    first = _append(None, moment)
    second = _append(first, moment + timedelta(minutes=1), payload={"a": 1})
    assert anchor_of([first, second]) == (2, second.entry_hash)
    assert anchor_of([]) is None


def test_a_chain_still_holding_its_anchor_verifies(moment: datetime) -> None:
    first = _append(None, moment)
    second = _append(first, moment + timedelta(minutes=1), payload={"a": 1})
    verify_chain([first, second], anchor_of([first, second]))
    # An older anchor is still satisfied by a chain that has grown past it.
    verify_chain([first, second], (1, first.entry_hash))


def test_truncation_past_an_anchor_is_divergence(moment: datetime) -> None:
    first = _append(None, moment)
    second = _append(first, moment + timedelta(minutes=1), payload={"a": 1})
    anchor = anchor_of([first, second])
    assert anchor is not None

    with pytest.raises(LedgerChainError, match="truncated"):
        verify_chain([first], anchor)


def test_a_rewritten_anchored_entry_is_divergence(moment: datetime) -> None:
    first = _append(None, moment)
    rewritten = _append(None, moment, payload={"a": "different history"})
    with pytest.raises(LedgerChainError, match="divergence"):
        verify_chain([rewritten], (1, first.entry_hash))


def test_an_anchor_below_sequence_one_is_refused(moment: datetime) -> None:
    first = _append(None, moment)
    with pytest.raises(LedgerChainError, match="positive sequence"):
        verify_chain([first], (0, first.entry_hash))
