"""What the chain says about anchoring, and who reads it. RF-07.

Two questions are asked of the anchor entries: how far the federation has
countersigned (AC-S13, which gates every publication) and when it last did
(SAD 11A.3, which is what the freshness duty alarms on). Both used to be
answered wrongly. `_anchored_through` probed the payload for the *value* null
-- a JSONB containment match the duty's integer could never satisfy -- so it
returned zero however many times the chain had been countersigned, and every
publication was refused for a reason the refusal did not name. Freshness read
`site.last_anchored_at`, a column nothing ever wrote.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from draupnir.core.application.orchestrator import Orchestrator
from draupnir.core.domain.federation import ANCHOR_SUBMITTED
from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry

SITE = "sindri"
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _entry(
    seq: int, transition: str, payload: Mapping[str, Any], subject_id: str = SITE
) -> LedgerEntry:
    return LedgerEntry(
        id=uuid.uuid4(),
        site_id=SITE,
        seq=seq,
        prev_hash=GENESIS_HASH,
        entry_hash="e" * 64,
        ts=NOW,
        actor="worker@sindri",
        subject_type="site",
        subject_id=subject_id,
        transition=transition,
        payload=dict(payload),
    )


class _Chain:
    """Only what these two readers need: the entries about one subject."""

    def __init__(self, *entries: LedgerEntry) -> None:
        self.entries = entries

    def entries_for_subject(self, subject_id: str) -> tuple[LedgerEntry, ...]:
        return tuple(item for item in self.entries if item.subject_id == subject_id)

    def entries_of_type(self, subject_type: str) -> tuple[LedgerEntry, ...]:
        return tuple(item for item in self.entries if item.subject_type == subject_type)

    def entries_matching(self, probe: Mapping[str, Any]) -> tuple[LedgerEntry, ...]:
        raise AssertionError(
            "the anchor readers must not go through a payload probe: "
            "`{'anchored_through': None}` matched nothing (RF-07)"
        )

    def head(self) -> LedgerEntry | None:
        return self.entries[-1] if self.entries else None

    def append(self, entry: LedgerEntry) -> None:
        raise AssertionError("these readers write nothing")

    def serialise(self) -> None:
        return None


def _orchestrator(*entries: LedgerEntry) -> Orchestrator:
    return Orchestrator(
        _Chain(*entries),
        projection=None,  # type: ignore[arg-type]
        site_id=SITE,
        actor="worker@sindri",
    )


def _accepted(seq: int, at: datetime) -> LedgerEntry:
    return _entry(
        seq,
        ANCHOR_SUBMITTED,
        {
            "seq": seq,
            "outcome": "countersigned",
            "countersignature": "c" * 64,
            "reason": "",
            "anchored_through": seq,
            "anchored_at": at.isoformat(),
        },
    )


def _rejected(seq: int) -> LedgerEntry:
    return _entry(
        seq,
        ANCHOR_SUBMITTED,
        {
            "seq": seq,
            "outcome": "rejected",
            "countersignature": "",
            "reason": "the registry is unreachable",
            "anchored_through": 0,
            "anchored_at": "",
        },
    )


def test_a_countersigned_head_is_read_back_out_of_the_chain() -> None:
    """AC-S13 gates publication on this number, so zero refuses everything."""
    orchestrator = _orchestrator(_accepted(4, NOW - timedelta(minutes=30)))

    assert orchestrator._anchored_through() == 4


def test_the_highest_sequence_wins_rather_than_the_last_entry() -> None:
    """A rejection after a success must not undo the success.

    Both outcomes are recorded, deliberately -- an operator during an outage
    needs "tried and was refused" told apart from "never tried". Taking the
    last entry rather than the highest anchored sequence would let a failed
    retry withdraw a countersignature the registry has already given.
    """
    orchestrator = _orchestrator(_accepted(4, NOW - timedelta(minutes=30)), _rejected(5))

    assert orchestrator._anchored_through() == 4


def test_nothing_anchored_is_zero_and_refuses_every_publication() -> None:
    """Correctly. An estate with no federation link has no countersigned head."""
    assert _orchestrator()._anchored_through() == 0


def test_the_last_anchoring_instant_comes_from_the_chain() -> None:
    """Not from `site.last_anchored_at`, which nothing ever wrote."""
    recent = NOW - timedelta(minutes=5)
    orchestrator = _orchestrator(_accepted(3, NOW - timedelta(hours=2)), _accepted(4, recent))

    assert orchestrator.last_anchored_at() == recent


def test_a_rejection_does_not_refresh_the_freshness_clock() -> None:
    """A rejection is recorded, and it is not an anchor.

    Letting one count would silence the alarm that says the chain's end is
    unprotected -- which is the state a rejection puts the forge in.
    """
    anchored = NOW - timedelta(hours=3)
    orchestrator = _orchestrator(_accepted(3, anchored), _rejected(4))

    assert orchestrator.last_anchored_at() == anchored


def test_a_site_that_has_never_anchored_has_no_instant() -> None:
    """Which is what `freshness` turns into its "never anchored" alarm."""
    assert _orchestrator(_rejected(1)).last_anchored_at() is None


def test_entries_about_other_subjects_are_not_anchors() -> None:
    """The read is scoped by subject and transition, not by payload shape.

    A run's payload carrying an `anchored_through` key -- a console's
    convenience field, say -- would otherwise be read as a countersignature.
    """
    stray = _entry(2, "->RELEASED", {"anchored_through": 99}, subject_id=str(uuid.uuid4()))

    assert _orchestrator(stray)._anchored_through() == 0
