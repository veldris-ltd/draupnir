"""Edge layer: the FastAPI surface.

Knows HTTP. Contains no domain logic. A route without a role declaration fails
at registration rather than at runtime (SAD 11B), and `enforce_declarations`
stops `create_app` before a socket is opened.

Owns: HTTP framing -- routing, the wire schemas, the role guard, problem
documents, idempotency, cursor pagination, conditional writes, the event
stream, and the request context every log line below carries.
Must not: Decide anything. Every judgement belongs to the module that owns it,
and a rule implemented at the edge is a rule the CLI does not have.
"""
