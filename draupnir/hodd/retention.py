"""Retention: deletion is an approved, ledgered action, never a cron job.

SAD 7.3: raw corpora are retained for 24 months from the release of the last
model derived from them, "then deleted under an approved and ledgered
retention action rather than by an unattended job."

Two rules follow, and both are enforced here rather than described.

The first is what survives. Deleting a raw corpus means the derived model can
afterwards be *verified* but no longer *re-derived*, so the curated manifests,
the per-source hashes and the licence register entries are kept indefinitely.
The Article 53 training content summary is built from those, and it must
outlive the text it summarises.

The second is what is refused. "A retention action that would break a lineage
chain is refused." AC-F20 requires the affected release to be named, which is
why `LineageIndex` exists: the check is not "is anything derived from this",
it is "which release stops resolving if this goes", and the answer is a list
of releases an operator can look at.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

#: SAD 7.3. Measured from the release of the last derived model, not from
#: ingest: a corpus whose model shipped last month has 24 months left however
#: old the text is.
RETENTION_MONTHS = 24

#: Days per month, for a duration nobody needs to the hour. A retention clock
#: measured in calendar months would make "is it due" depend on which month it
#: is, and the answer would change under a reader.
DAYS_PER_MONTH = 30
RETENTION = timedelta(days=RETENTION_MONTHS * DAYS_PER_MONTH)


class RetentionPolicy(StrEnum):
    """What a retention action is doing, and to what."""

    #: Delete the raw text. Manifests, hashes and licence entries are kept.
    RAW_CORPUS = "raw-corpus-24-months"
    #: Delete an intermediate checkpoint no release depends on.
    INTERMEDIATE = "intermediate-checkpoint"


class RetentionError(Exception):
    """Raised when a retention action cannot be performed."""


class LineageBreakError(RetentionError):
    """Raised when deletion would break a lineage chain. Names the releases.

    AC-F20. The releases are the point: an operator told "this would break
    lineage" asks which one, and an operator told which one can decide.
    """

    def __init__(self, artefact_uri: str, releases: Iterable[str]) -> None:
        """Record the artefact and every release that depends on it."""
        self.artefact_uri = artefact_uri
        self.releases = tuple(sorted(releases))
        listed = ", ".join(self.releases)
        super().__init__(
            f"deleting {artefact_uri} would break the lineage of {len(self.releases)} "
            f"release(s): {listed}. Lineage for every derived release must remain "
            "complete after a retention action (SAD 7.3, AC-F20)."
        )


class NotDueError(RetentionError):
    """Raised when a retention action is attempted before its due date."""

    def __init__(self, artefact_uri: str, due_at: datetime, now: datetime) -> None:
        """Record when it becomes due."""
        self.artefact_uri = artefact_uri
        self.due_at = due_at
        remaining = due_at - now
        super().__init__(
            f"{artefact_uri} is not due for retention until {due_at.isoformat()}, "
            f"in {remaining.days} days. Retention runs {RETENTION_MONTHS} months from "
            "the release of the last derived model (SAD 7.3)."
        )


class UnapprovedError(RetentionError):
    """Raised when a retention action has no approval.

    Deletion is "an approved and ledgered retention action rather than an
    unattended job", and an action without an approver is the unattended job.
    """

    def __init__(self, artefact_uri: str) -> None:
        """Name what was not deleted."""
        self.artefact_uri = artefact_uri
        super().__init__(
            f"{artefact_uri} was not deleted: a retention action requires a recorded "
            "approver. Deletion is an approved, ledgered action, never a cron job "
            "(SAD 7.3)."
        )


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LineageEdge:
    """One artefact derived from another."""

    artefact_uri: str
    derived_from: tuple[str, ...] = ()


class LineageIndex:
    """Which releases depend on which artefacts, transitively.

    Built from edges rather than queried from the database, so the same check
    runs in a unit test and against a live estate without either being a
    different check.
    """

    def __init__(
        self,
        edges: Iterable[LineageEdge] = (),
        releases: Mapping[str, str] | None = None,
    ) -> None:
        """Take the derivation edges and which artefacts are released.

        `releases` maps an artefact URI to the release identifier that
        published it.
        """
        self._parents: dict[str, tuple[str, ...]] = {
            edge.artefact_uri: edge.derived_from for edge in edges
        }
        self._releases = dict(releases or {})

    def ancestors(self, artefact_uri: str) -> frozenset[str]:
        """Every artefact this one was derived from, transitively."""
        seen: set[str] = set()
        pending = [artefact_uri]
        while pending:
            current = pending.pop()
            for parent in self._parents.get(current, ()):
                if parent not in seen:
                    seen.add(parent)
                    pending.append(parent)
        return frozenset(seen)

    def releases_depending_on(self, artefact_uri: str) -> tuple[str, ...]:
        """Every release whose lineage passes through this artefact.

        Includes a release *of* the artefact itself: deleting a released
        artefact breaks its own lineage as surely as deleting its corpus.
        """
        affected = {
            release
            for released_uri, release in self._releases.items()
            if released_uri == artefact_uri or artefact_uri in self.ancestors(released_uri)
        }
        return tuple(sorted(affected))

    def is_load_bearing(self, artefact_uri: str) -> bool:
        """Whether any release depends on this artefact."""
        return bool(self.releases_depending_on(artefact_uri))


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def due_at(last_release_at: datetime) -> datetime:
    """When a corpus becomes eligible for deletion. SAD 7.3."""
    if last_release_at.tzinfo is None:
        msg = "retention timestamps carry an explicit offset (SAD 11E.2)"
        raise RetentionError(msg)
    return last_release_at + RETENTION


@dataclass(frozen=True, slots=True)
class RetentionAction:
    """One approved deletion. Attributes per the `retention_action` of SAD 7.1."""

    id: UUID
    subject_id: UUID
    artefact_uri: str
    policy: RetentionPolicy
    due_at: datetime
    approved_by: str | None = None
    executed_at: datetime | None = None
    #: Always true for a raw corpus deletion: the manifests, per-source hashes
    #: and licence entries are what let lineage survive the text.
    manifests_retained: bool = True
    #: What was kept, named, so the ledger entry says it rather than implying it.
    retained: tuple[str, ...] = field(default_factory=tuple)

    @property
    def approved(self) -> bool:
        """Whether an approver has been recorded."""
        return bool(self.approved_by)

    def as_payload(self) -> dict[str, Any]:
        """The ledger payload for this action."""
        return {
            "artefact": self.artefact_uri,
            "policy": str(self.policy),
            "dueAt": self.due_at.isoformat(),
            "approvedBy": self.approved_by,
            "manifestsRetained": self.manifests_retained,
            "retained": list(self.retained),
        }


#: What a raw corpus deletion keeps. SAD 7.3 names all three.
RETAINED_BY_RAW_CORPUS_DELETION = (
    "curated manifests",
    "per-source SHA-256 hashes",
    "licence register entries",
)


def plan(
    *,
    action_id: UUID,
    subject_id: UUID,
    artefact_uri: str,
    last_release_at: datetime,
    policy: RetentionPolicy = RetentionPolicy.RAW_CORPUS,
    approved_by: str | None = None,
) -> RetentionAction:
    """Build a retention action, due 24 months after the last derived release."""
    return RetentionAction(
        id=action_id,
        subject_id=subject_id,
        artefact_uri=artefact_uri,
        policy=policy,
        due_at=due_at(last_release_at),
        approved_by=approved_by,
        retained=RETAINED_BY_RAW_CORPUS_DELETION if policy is RetentionPolicy.RAW_CORPUS else (),
    )


def authorise(
    action: RetentionAction,
    lineage: LineageIndex,
    *,
    now: datetime,
) -> RetentionAction:
    """Raise unless this action may be executed. Returns it unchanged if it may.

    Three refusals, in the order an operator meets them: an unapproved action,
    an action before its due date, and an action that would break lineage. The
    third is checked last because it is the most expensive to compute and the
    least likely to be the reason.
    """
    if not action.approved:
        raise UnapprovedError(action.artefact_uri)

    if now < action.due_at:
        raise NotDueError(action.artefact_uri, action.due_at, now)

    affected = lineage.releases_depending_on(action.artefact_uri)
    if affected and action.policy is not RetentionPolicy.RAW_CORPUS:
        raise LineageBreakError(action.artefact_uri, affected)

    return action


def execute(
    action: RetentionAction,
    lineage: LineageIndex,
    store: Any,
    *,
    now: datetime,
    manifest_survives: bool = True,
) -> RetentionAction:
    """Delete the artefact, having established that it may be deleted.

    A raw corpus deletion is permitted even where releases depend on it --
    that is the whole point of the 24 month rule -- *provided* the manifests
    survive, because that is what keeps the lineage resolvable. If they would
    not survive, the deletion breaks lineage and is refused, naming the
    releases (AC-F20).
    """
    authorise(action, lineage, now=now)

    affected = lineage.releases_depending_on(action.artefact_uri)
    if affected and not manifest_survives:
        raise LineageBreakError(action.artefact_uri, affected)

    store.delete(action.artefact_uri)

    return replace(action, executed_at=now, manifests_retained=manifest_survives)


def survivors(action: RetentionAction) -> tuple[str, ...]:
    """What remains after this action. Empty for an intermediate checkpoint."""
    return action.retained


# ---------------------------------------------------------------------------
# The record: a retention action as the chain holds it. RF-27.
# ---------------------------------------------------------------------------
#
# SAD 7.1 gives retention a `retention_action` table, and nothing wrote it. The
# daily duty recorded its proposals in the chain, S06 read a table that stayed
# empty, and the one button on the screen closed a dialog. `authorise` and
# `execute` above were called by unit tests and by nothing else.
#
# So an action is folded from its entries -- proposed, approved, executed or
# refused -- the way RF-13 folded the array, rather than projected into a
# second place that would disagree with the chain the first time either was
# rebuilt. Here rather than in the worker because the API folds the same
# entries, and the API may not import the worker.

#: The subject every retention entry is recorded against: the corpus, by digest.
CORPUS_SUBJECT = "corpus"
#: The duty found a corpus past retention. Deletes nothing.
PROPOSED = "retention.due"
#: An approver approved the deletion, answering the proposal.
APPROVED = "retention.approved"
#: The worker deleted the raw corpus, and the manifests were retained.
EXECUTED = "retention.executed"
#: The worker did not delete it, and says why -- naming the releases, where
#: deleting would have broken their lineage (AC-F20).
REFUSED = "retention.refused"
#: How an approval, an execution or a refusal names the proposal it answers.
#: The same key `worker.accepted` uses, for the same reason.
ANSWERS = "answers_seq"


class ProposalState(StrEnum):
    """Where one retention action has reached."""

    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    EXECUTED = "EXECUTED"
    REFUSED = "REFUSED"


@dataclass(frozen=True, slots=True)
class Proposal:
    """One retention action, folded from the entries about it."""

    #: The proposal entry's identifier, which is the action's identifier.
    id: UUID
    #: The proposal entry's sequence, which every later entry answers.
    seq: int
    corpus_sha256: str
    #: The raw corpus a deletion removes. Empty where the proposal could not
    #: name one, and then nothing is deleted.
    artefact_uri: str
    jurisdiction: str
    due_at: datetime
    #: The runs whose releases were built from this corpus.
    releases: tuple[str, ...]
    curated_by: str = ""
    state: ProposalState = ProposalState.PROPOSED
    approved_by: str | None = None
    approved_seq: int | None = None
    executed_at: datetime | None = None
    manifests_retained: bool = True
    #: Why the last attempt did not delete it, where it did not.
    reason: str | None = None

    @property
    def approvable(self) -> bool:
        """Whether an approver may approve it now.

        A refusal may be approved again: the reason is on the record, and the
        approver who reads it is the one who decides whether it still stands.
        """
        return self.state in {ProposalState.PROPOSED, ProposalState.REFUSED}

    @property
    def awaiting_execution(self) -> bool:
        """Whether the worker has an approved deletion to carry out."""
        return self.state is ProposalState.APPROVED

    def version(self) -> dict[str, Any]:
        """What a conditional write on this action is conditional on.

        Everything that can change. An entity tag over the identifier alone
        never changes, and a precondition that cannot fail is not one.
        """
        return {
            "id": str(self.id),
            "state": str(self.state),
            "approvedSeq": self.approved_seq,
            "reason": self.reason,
        }

    def action(self) -> RetentionAction:
        """The action `execute` performs, built from the record."""
        return RetentionAction(
            id=self.id,
            subject_id=self.id,
            artefact_uri=self.artefact_uri,
            policy=RetentionPolicy.RAW_CORPUS,
            due_at=self.due_at,
            approved_by=self.approved_by,
            retained=RETAINED_BY_RAW_CORPUS_DELETION,
        )

    def lineage(self) -> LineageIndex:
        """The releases built from this corpus, as `execute` asks for them."""
        return LineageIndex(
            edges=[LineageEdge(f"release:{item}", (self.artefact_uri,)) for item in self.releases],
            releases={f"release:{item}": item for item in self.releases},
        )


def fold(entries: Iterable[Any]) -> tuple[Proposal, ...]:
    """Every retention action at a site, soonest due first."""
    proposals: dict[int, Proposal] = {}
    for entry in entries:
        if getattr(entry, "subject_type", CORPUS_SUBJECT) != CORPUS_SUBJECT:
            continue
        payload = entry.payload if isinstance(entry.payload, Mapping) else {}

        if entry.transition == PROPOSED:
            try:
                due = datetime.fromisoformat(str(payload["dueAt"]))
            except (KeyError, ValueError):
                continue
            proposals[int(entry.seq)] = Proposal(
                id=entry.id if isinstance(entry.id, UUID) else UUID(str(entry.id)),
                seq=int(entry.seq),
                corpus_sha256=str(payload.get("corpusSha256") or entry.subject_id),
                artefact_uri=str(payload.get("artefact") or ""),
                jurisdiction=str(payload.get("jurisdiction") or ""),
                due_at=due,
                releases=tuple(str(item) for item in payload.get("releases", ())),
                curated_by=str(payload.get("curatedBy") or ""),
            )
            continue

        answered = payload.get(ANSWERS)
        if not isinstance(answered, int) or answered not in proposals:
            continue
        current = proposals[answered]
        if entry.transition == APPROVED:
            proposals[answered] = replace(
                current,
                state=ProposalState.APPROVED,
                approved_by=str(entry.actor),
                approved_seq=int(entry.seq),
                reason=None,
            )
        elif entry.transition == EXECUTED:
            proposals[answered] = replace(
                current,
                state=ProposalState.EXECUTED,
                executed_at=entry.ts,
                manifests_retained=bool(payload.get("manifestsRetained", True)),
                reason=None,
            )
        elif entry.transition == REFUSED:
            proposals[answered] = replace(
                current,
                state=ProposalState.REFUSED,
                reason=str(payload.get("reason") or "refused, with no reason recorded"),
            )

    return tuple(sorted(proposals.values(), key=lambda item: (item.due_at, item.seq)))


def carry_out(
    proposal: Proposal, store: Any, *, now: datetime, manifest_survives: bool
) -> tuple[str, dict[str, Any]]:
    """Execute one approved action, or say why not. AC-F19 and AC-F20.

    Returns the transition and payload to record rather than recording them:
    the chain is written by the transaction that owns it.
    """
    answering: dict[str, Any] = {
        ANSWERS: proposal.seq,
        "approvedSeq": proposal.approved_seq,
        "artefact": proposal.artefact_uri,
        "releases": list(proposal.releases),
    }
    if store is None:
        return REFUSED, {
            **answering,
            "reason": (
                "this worker has no artefact store configured (DRAUPNIR_VAULT_ROOT), so "
                "there is nothing it can delete from. Nothing was deleted."
            ),
        }
    if not proposal.artefact_uri:
        return REFUSED, {
            **answering,
            "reason": (
                "the proposal names no raw corpus, so there is nothing identified to "
                "delete. Nothing was deleted."
            ),
        }
    try:
        done = execute(
            proposal.action(),
            proposal.lineage(),
            store,
            now=now,
            manifest_survives=manifest_survives,
        )
    except RetentionError as refusal:
        return REFUSED, {**answering, "reason": str(refusal)}
    # Broad, because the store is a driver and its failures are its own; the
    # reason belongs in the entry the approver reads, not in a worker log.
    except Exception as failure:
        return REFUSED, {
            **answering,
            "reason": f"the store did not delete {proposal.artefact_uri}: {failure}",
        }
    return EXECUTED, {
        **answering,
        "manifestsRetained": done.manifests_retained,
        "retained": list(done.retained),
    }
