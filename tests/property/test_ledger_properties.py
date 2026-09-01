"""Property tests for the ledger chain.

SAD 11E.3 and AC-Q4: minimum 500 examples per property. AC-Q4 also names
specification hash determinism and projector idempotence; those two arrive with
the specification compiler and the projector in Prompt 1, and their property
tests belong with them. What exists now is tested to the same standard.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from draupnir.core.domain.ledger import (
    GENESIS_HASH,
    LedgerChainError,
    anchor_of,
    append,
    canonical,
    compute_entry_hash,
    verify_chain,
)

EXAMPLES = settings(
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**53), max_value=2**53)
    | st.floats(allow_nan=False, allow_infinity=False, width=32)
    | st.text(max_size=40),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(min_size=1, max_size=12), children, max_size=4)
    ),
    max_leaves=8,
)

payloads = st.dictionaries(st.text(min_size=1, max_size=12), json_values, max_size=5)


def _chain(payload_list: list[dict[str, Any]], site_id: str = "sindri") -> list[Any]:
    entries = []
    previous = None
    for index, payload in enumerate(payload_list):
        previous = append(
            previous=previous,
            site_id=site_id,
            ts=EPOCH + timedelta(minutes=index),
            actor="system:test",
            subject_type="run",
            subject_id=f"run-{index}",
            transition="QUEUED->TRAINING",
            payload=payload,
        )
        entries.append(previous)
    return entries


@EXAMPLES
@given(payload=payloads)
def test_canonical_form_is_stable(payload: dict[str, Any]) -> None:
    """The same payload always serialises to the same bytes."""
    assert canonical(payload) == canonical(dict(reversed(list(payload.items()))))


@EXAMPLES
@given(payload=payloads)
def test_hashing_is_deterministic(payload: dict[str, Any]) -> None:
    """A digest depends only on the previous hash and the payload."""
    assert compute_entry_hash(GENESIS_HASH, payload) == compute_entry_hash(GENESIS_HASH, payload)


@EXAMPLES
@given(payload_list=st.lists(payloads, min_size=1, max_size=12))
def test_any_appended_chain_verifies(payload_list: list[dict[str, Any]]) -> None:
    """Appending is the only way a chain is built, and it always verifies."""
    verify_chain(_chain(payload_list))


@EXAMPLES
@given(
    payload_list=st.lists(payloads, min_size=2, max_size=10),
    index=st.integers(min_value=0),
    replacement=payloads,
)
def test_tampering_with_any_payload_is_detected(
    payload_list: list[dict[str, Any]], index: int, replacement: dict[str, Any]
) -> None:
    """Rewriting history at any position breaks verification."""
    entries = _chain(payload_list)
    position = index % len(entries)
    original = entries[position]
    if canonical(original.payload) == canonical(replacement):
        return  # not a tamper

    entries[position] = replace(original, payload=replacement)
    try:
        verify_chain(entries)
    except LedgerChainError:
        return
    raise AssertionError(f"tampering at position {position} went undetected")


@EXAMPLES
@given(payload_list=st.lists(payloads, min_size=2, max_size=10), index=st.integers(min_value=0))
def test_removing_an_entry_before_the_head_is_detected(
    payload_list: list[dict[str, Any]], index: int
) -> None:
    """A hole anywhere but the end breaks the chain that follows it."""
    entries = _chain(payload_list)
    del entries[index % (len(entries) - 1)]
    try:
        verify_chain(entries)
    except LedgerChainError:
        return
    raise AssertionError("a removed entry went undetected")


@EXAMPLES
@given(payload_list=st.lists(payloads, min_size=2, max_size=10))
def test_truncation_at_the_head_is_detected_only_against_an_anchor(
    payload_list: list[dict[str, Any]],
) -> None:
    """Entries 1 to n-1 of a valid chain are themselves a valid chain.

    This is not a defect in the hashing; it is why SAD 11A.3 exists. A forge
    that drops its most recent entries still verifies locally, and is caught
    when its head is compared against the anchor MEGINGJORD countersigned.
    """
    entries = _chain(payload_list)
    anchor = anchor_of(entries)
    assert anchor is not None

    truncated = entries[:-1]
    verify_chain(truncated)  # locally consistent, and therefore not enough

    try:
        verify_chain(truncated, anchor)
    except LedgerChainError:
        return
    raise AssertionError("truncation went undetected against a known anchor")


@EXAMPLES
@given(payload_list=st.lists(payloads, min_size=1, max_size=10))
def test_sequence_numbers_are_contiguous_from_one(payload_list: list[dict[str, Any]]) -> None:
    """Sequence numbers are the chain's ordering, so they never skip."""
    entries = _chain(payload_list)
    assert [entry.seq for entry in entries] == list(range(1, len(entries) + 1))


@EXAMPLES
@given(payload_list=st.lists(payloads, min_size=1, max_size=8))
def test_two_site_chains_are_independent(payload_list: list[dict[str, Any]]) -> None:
    """Each site's chain verifies alone, which is what makes anchoring local."""
    verify_chain(_chain(payload_list, "sindri"))
    verify_chain(_chain(payload_list, "brokkr"))
