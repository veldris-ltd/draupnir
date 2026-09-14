"""The fold that projects artefacts, approvals and releases from the chain. RF-33.

`artefact`, `approval` and `release` were written by the seed and by nothing
else. These tests pin what the fold makes of the entries the worker and the API
actually record, and what it refuses to complete with a guess.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from draupnir.api import release_documents
from draupnir.core.domain import releases
from draupnir.core.domain.federation import ANCHOR_SUBMITTED
from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry

pytestmark = pytest.mark.unit

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
RUN = uuid.UUID("019cf270-ba80-76c9-84ca-7374e16c7632")
DIGEST = "a" * 64
URI = "hodd://sindri/quantised/run/nvfp4.bin"


def entry(
    seq: int,
    transition: str,
    payload: dict[str, Any],
    *,
    subject_type: str = "run",
    subject_id: str = str(RUN),
    actor: str = "system:worker",
) -> LedgerEntry:
    return LedgerEntry(
        id=uuid.uuid4(),
        site_id="sindri",
        seq=seq,
        prev_hash=GENESIS_HASH,
        entry_hash="b" * 64,
        ts=AT + timedelta(minutes=seq),
        actor=actor,
        subject_type=subject_type,
        subject_id=subject_id,
        transition=transition,
        payload=payload,
    )


def quantised(seq: int = 1) -> LedgerEntry:
    return entry(
        seq,
        "MERGED->QUANTISED",
        {"artefacts": [{"uri": URI, "sha256": DIGEST, "kind": "quantised", "size": 1024}]},
    )


def approved(seq: int = 2, **extra: Any) -> LedgerEntry:
    return entry(
        seq,
        "AWAITING_APPROVAL->RELEASED",
        {
            "approver": "akuma",
            "signature": "c" * 64,
            "decided_at": (AT + timedelta(minutes=1, seconds=30)).isoformat(),
            "sole_approver_exception": True,
            "artefact_sha256": DIGEST,
            **extra,
        },
    )


def published(seq: int = 3, **payload: Any) -> LedgerEntry:
    return entry(
        seq,
        releases.PUBLISHED,
        {"artefact_sha256": DIGEST, **payload},
        subject_type=releases.RELEASE_SUBJECT,
        subject_id=DIGEST,
        actor="akuma",
    )


def anchor(seq: int, through: int, at: datetime) -> LedgerEntry:
    return entry(
        seq,
        ANCHOR_SUBMITTED,
        {"anchored_through": through, "anchored_at": at.isoformat()},
        subject_type="site",
        subject_id="sindri",
    )


# ---------------------------------------------------------------------------
# Artefacts
# ---------------------------------------------------------------------------


def test_a_recorded_artefact_with_an_address_kind_and_size_is_a_row() -> None:
    (artefact,) = releases.fold([quantised()]).artefacts

    assert artefact.uri == URI
    assert artefact.sha256 == DIGEST
    assert artefact.kind == "quantised"
    assert artefact.size == 1024
    assert artefact.created_from_run == RUN


def test_the_identifier_is_derived_from_the_address_so_a_rebuild_keeps_it() -> None:
    first = releases.fold([quantised()]).artefacts[0].id
    second = releases.fold([quantised(seq=7)]).artefacts[0].id

    assert first == second


@pytest.mark.parametrize(
    "item",
    [
        {"uri": "", "sha256": DIGEST, "kind": "quantised", "size": 1},
        {"uri": URI, "sha256": DIGEST, "size": 1},
        {"uri": URI, "sha256": DIGEST, "kind": "quantised"},
        {"uri": URI, "sha256": DIGEST, "kind": "weights", "size": 1},
        {"uri": URI, "sha256": DIGEST, "kind": "quantised", "size": True},
    ],
)
def test_an_item_nobody_stored_is_not_completed_with_a_guess(item: dict[str, Any]) -> None:
    """No address, no kind, an unknown kind, no size: a row would be a claim."""
    projected = releases.fold([entry(1, "MERGED->QUANTISED", {"artefacts": [item]})])

    assert projected.artefacts == ()


def test_a_merged_sweep_point_belongs_to_its_run() -> None:
    sweep = entry(
        1,
        "sweep.evaluated",
        {"artefacts": [{"uri": URI, "sha256": DIGEST, "kind": "merged", "size": 9}]},
        subject_type="sweep",
    )

    assert releases.fold([sweep]).artefacts[0].created_from_run == RUN


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


def test_an_approval_is_the_decision_that_released_the_run() -> None:
    decision = approved()
    (approval,) = releases.fold([decision]).approvals

    assert approval.id == decision.id
    assert approval.subject_id == RUN
    assert approval.decision == "APPROVED"
    assert approval.approver == "akuma"
    assert approval.sole_approver_exception is True
    assert approval.decided_at == AT + timedelta(minutes=1, seconds=30)
    assert approval.artefact_sha256 == DIGEST


def test_a_rejection_is_an_approval_row_with_no_signature() -> None:
    """A rejection needs no signature, and the row says so rather than inventing one."""
    rejection = entry(
        1,
        "AWAITING_APPROVAL->QUARANTINED",
        {"rejection_reason": "no DPIA reference", "approver": "akuma"},
    )
    (approval,) = releases.fold([rejection]).approvals

    assert approval.decision == "REJECTED"
    assert approval.signature == ""
    assert approval.reason == "no DPIA reference"


# ---------------------------------------------------------------------------
# Releases
# ---------------------------------------------------------------------------


def test_a_publication_binds_to_the_approval_it_names() -> None:
    decision = approved(seq=2)
    projected = releases.fold([quantised(), decision, published(approved_at_seq=2)])

    (release,) = projected.releases
    assert release.approval_id == decision.id
    assert release.artefact_uri == URI
    assert release.signature == "c" * 64
    assert release.published_at == AT + timedelta(minutes=3)


def test_a_publication_naming_no_approval_binds_to_the_last_one_of_its_bytes() -> None:
    earlier, later = approved(seq=2), approved(seq=3)

    (release,) = releases.fold([quantised(), earlier, later, published(seq=4)]).releases

    assert release.approval_id == later.id


def test_a_publication_with_nothing_to_bind_to_is_explained_not_projected() -> None:
    no_artefact = releases.fold([approved(), published()])
    no_approval = releases.fold([quantised(), published()])

    assert no_artefact.releases == ()
    assert "no stored artefact" in no_artefact.unbound[0]
    assert no_approval.releases == ()
    assert "no approval" in no_approval.unbound[0]


def test_a_rejection_is_never_what_a_publication_binds_to() -> None:
    rejection = entry(
        2, "AWAITING_APPROVAL->QUARANTINED", {"approver": "akuma", "artefact_sha256": DIGEST}
    )

    projected = releases.fold([quantised(), rejection, published(approved_at_seq=2)])

    assert projected.releases == ()


def test_a_release_is_anchored_when_a_countersigned_anchor_covers_it() -> None:
    before = anchor(4, through=2, at=AT + timedelta(hours=1))
    covering = anchor(5, through=6, at=AT + timedelta(hours=2))
    later = anchor(6, through=9, at=AT + timedelta(hours=3))

    (release,) = releases.fold(
        [quantised(), approved(), published(), before, covering, later]
    ).releases

    assert release.anchored_at == AT + timedelta(hours=2)


def test_an_unanchored_release_says_so() -> None:
    (release,) = releases.fold([quantised(), approved(), published()]).releases

    assert release.anchored_at is None


def test_the_document_addresses_are_the_ones_the_api_serves() -> None:
    """The core may not import the API, so the two lists are held together here."""
    assert set(releases.DOCUMENTS) == set(release_documents.DOCUMENTS)

    (release,) = releases.fold([quantised(), approved(), published()]).releases
    assert release.documents["sbom"] == f"/v1/releases/{DIGEST}/documents/sbom"


def test_the_fold_reads_entries_in_sequence_order_whatever_order_it_is_given() -> None:
    ordered = [quantised(), approved(), published()]

    assert releases.fold(reversed(ordered)).releases == releases.fold(ordered).releases
