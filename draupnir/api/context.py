"""What a request carries: who is calling, which site, and one correlation id.

Every log line carries run id, site id and actor (the prompt's requirement), so
those three have to be resolved before a handler runs and available to
everything below it. That is what this module produces.

Two decisions worth stating.

**The site scope is resolved from the principal, not from the request.** A
header or a query parameter naming a site would be a parameter an attacker
controls, and the row level security variable is set from whatever resolves
here. So the site comes from the token's claim or from the deployment's own
configuration, and there is no way to spell "some other site" in a request.

**The actor is the subject, never the token.** `RequestContext` holds no
credential, no claim set and no bearer value, so a context that reaches a log
line -- which is the whole point of it -- cannot carry one there.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from draupnir.core.domain.identifiers import new_id
from draupnir.core.domain.sites import SiteScope
from draupnir.svalinn.identity import Principal

#: The header a client may set to correlate its own logs with ours. Accepted,
#: never trusted for anything but correlation: it is echoed and logged and no
#: decision is made on it.
CORRELATION_HEADER = "X-Correlation-Id"

#: The header carrying the idempotency key of SAD 11E.2.
IDEMPOTENCY_HEADER = "Idempotency-Key"

#: The current request's context, for a logger or a span that cannot be passed
#: one. Set by the middleware and reset when the request ends.
_CURRENT: contextvars.ContextVar[RequestContext | None] = contextvars.ContextVar(
    "draupnir_request_context", default=None
)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Everything a handler and a log line need about one request."""

    #: This request. Distinct from the correlation id, which the client owns.
    request_id: UUID
    #: The site this request reads and writes. Resolved, never requested.
    scope: SiteScope
    #: Who is calling. `None` only on a route declared public.
    principal: Principal | None = None
    #: The run this request concerns, where it concerns one.
    run_id: UUID | None = None
    correlation_id: str | None = None
    method: str = ""
    path: str = ""

    @property
    def actor(self) -> str:
        """Who to attribute this request to in the ledger and in the log."""
        return self.principal.subject if self.principal else "anonymous"

    @property
    def site_id(self) -> str:
        """The site this request is scoped to."""
        return self.scope.site_id

    def with_run(self, run_id: UUID | None) -> RequestContext:
        """The same context, now concerning a particular run."""
        from dataclasses import replace

        return replace(self, run_id=run_id)

    def as_log_context(self) -> dict[str, Any]:
        """The fields every log line carries.

        Run id, site id and actor, as the requirement states, plus the request
        id so that the lines of one request can be gathered. Deliberately no
        token, no claim set, no header bag: a context that carries a credential
        is a credential in the log aggregator, and this object exists to be
        logged.
        """
        return {
            "requestId": str(self.request_id),
            "runId": str(self.run_id) if self.run_id else None,
            "siteId": self.site_id,
            "actor": self.actor,
            "correlationId": self.correlation_id,
            "method": self.method,
            "path": self.path,
        }


def build(
    *,
    scope: SiteScope,
    principal: Principal | None = None,
    correlation_id: str | None = None,
    method: str = "",
    path: str = "",
) -> RequestContext:
    """Build a context for one request."""
    return RequestContext(
        request_id=new_id(),
        scope=scope,
        principal=principal,
        correlation_id=correlation_id,
        method=method,
        path=path,
    )


def bind(context: RequestContext) -> contextvars.Token[RequestContext | None]:
    """Make `context` the current one. Returns the token that undoes it."""
    return _CURRENT.set(context)


def unbind(token: contextvars.Token[RequestContext | None]) -> None:
    """Restore whatever context was current before `bind`."""
    _CURRENT.reset(token)


def current() -> RequestContext | None:
    """The current request's context, if there is one."""
    return _CURRENT.get()


def log_context() -> dict[str, Any]:
    """The current request's log fields, or an empty mapping outside one."""
    context = current()
    return context.as_log_context() if context else {}


@dataclass
class Recorder:
    """Collects log lines for a test to assert about.

    Structured logging in deployment goes through structlog to stdout. This
    exists because "every log line carries run id, site id and actor" and "no
    secret appears in any log line" are properties a test has to be able to
    check, and checking them by parsing stdout would test the formatter.
    """

    lines: list[dict[str, Any]] = field(default_factory=list)

    def record(self, event: str, **fields: Any) -> dict[str, Any]:
        """Record one line, merged with the current request's context."""
        line = {"event": event, **log_context(), **fields}
        self.lines.append(line)
        return line

    def clear(self) -> None:
        """Forget everything recorded."""
        self.lines.clear()
