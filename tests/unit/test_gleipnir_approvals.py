"""Release sign-off: AC-S5, AC-S15, and the exception that cannot be hidden.

AC-S5: "Publishing without a signed approval record returns 409. The same
identity submitting and approving is permitted only where a recorded single
approver exception exists, and the exception is visible in the lineage."

AC-S15: "Every release where the approver also submitted the run carries the
sole approver exception, and it is visible in the lineage output and the model
card."

The 409 is the API's business and arrives with Prompt 7. What is testable now
is the refusal itself, which lives in the domain so that the CLI and the
worker are bound by it too.

The tests that matter most here are the ones that try to get a release out
without the exception being visible: setting the flag false at construction,
clearing it after signing, and editing the record. All three have to fail.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from draupnir.gleipnir.approvals import (
    SOLE_APPROVER_NOTE,
    Approval,
    ApprovalError,
    Decision,
    RoleError,
    UnsignedApprovalError,
    approve,
    authorise_release,
)
from draupnir.svalinn.signing import generate_key_pair, load_private_key, load_public_key

DECIDED = datetime(2026, 4, 11, 14, 30, tzinfo=UTC)
AKUMA = "akuma@veldris.internal"
OPERATOR = "operator@veldris.internal"


class Ed25519Signer:
    """SVALINN's signing, as an approval sees it."""

    def __init__(self, private_pem: bytes) -> None:
        self._key = load_private_key(private_pem)

    def sign(self, payload: bytes) -> bytes:
        return self._key.sign(payload)


class Ed25519Verifier:
    """SVALINN's verification, as a release check sees it."""

    def __init__(self, public_pem: bytes) -> None:
        self._key = load_public_key(public_pem)

    def verify(self, signature: bytes, payload: bytes) -> bool:
        from cryptography.exceptions import InvalidSignature

        try:
            self._key.verify(signature, payload)
        except InvalidSignature:
            return False
        return True


@pytest.fixture(scope="module")
def keys() -> tuple[bytes, bytes]:
    return generate_key_pair()


@pytest.fixture
def signer(keys: tuple[bytes, bytes]) -> Ed25519Signer:
    return Ed25519Signer(keys[0])


@pytest.fixture
def verifier(keys: tuple[bytes, bytes]) -> Ed25519Verifier:
    return Ed25519Verifier(keys[1])


def make_approval(
    signer: Ed25519Signer,
    *,
    approver: str = AKUMA,
    submitter: str = OPERATOR,
    decision: Decision = Decision.APPROVED,
    subject: UUID | None = None,
) -> Approval:
    return approve(
        approval_id=uuid4(),
        subject_id=subject or uuid4(),
        approver=approver,
        submitter=submitter,
        decision=decision,
        policy_version="gleipnir-licence/2026.01",
        decided_at=DECIDED,
        signer=signer,
        approver_roles={"approver"},
    )


# ---------------------------------------------------------------------------
# AC-S5: no publication without a signed approval
# ---------------------------------------------------------------------------


def test_a_signed_approval_authorises_the_release(
    signer: Ed25519Signer, verifier: Ed25519Verifier
) -> None:
    subject = uuid4()
    approval = make_approval(signer, subject=subject)
    assert authorise_release(approval, subject, verifier) is approval


def test_publishing_without_any_approval_is_refused(verifier: Ed25519Verifier) -> None:
    with pytest.raises(UnsignedApprovalError, match="no approval record"):
        authorise_release(None, uuid4(), verifier)


def test_publishing_on_another_subjects_approval_is_refused(
    signer: Ed25519Signer, verifier: Ed25519Verifier
) -> None:
    approval = make_approval(signer, subject=uuid4())
    with pytest.raises(UnsignedApprovalError, match="is for"):
        authorise_release(approval, uuid4(), verifier)


def test_publishing_on_a_rejection_is_refused(
    signer: Ed25519Signer, verifier: Ed25519Verifier
) -> None:
    subject = uuid4()
    approval = make_approval(signer, decision=Decision.REJECTED, subject=subject)
    with pytest.raises(UnsignedApprovalError, match="REJECTED"):
        authorise_release(approval, subject, verifier)


def test_publishing_on_an_unsigned_record_is_refused(verifier: Ed25519Verifier) -> None:
    subject = uuid4()
    unsigned = Approval(
        id=uuid4(),
        subject_id=subject,
        approver=AKUMA,
        decision=Decision.APPROVED,
        signature="",
        policy_version="gleipnir-licence/2026.01",
        decided_at=DECIDED,
    )
    with pytest.raises(UnsignedApprovalError, match="no signature"):
        authorise_release(unsigned, subject, verifier)


def test_an_altered_approval_no_longer_verifies(
    signer: Ed25519Signer, verifier: Ed25519Verifier
) -> None:
    subject = uuid4()
    approval = make_approval(signer, subject=subject)

    # Somebody edits the approver after the fact.
    tampered = replace(approval, approver="someone.else@veldris.internal")
    with pytest.raises(UnsignedApprovalError, match="altered"):
        authorise_release(tampered, subject, verifier)


def test_a_signature_from_another_key_is_refused(signer: Ed25519Signer) -> None:
    subject = uuid4()
    approval = make_approval(signer, subject=subject)
    _, other_public = generate_key_pair()

    with pytest.raises(UnsignedApprovalError, match="does not verify"):
        authorise_release(approval, subject, Ed25519Verifier(other_public))


def test_a_malformed_signature_is_refused_rather_than_crashing(
    signer: Ed25519Signer, verifier: Ed25519Verifier
) -> None:
    subject = uuid4()
    approval = replace(make_approval(signer, subject=subject), signature="!!not base64!!")
    with pytest.raises(UnsignedApprovalError):
        authorise_release(approval, subject, verifier)


def test_only_the_approver_role_may_approve(signer: Ed25519Signer) -> None:
    # SAD 9.4: deciding gates and publishing releases belong to `approver`.
    with pytest.raises(RoleError, match="approver role"):
        approve(
            approval_id=uuid4(),
            subject_id=uuid4(),
            approver=OPERATOR,
            submitter=OPERATOR,
            decision=Decision.APPROVED,
            policy_version="gleipnir-licence/2026.01",
            decided_at=DECIDED,
            signer=signer,
            approver_roles={"operator", "viewer"},
        )


def test_an_approval_records_the_policy_version_in_force(signer: Ed25519Signer) -> None:
    with pytest.raises(ApprovalError, match="policy version"):
        approve(
            approval_id=uuid4(),
            subject_id=uuid4(),
            approver=AKUMA,
            submitter=OPERATOR,
            decision=Decision.APPROVED,
            policy_version="",
            decided_at=DECIDED,
            signer=signer,
            approver_roles={"approver"},
        )


# ---------------------------------------------------------------------------
# AC-S15 and constraint C-11: the single approver exception
# ---------------------------------------------------------------------------


def test_separate_submitter_and_approver_carries_no_exception(
    signer: Ed25519Signer,
) -> None:
    approval = make_approval(signer, approver=AKUMA, submitter=OPERATOR)
    assert not approval.sole_approver_exception
    assert approval.for_lineage()["soleApproverException"] is False
    assert approval.for_lineage()["soleApproverNote"] is None
    assert SOLE_APPROVER_NOTE not in approval.for_model_card()


def test_the_same_identity_submitting_and_approving_is_permitted_and_recorded(
    signer: Ed25519Signer, verifier: Ed25519Verifier
) -> None:
    """Constraint C-11. Permitted, because the alternative is not shipping."""
    subject = uuid4()
    approval = make_approval(signer, approver=AKUMA, submitter=AKUMA, subject=subject)

    assert approval.sole_approver_exception
    # Permitted: the release goes ahead.
    assert authorise_release(approval, subject, verifier) is approval


def test_the_exception_is_visible_in_the_lineage(signer: Ed25519Signer) -> None:
    """AC-S15, first half."""
    lineage = make_approval(signer, approver=AKUMA, submitter=AKUMA).for_lineage()
    assert lineage["soleApproverException"] is True
    assert lineage["soleApproverNote"] == SOLE_APPROVER_NOTE
    assert "C-11" in lineage["soleApproverNote"]


def test_the_exception_is_visible_in_the_model_card(signer: Ed25519Signer) -> None:
    """AC-S15, second half."""
    card = make_approval(signer, approver=AKUMA, submitter=AKUMA).for_model_card()
    assert SOLE_APPROVER_NOTE in card
    assert "also submitted the run" in card


def test_the_lineage_field_is_present_even_when_the_exception_is_not_set(
    signer: Ed25519Signer,
) -> None:
    # An absent field cannot be told from a system that does not record
    # exceptions. A false one can.
    assert "soleApproverException" in make_approval(signer).for_lineage()


def test_the_exception_cannot_be_supplied_rather_than_computed(
    signer: Ed25519Signer,
) -> None:
    # There is no argument for it. It is derived from comparing the approver
    # against the submitter, every time.
    import inspect

    parameters = set(inspect.signature(approve).parameters)
    assert "sole_approver_exception" not in parameters
    assert {"approver", "submitter"} <= parameters


def test_clearing_the_exception_breaks_the_signature(
    signer: Ed25519Signer, verifier: Ed25519Verifier
) -> None:
    """The edit this whole arrangement exists to prevent.

    The flag is inside the signed payload, so suppressing it after the fact
    invalidates the signature and the release is refused.
    """
    subject = uuid4()
    approval = make_approval(signer, approver=AKUMA, submitter=AKUMA, subject=subject)
    assert approval.sole_approver_exception

    suppressed = replace(approval, sole_approver_exception=False)
    with pytest.raises(UnsignedApprovalError, match="altered"):
        authorise_release(suppressed, subject, verifier)


def test_an_approval_is_frozen(signer: Ed25519Signer) -> None:
    approval = make_approval(signer)
    with pytest.raises(AttributeError):
        approval.sole_approver_exception = False  # type: ignore[misc]


def test_the_ledger_payload_carries_the_exception(signer: Ed25519Signer) -> None:
    payload = make_approval(signer, approver=AKUMA, submitter=AKUMA).as_payload()
    assert payload["soleApproverException"] is True
    assert payload["policyVersion"] == "gleipnir-licence/2026.01"
    assert payload["signature"]
