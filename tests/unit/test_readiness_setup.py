"""The readiness checks do their setup at startup, never inside a probe. RF-46.

The object-store check imported `minio` and built a client inside the probe, so
the first `/readyz` of every process paid for an import and a construction on
top of the check -- and the first probe is the one an orchestrator sends while
the process comes up. An import is also the one piece of work a check's timeout
cannot bound. These pin that the setup happens when the probe is made, that a
setup that fails degrades rather than stopping startup, and that no check
carries an import statement a first probe would execute.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from draupnir.api import readiness

pytestmark = pytest.mark.unit

SOURCE = Path(readiness.__file__)


def settings(**changes: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "vault_root": "",
        "object_store_endpoint": "127.0.0.1:9000",
        "object_store_access_key": "draupnir",
        "object_store_secret_key": "not-a-secret",
        "object_store_secure": False,
        "object_store_bucket": "artefacts",
    }
    values.update(changes)
    return SimpleNamespace(**values)


class Client:
    """Stands in for `minio.Minio`, counting how often one is built."""

    built = 0

    def __init__(self, endpoint: str, **_credentials: Any) -> None:
        Client.built += 1
        self.endpoint = endpoint
        self.asked: list[str] = []
        Client.last = self

    last: Client

    def bucket_exists(self, bucket: str) -> bool:
        self.asked.append(bucket)
        return True


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> type[Client]:
    import minio

    Client.built = 0
    monkeypatch.setattr(minio, "Minio", Client)
    return Client


def test_the_client_is_built_when_the_probe_is_made_and_never_by_a_probe(
    client: type[Client],
) -> None:
    probe = readiness.object_store_probe(settings())

    assert probe is not None
    assert client.built == 1, "the client was not built when the lifespan made the probe"

    assert probe() is True
    assert probe() is True

    assert client.built == 1, "a probe built a client"
    assert client.last.asked == ["artefacts", "artefacts"]


def test_a_client_that_cannot_be_built_degrades_rather_than_stopping_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SAD 11.2: readiness reports a dependency down; it does not take the process down."""
    import minio

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        msg = "invalid endpoint"
        raise ValueError(msg)

    monkeypatch.setattr(minio, "Minio", refuse)

    probe = readiness.object_store_probe(settings(object_store_endpoint="not a host"))

    assert probe is not None
    assert probe() is False


def test_a_configured_vault_means_no_bucket_is_probed() -> None:
    assert readiness.object_store_probe(settings(vault_root="/forge/vault")) is None


def test_no_check_imports_anything_a_first_probe_would_pay_for() -> None:
    """Read from the source: an import inside a check runs on the first probe.

    `object_store_probe` itself may import, because the lifespan calls it once
    at startup. What it returns, and every check `Dependencies` runs, may not.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    offending: list[str] = []

    def visit(node: ast.AST, enclosing: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                visit(child, [*enclosing, child.name])
            else:
                if (
                    isinstance(child, ast.Import | ast.ImportFrom)
                    and enclosing
                    and enclosing[-1] != "object_store_probe"
                ):
                    offending.append(f"{'.'.join(enclosing)} line {child.lineno}")
                visit(child, enclosing)

    visit(tree, [])

    assert offending == [], f"imports a probe would run: {offending}"
