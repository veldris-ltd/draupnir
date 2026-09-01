"""Release sign-off: signed approvals, and the exception that cannot be hidden.

SAD Decision S6 says no role may both submit a run and approve its release.
Constraint C-11 says that for Release 1 the approver is one named individual
who also submits, so the control is unavailable. The SAD's position is worth
quoting, because it is what this module implements:

    "Every release where the approver also submitted the run carries
    `sole_approver_exception = true`, which appears in the lineage attestation
    and in the model card provenance section. ... A purchaser's technical
    diligence will establish the approval arrangement either way, and finding
    it disclosed in the artefact is a materially different conversation from
    finding it absent."

So the exception is permitted, and it is *computed*, not supplied. There is no
argument to `approve()` that sets it and no configuration that clears it: it
is derived from comparing the approver against the submitter, every time, and
`Approval` is frozen. Suppressing it would mean editing this file, which is a
code review rather than a deployment.

AC-S5 is the other half: publishing without a signed approval record is
refused. An approval carries a signature over the decision, the approver
identity and the policy version in force at the moment of the decision --
because an approval that cannot be evidenced is, at due diligence, worth
nothing.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from draupnir.core.domain.ledger import canonical

SCHEMA = "draupnir/approval/v1"


class Decision(StrEnum):
    """What an approver decided. Matches the `approval` check constraint."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalError(Exception):
    """Raised when an approval cannot be made or cannot be relied upon."""


class UnsignedApprovalError(ApprovalError):
    """Raised when publication is attempted without a signed approval.

    AC-S5. The API maps this to 409; here it is a refusal in the domain, so
    that the same rule holds for the CLI, the worker and anything else that
    ever publishes.
    """

    def __init__(self, subject_id: UUID, reason: str) -> None:
        """Name the subject and why its approval is not usable."""
        self.subject_id = subject_id
        super().__init__(
            f"release of {subject_id} refused: {reason}. Publication requires a signed "
            "approval record (SAD 9.4, AC-S5)."
        )


class RoleError(ApprovalError):
    """Raised when the actor does not hold the approver role."""

    def __init__(self, actor: str) -> None:
        """Name the actor who was refused."""
        self.actor = actor
        super().__init__(
            f"{actor} does not hold the approver role. Deciding gates and publishing "
            "releases belong to `approver`, and to no other role (SAD 9.4)."
        )


@dataclass(frozen=True, slots=True)
class Approval:
    """One signed decision. Attributes per the `approval` entity of SAD 7.1.

    Frozen, and `sole_approver_exception` is set at construction from the facts
    rather than passed in. There is no supported way to record an approval
    whose exception flag disagrees with who submitted the run.
    """

    id: UUID
    subject_id: UUID
    approver: str
    decision: Decision
    signature: str
    #: The version of the policy in force when the decision was taken. A
    #: decision without it is unexplainable once policy moves on.
    policy_version: str
    decided_at: datetime
    #: True when the approver also submitted the run. Computed, never given.
    sole_approver_exception: bool = False
    reason: str | None = None
    submitter: str | None = None

    @property
    def approved(self) -> bool:
        """Whether this decision permits publication."""
        return self.decision is Decision.APPROVED

    def signing_payload(self) -> bytes:
        """The exact bytes the signature covers.

        Includes the exception flag. A signature that did not cover it would
        let the flag be cleared without breaking the signature, which is the
        one edit this whole arrangement exists to prevent.
        """
        return canonical(
            {
                "schema": SCHEMA,
                "subject": str(self.subject_id),
                "approver": self.approver,
                "decision": str(self.decision),
                "policyVersion": self.policy_version,
                "decidedAt": self.decided_at.isoformat(),
                "soleApproverException": self.sole_approver_exception,
            }
        )

    def as_payload(self) -> dict[str, Any]:
        """The ledger payload for this decision."""
        return {
            "approval": str(self.id),
            "subject": str(self.subject_id),
            "approver": self.approver,
            "decision": str(self.decision),
            "policyVersion": self.policy_version,
            "decidedAt": self.decided_at.isoformat(),
            "soleApproverException": self.sole_approver_exception,
            "signature": self.signature,
            "reason": self.reason,
        }

    def for_lineage(self) -> dict[str, Any]:
        """What the lineage attestation carries. AC-S15.

        The exception is present whether or not it is set, so that a reader
        can tell "no exception" from "this system does not record exceptions".
        An absent field is ambiguous; a false one is not.
        """
        return {
            "approver": self.approver,
            "decidedAt": self.decided_at.isoformat(),
            "policyVersion": self.policy_version,
            "soleApproverException": self.sole_approver_exception,
            "soleApproverNote": (SOLE_APPROVER_NOTE if self.sole_approver_exception else None),
        }

    def for_model_card(self) -> str:
        """The provenance paragraph the model card renders. AC-S15."""
        base = (
            f"Released on {self.decided_at.date().isoformat()} under approval "
            f"{self.id}, signed by {self.approver}, against policy "
            f"{self.policy_version}."
        )
        if not self.sole_approver_exception:
            return base
        return f"{base}\n\n{SOLE_APPROVER_NOTE}"


#: The disclosure that travels with an exception. Written once, here, so that
#: the lineage attestation and the model card cannot say different things.
SOLE_APPROVER_NOTE = (
    "Single approver exception: the approver of this release also submitted the "
    "run that produced it. Separation of submission from approval (SAD Decision "
    "S6) was not available for this release, and this is recorded rather than "
    "omitted. See constraint C-11."
)


@runtime_checkable
class Signer(Protocol):
    """What an approval needs from SVALINN in order to be signed."""

    def sign(self, payload: bytes) -> bytes:
        """Return a detached signature over `payload`."""
        ...


def approve(
    *,
    approval_id: UUID,
    subject_id: UUID,
    approver: str,
    submitter: str,
    decision: Decision,
    policy_version: str,
    decided_at: datetime,
    signer: Signer,
    approver_roles: Iterable[str],
    reason: str | None = None,
) -> Approval:
    """Record a signed decision, computing the single approver exception.

    The exception is `approver == submitter`. That comparison happens here and
    nowhere else, it happens on every approval, and its result is inside the
    signed payload.
    """
    roles = frozenset(approver_roles)
    if "approver" not in roles:
        raise RoleError(approver)
    if decided_at.tzinfo is None:
        msg = "approval timestamps carry an explicit offset (SAD 11E.2)"
        raise ApprovalError(msg)
    if not policy_version:
        msg = "an approval records the policy version in force at decision time"
        raise ApprovalError(msg)

    # Decision S6 and constraint C-11. Permitted, recorded, never silent.
    exception = approver == submitter

    unsigned = Approval(
        id=approval_id,
        subject_id=subject_id,
        approver=approver,
        decision=decision,
        signature="",
        policy_version=policy_version,
        decided_at=decided_at,
        sole_approver_exception=exception,
        reason=reason,
        submitter=submitter,
    )

    signature = base64.b64encode(signer.sign(unsigned.signing_payload())).decode("ascii")

    from dataclasses import replace

    return replace(unsigned, signature=signature)


@runtime_checkable
class Verifier(Protocol):
    """What checking an approval needs from SVALINN."""

    def verify(self, signature: bytes, payload: bytes) -> bool:
        """Whether the signature covers the payload."""
        ...


def authorise_release(approval: Approval | None, subject_id: UUID, verifier: Verifier) -> Approval:
    """Raise unless this approval permits publishing `subject_id`. AC-S5.

    Four ways to fail, and each says which: no approval at all, an approval of
    something else, a rejection, and a signature that does not verify. The
    fourth is the one that matters after an incident.
    """
    if approval is None:
        raise UnsignedApprovalError(subject_id, "there is no approval record")

    if approval.subject_id != subject_id:
        raise UnsignedApprovalError(
            subject_id, f"the approval on record is for {approval.subject_id}"
        )

    if not approval.approved:
        raise UnsignedApprovalError(subject_id, f"the approver decided {approval.decision}")

    if not approval.signature:
        raise UnsignedApprovalError(subject_id, "the approval carries no signature")

    try:
        valid = verifier.verify(base64.b64decode(approval.signature), approval.signing_payload())
    except (ValueError, TypeError) as error:
        raise UnsignedApprovalError(
            subject_id, f"the approval signature could not be read: {error}"
        ) from error

    if not valid:
        raise UnsignedApprovalError(
            subject_id,
            "the approval signature does not verify; the record has been altered "
            "since it was signed",
        )

    return approval
