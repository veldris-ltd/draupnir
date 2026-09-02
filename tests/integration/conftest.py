"""Ephemeral PostgreSQL and MinIO, per SAD 11E.3.

The containers are session scoped: starting PostgreSQL once and migrating once
is the difference between an integration suite people run and one they skip.
Isolation between tests comes from transactions that roll back, not from fresh
containers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

from draupnir.core.infrastructure.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def _adopt_docker_context() -> None:
    """Point the Docker SDK at the endpoint the `docker` CLI is actually using.

    The SDK defaults to the default context's socket. Docker Desktop runs on a
    different one, so a developer whose `docker ps` works would otherwise see
    every integration test error out with "cannot find the file specified".
    Reading the active context here costs one subprocess and removes that.
    """
    if os.environ.get("DOCKER_HOST"):
        return
    binary = shutil.which("docker")
    if binary is None:
        return
    result = subprocess.run(  # noqa: S603
        [binary, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    host = result.stdout.strip()
    if result.returncode == 0 and host:
        os.environ["DOCKER_HOST"] = host


_adopt_docker_context()

#: A role that is not the table owner and not a superuser, so that the row
#: level security policies of SAD 11C actually apply to it.
APP_ROLE = "draupnir_app"
APP_PASSWORD = "draupnir-integration"  # noqa: S105 -- ephemeral container credential

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark everything in this directory as an integration test."""
    for item in items:
        item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """Start PostgreSQL 16 and yield an owner connection URL."""
    testcontainers = pytest.importorskip(
        "testcontainers.postgres", reason="Docker and testcontainers are required"
    )
    with testcontainers.PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        yield container.get_connection_url()


def migrate_and_grant(url: str) -> None:
    """Apply every migration, then grant the unprivileged app role its rights.

    Exposed rather than inlined into the fixture because the seed fixture needs
    to reset and re-apply the schema, which is the same operation `make
    reset-db` performs.
    """
    from alembic import command
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    os.environ["DRAUPNIR_DATABASE_URL_SYNC"] = url

    # `get_settings` is `lru_cache`d process-wide, so anything that read the
    # settings before this line -- one request to `/healthz` is enough -- has
    # pinned the default `localhost:5432`, where nothing is listening. Every
    # later fixture then times out, and the failure looks like a broken
    # container rather than a stale cache. Clearing it here makes the ordering
    # hazard impossible rather than merely unlikely.
    get_settings.cache_clear()

    command.upgrade(config, "head")

    engine = create_engine(url, future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                f"DO $do$ BEGIN "
                f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
                f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' "
                f"NOSUPERUSER NOBYPASSRLS; END IF; END $do$;"
            )
        )
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        connection.execute(
            text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
            )
        )
    engine.dispose()


def reset_schema(url: str) -> None:
    """Drop and rebuild the schema. The ledger cannot be emptied any other way.

    `ledger_entry` refuses DELETE and TRUNCATE by design (SAD 11C), so a test
    that needs an empty database has to take the schema down with it. This is
    the same operation as `make reset-db`.
    """
    engine = create_engine(url, future=True)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    migrate_and_grant(url)


@pytest.fixture(scope="session")
def migrated(postgres_url: str) -> str:
    """Apply every migration once, then create the unprivileged app role."""
    migrate_and_grant(postgres_url)
    return postgres_url


@pytest.fixture(scope="session")
def owner_engine(migrated: str) -> Iterator[Engine]:
    """An engine connected as the schema owner."""
    engine = create_engine(migrated, future=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(migrated: str) -> Iterator[Engine]:
    """An engine connected as the unprivileged application role."""
    url = migrated.split("://", 1)[1].split("@", 1)[1]
    engine = create_engine(f"postgresql+psycopg://{APP_ROLE}:{APP_PASSWORD}@{url}", future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def owner(owner_engine: Engine) -> Iterator[Connection]:
    """A transaction as the owner, rolled back after the test."""
    with owner_engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@pytest.fixture
def app(app_engine: Engine) -> Iterator[Connection]:
    """A transaction as the application role, rolled back after the test."""
    with app_engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@pytest.fixture(scope="session")
def minio() -> Iterator[dict[str, str]]:
    """Start MinIO and yield its connection details."""
    module = pytest.importorskip(
        "testcontainers.minio", reason="Docker and testcontainers are required"
    )
    with module.MinioContainer() as container:
        config = container.get_config()
        yield {
            "endpoint": config["endpoint"],
            "access_key": config["access_key"],
            "secret_key": config["secret_key"],
        }
