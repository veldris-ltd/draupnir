"""Anchoring: chain continuity verified before a head is countersigned.

SAD 11A.3. GULLINBURSTI submits a chain head -- site, sequence, entry hash --
signed. MEGINGJORD verifies continuity with what it already holds for that
site, countersigns, and records it. Three properties follow: a forge cannot
rewrite history after an anchor without the divergence being visible centrally,
the federation holds no forge content, and a forge can prove its own chain
integrity locally with MEGINGJORD unreachable.

The verification is the interesting part, and it is narrow on purpose.
MEGINGJORD does not hold the forge's entries, so it cannot check that entry
1,000 follows from entry 999. What it can check is that the site is not
contradicting itself: a sequence that goes backwards, a sequence that repeats
with a different hash, a previous-hash that does not match the head this
registry already countersigned. Each of those is a rewrite, and each is
detectable from hashes alone -- which is what makes the federation able to hold
hashes alone.

Divergence puts the forge read-only and both sides alarm (SAD 11A.4). That is
not a retry: a divergent chain is either a defect or a compromise, and both
want a human before another anchor is accepted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from draupnir.core.domain.federation import (
    Anchor,
    AnchorOutcome,
    AnchorSubmission,
    Receipt,
    sealed,
)


class AnchorError(Exception):
    """Raised when an anchor cannot be accepted."""


class DivergenceError(AnchorError):
    """Raised when a site contradicts an anchor already held. AC-S13, T12.

    The forge goes read-only on this. Both sides alarm, and no release from
    that forge is possible until it is investigated.
    """

    def __init__(self, site_id: str, seq: int, held: str, offered: str) -> None:
        """Name the site, the sequence and both hashes."""
        self.site_id = site_id
        self.seq = seq
        self.held = held
        self.offered = offered
        super().__init__(
            f"site {site_id} offered a different entry hash at sequence {seq}: this "
            f"registry holds {held[:12]} and was offered {offered[:12]}. The forge's "
            "ledger has been rewritten after being anchored. The forge enters read "
            "only mode and no release is possible from it until this is investigated "
            "(SAD 11A.4, threat T12). This is not retried."
        )


@dataclass
class AnchorStore:
    """Every countersigned head, by site. The federation's whole state."""

    anchors: dict[str, list[Anchor]] = field(default_factory=dict)
    #: Sites put read-only by a divergence. SAD 11A.4.
    read_only: set[str] = field(default_factory=set)

    def latest(self, site_id: str) -> Anchor | None:
        """The most recently anchored head for a site."""
        held = self.anchors.get(site_id)
        return held[-1] if held else None

    def at(self, site_id: str, seq: int) -> Anchor | None:
        """The anchor at one sequence, if it is held."""
        for item in self.anchors.get(site_id, ()):
            if item.seq == seq:
                return item
        return None

    def check_continuity(self, head: AnchorSubmission) -> str | None:
        """Why this head cannot follow what is held, or `None` if it can.

        Narrow by design. MEGINGJORD holds no entries, so it cannot verify that
        one follows from another; what it can verify is that the site is not
        contradicting itself, which is detectable from hashes alone.

        The order matters. An identical head already held is checked first and
        is a duplicate wherever it sits in the chain: a retry that resubmits an
        anchored head is idempotent and harmless, and rejecting it as "behind"
        would turn a network hiccup into an operator incident.
        """
        existing = self.at(head.site_id, head.seq)
        if existing is not None:
            return None if existing.head.entry_hash == head.entry_hash else "diverged"

        latest = self.latest(head.site_id)
        if latest is None:
            return None

        if head.seq < latest.seq:
            return (
                f"sequence {head.seq} is behind the anchored head at {latest.seq} and "
                "was never anchored. Anchors are submitted in order; a gap below the "
                "head is a hole in the chain, not a late arrival."
            )

        joins = head.previous_hash is not None and head.seq == latest.seq + 1
        if joins and head.previous_hash != latest.head.entry_hash:
            return (
                f"the head at {head.seq} names a previous hash of "
                f"{head.previous_hash[:12] if head.previous_hash else None} and the "
                f"anchor at {latest.seq} is {latest.head.entry_hash[:12]}. The chain "
                "does not join."
            )
        return None

    def countersign(
        self, head: AnchorSubmission, *, at: datetime, countersignature: str
    ) -> Receipt:
        """Verify continuity, then countersign and record. SAD 11A.3.

        A duplicate is accepted rather than rejected. After a partition
        GULLINBURSTI resubmits every unanchored head in order, and a head this
        registry already holds identically is a successful resubmission.
        """
        if head.site_id in self.read_only:
            return Receipt(
                outcome=AnchorOutcome.REJECTED,
                anchor=None,
                reason=(
                    f"site {head.site_id} is read only after a divergence and no "
                    "further anchor is accepted until it is investigated"
                ),
            )

        if not head.signature:
            return Receipt(
                outcome=AnchorOutcome.REJECTED,
                anchor=None,
                reason=(
                    "the head carries no signature. An unsigned head is not a claim "
                    "about a chain, it is a packet."
                ),
            )

        problem = self.check_continuity(head)
        if problem == "diverged":
            existing = self.at(head.site_id, head.seq)
            assert existing is not None  # noqa: S101 -- established by check_continuity
            self.read_only.add(head.site_id)
            raise DivergenceError(head.site_id, head.seq, existing.head.entry_hash, head.entry_hash)
        if problem is not None:
            return Receipt(outcome=AnchorOutcome.REJECTED, anchor=None, reason=problem)

        existing = self.at(head.site_id, head.seq)
        if existing is not None:
            return Receipt(
                outcome=AnchorOutcome.DUPLICATE,
                anchor=existing,
                reason="already anchored identically; resubmission after a partition",
            )

        anchor = Anchor(head=head, countersigned_at=at, countersignature=countersignature)
        self.anchors.setdefault(head.site_id, []).append(anchor)
        return Receipt(
            outcome=AnchorOutcome.COUNTERSIGNED,
            anchor=anchor,
            reason=f"chain continuity verified to sequence {head.seq}",
        )

    def submit_queue(
        self, heads: Sequence[AnchorSubmission], *, at: datetime, countersignature: str
    ) -> tuple[Receipt, ...]:
        """Countersign a queue of heads in order. AC-S13's reconnect path.

        In order, and stopping at the first rejection. Accepting a later head
        after rejecting an earlier one would anchor a chain with a hole in it.
        """
        receipts: list[Receipt] = []
        for head in sorted(heads, key=lambda item: item.seq):
            receipt = self.countersign(head, at=at, countersignature=countersignature)
            receipts.append(receipt)
            if not receipt.accepted:
                break
        return tuple(receipts)

    def may_release(self, site_id: str, seq: int) -> tuple[bool, str]:
        """Whether a site may publish. SAD 11A.4, Decision S8.

        Release requires a countersigned anchor at or beyond the sequence the
        release records. A forge in partition keeps training and cannot
        publish.
        """
        if site_id in self.read_only:
            return False, f"site {site_id} is read only after a divergence"
        latest = self.latest(site_id)
        if latest is None:
            return False, f"site {site_id} has no countersigned anchor"
        if latest.seq < seq:
            return (
                False,
                (
                    f"the release records sequence {seq} and the latest countersigned "
                    f"anchor is {latest.seq}. Publication requires a countersigned "
                    "anchor: training continues through a partition, release does not "
                    "(Decision S8). The artefact waits in AWAITING_APPROVAL."
                ),
            )
        return True, f"countersigned to sequence {latest.seq}"

    def as_payload(self) -> Mapping[str, Any]:
        """Everything held, sealed. The federation's whole state is hashes."""
        return sealed(
            {
                "sites": {
                    site: [item.as_payload() for item in held]
                    for site, held in sorted(self.anchors.items())
                },
                "readOnly": sorted(self.read_only),
            },
            name="anchorStore",
        )


def heads_of(anchors: Iterable[Anchor]) -> tuple[tuple[int, str], ...]:
    """Sequence and entry hash for each anchor, oldest first."""
    return tuple((item.seq, item.head.entry_hash) for item in sorted(anchors, key=lambda a: a.seq))
