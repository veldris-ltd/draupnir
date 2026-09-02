"""Spans from the edge to the driver boundary, and log lines that carry no secret.

Two requirements, and they pull in the same direction.

"OpenTelemetry spans from edge through orchestrator to driver boundary" means a
trace has to survive being handed from an HTTP request to a scheduler
submission to a driver's `render`. That is one context propagated across three
layers, and the place it usually breaks is the boundary where the work stops
being a coroutine.

"Every log line carries run id, site id and actor. No secret, token or corpus
excerpt in any log line" is the harder one, because it is a claim about lines
this module does not write. A rule that only applies to careful callers is not
a rule, so the redaction is in the emitter: everything goes through `log`, `log`
merges the request context and scrubs the fields, and a caller cannot emit a
line that skips either step without reaching past this module.

What counts as a secret is deliberately broad. A bearer token is obvious. A
corpus excerpt is not -- it is a long string in a field called `text` or
`sample` or `content`, and it looks exactly like a helpful diagnostic. Long
free text is truncated to a length that cannot carry a document, and the
truncation is visible so nobody wonders whether the field was empty.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Final

from draupnir.api import context as request_context

#: The longest a free-text field may be in a log line. Long enough for a
#: message, a path or a state name; far too short for a corpus excerpt.
MAX_TEXT = 200

#: What a redacted value becomes. Fixed and obviously not a value, so a reader
#: cannot mistake it for one and cannot infer the original's length.
REDACTED: Final = "[redacted]"

#: Field names whose value is never logged, whatever it holds.
SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "token",
        "access_token",
        "id_token",
        "refresh_token",
        "bearer",
        "password",
        "passwd",
        "secret",
        "api_key",
        "apikey",
        "client_secret",
        "credential",
        "credentials",
        "private_key",
        "cookie",
        "set-cookie",
        "claims",
    }
)

#: Field names that carry corpus or model content rather than metadata. These
#: are truncated rather than dropped: their presence is diagnostic, their
#: contents are not ours to log.
CONTENT_KEYS: Final[frozenset[str]] = frozenset(
    {"text", "sample", "excerpt", "content", "body", "prompt", "completion", "document"}
)

#: Values that look like a credential wherever they appear. The scanner in
#: SVALINN is the thorough one; this is the tripwire on the way to stdout.
_CREDENTIAL_SHAPES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),  # A JWT.
)


def scrub(value: Any, *, key: str = "") -> Any:
    """Redact or truncate one value on its way to a log line.

    Applied by name and by shape. By name catches the field somebody added
    called `access_token`; by shape catches the token that arrived inside a
    message somebody was helpfully including.
    """
    lowered = key.lower().replace("-", "_")

    if lowered in SENSITIVE_KEYS:
        return REDACTED

    if isinstance(value, Mapping):
        return {name: scrub(item, key=str(name)) for name, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [scrub(item, key=key) for item in value]

    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"

    if isinstance(value, str):
        for shape in _CREDENTIAL_SHAPES:
            if shape.search(value):
                return REDACTED
        if len(value) > MAX_TEXT:
            return f"{value[:MAX_TEXT]}… [{len(value)} chars, truncated]"
        return value

    return value


def scrubbed(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Scrub every field of a prospective log line."""
    return {name: scrub(value, key=name) for name, value in fields.items()}


@dataclass(frozen=True, slots=True)
class Line:
    """One structured log line, already scrubbed and already contextualised."""

    event: str
    fields: Mapping[str, Any]

    def as_payload(self) -> dict[str, Any]:
        """The line as it is emitted."""
        return {"event": self.event, **dict(self.fields)}


def build_line(event: str, **fields: Any) -> Line:
    """Compose a log line: context first, then scrubbed fields.

    Context first so that a caller cannot overwrite `siteId` or `actor` with a
    field of the same name; the request's own values win.
    """
    return Line(event=event, fields={**scrubbed(fields), **request_context.log_context()})


@dataclass
class Log:
    """The emitter. Everything goes through here, which is what makes the rule real.

    In deployment this hands `as_payload()` to structlog. It is a class rather
    than a module function so that a test can hold one and assert about what a
    request produced, which is how "no secret in any log line" is checked
    without parsing stdout.
    """

    lines: list[Line] = field(default_factory=list)
    capture: bool = True

    def emit(self, event: str, **fields: Any) -> Line:
        """Emit one line."""
        line = build_line(event, **fields)
        if self.capture:
            self.lines.append(line)
        return line

    def payloads(self) -> tuple[dict[str, Any], ...]:
        """Every emitted line, for assertions and for the evidence pack."""
        return tuple(item.as_payload() for item in self.lines)

    def clear(self) -> None:
        """Forget what has been emitted."""
        self.lines.clear()


#: The process-wide emitter. Replaced in a test with a fresh one.
LOG = Log()


def log(event: str, **fields: Any) -> Line:
    """Emit a structured line carrying run id, site id and actor."""
    return LOG.emit(event, **fields)


# ---------------------------------------------------------------------------
# Spans
# ---------------------------------------------------------------------------

#: The three boundaries a trace has to cross. Named so a span at each is a
#: constant rather than a string somebody spells differently the second time.
EDGE = "draupnir.edge"
ORCHESTRATOR = "draupnir.orchestrator"
DRIVER = "draupnir.driver"


@dataclass
class Span:
    """One span, with the attributes every DRAUPNIR span carries."""

    name: str
    layer: str
    attributes: dict[str, Any] = field(default_factory=dict)
    children: list[Span] = field(default_factory=list)
    ended: bool = False

    def set(self, **attributes: Any) -> Span:
        """Add attributes, scrubbed like a log line."""
        self.attributes.update(scrubbed(attributes))
        return self

    def as_payload(self) -> dict[str, Any]:
        """The span tree, for a test and for the evidence pack."""
        return {
            "name": self.name,
            "layer": self.layer,
            "attributes": dict(self.attributes),
            "children": [item.as_payload() for item in self.children],
        }


@dataclass
class Tracer:
    """Collects spans. The OpenTelemetry SDK is wired behind this in deployment.

    Held as a small local implementation so that "spans from edge through
    orchestrator to driver boundary" is a property a test can assert about the
    shape of a trace, rather than something established by reading exporter
    configuration.
    """

    roots: list[Span] = field(default_factory=list)
    _stack: list[Span] = field(default_factory=list, repr=False)

    @contextmanager
    def span(self, name: str, layer: str, **attributes: Any) -> Iterator[Span]:
        """Open a span, nested under whatever is currently open."""
        current = Span(name=name, layer=layer, attributes=scrubbed(attributes))
        current.attributes.update(request_context.log_context())

        if self._stack:
            self._stack[-1].children.append(current)
        else:
            self.roots.append(current)

        self._stack.append(current)
        try:
            yield current
        finally:
            current.ended = True
            self._stack.pop()

    def layers(self) -> tuple[str, ...]:
        """Every layer present in the collected traces, in depth order."""
        found: list[str] = []

        def walk(span: Span) -> None:
            if span.layer not in found:
                found.append(span.layer)
            for child in span.children:
                walk(child)

        for root in self.roots:
            walk(root)
        return tuple(found)

    def clear(self) -> None:
        """Forget every collected span."""
        self.roots.clear()
        self._stack.clear()


#: The process-wide tracer. Replaced in a test with a fresh one.
TRACER = Tracer()


@contextmanager
def span(name: str, layer: str = EDGE, **attributes: Any) -> Iterator[Span]:
    """Open a span on the process tracer."""
    with TRACER.span(name, layer, **attributes) as current:
        yield current
