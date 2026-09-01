"""Edge layer: the FastAPI surface.

Knows HTTP. Contains no domain logic. A route without a role declaration must
fail at registration rather than at runtime (SAD 11B); the guard that enforces
that arrives with the API surface in Prompt 7.
"""
