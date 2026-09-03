"""The write path at the edge: an orchestrator, per request, in a thread.

The read model has two implementations behind one protocol so that a contract
test needs no database. The write side needs the same shape and for a sharper
reason: a mechanism test of "does a duplicate submission return 409" should not
require PostgreSQL, and a deployment must not answer it from memory.

So `Writer` is a protocol with two implementations. `NoWriter` records nothing
and says so; it is the default, and it is what the contract tests run against.
`DatabaseWriter` opens a site-scoped transaction, builds the orchestrator over
it, and commits -- in a worker thread, because the repositories are synchronous
by design (SAD 11B: "the async edge reaches them through a thread, which is the
right trade while a request does no more than one of these per call").

**Why the edge writes at all.** Until this, a mutating endpoint computed what
it was going to do, published an event and recorded nothing. The console showed
a run the ledger had never heard of; a corpus ingested through the console left
no trace; a gate decided there was a signed approval nobody could find. An
audit record that is a property of the system rather than of the operator
(SAD 1.1) has to be written by the thing the operator actually uses.

**Three operations, and the difference between them matters.** `register_run`
and `transition_run` write about a run and go through the state machine, which
checks the transition against SAD 6.1 and refuses a payload missing a field the
table requires. `record` writes about anything else -- a source, a corpus, a
release -- and the orchestrator refuses a run subject through it, because the
projector folds every run entry and one it cannot parse stops the registry
rebuilding for every run at the site.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

import anyio
from sqlalchemy import Connection, Engine

from draupnir.core.application.orchestrator import Applied, Orchestrator, RunFacts
from draupnir.core.domain.ledger import LedgerEntry
from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import RunState
from draupnir.core.infrastructure.orchestration import for_connection


class Writer(Protocol):
    """What the edge needs in order to record a state change."""

    @property
    def records(self) -> bool:
        """Whether this writer actually appends to a chain."""
        ...

    async def register_run(
        self,
        *,
        site_id: str,
        actor: str,
        run_id: UUID,
        name: str,
        spec_hash: str,
        kind: str,
        identity: str,
        payload: Mapping[str, Any],
    ) -> Applied | None:
        """Record a run at DRAFT, or return None when nothing is recorded."""
        ...

    async def transition_run(
        self,
        *,
        site_id: str,
        actor: str,
        run_id: UUID,
        target: RunState,
        facts: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> Applied | None:
        """Move a run through SAD 6.1, or return None when nothing is recorded."""
        ...

    async def record(
        self,
        *,
        site_id: str,
        actor: str,
        subject_type: str,
        subject_id: str,
        transition: str,
        payload: Mapping[str, Any],
    ) -> LedgerEntry | None:
        """Record something that happened to a subject other than a run."""
        ...

    async def read(self, *, site_id: str, actor: str, question: Question) -> Any:
        """Answer a question the write path needs before it writes.

        A separate call rather than a value passed in, because both questions
        this asks -- who submitted a run, which run a released artefact belongs
        to -- are answers a request must not be trusted to supply.
        """
        ...


class Question(Protocol):
    """One read against the chain, phrased as a callable.

    A protocol rather than an enumeration of question names: the alternative is
    a string the writer switches on, and a switch grows a default case that
    returns None for a question nobody implemented.
    """

    def __call__(self, orchestrator: Orchestrator) -> Any:
        """Answer against a bound orchestrator."""
        ...


def facts_of(run_id: UUID) -> Question:
    """Everything a decision needs about a run before it decides."""

    def ask(orchestrator: Orchestrator) -> RunFacts:
        return orchestrator.facts_of(run_id)

    return ask


def released_entry_for(artefact_sha256: str) -> Question:
    """The approval that released these bytes, if one exists."""

    def ask(orchestrator: Orchestrator) -> LedgerEntry | None:
        return orchestrator.released_entry_for(artefact_sha256)

    return ask


class NoWriter:
    """Records nothing. The default, and what a contract test runs against.

    Returning `None` rather than raising: an endpoint that works without a
    database is the point of the empty read model, and the write side answers
    the same way. The endpoint knows the difference and says so in its response,
    so nobody is told a run was recorded when it was not.
    """

    @property
    def records(self) -> bool:
        """No."""
        return False

    async def register_run(self, **_: Any) -> Applied | None:
        """Record nothing."""
        return None

    async def transition_run(self, **_: Any) -> Applied | None:
        """Record nothing."""
        return None

    async def record(self, **_: Any) -> LedgerEntry | None:
        """Record nothing."""
        return None

    async def read(self, **_: Any) -> Any:
        """Know nothing. A question with no chain behind it has no answer."""
        return None


class DatabaseWriter:
    """Writes through the orchestrator, one transaction per request."""

    def __init__(self, engine: Engine) -> None:
        """Bind to a synchronous engine. The lifespan owns its disposal."""
        self._engine = engine

    @property
    def records(self) -> bool:
        """Yes."""
        return True

    async def register_run(
        self,
        *,
        site_id: str,
        actor: str,
        run_id: UUID,
        name: str,
        spec_hash: str,
        kind: str,
        identity: str,
        payload: Mapping[str, Any],
    ) -> Applied | None:
        """Register the run, in a worker thread, in one transaction."""
        applied: Applied = await self._in_thread(
            site_id,
            actor,
            lambda orchestrator: orchestrator.register(
                run_id,
                name=name,
                spec_hash=spec_hash,
                kind=kind,
                identity=identity,
                payload=payload,
            ),
        )
        return applied

    async def transition_run(
        self,
        *,
        site_id: str,
        actor: str,
        run_id: UUID,
        target: RunState,
        facts: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> Applied | None:
        """Move the run, in a worker thread, in one transaction."""
        applied: Applied = await self._in_thread(
            site_id,
            actor,
            lambda orchestrator: orchestrator.transition(
                run_id, target, facts=facts, payload=payload
            ),
        )
        return applied

    async def record(
        self,
        *,
        site_id: str,
        actor: str,
        subject_type: str,
        subject_id: str,
        transition: str,
        payload: Mapping[str, Any],
    ) -> LedgerEntry | None:
        """Append an entry about a subject that is not a run."""
        entry: LedgerEntry = await self._in_thread(
            site_id,
            actor,
            lambda orchestrator: orchestrator.record(
                subject_type=subject_type,
                subject_id=subject_id,
                transition=transition,
                payload=payload,
            ),
        )
        return entry

    async def read(self, *, site_id: str, actor: str, question: Question) -> Any:
        """Answer one question against the chain, read only."""
        return await self._in_thread(site_id, actor, question, write=False)

    async def _in_thread(
        self,
        site_id: str,
        actor: str,
        work: Question,
        *,
        write: bool = True,
    ) -> Any:
        """Run `work` against a bound orchestrator, in one transaction."""

        def run() -> Any:
            with self._engine.connect() as connection:
                return self._within(connection, site_id, actor, work, write=write)

        return await anyio.to_thread.run_sync(run)

    @staticmethod
    def _within(
        connection: Connection,
        site_id: str,
        actor: str,
        work: Question,
        *,
        write: bool,
    ) -> Any:
        """Bind an orchestrator to a transaction and commit or roll back.

        A read rolls back rather than commits. Nothing it did needs keeping,
        and a read that commits is a read that has to be trusted not to have
        written -- which is a property of the code rather than of the
        transaction, and therefore not a property at all.
        """
        transaction = connection.begin()
        try:
            answer = work(for_connection(connection, SiteScope(site_id), actor=actor))
        except BaseException:
            transaction.rollback()
            raise
        if write:
            transaction.commit()
        else:
            transaction.rollback()
        return answer


#: The process-wide writer, reached through `writer()` for the same reason the
#: read model is: a router that captured the object at import time would keep
#: the no-op writer after startup installed the real one.
WRITER: Writer = NoWriter()


def writer() -> Writer:
    """The current writer, resolved at call time."""
    return WRITER


def set_writer(implementation: Writer) -> None:
    """Install the writer. Called by the lifespan, and by tests."""
    global WRITER
    WRITER = implementation


__all__ = [
    "DatabaseWriter",
    "NoWriter",
    "Question",
    "Writer",
    "facts_of",
    "released_entry_for",
    "set_writer",
    "writer",
]
