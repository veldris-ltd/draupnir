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
from draupnir.core.domain.evidence import (
    BASELINE_CAPTURED,
    BASELINE_SUBJECT,
    Evidence,
    EvidenceError,
    EvidenceLog,
)
from draupnir.core.domain.federation import ANCHOR_SUBMITTED
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

    def entries_of_type(self, subject_type: str) -> tuple[LedgerEntry, ...]:
        """Every entry about subjects of one kind, oldest first."""
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
    #: The specification, as the registration entry recorded it. RF-10.
    #:
    #: `None` for a run registered before this was recorded, and for one
    #: registered by something that does not record it. A caller that needs it
    #: says so and defers; a caller that guessed would be dispatching work
    #: nobody specified.
    specification: Mapping[str, Any] | None = None

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


@dataclass(frozen=True, slots=True)
class PublicationFacts:
    """What a publication is decided against, read as of one moment."""

    approval: LedgerEntry
    evidence: EvidenceLog
    built_formats: tuple[str, ...]
    #: Where the bytes are, as the chain recorded it. Empty when no entry named
    #: a location, which is a refusal rather than a guess: an artefact whose
    #: whereabouts nobody recorded cannot be re-hashed, and constructing a path
    #: from a convention would hash whatever happened to be there.
    artefact_uri: str
    #: Where the approval sits in the chain, and how far the federation has
    #: countersigned. AC-S13 compares the two.
    release_seq: int
    anchored_through: int


def _evidence_from(results: Mapping[str, Any]) -> tuple[Evidence, ...]:
    """Rebuild evidence entries from the payload the chain recorded.

    Tolerant on purpose: an entry whose shape this does not recognise is
    skipped rather than raising, because a publication refused by a parse error
    would be indistinguishable from one refused by a control -- and the second
    is the answer that means something.
    """
    found: list[Evidence] = []
    for name, item in results.items():
        if not isinstance(item, Mapping):
            continue
        digest = item.get("artefactSha256") or item.get("artefact_sha256")
        if not digest:
            continue
        # Absent is `None`, not the empty string. `Evidence` refuses a baseline
        # that is not a SHA-256, and "" is not one -- so passing "" for "there
        # was no baseline" raised, which is exactly what this function's
        # tolerance exists to avoid. A re-gated quantised format has no
        # baseline: the absolute gates are the ones that apply to it.
        baseline = item.get("baselineSha256") or item.get("baseline_sha256")
        try:
            found.append(
                Evidence(
                    artefact_sha256=str(digest),
                    artefact_kind=str(item.get("artefactKind") or item.get("artefact_kind") or ""),
                    format=str(item.get("format") or name),
                    suite=str(item.get("suite") or ""),
                    suite_version=str(item.get("suiteVersion") or item.get("suite_version") or ""),
                    baseline_sha256=str(baseline) if baseline else None,
                    evaluated_at=_moment(item.get("evaluatedAt") or item.get("evaluated_at")),
                    passed=bool(item.get("passed")),
                    # `failing` is derived by `Evidence` from its outcomes rather
                    # than stored, so the recorded list is not passed back in: the
                    # type computes it, and a second source would let the two
                    # disagree about which gate failed.
                    outcomes=(),
                )
            )
        except EvidenceError:
            # Skipped rather than raised, as the docstring above promises and as
            # this did not do. A publication refused by a parse error is
            # indistinguishable to an operator from one refused by a control,
            # and the second is the answer that means something. Evidence that
            # will not construct is evidence the publication does not have, and
            # `verify_artefact` refuses on that in its own words.
            continue
    return tuple(found)


def _uri_for(payload: Mapping[str, Any], artefact_sha256: str) -> str:
    """Where the chain says these bytes are, if any entry said."""
    direct = payload.get("artefact_uri") or payload.get("artefactUri")
    if direct and payload.get("artefact_sha256") == artefact_sha256:
        return str(direct)

    listed = payload.get("artefacts")
    if isinstance(listed, list):
        for item in listed:
            if not isinstance(item, Mapping):
                continue
            digest = item.get("sha256") or item.get("artefactSha256")
            found = item.get("uri")
            if digest == artefact_sha256 and found:
                return str(found)
    return ""


def _moment(raw: Any) -> datetime:
    """An offset-aware instant from a recorded one, or the epoch."""
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, tz=UTC)


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
        specification: Mapping[str, Any] | None = None

        for entry in history:
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            if entry.transition == REGISTRATION:
                submitter = entry.actor
                budget = int(payload.get("retry_budget", 0) or 0)

            # From whichever entry carried it, latest wins. A submission
            # through the API records it at registration; a run curated by the
            # Sindri procedure records it at QUEUED, because the specification
            # is compiled once the corpus exists and there is nothing to record
            # before that. Keying on the transition would have found one of the
            # two and silently not the other.
            recorded_spec = payload.get("specification")
            if isinstance(recorded_spec, Mapping):
                specification = recorded_spec
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
            specification=specification,
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

    def publication_facts(self, artefact_sha256: str) -> PublicationFacts | None:
        """Everything the publication checks need, as of one moment.

        One read rather than five, for the reason `facts_of` gives: each would
        be its own transaction and the five answers have to describe the same
        chain. A release admitted against evidence from before an anchor moved
        is a release admitted against a chain that no longer exists.

        `None` when no approval exists for these bytes, which the caller turns
        into the refusal it already had.
        """
        approval = self.released_entry_for(artefact_sha256)
        if approval is None:
            return None

        history = self._ledger.entries_for_subject(approval.subject_id)
        evidence: list[Evidence] = []
        built: list[str] = []
        uri = ""
        uri = ""

        for entry in history:
            payload = entry.payload if isinstance(entry.payload, dict) else {}

            # What was *built*, from the entry that built it. AC-F9 is driven by
            # this rather than by the evidence, because iterating the evidence
            # confirms that everything evaluated passed -- which is true of an
            # empty set and of a set missing the one format nobody ran.
            for key in ("formats", "formats_regated", "built_formats"):
                found = payload.get(key)
                if isinstance(found, list):
                    built.extend(str(item) for item in found)

            for key in ("format_gate_results", "gate_results"):
                results = payload.get(key)
                if isinstance(results, dict):
                    evidence.extend(_evidence_from(results))

            # The location of these particular bytes. Matched on the digest, so
            # an entry naming a different artefact of the same run cannot be
            # mistaken for this one.
            uri = _uri_for(payload, artefact_sha256) or uri

        return PublicationFacts(
            approval=approval,
            evidence=EvidenceLog(entries=tuple(evidence)),
            built_formats=tuple(dict.fromkeys(built)),
            artefact_uri=uri,
            release_seq=approval.seq,
            anchored_through=self._anchored_through(),
        )

    def _anchor_entries(self) -> tuple[LedgerEntry, ...]:
        """Every anchoring attempt this site has recorded, oldest first.

        By transition and subject rather than by payload containment. This was
        `entries_matching({"anchored_through": None})`, which is a JSONB
        containment probe for the *value* null -- and the duty records an
        integer, never null, so the probe matched nothing and
        `_anchored_through` answered zero however many times the chain had been
        countersigned. Every publication was refused, for a reason the refusal
        did not name.
        """
        return tuple(
            entry
            for entry in self._ledger.entries_for_subject(self.site_id)
            if entry.transition == ANCHOR_SUBMITTED
        )

    def _anchored_through(self) -> int:
        """The highest sequence the federation has countersigned. AC-S13.

        Zero when nothing has been anchored, which refuses every publication --
        correctly. An estate with no federation link has no countersigned chain
        head, and publishing against one would put an artefact in the registry
        whose provenance no other site can attest.
        """
        highest = 0
        for entry in self._anchor_entries():
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            recorded = payload.get("anchored_through") or payload.get("anchoredThrough")
            if recorded is None:
                continue
            try:
                highest = max(highest, int(recorded))
            except (TypeError, ValueError):
                continue
        return highest

    def baseline_payloads(self) -> tuple[Mapping[str, Any], ...]:
        """Every baseline this site has captured, latest per subject. RF-10.

        Latest per subject rather than every entry, because re-capturing a
        baseline is a deliberate, recorded act -- `BaselineRegistry.capture`
        refuses to overwrite silently -- and the chain keeps the ones it
        replaced. A reader asking "what is a run judged against today" wants
        the current one; the history is there for the auditor asking when it
        moved and who moved it.

        Payloads rather than `Baseline` objects, because the core may not
        import RAUN. The caller reconstructs, and `raun.baselines.from_payload`
        raises rather than tolerating: a baseline that will not reconstruct
        must not become a baseline of `None`, since a relative gate compared
        against nothing fails for want of a value and reads as a bad model.
        """
        latest: dict[str, Mapping[str, Any]] = {}
        for entry in self._ledger.entries_of_type(BASELINE_SUBJECT):
            if entry.transition == BASELINE_CAPTURED and isinstance(entry.payload, Mapping):
                latest[entry.subject_id] = entry.payload
        return tuple(latest.values())

    def last_anchored_at(self) -> datetime | None:
        """When the federation last countersigned this chain, or None. RF-07.

        Out of the chain rather than off the site row. `site.last_anchored_at`
        was what the freshness duty read and nothing ever wrote it, so the duty
        alarmed on every tick for ever -- and a side table that has to be kept
        in step with the chain is a side table that will not be. The entries
        the anchor duty writes are the record; this reads them.

        Only accepted attempts count. A rejection is recorded too, deliberately
        -- an operator during an outage needs "tried and was refused" told apart
        from "never tried" -- but a rejection is not an anchor, and letting one
        refresh the clock would silence the alarm that says the chain's end is
        unprotected.
        """
        latest: datetime | None = None
        for entry in self._anchor_entries():
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            if not (payload.get("anchored_through") or payload.get("anchoredThrough")):
                continue
            raw = payload.get("anchored_at") or payload.get("anchoredAt")
            if not raw:
                continue
            try:
                found = datetime.fromisoformat(str(raw))
            except (TypeError, ValueError):
                continue
            if latest is None or found > latest:
                latest = found
        return latest

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
