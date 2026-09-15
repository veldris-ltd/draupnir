"""`draupnirctl`, the generated command line client.

SAD 5.1: a Python CLI shipped as a single binary via `uv tool`. SAD 11H: the
client regeneration stage exists specifically to fail the build when the CLI or
the TypeScript client has drifted from the OpenAPI specification, since a hand
edited client is the most common way a generated interface quietly stops being
generated.

Consequently the command table in `_generated.py` is machine written and the
dispatcher below is the only hand written part.

**Conditional writes (RF-39).** The six operations that take `If-Match` were
refused with 428 every time, because nothing here sent one. Every such command
now takes `--if-match`. Given none, it reads the tag from the read the OpenAPI
document names for it, sends that, and says so on standard error -- so the
write is conditional on what was read just now, and the operator is told that
"just now" is what it was conditional on. The mapping is the document's, not
this file's: the table carries it, like everything else it carries.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from draupnirctl._generated import (
    GENERATED_FROM_OPENAPI_VERSION,
    OPERATIONS,
    Operation,
    Precondition,
)

app = typer.Typer(
    name="draupnirctl",
    help="DRAUPNIR control plane client.",
    no_args_is_help=True,
    add_completion=False,
)

ACCEPT = "application/json, application/problem+json"


def _base_url() -> str:
    return os.environ.get("DRAUPNIR_API_URL", "http://127.0.0.1:8000").rstrip("/")


def _print(response: httpx.Response) -> None:
    try:
        typer.echo(json.dumps(response.json(), indent=2, sort_keys=True))
    except ValueError:
        typer.echo(response.text)


def _read_tag(operation: Operation, path_params: dict[str, str]) -> str | None:
    """Read the tag a conditional write is conditional on, from the declared read.

    Returns `None`, having said why, when the read fails or holds no tag. A
    write sent without one would only be refused with 428, which tells the
    operator less than this does.
    """
    declared: Precondition | None = operation.precondition
    read = next(
        (item for item in OPERATIONS if declared and item.operation_id == declared.read), None
    )
    if declared is None or read is None:
        typer.secho(
            f"{operation.command}: the command table names no read for its If-Match; "
            "pass --if-match.",
            fg=typer.colors.RED,
            err=True,
        )
        return None

    mapping = dict(declared.parameters)
    url = _base_url() + read.path.format(
        **{name: path_params[mapping.get(name, name)] for name in read.path_params}
    )
    try:
        response = httpx.request("GET", url, headers={"Accept": ACCEPT}, timeout=30.0)
    except httpx.HTTPError as error:
        typer.secho(f"{read.command}: {error}", fg=typer.colors.RED, err=True)
        return None
    if response.status_code >= 400:
        typer.secho(
            f"{operation.command}: reading the current tag with {read.command} failed:",
            fg=typer.colors.RED,
            err=True,
        )
        _print(response)
        return None

    if declared.collection is not None:
        wanted = path_params[str(declared.parameter)]
        entries = response.json().get(declared.collection) or []
        entry = next((item for item in entries if str(item.get(declared.key)) == wanted), None)
        tag = str(entry.get("etag") or "") if entry is not None else ""
        if not tag:
            typer.secho(
                f"{operation.command}: {read.command} lists no {declared.key} {wanted} with a "
                "tag, so there is nothing current to act on.",
                fg=typer.colors.RED,
                err=True,
            )
            return None
    else:
        tag = response.headers.get("ETag") or ""
        if not tag:
            typer.secho(
                f"{operation.command}: {read.command} returned no ETag.",
                fg=typer.colors.RED,
                err=True,
            )
            return None

    typer.secho(
        f"{operation.command}: If-Match {tag}, as {read.command} returned it just now. "
        "Pass --if-match to make the write conditional on what you read earlier instead.",
        err=True,
    )
    return tag


def _call(
    operation: Operation,
    path_params: dict[str, str],
    body: Any | None,
    if_match: str | None = None,
) -> int:
    """Perform one request and print the response. Returns a process exit code."""
    url = _base_url() + operation.path.format(**path_params)
    headers = {"Accept": ACCEPT}

    # Every mutating endpoint requires an `Idempotency-Key` (SAD 11E.2), and
    # the API answers 428 without one. Generated here rather than asked of the
    # operator: the key exists so that a retried request does not act twice,
    # and a key a human types is a key a human reuses.
    if operation.method in {"POST", "PUT", "PATCH", "DELETE"}:
        headers["Idempotency-Key"] = str(uuid.uuid4())

    if operation.if_match:
        tag = if_match if if_match is not None else _read_tag(operation, path_params)
        if tag is None:
            return 1
        headers["If-Match"] = tag

    try:
        response = httpx.request(operation.method, url, headers=headers, json=body, timeout=30.0)
    except httpx.HTTPError as error:
        typer.secho(f"{operation.operation_id}: {error}", fg=typer.colors.RED, err=True)
        return 2

    _print(response)

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
        if_match: Annotated[
            str | None,
            typer.Option(
                "--if-match",
                help=(
                    "The ETag this write is conditional on. Omitted, a conditional command "
                    "reads the current tag first and says so."
                ),
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

        if if_match is not None and not operation.if_match:
            typer.secho(
                f"{operation.command} is not a conditional write and takes no --if-match",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(2)

        payload: Any | None = None
        if spec is not None:
            payload = {"specification": _read_specification(spec)}
        elif body is not None:
            raw = Path(body[1:]).read_text(encoding="utf-8") if body.startswith("@") else body
            payload = json.loads(raw)

        raise typer.Exit(_call(operation, path_params, payload, if_match))

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
def version(
    revision: bool = typer.Option(
        False, "--revision", help="Print the build revision alone, for a script to capture."
    ),
) -> None:
    """Print the client version, or just the revision.

    `--revision` exists because the default prints a sentence and something
    captured it as a deployment revision: `deploy.yaml` recorded
    `draupnirctl 0.1.0 (OpenAPI 1.0.0)` as the rollback target, which
    `rollback.sh` turned into an image tag (RF-04). A caller that wants a
    machine-readable answer now has one.

    It is still not the right answer for a rollback -- that is
    `deploy/current-revision.sh`, which reads what the *host* is running rather
    than what this client was built from -- and the help text does not pretend
    otherwise.
    """
    from draupnir import __version__

    if revision:
        typer.echo(__version__)
        return
    typer.echo(f"draupnirctl {__version__} (OpenAPI {GENERATED_FROM_OPENAPI_VERSION})")


def main() -> None:
    """Console script entry point."""
    app()
