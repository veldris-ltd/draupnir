"""RFC 9457 problem details.

SAD 11E.2: every problem type has a stable URI, a human readable title and a
machine readable code. No bare 500 reaches a client.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

#: The stable base for every problem type URI. A client may dereference it.
PROBLEM_BASE = "https://veldris.internal/problems/draupnir"

CONTENT_TYPE = "application/problem+json"


class Problem(BaseModel):
    """An RFC 9457 problem document."""

    type: str = Field(description="Stable URI identifying the problem type.")
    title: str = Field(description="Short, human readable summary.")
    status: int = Field(description="HTTP status code.")
    code: str = Field(description="Machine readable, stable problem code.")
    detail: str | None = Field(default=None, description="Explanation for this occurrence.")
    instance: str | None = Field(default=None, description="URI of this occurrence.")


class ProblemError(Exception):
    """Raised to return a problem document from anywhere in the edge layer."""

    def __init__(self, *, status: int, code: str, title: str, detail: str | None = None) -> None:
        """Record the problem to be rendered."""
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        super().__init__(f"{code}: {title}")


def _render(problem: Problem) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(exclude_none=True),
        media_type=CONTENT_TYPE,
    )


def _problem(
    request: Request, *, status: int, code: str, title: str, detail: str | None
) -> JSONResponse:
    return _render(
        Problem(
            type=f"{PROBLEM_BASE}/{code}",
            title=title,
            status=status,
            code=code,
            detail=detail,
            instance=str(request.url.path),
        )
    )


async def handle_problem_error(request: Request, exc: Exception) -> JSONResponse:
    """Render a `ProblemError` raised by a handler."""
    assert isinstance(exc, ProblemError)  # noqa: S101 -- narrowing for the handler signature
    return _problem(request, status=exc.status, code=exc.code, title=exc.title, detail=exc.detail)


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Map Starlette's HTTPException onto a problem document."""
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    return _problem(
        request,
        status=exc.status_code,
        code=f"http-{exc.status_code}",
        title=str(exc.detail),
        detail=None,
    )


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Map request validation failure onto a problem document."""
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    return _problem(
        request,
        status=422,
        code="request-invalid",
        title="The request did not validate against the schema",
        detail="; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()),
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Catch anything unhandled so that no bare 500 reaches a client.

    The detail deliberately carries no exception text: an unexpected error is
    the most likely place for a secret or a corpus excerpt to leak outward
    (SAD 11E.4). The trace goes to the log, correlated by request path.
    """
    del exc
    return _problem(
        request,
        status=500,
        code="internal-error",
        title="The request could not be completed",
        detail="The failure has been recorded. Quote the request path when reporting it.",
    )


#: Registered on the application at construction, so that a route cannot be
#: added without inheriting the mapping.
EXCEPTION_HANDLERS: dict[Any, Any] = {
    ProblemError: handle_problem_error,
    StarletteHTTPException: handle_http_exception,
    RequestValidationError: handle_validation_error,
    Exception: handle_unexpected_error,
}
