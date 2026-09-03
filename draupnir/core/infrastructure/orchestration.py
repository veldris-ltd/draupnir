"""Binding the orchestrator to PostgreSQL. SAD 11B, the outermost core layer.

The orchestrator knows two ports and no storage. This is where those ports
become the repositories: infrastructure may know about application, and
application may not know about infrastructure, which is the whole of what
"dependencies point inward" buys.

It is a function rather than a class because there is nothing to remember. One
connection, one scope, one actor, one orchestrator; a second call with a
different actor is a different orchestrator, which is the correct answer when
two people are working on the same site.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Connection

from draupnir.core.application.orchestrator import Orchestrator, _now
from draupnir.core.domain.sites import SiteScope
from draupnir.core.infrastructure.repositories import (
    LedgerRepository,
    RunProjection,
    set_site_scope,
)


def for_connection(
    connection: Connection,
    scope: SiteScope,
    *,
    actor: str,
    clock: Callable[[], datetime] = _now,
) -> Orchestrator:
    """Build an orchestrator writing to one site's chain through `connection`.

    Sets the row level security variable as it goes. That is not a convenience:
    a session that has not set `draupnir.site_id` sees zero rows, so an
    orchestrator built without it would read an empty projection and conclude
    that every run it was asked about is unregistered.
    """
    set_site_scope(connection, scope)
    return Orchestrator(
        LedgerRepository(connection, scope),
        RunProjection(connection, scope),
        site_id=scope.site_id,
        actor=actor,
        clock=clock,
    )


__all__ = ["for_connection"]
