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

**Why the edge writes at all.** Until this, submitting a run through the API
computed its identity, published an event and recorded nothing. The console
showed a run the ledger had never heard of, and the duplicate detection of
AC-F2 existed only for the procedure runner. An audit record that is a property
of the system rather than of the operator (SAD 1.1) has to be written by the
thing the operator actually uses.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

import anyio
from sqlalchemy import Engine

from draupnir.core.application.orchestrator import Applied
from draupnir.core.domain.sites import SiteScope
from draupnir.core.infrastructure.orchestration import for_connection


class Writer(Protocol):
    """What the edge needs in order to record a state change."""

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
        payload: dict[str, Any],
    ) -> Applied | None:
        """Record a run at DRAFT, or return None when nothing is recorded."""
        ...

    @property
    def records(self) -> bool:
        """Whether this writer actually appends to a chain."""
        ...


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
        payload: dict[str, Any],
    ) -> Applied | None:
        """Register the run, in a worker thread, in one transaction."""

        def work() -> Applied:
            with self._engine.begin() as connection:
                orchestrator = for_connection(connection, SiteScope(site_id), actor=actor)
                return orchestrator.register(
                    run_id,
                    name=name,
                    spec_hash=spec_hash,
                    kind=kind,
                    identity=identity,
                    payload=payload,
                )

        return await anyio.to_thread.run_sync(work)


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


__all__ = ["DatabaseWriter", "NoWriter", "Writer", "set_writer", "writer"]
