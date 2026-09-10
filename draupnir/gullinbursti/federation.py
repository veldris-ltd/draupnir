"""Reaching MEGINGJORD, through the broker. RF-07.

`gullinbursti.agent` is complete and was never constructed. `megingjord.anchors`
and `megingjord.registry` were orphans. There was no outbound HTTP client
anywhere in `draupnir/` — so `last_anchored_at` was always `None`, and the
freshness duty returned its "this site has never anchored its chain" alarm on
every tick, forever, while its own message said "publication is refused while
the anchor is stale" and nothing refused anything.

This is the wire. It is deliberately thin: the agent decides what to submit and
in what order, `megingjord.anchors` decides whether a head is continuous, and
this only carries bytes between them.

**Every call goes through the egress broker**, with a declared destination,
purpose and approving policy. RF-07 called this "the first real outbound call
in the system and it should establish the pattern" — by the time it was built,
RF-01's JWKS fetch and RF-E15's telemetry read had established it, so this
follows rather than sets it. The client is injected for the same reason theirs
are: the composition root supplies one that goes through the broker, and a
client built here would be a call nobody decided.

**A failure is a finding, never an exception that reaches a run.** Decision S8:
a partitioned forge trains and evaluates and does not release. So every failure
below returns a `Receipt` with an outcome and a reason, and the agent queues
the head for the reconnect path — which is the same code the ordinary path
uses, because a path only exercised during an outage is a path that does not
work.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Protocol

from draupnir.core.domain.federation import (
    Anchor,
    AnchorOutcome,
    AnchorSubmission,
    Receipt,
    sealed,
)

#: Where the registry answers. Not site scoped: SAD 11A makes MEGINGJORD one
#: registry for the whole Forge Matrix, and it is reached over the federation
#: link rather than from within a forge's zone.
DEFAULT_REGISTRY: Final = "https://megingjord.veldris.internal"

#: The path an anchor is submitted to.
ANCHOR_PATH: Final = "/federation/v1/anchors"

#: How long to wait. Longer than a request to a local service, because this one
#: crosses a WireGuard link to another site; short enough that a worker tick
#: does not stall behind an unreachable registry.
TIMEOUT_SECONDS: Final = 15

#: What the call declares to the broker. The same purpose and policy the JWKS
#: fetch cites, because it is the same host reached over the same link under
#: the same federation agreement.
EGRESS_PURPOSE: Final = (
    "chain-head anchoring, policy pull, release metadata push, and the JWKS this API "
    "verifies bearer tokens against"
)
EGRESS_POLICY: Final = "federation/2026.01"

_OK: Final = 200


class Response(Protocol):
    """The part of an HTTP response this needs."""

    status_code: int

    def json(self) -> Any:
        """The decoded body."""
        ...


class Client(Protocol):
    """The part of an HTTP client this needs."""

    def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any] | None = ...,
        headers: Mapping[str, str] | None = ...,
    ) -> Response:
        """Submit one document."""
        ...


@dataclass
class RemoteRegistry:
    """MEGINGJORD's anchoring half, over HTTP.

    Satisfies `agent.Countersigner`, so `Gullinbursti.drain` cannot tell this
    from the in-process `AnchorStore` the tests use — which is the property
    that makes the reconnect path testable without a network.
    """

    base_url: str = DEFAULT_REGISTRY
    client: Client | None = field(default=None, repr=False)

    def countersign(
        self, head: AnchorSubmission, *, at: datetime, countersignature: str
    ) -> Receipt:
        """Submit one head and read back what the registry said."""
        del countersignature  # The registry produces its own; this carries it back.

        if self.client is None:
            return Receipt(
                outcome=AnchorOutcome.REJECTED,
                anchor=None,
                reason=(
                    "no federation client is configured, so nothing was submitted. The "
                    "head stays queued and is drained when the link exists."
                ),
            )

        # Through `sealed`, and there is no second construction path. Everything
        # crossing the federation boundary is a hash, a name, a timestamp or a
        # number, and making the check the constructor means a payload that
        # skipped it is a payload that was never built.
        body = sealed(
            {
                "siteId": head.head.site_id,
                "seq": head.head.seq,
                "entryHash": head.head.entry_hash,
                "previousHash": head.previous_hash,
                "submittedAt": at.isoformat(),
                "keyId": head.key_id,
            },
            name="anchor submission",
        )

        # The signature travels beside the body rather than inside it, and that
        # is `sealed`'s doing rather than a convenience. It admits hashes,
        # names, timestamps and numbers; a 128-character Ed25519 signature is
        # none of those, and it is refused as an encoded run -- correctly,
        # because the check cannot tell a signature from a slice of a weight
        # tensor and should not try. Widening it to admit one would weaken a
        # content control in order to carry a credential.
        #
        # So the body stays inspectable by anything that reads federation
        # traffic, and the signature is a header over exactly those bytes.
        headers = {"X-Veldris-Signature": head.signature, "X-Veldris-Key-Id": head.key_id}

        try:
            answer = self.client.post(
                f"{self.base_url.rstrip('/')}{ANCHOR_PATH}", json=body, headers=headers
            )
        except Exception as error:
            # Broad, and every one of them means the same thing: the registry
            # could not be reached. A partition is a degraded mode (Decision
            # S8), so this is a receipt rather than a raise.
            return Receipt(
                outcome=AnchorOutcome.REJECTED,
                anchor=None,
                reason=f"{self.base_url} could not be reached: {type(error).__name__}",
            )

        if answer.status_code != _OK:
            return Receipt(
                outcome=AnchorOutcome.REJECTED,
                anchor=None,
                reason=f"the registry answered {answer.status_code} for sequence {head.head.seq}",
            )

        try:
            payload = answer.json()
        except Exception:
            return Receipt(
                outcome=AnchorOutcome.REJECTED,
                anchor=None,
                reason="the registry returned a body that is not JSON",
            )

        return _receipt_from(payload, head=head)


def _receipt_from(payload: Any, *, head: AnchorSubmission) -> Receipt:
    """Read the registry's answer, refusing anything that is not one.

    The outcome vocabulary is the registry's — countersigned, duplicate,
    diverged, rejected — and there is no "unreachable" among them, deliberately:
    the agent's queue does not care *why* a head was not anchored, only that it
    was not. So an unreachable registry is a rejection carrying a reason that
    says as much, and the reason is what an operator reads. A `diverged`
    outcome is the one that means something different and is passed through
    unchanged: it is the registry saying this site's chain and its record of it
    do not agree, which is the thing SAD 11A.3 exists to surface.
    """
    if not isinstance(payload, Mapping):
        return Receipt(AnchorOutcome.REJECTED, None, "the registry's answer is not an object")

    raw = str(payload.get("outcome") or "")
    try:
        outcome = AnchorOutcome(raw)
    except ValueError:
        return Receipt(
            AnchorOutcome.REJECTED, None, f"the registry reported an unknown outcome {raw!r}"
        )

    reason = str(payload.get("reason") or "")
    if outcome not in {AnchorOutcome.COUNTERSIGNED, AnchorOutcome.DUPLICATE}:
        return Receipt(outcome, None, reason or f"the registry refused sequence {head.seq}")

    signature = str(payload.get("countersignature") or "")
    if not signature:
        # Countersigned and unsigned is not countersigned. Accepting it would
        # record an anchor nobody can verify, which is worse than no anchor:
        # the freshness duty would go quiet and the truncation SAD 11A.3 exists
        # to detect would go unnoticed.
        return Receipt(
            AnchorOutcome.REJECTED,
            None,
            f"the registry reported {outcome} for sequence {head.head.seq} and returned no "
            "countersignature. An anchor nobody can verify is worse than no anchor: it "
            "silences the alarm without providing the evidence.",
        )

    return Receipt(
        outcome,
        Anchor(
            head=head,
            countersigned_at=_moment(payload.get("anchoredAt")),
            countersignature=signature,
            outcome=outcome,
        ),
        reason,
    )


def _moment(raw: Any) -> datetime:
    """An offset-aware instant from the registry's answer."""
    from datetime import UTC

    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return datetime.now(UTC)


__all__ = [
    "ANCHOR_PATH",
    "DEFAULT_REGISTRY",
    "EGRESS_POLICY",
    "EGRESS_PURPOSE",
    "TIMEOUT_SECONDS",
    "Client",
    "RemoteRegistry",
    "Response",
]
