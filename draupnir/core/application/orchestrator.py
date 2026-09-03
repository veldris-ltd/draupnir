"""The orchestrator: guard, act, write the ledger, project. One transaction.

SAD 11B gives the application layer one job, and this is it. Everything above
it -- the API, the CLI, the procedure runner -- asks for a state change and
gets either a ledger entry or a refusal; nothing above it writes to the ledger,
and nothing above it writes to `run` at all.

Three properties are worth stating, because each is a decision rather than an
implementation detail.

**The source state is read, never supplied.** A caller that tells the
orchestrator which state a run is in can tell it a stale one, and the
transition then applies to a run that has already moved. The state comes from
the projection, inside the same transaction as the write.

**The write is one transaction.** The ledger entry, the projection catch-up and
the checkpoint commit together or not at all. A ledger entry that committed
while its projection did not would leave the registry behind the chain, which
is recoverable; a projection that committed without its entry would leave the
registry ahead of it, which is not.

**Concurrency is settled by the store.** Two operators moving the same run at
once both compute seq N, and the unique constraint on `(site_id, seq)` refuses
the second. That is deliberate: the chain is the serialisation point, so the
answer does not depend on the control plane having exactly one process.

The two ports below are why this module imports no SQLAlchemy. SAD 11B puts
application above infrastructure and the import linter holds it: an
orchestrator that reached for a repository would be an application layer that
knows how its state is stored, and the first thing that breaks then is testing
it. `draupnir.core.infrastructure.orchestration` is the factory that binds
these ports to PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from draupnir.core.domain import states
from draupnir.core.domain.ledger import LedgerEntry, append
from draupnir.core.domain.projector import REGISTRATION, RUN_SUBJECT, ProjectedRun
from draupnir.core.domain.states import RunState, Transition, TransitionContext

#: What a registration entry must carry for the projector to build a row from
#: it. `projector._registration` refuses without them; naming them here means
#: the refusal arrives before the write rather than during the fold.
REGISTRATION_FIELDS: tuple[str, ...] = ("name", "spec_hash", "kind")


class LedgerPort(Protocol):
    """What the orchestrator needs from a chain: read the head, append to it."""

    def head(self) -> LedgerEntry | None:
        """The highest-sequence entry, or None for an empty chain."""
        ...

    def append(self, entry: LedgerEntry) -> None:
        """Insert one entry. Raises on a sequence number already taken."""
        ...

    def entries_matching(self, probe: Mapping[str, Any]) -> tuple[LedgerEntry, ...]:
        """Every entry whose payload contains `probe`, oldest first."""
        ...

    def entries_for_subject(self, subject_id: str) -> tuple[LedgerEntry, ...]:
        """Every entry about one subject, oldest first."""
        ...

    def serialise(self) -> None:
        """Take the site's write lock for the rest of this transaction.

        A chain is serial: the next entry is at seq N+1, and two writers that
        both read N both compute N+1. Without this the second one loses to a
        unique constraint and its caller is told to try again -- which is a
        correct backstop and a poor answer to the ordinary case of two
        operators on one run board.
        """
        ...


class ProjectionPort(Protocol):
    """What the orchestrator needs from the run registry."""

    def catch_up(self) -> object:
        """Fold entries the projection has not yet consumed."""
        ...

    def read(self) -> tuple[ProjectedRun, ...]:
        """The projected runs as the registry currently holds them."""
        ...


class OrchestrationError(Exception):
    """Raised when a state change cannot be recorded."""


class UnknownRunError(OrchestrationError):
    """Raised when a transition names a run this site's chain never registered."""

    def __init__(self, run_id: UUID, site_id: str) -> None:
        """Name the run and the site it was looked for in."""
        self.run_id = run_id
        self.site_id = site_id
        super().__init__(
            f"run {run_id} is not registered at {site_id}. A transition can only "
            "move a run the chain already knows about, and a run registered at "
            "another site is not visible here (SAD 11C constraint 3)."
        )


class ConcurrentTransitionError(OrchestrationError):
    """Raised when another writer took the sequence number this one computed.

    Two writers contending for one sequence number are queued by
    `LedgerPort.serialise`, so this is not the ordinary concurrent case: it is
    what is left when the lock did not hold -- a store that cannot serialise,
    or a constraint violated for a reason nobody anticipated. The caller
    re-reads and decides again.
    """

    def __init__(self, subject_id: str, seq: int) -> None:
        """Name the subject and the contested sequence number."""
        self.subject_id = subject_id
        self.seq = seq
        super().__init__(
            f"another writer appended seq {seq} while this entry about {subject_id} "
            "was being prepared. Re-read the subject and decide again."
        )


class DuplicateRunError(OrchestrationError):
    """Raised when a run with this identity is already in the chain. AC-F2.

    "Submitting the same specification twice with unchanged inputs is detected
    and reported as a duplicate rather than silently re-running." Reported, not
    refused-and-forgotten: the message names the run that already exists, so an
    operator who meant to compare two runs can go and look at the first one,
    and an operator who fat-fingered a resubmission has not spent an allocation
    finding out.

    The identity is the hash of the specification and its resolved input
    artefact hashes (AC-F1). Two submissions of one file are one identity and
    two identifiers, which is the relationship that makes this checkable at
    all: identifiers are UUIDv7 and always differ.
    """

    def __init__(self, identity: str, existing: UUID) -> None:
        """Name the identity and the run that already carries it."""
        self.identity = identity
        self.existing = existing
        super().__init__(
            f"a run with identity {identity[:12]} is already recorded as {existing}. "
            "The specification and its resolved inputs are unchanged, so this would "
            "re-run work that has already been done. Read the existing run, or change "
            "the specification -- a different result needs a different input."
        )


@dataclass(frozen=True, slots=True)
class RunFacts:
    """What the chain knows about a run, for a caller about to act on it."""

    run_id: UUID
    name: str
    state: RunState
    #: Who registered it. Read from the registration entry, never supplied.
    submitter: str
    spec_hash: str
    #: How many times it has been requeued. From the projection, which counts
    #: the one transition that spends budget.
    retry_count: int = 0
    #: What the specification allowed, recorded at registration. A budget the
    #: caller supplied would be a budget the caller could raise.
    retry_budget: int = 0
    #: The gates the last evaluation recorded as failing, if any. A requeue is
    #: for a run that failed one; a run that failed none has nothing to retry.
    failing_gates: tuple[str, ...] = ()

    @property
    def budget_remaining(self) -> int:
        """How many requeues are left. Never negative."""
        return max(0, self.retry_budget - self.retry_count)


@dataclass(frozen=True, slots=True)
class Applied:
    """What one state change produced."""

    entry: LedgerEntry
    run: ProjectedRun
    #: `None` for a registration, which is not a transition in SAD 6.1.
    transition: Transition | None = None

    @property
    def state(self) -> RunState:
        """The state the run is in after this change."""
        return self.run.state


def _now() -> datetime:
    """The current instant, with an explicit offset. SAD 11E.2."""
    return datetime.now(UTC)


class Orchestrator:
    """Applies state changes to one site's chain.

    Synchronous, like the repositories it uses. The async edge reaches it
    through a thread, which is the right trade while a request does no more
    than one of these per call.
    """

    def __init__(
        self,
        ledger: LedgerPort,
        projection: ProjectionPort,
        *,
        site_id: str,
        actor: str,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        """Bind to one chain, one registry, one site and one actor.

        The actor is a constructor argument rather than a per-call one because
        an orchestrator that took the actor from the payload would record
        whoever the payload said, and the payload is the least trustworthy
        thing in the request.
        """
        if not actor:
            msg = "every ledger entry records who caused it; `actor` cannot be empty"
            raise ValueError(msg)
        self._ledger = ledger
        self._projection = projection
        self._site_id = site_id
        self._actor = actor
        self._clock = clock

    @property
    def site_id(self) -> str:
        """The site this orchestrator writes to."""
        return self._site_id

    @property
    def actor(self) -> str:
        """Who every entry written through this orchestrator records."""
        return self._actor

    # -- reading ------------------------------------------------------------

    def runs(self) -> dict[UUID, ProjectedRun]:
        """Every run this site's projection currently holds, by identifier."""
        return {UUID(str(run.id)): run for run in self._projection.read()}

    def run(self, run_id: UUID) -> ProjectedRun:
        """One run, or raise. Read from the projection, inside this transaction."""
        found = self.runs().get(run_id)
        if found is None:
            raise UnknownRunError(run_id, self.site_id)
        return found

    def state_of(self, run_id: UUID) -> RunState:
        """The state the projection says this run is in."""
        return self.run(run_id).state

    # -- writing ------------------------------------------------------------

    def existing_run_with(self, identity: str) -> UUID | None:
        """The run already recorded under this identity, if there is one. AC-F2.

        Read from the chain rather than from the projection, because the
        identity is recorded in the entry payload and the projection keeps only
        what a run board renders. The chain is also the answer that survives a
        projection rebuild.
        """
        if not identity:
            return None
        for entry in self._ledger.entries_matching({"run_identity": identity}):
            return UUID(entry.subject_id)
        return None

    def facts_of(self, run_id: UUID) -> RunFacts:
        """What a decision needs to know about a run before it decides.

        One read rather than three, because each would be its own transaction
        and the three answers have to describe the same moment.
        """
        run = self.run(run_id)
        history = self._ledger.entries_for_subject(str(run_id))
        submitter = ""
        budget = 0
        failing: tuple[str, ...] = ()

        for entry in history:
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            if entry.transition == REGISTRATION:
                submitter = entry.actor
                budget = int(payload.get("retry_budget", 0) or 0)
            # The most recent evaluation wins: a run requeued twice has two
            # entries, and what matters is what the last one found.
            recorded = payload.get("failing_gates") or payload.get("failing_gate")
            if recorded:
                failing = (
                    tuple(str(item) for item in recorded)
                    if isinstance(recorded, list)
                    else (str(recorded),)
                )

        if not submitter:
            raise UnknownRunError(run_id, self.site_id)

        return RunFacts(
            run_id=run_id,
            name=run.name,
            state=run.state,
            submitter=submitter,
            spec_hash=run.spec_hash,
            retry_count=run.retry_count,
            retry_budget=budget,
            failing_gates=failing,
        )

    def history(self, run_id: UUID) -> tuple[LedgerEntry, ...]:
        """Every entry the chain holds about one run, oldest first.

        What the projection does not keep. A run board needs a state and a
        name; a caller resuming work needs the scheduler job it placed, the
        checkpoint it produced and the formats it built, and those live in the
        entry payloads rather than in the projected row. Reading them back is
        what makes a worker able to hold nothing between ticks (SAD 11.2 row 1).
        """
        return self._ledger.entries_for_subject(str(run_id))

    def submitter_of(self, run_id: UUID) -> str:
        """Who registered this run.

        Read from the registration entry rather than taken from a request. The
        sole approver exception of Decision S6 is `approver == submitter`, and
        a submitter the approver could supply is an exception the approver could
        suppress -- which is exactly what constraint C-11 forbids.
        """
        for entry in self._ledger.entries_for_subject(str(run_id)):
            if entry.transition == REGISTRATION:
                return entry.actor
        raise UnknownRunError(run_id, self.site_id)

    def released_entry_for(self, artefact_sha256: str) -> LedgerEntry | None:
        """The approval that released these bytes, if one exists.

        Publication names an artefact and the chain records runs, so this is
        the join between them. Matched on the payload rather than on a table,
        because the approval that permits a publication *is* a ledger entry and
        the question is whether one exists -- which is also why the entry comes
        back rather than the run identifier: what it recorded is what the
        publication is permitted to say.
        """
        for entry in self._ledger.entries_matching({"artefact_sha256": artefact_sha256}):
            if entry.transition == f"{RunState.AWAITING_APPROVAL}->{RunState.RELEASED}":
                return entry
        return None

    def register(
        self,
        run_id: UUID,
        *,
        name: str,
        spec_hash: str,
        kind: str,
        identity: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> Applied:
        """Record a run into the chain at DRAFT.

        Registration is not a transition: SAD 6.1's table has no row arriving
        at DRAFT, because nothing precedes it. The projector recognises the
        entry by its `->DRAFT` transition string.

        Pass `identity` -- the run identity of AC-F1 -- and a second submission
        of the same specification with the same inputs is reported as a
        duplicate rather than silently starting again.
        """
        duplicate = self.existing_run_with(identity)
        if duplicate is not None:
            raise DuplicateRunError(identity, duplicate)

        body: dict[str, Any] = {
            **(payload or {}),
            "name": name,
            "spec_hash": spec_hash,
            "kind": kind,
        }
        if identity:
            body["run_identity"] = identity
        missing = [field for field in REGISTRATION_FIELDS if not body.get(field)]
        if missing:
            msg = (
                f"a registration records {', '.join(REGISTRATION_FIELDS)}; "
                f"{', '.join(missing)} is empty. A run the projector cannot build a "
                "row from is a run that exists only in the chain."
            )
            raise OrchestrationError(msg)

        return self._write(
            run_id=run_id,
            transition_name=REGISTRATION,
            payload=body,
            transition=None,
        )

    def transition(
        self,
        run_id: UUID,
        target: RunState,
        *,
        facts: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> Applied:
        """Move a run, or refuse.

        `facts` is what the guard reads; `payload` is what the ledger records.
        They are separate arguments because they are separate things: a guard
        asks whether the world permits this change, and the payload is the
        audit record of the change having been made. Conflating them is how a
        guard ends up reading a value the caller put there to satisfy it.
        """
        current = self.run(run_id)
        source = current.state

        # Guard, then check the audit record. Both raise, and both raise before
        # anything is written.
        transition = states.apply(
            source,
            target,
            TransitionContext(facts=dict(facts)),
            payload,
        )

        return self._write(
            run_id=run_id,
            transition_name=transition.name,
            payload=dict(payload),
            transition=transition,
        )

    def record(
        self,
        *,
        subject_type: str,
        subject_id: str,
        transition: str,
        payload: Mapping[str, Any],
    ) -> LedgerEntry:
        """Append an entry about something that is not a run.

        SAD 7.1 has entities other than `run` -- a source, a corpus, a release,
        an artefact -- and things happen to them that belong in the audit record
        even though they are not lifecycle transitions. The projector skips
        them; the chain keeps them.

        A run subject is refused. The projector reads every `run` entry and
        raises on a transition string it does not recognise, so one free-form
        entry about a run would stop the registry rebuilding -- and it would
        stop it for everyone, because the fold is over the whole chain.
        """
        if subject_type == RUN_SUBJECT:
            msg = (
                "a run's entries are written by `register` and `transition`, which check "
                "the transition against SAD 6.1. A free-form entry about a run is one the "
                "projector cannot fold, and it would stop the registry rebuilding for "
                "every run at this site, not only this one."
            )
            raise OrchestrationError(msg)
        if not subject_id or not transition:
            msg = "an entry records what it is about and what happened; neither can be empty"
            raise OrchestrationError(msg)

        return self._append(
            subject_type=subject_type,
            subject_id=subject_id,
            transition=transition,
            payload=dict(payload),
        )

    # -- the one write path -------------------------------------------------

    def _write(
        self,
        *,
        run_id: UUID,
        transition_name: str,
        payload: Mapping[str, Any],
        transition: Transition | None,
    ) -> Applied:
        """Append a run's entry and advance the projection, together."""
        entry = self._append(
            subject_type=RUN_SUBJECT,
            subject_id=str(run_id),
            transition=transition_name,
            payload=payload,
        )
        return Applied(entry=entry, run=self.run(run_id), transition=transition)

    def _append(
        self,
        *,
        subject_type: str,
        subject_id: str,
        transition: str,
        payload: Mapping[str, Any],
    ) -> LedgerEntry:
        """The one place anything is appended. Serialised, then projected."""
        # Queue behind any other writer to this site, for the rest of this
        # transaction. The head is read *after* the lock is granted: one read
        # before it is a head that may have moved by the time it is.
        self._ledger.serialise()
        previous = self._ledger.head()
        entry = append(
            previous=previous,
            site_id=self.site_id,
            ts=self._clock(),
            actor=self._actor,
            subject_type=subject_type,
            subject_id=subject_id,
            transition=transition,
            payload=dict(payload),
        )

        try:
            self._ledger.append(entry)
        except Exception as error:
            # Whatever the store raises for a taken sequence number. Narrowing
            # it to one library's exception type here would put a dependency on
            # that library in the application layer, which is the thing the
            # ports exist to prevent.
            if _is_conflict(error):
                raise ConcurrentTransitionError(subject_id, entry.seq) from error
            raise

        # Every append advances the projection, including one about a corpus.
        # The fold skips non-run subjects, so this costs a checkpoint write and
        # buys one rule instead of two: an entry is appended and projected, or
        # neither happened.
        self._projection.catch_up()
        return entry


def _is_conflict(error: Exception) -> bool:
    """Whether an append failed because the sequence number was already taken.

    Matched on the class name rather than on the class, so that recognising a
    conflict does not require importing the driver that raised it. The
    alternative -- catching everything as a conflict -- would report a disk
    full as two operators racing.
    """
    names = {type(item).__name__ for item in (error, error.__cause__) if item is not None}
    return bool(names & {"IntegrityError", "UniqueViolation", "UniqueViolationError"})


__all__ = [
    "Applied",
    "ConcurrentTransitionError",
    "DuplicateRunError",
    "OrchestrationError",
    "Orchestrator",
    "RunFacts",
    "UnknownRunError",
]
