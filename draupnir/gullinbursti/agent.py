"""The per-site agent: anchors, policy, release metadata, capacity.

SAD 11A.1 gives GULLINBURSTI four jobs at each forge: anchor the local chain
head, pull policy, push release metadata, report capacity and health.

Everything here is shaped by SAD 11A.4 and Decision S8: the forge keeps working
through a partition, and release does not. So the agent has no notion of
failing. A submission that cannot reach MEGINGJORD is queued, in order, and the
forge carries on; a policy pull that cannot reach MEGINGJORD returns what was
last pulled, and the forge carries on. The one thing that changes is that
`may_release` says no, with a reason an operator can act on.

The queue is ordered and is drained in order. Anchors submitted out of order
would make continuity unverifiable at the far end, and MEGINGJORD stops at the
first rejection rather than anchoring a chain with a hole in it. AC-S13 is the
round trip: disconnected, keeps training and recording; reconnect, queue
submits in order, blocked releases become available.

AC-N12 is the other half: 72 hours with the link down and no degradation other
than blocked release. There is nothing here that grows without bound except the
queue, which grows at the rate the ledger does; `queue_depth` is reported so
that "no degradation" is observable rather than assumed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from draupnir.core.domain.federation import (
    AnchorOutcome,
    AnchorSubmission,
    Capacity,
    PolicyBundle,
    Receipt,
    ReleaseRecord,
    sealed,
)

#: How often the agent anchors when nothing else prompts it. On every release
#: transition as well (SAD 11A.3), which is what makes a release anchorable.
ANCHOR_INTERVAL = timedelta(minutes=15)

#: AC-N11's budget for one anchor round trip over WireGuard.
ANCHOR_ROUND_TRIP_BUDGET = timedelta(seconds=2)


@runtime_checkable
class Countersigner(Protocol):
    """The anchoring half of MEGINGJORD: it verifies continuity and signs.

    Three protocols rather than one, because MEGINGJORD is not one object. The
    anchor store holds chain heads and the registry holds policy and release
    metadata, and an agent method that took "the registry" would be claiming a
    dependency on both when it needs one.
    """

    def countersign(
        self, head: AnchorSubmission, *, at: datetime, countersignature: str
    ) -> Receipt:
        """Verify continuity and countersign a submitted head."""
        ...


@runtime_checkable
class PolicySource(Protocol):
    """The policy-distribution half. Pull, never push (SAD 11A.4)."""

    def pull_policy(self, name: str, version: str | None = None) -> PolicyBundle:
        """Return a published policy bundle."""
        ...


@runtime_checkable
class MetadataSink(Protocol):
    """Where release metadata and capacity reports go."""

    def record_release(self, record: ReleaseRecord) -> ReleaseRecord:
        """Record release metadata pushed by a site."""
        ...

    def report_capacity(self, report: Capacity) -> Capacity:
        """Record a site's capacity report."""
        ...


class LinkState(StrEnum):
    """Whether the federation link is usable."""

    UP = "up"
    DOWN = "down"
    #: Divergence detected. The forge is read only (SAD 11A.4).
    READ_ONLY = "read-only"


class AgentError(Exception):
    """Raised when the agent cannot do something that is not a partition."""


class ReleaseBlockedError(AgentError):
    """Raised when a release is attempted without a countersigned anchor.

    AC-S13's "refused with a clear reason". The reason matters more than usual
    here, because the operator's next question is whether to wait or to
    escalate, and those have different answers for a partition and a
    divergence.
    """

    def __init__(self, site_id: str, seq: int, reason: str) -> None:
        """Name the site, the sequence and why it is blocked."""
        self.site_id = site_id
        self.seq = seq
        super().__init__(
            f"release from {site_id} at ledger sequence {seq} is blocked: {reason}. "
            "Training continues through a partition; release does not, because "
            "release is the moment a commercial artefact and its regulatory "
            "documentation leave Veldris control and that moment depends on the trust "
            "root rather than on a local decision (Decision S8). The artefact waits in "
            "AWAITING_APPROVAL."
        )


@dataclass(frozen=True, slots=True)
class Submission:
    """One head waiting to be anchored, with when it was queued."""

    head: AnchorSubmission
    queued_at: datetime

    @property
    def seq(self) -> int:
        """The sequence this head anchors."""
        return self.head.seq


@dataclass
class Gullinbursti:
    """The site agent. Queues through a partition and drains in order."""

    site_id: str
    signing_key_id: str
    link: LinkState = LinkState.UP
    #: Heads waiting to be anchored. Ordered by sequence when drained.
    queue: list[Submission] = field(default_factory=list)
    #: The highest sequence MEGINGJORD has countersigned for this site.
    anchored_seq: int = 0
    #: The policy last successfully pulled. Kept so a partitioned forge keeps
    #: working under the policy it had rather than under none.
    policies: dict[str, PolicyBundle] = field(default_factory=dict)
    #: Release metadata waiting to be pushed. Pushed after the anchor it
    #: depends on, because metadata for an unanchored release is a claim the
    #: registry cannot check.
    pending_releases: list[ReleaseRecord] = field(default_factory=list)
    receipts: list[Receipt] = field(default_factory=list)

    # -- anchoring ---------------------------------------------------------

    def submit(self, head: AnchorSubmission, *, at: datetime) -> Submission:
        """Queue a chain head for anchoring.

        Always queues, even when the link is up. The queue is what makes the
        partition path and the ordinary path the same code, and a path that is
        only exercised during an outage is a path that does not work.
        """
        if head.site_id != self.site_id:
            msg = (
                f"this agent is for site {self.site_id} and the head names "
                f"{head.site_id}. An agent anchoring another site's chain is how two "
                "forges end up sharing a ledger segment."
            )
            raise AgentError(msg)
        submission = Submission(head=head, queued_at=at)
        self.queue.append(submission)
        return submission

    @property
    def queue_depth(self) -> int:
        """How many heads are waiting. AC-N12's observable."""
        return len(self.queue)

    @property
    def unanchored(self) -> tuple[int, ...]:
        """The sequences waiting to be anchored, in order."""
        return tuple(sorted(item.seq for item in self.queue))

    def drain(
        self, registry: Countersigner, *, at: datetime, countersignature: str
    ) -> tuple[Receipt, ...]:
        """Submit every queued head, in order. AC-S13's reconnect path.

        Stops at the first rejection and keeps the rest queued: a head after a
        rejected one cannot be anchored without leaving a hole, and dropping it
        would lose the record of what the forge did.
        """
        if self.link is LinkState.DOWN:
            return ()

        receipts: list[Receipt] = []
        remaining = sorted(self.queue, key=lambda item: item.seq)

        while remaining:
            submission = remaining[0]
            receipt = registry.countersign(
                submission.head, at=at, countersignature=countersignature
            )
            receipts.append(receipt)
            self.receipts.append(receipt)
            if not receipt.accepted:
                break
            remaining.pop(0)
            self.anchored_seq = max(self.anchored_seq, submission.seq)

        self.queue = remaining
        return tuple(receipts)

    # -- policy ------------------------------------------------------------

    def pull_policy(
        self, registry: PolicySource, name: str, *, version: str | None = None
    ) -> PolicyBundle:
        """Pull a policy, falling back to what was last pulled.

        A partitioned forge keeps working under the policy it had. Returning
        nothing would stop the pipeline on a network fault, which is what
        Decision S8 says not to do; returning a default would be worse, because
        it would be a policy nobody chose.
        """
        if self.link is LinkState.DOWN:
            held = self._last_pulled(name)
            if held is None:
                msg = (
                    f"the federation link is down and no {name!r} policy has ever been "
                    "pulled at this site. A forge with no policy at all cannot proceed: "
                    "there is nothing to fall back to."
                )
                raise AgentError(msg)
            return held

        bundle = registry.pull_policy(name, version)
        self.policies[bundle.key] = bundle
        return bundle

    def _last_pulled(self, name: str) -> PolicyBundle | None:
        """The most recent policy of this name that was successfully pulled."""
        candidates = [item for item in self.policies.values() if item.name == name]
        return max(candidates, key=lambda item: item.issued_at) if candidates else None

    # -- release -----------------------------------------------------------

    def may_release(self, seq: int) -> tuple[bool, str]:
        """Whether this site may publish a release recorded at `seq`.

        Answered locally from the anchored sequence, so an operator gets an
        answer during a partition rather than a timeout.
        """
        if self.link is LinkState.READ_ONLY:
            return False, "the forge is read only after a chain divergence (SAD 11A.4)"
        if self.anchored_seq < seq:
            state = (
                "the federation link is down"
                if self.link is LinkState.DOWN
                else "the anchor has not been countersigned"
            )
            return (
                False,
                (
                    f"{state}; the release records sequence {seq} and the latest "
                    f"countersigned anchor is {self.anchored_seq}. "
                    f"{self.queue_depth} head(s) are queued and will submit in order "
                    "when the link returns."
                ),
            )
        return True, f"countersigned to sequence {self.anchored_seq}"

    def require_release(self, seq: int) -> None:
        """Raise unless this site may publish. AC-S13."""
        permitted, reason = self.may_release(seq)
        if not permitted:
            raise ReleaseBlockedError(self.site_id, seq, reason)

    def push_release(self, registry: MetadataSink, record: ReleaseRecord) -> ReleaseRecord | None:
        """Push release metadata, after the anchor it depends on.

        Queued while partitioned, like an anchor. Metadata for a release the
        registry cannot tie to a countersigned anchor is a claim it cannot
        check.
        """
        self.require_release(record.seq)
        if self.link is LinkState.DOWN:
            self.pending_releases.append(record)
            return None
        return registry.record_release(record)

    # -- capacity ----------------------------------------------------------

    def report_capacity(self, registry: MetadataSink, report: Capacity) -> Capacity | None:
        """Report capacity and health. Dropped rather than queued when down.

        Capacity is a current fact. A queue of stale capacity reports delivered
        on reconnect describes a forge as it was three days ago, which is worse
        than a gap.
        """
        if self.link is LinkState.DOWN:
            return None
        return registry.report_capacity(report)

    # -- state -------------------------------------------------------------

    def partition(self) -> None:
        """Record that the federation link has been lost."""
        if self.link is not LinkState.READ_ONLY:
            self.link = LinkState.DOWN

    def restore(self) -> None:
        """Record that the federation link is back."""
        if self.link is not LinkState.READ_ONLY:
            self.link = LinkState.UP

    def diverged(self) -> None:
        """Record that a divergence was detected. The forge goes read only."""
        self.link = LinkState.READ_ONLY

    def status(self, *, at: datetime) -> Mapping[str, Any]:
        """What the agent reports about itself. Hashes, names and numbers."""
        return sealed(
            {
                "siteId": self.site_id,
                "link": str(self.link),
                "anchoredSeq": self.anchored_seq,
                "queueDepth": self.queue_depth,
                "unanchored": list(self.unanchored),
                "pendingReleases": len(self.pending_releases),
                "policies": sorted(self.policies),
                "reportedAt": at.isoformat(),
            },
            name="agentStatus",
        )


def round_trip_within_budget(elapsed: Iterable[timedelta]) -> tuple[bool, timedelta]:
    """Whether anchor round trips meet AC-N11: under 2s at the 95th percentile.

    The 95th percentile rather than the mean, because the criterion says so and
    because a mean hides the tail that an operator actually experiences.
    """
    samples = sorted(elapsed)
    if not samples:
        msg = "no round trip samples were given"
        raise AgentError(msg)
    index = max(int(len(samples) * 0.95) - 1, 0)
    percentile = samples[index]
    return percentile <= ANCHOR_ROUND_TRIP_BUDGET, percentile


def anchored_through(receipts: Sequence[Receipt]) -> int:
    """The highest sequence successfully anchored in a drain."""
    accepted = [item.anchor.seq for item in receipts if item.accepted and item.anchor is not None]
    return max(accepted, default=0)


def rejected(receipts: Sequence[Receipt]) -> tuple[Receipt, ...]:
    """Every receipt that was not accepted."""
    return tuple(item for item in receipts if item.outcome is AnchorOutcome.REJECTED)
