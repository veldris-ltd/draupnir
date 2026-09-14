"""Artefacts, approvals and releases, folded from the chain. RF-33.

The run registry has always been a projection: the ledger is the record and
`run` is derived from it. Three tables beside it were not. `artefact`,
`approval` and `release` were written by the seed and by nothing else, so on a
seeded stack a published artefact had a release package, a lineage with its
approval and a document to download -- and on an estate it had none of them.
`publishRelease` recorded a `published` entry and wrote no row, and a decision
recorded its transition and wrote no approval.

This is the fold that makes them projections too. Like `projector.project`, it
is pure: the same entries in the same order give the same rows, it never reads
the tables it produces, and a rebuild from sequence 1 and an incremental catch-up
take the same path.

What each row is folded from:

- **An artefact** from an `artefacts` list an entry recorded -- the worker's
  TRAINED adapter, its merged sweep points and its QUANTISED formats -- where
  the item carries an address, a digest, a kind and a size. An item without
  those is not an artefact anybody stored: the procedure hashes files it keeps
  in a work directory, and an older entry recorded no kind or size. Such an item
  is skipped rather than completed with a guess, because the row is a statement
  that these bytes are at this address.
- **An approval** from the decision that moved a run out of AWAITING_APPROVAL.
- **A release** from a `published` entry, bound to the approval it names by the
  sequence number the publication recorded, and dated by the entry. Anchored
  when a countersigned anchor covers the publication. Its documents are the
  ones `downloadReleaseDocument` generates, so the addresses it carries are
  where they are served.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from draupnir.core.domain.federation import ANCHOR_SUBMITTED
from draupnir.core.domain.ledger import LedgerEntry
from draupnir.core.domain.states import RunState

#: A release is about the artefact, not the run (SAD 7.1).
RELEASE_SUBJECT: Final = "release"

#: The transition a publication records against its release.
PUBLISHED: Final = "published"

#: What a decision moves a run to, and the decision it records.
DECISIONS: Final = {
    f"{RunState.AWAITING_APPROVAL}->{RunState.RELEASED}": "APPROVED",
    f"{RunState.AWAITING_APPROVAL}->{RunState.QUARANTINED}": "REJECTED",
}

#: The kinds the `artefact_kind` type admits. An item of any other kind is not
#: projected: the row would be refused, and refusing a whole catch-up over one
#: item would stop every later entry being projected.
ARTEFACT_KINDS: Final = frozenset(
    {
        "corpus_raw",
        "corpus_curated",
        "base_model",
        "substrate",
        "adapter",
        "merged",
        "quantised",
        "report",
    }
)

#: The documents of a release package, by the name `downloadReleaseDocument`
#: serves them under. `tests/unit/test_release_projection.py` holds this to
#: `api.release_documents.DOCUMENTS`, which the core may not import.
DOCUMENTS: Final = ("model-card", "sbom", "lineage", "training-summary", "copyright-policy")

#: Where a projected artefact's identifier comes from: its address, which is
#: unique, so a rebuild gives every artefact the identifier it had before.
_ARTEFACT_NAMESPACE: Final = uuid.UUID("0199a1f0-5c3e-7d2a-9b41-6a1f2e8c4d70")

#: Subjects whose entries name a run as their subject identifier.
_RUN_SUBJECTS: Final = frozenset({"run", "sweep"})


@dataclass(frozen=True, slots=True)
class ProjectedArtefact:
    """One stored artefact."""

    id: UUID
    site_id: str
    kind: str
    uri: str
    sha256: str
    size: int
    created_from_run: UUID | None
    #: Sealed at the put (RF-08), so recorded when the entry was.
    immutable_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectedApproval:
    """One decision on an artefact awaiting approval."""

    id: UUID
    subject_id: UUID
    approver: str
    decision: str
    reason: str | None
    #: Empty for a rejection, which needs none.
    signature: str
    sole_approver_exception: bool
    decided_at: datetime
    #: The artefact decided on, where the decision recorded it.
    artefact_sha256: str | None
    seq: int


@dataclass(frozen=True, slots=True)
class ProjectedRelease:
    """One publication of an approved artefact."""

    id: UUID
    artefact_sha256: str
    artefact_uri: str
    approval_id: UUID
    signature: str
    published_at: datetime
    anchored_at: datetime | None
    documents: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Projected:
    """Everything this fold produces for one chain."""

    artefacts: tuple[ProjectedArtefact, ...] = ()
    approvals: tuple[ProjectedApproval, ...] = ()
    releases: tuple[ProjectedRelease, ...] = ()
    #: Entries that named a release this fold could not bind, and why. Kept so
    #: a missing package is explained rather than silently absent.
    unbound: tuple[str, ...] = field(default=())


def document_address(artefact_sha256: str, document: str) -> str:
    """Where `downloadReleaseDocument` serves one document of a package."""
    return f"/v1/releases/{artefact_sha256}/documents/{document}"


def fold(entries: Iterable[LedgerEntry]) -> Projected:
    """Fold one site's chain, oldest first, into artefacts, approvals and releases."""
    ordered = sorted(entries, key=lambda entry: entry.seq)

    artefacts: dict[str, ProjectedArtefact] = {}
    approvals: dict[int, ProjectedApproval] = {}
    anchors: list[tuple[int, datetime]] = []
    publications: list[LedgerEntry] = []

    for entry in ordered:
        payload = entry.payload if isinstance(entry.payload, Mapping) else {}

        for item in _artefacts_in(entry, payload):
            # Latest wins: an address is written once and sealed, so a second
            # record of it is the same artefact recorded again.
            artefacts[item.uri] = item

        decision = DECISIONS.get(entry.transition)
        if entry.subject_type == "run" and decision is not None:
            approvals[entry.seq] = _approval(entry, payload, decision)

        if entry.transition == ANCHOR_SUBMITTED:
            anchor = _anchor(payload)
            if anchor is not None:
                anchors.append(anchor)

        if entry.subject_type == RELEASE_SUBJECT and entry.transition == PUBLISHED:
            publications.append(entry)

    releases: list[ProjectedRelease] = []
    unbound: list[str] = []
    for entry in publications:
        bound = _release(entry, artefacts, approvals, anchors)
        if isinstance(bound, str):
            unbound.append(bound)
        else:
            releases.append(bound)

    return Projected(
        artefacts=tuple(artefacts.values()),
        approvals=tuple(approvals.values()),
        releases=tuple(releases),
        unbound=tuple(unbound),
    )


def _artefacts_in(entry: LedgerEntry, payload: Mapping[str, Any]) -> Iterable[ProjectedArtefact]:
    listed = payload.get("artefacts")
    if not isinstance(listed, list):
        return
    run_id = _uuid(entry.subject_id) if entry.subject_type in _RUN_SUBJECTS else None
    for item in listed:
        if not isinstance(item, Mapping):
            continue
        uri, digest, kind, size = (
            item.get("uri"),
            item.get("sha256"),
            item.get("kind"),
            item.get("size"),
        )
        if not (isinstance(uri, str) and uri and isinstance(digest, str) and digest):
            continue
        if kind not in ARTEFACT_KINDS or not isinstance(size, int) or isinstance(size, bool):
            continue
        yield ProjectedArtefact(
            id=uuid.uuid5(_ARTEFACT_NAMESPACE, f"{entry.site_id}:{uri}"),
            site_id=entry.site_id,
            kind=str(kind),
            uri=uri,
            sha256=digest,
            size=size,
            created_from_run=run_id,
            immutable_at=entry.ts,
        )


def _approval(entry: LedgerEntry, payload: Mapping[str, Any], decision: str) -> ProjectedApproval:
    reason = payload.get("reason") or payload.get("rejection_reason")
    digest = payload.get("artefact_sha256")
    return ProjectedApproval(
        id=entry.id,
        subject_id=_uuid(entry.subject_id) or entry.id,
        approver=str(payload.get("approver") or entry.actor),
        decision=decision,
        reason=str(reason) if reason else None,
        signature=str(payload.get("signature") or ""),
        sole_approver_exception=payload.get("sole_approver_exception") is True,
        decided_at=_instant(payload.get("decided_at")) or entry.ts,
        artefact_sha256=str(digest) if digest else None,
        seq=entry.seq,
    )


def _release(
    entry: LedgerEntry,
    artefacts: Mapping[str, ProjectedArtefact],
    approvals: Mapping[int, ProjectedApproval],
    anchors: list[tuple[int, datetime]],
) -> ProjectedRelease | str:
    digest = entry.subject_id
    payload = entry.payload if isinstance(entry.payload, Mapping) else {}

    stored = [item for item in artefacts.values() if item.sha256 == digest]
    if not stored:
        return f"seq {entry.seq}: {digest[:12]} was published and no stored artefact has it"

    approval = _approval_for(payload, digest, approvals)
    if approval is None:
        return f"seq {entry.seq}: {digest[:12]} was published and no approval of it is recorded"

    covering = [at for through, at in anchors if through >= entry.seq]
    return ProjectedRelease(
        id=entry.id,
        artefact_sha256=digest,
        # The latest address these bytes were recorded at.
        artefact_uri=stored[-1].uri,
        approval_id=approval.id,
        signature=approval.signature,
        published_at=entry.ts,
        anchored_at=min(covering) if covering else None,
        documents={name: document_address(digest, name) for name in DOCUMENTS},
    )


def _approval_for(
    payload: Mapping[str, Any], digest: str, approvals: Mapping[int, ProjectedApproval]
) -> ProjectedApproval | None:
    """The approval a publication names, or failing that the last one of its bytes."""
    named = payload.get("approved_at_seq")
    if isinstance(named, int) and not isinstance(named, bool):
        found = approvals.get(named)
        if found is not None and found.decision == "APPROVED":
            return found
    matching = [
        item
        for item in approvals.values()
        if item.decision == "APPROVED" and item.artefact_sha256 == digest
    ]
    return max(matching, key=lambda item: item.seq) if matching else None


def _anchor(payload: Mapping[str, Any]) -> tuple[int, datetime] | None:
    """An accepted anchor: how far it countersigned, and when. As `Orchestrator` reads it."""
    through = payload.get("anchored_through") or payload.get("anchoredThrough")
    at = _instant(payload.get("anchored_at") or payload.get("anchoredAt"))
    if at is None or through is None:
        return None
    try:
        return int(through), at
    except (TypeError, ValueError):
        return None


def _instant(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _uuid(value: str) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None
