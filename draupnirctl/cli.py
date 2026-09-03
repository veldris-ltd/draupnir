"""`draupnirctl`, the generated command line client.

SAD 5.1: a Python CLI shipped as a single binary via `uv tool`. SAD 11H: the
client regeneration stage exists specifically to fail the build when the CLI or
the TypeScript client has drifted from the OpenAPI specification, since a hand
edited client is the most common way a generated interface quietly stops being
generated.

Consequently the command table in `_generated.py` is machine written and the
dispatcher below is the only hand written part.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from draupnirctl._generated import GENERATED_FROM_OPENAPI_VERSION, OPERATIONS, Operation

app = typer.Typer(
    name="draupnirctl",
    help="DRAUPNIR control plane client.",
    no_args_is_help=True,
    add_completion=False,
)


def _base_url() -> str:
    return os.environ.get("DRAUPNIR_API_URL", "http://127.0.0.1:8000").rstrip("/")


def _call(operation: Operation, path_params: dict[str, str], body: Any | None) -> int:
    """Perform one request and print the response. Returns a process exit code."""
    url = _base_url() + operation.path.format(**path_params)
    headers = {"Accept": "application/json, application/problem+json"}

    # Every mutating endpoint requires an `Idempotency-Key` (SAD 11E.2), and
    # the API answers 428 without one. Generated here rather than asked of the
    # operator: the key exists so that a retried request does not act twice,
    # and a key a human types is a key a human reuses.
    if operation.method in {"POST", "PUT", "PATCH", "DELETE"}:
        headers["Idempotency-Key"] = str(uuid.uuid4())

    try:
        response = httpx.request(operation.method, url, headers=headers, json=body, timeout=30.0)
    except httpx.HTTPError as error:
        typer.secho(f"{operation.operation_id}: {error}", fg=typer.colors.RED, err=True)
        return 2

    text = response.text
    try:
        typer.echo(json.dumps(response.json(), indent=2, sort_keys=True))
    except ValueError:
        typer.echo(text)

    if response.status_code >= 400:
        return 1
    return 0


def _register(operation: Operation) -> None:
    """Attach one generated operation to the Typer application."""

    def command(
        param: Annotated[
            list[str] | None,
            typer.Option("--param", "-p", help="Path parameter as name=value."),
        ] = None,
        body: Annotated[
            str | None,
            typer.Option("--body", help="Request body as JSON, or @filename."),
        ] = None,
        spec: Annotated[
            Path | None,
            typer.Option(
                "--spec",
                help="A run specification file (SAD 6.2), wrapped as the request body.",
            ),
        ] = None,
    ) -> None:
        path_params = dict(item.split("=", 1) for item in (param or []))
        missing = [name for name in operation.path_params if name not in path_params]
        if missing:
            typer.secho(
                f"missing path parameter(s): {', '.join(missing)}", fg=typer.colors.RED, err=True
            )
            raise typer.Exit(2)

        if spec is not None and body is not None:
            typer.secho("give --spec or --body, not both", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)

        payload: Any | None = None
        if spec is not None:
            payload = {"specification": _read_specification(spec)}
        elif body is not None:
            raw = Path(body[1:]).read_text(encoding="utf-8") if body.startswith("@") else body
            payload = json.loads(raw)

        raise typer.Exit(_call(operation, path_params, payload))

    command.__doc__ = operation.summary
    app.command(name=operation.command, help=operation.summary)(command)


for _operation in OPERATIONS:
    _register(_operation)


def _read_specification(path: Path) -> Any:
    """Read a run specification file and return it parsed.

    This is input marshalling, not a client method. The request still goes
    through the generated operation table below; what this does is spare an
    operator from wrapping their specification in `{"specification": …}` by
    hand and from discovering the shape by reading the OpenAPI document.

    The identity is not computed here. AC-F1 requires the CLI and the console
    to agree on it, and they do because neither of them computes it: both post
    the specification and the API returns the identity it recorded. A client
    that hashed the specification itself would be a second implementation of
    the rule, and the first time the two disagreed the disagreement would be
    invisible.

    JSON only for now. A specification is JSON or YAML in SAD 6.2 and this
    package deliberately carries no YAML parser, because adding one to the CLI
    adds it to every deployment of the control plane. `yq . spec.yaml` converts.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        typer.secho(
            f"{path}: not readable as JSON ({error}). A YAML specification can be "
            "converted with `yq . spec.yaml`.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2) from error


@app.command(name="version", help="Print the client version and the contract it was built from.")
def version() -> None:
    """Print the client version and the OpenAPI version it was generated from."""
    from draupnir import __version__

    typer.echo(f"draupnirctl {__version__} (OpenAPI {GENERATED_FROM_OPENAPI_VERSION})")


def main() -> None:
    """Console script entry point."""
    app()
