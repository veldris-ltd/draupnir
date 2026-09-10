"""The two pieces the deployment runs and nothing exercised. RF-20.

`draupnir/core/infrastructure/database.py` was at **0 per cent** on the stage
that names it, and `draupnir/worker/__main__.py` at 0 on no stage at all. Both
are things a running forge depends on absolutely: one builds every engine in
the process, the other *is* the worker as `deploy/units/draupnir-worker` starts
it.

Neither was uncovered because it is hard to reach. They were uncovered because
of where they sit: the integration suite starts the API as a *subprocess*, so
coverage never sees the engine it builds, and the worker tests construct
`Worker` directly rather than going in through the command line. A module can
be exercised constantly by a running system and measured at zero, and that is
the least useful kind of zero -- it says nothing about risk and it drags a
stage's floor down until somebody lowers the floor.

`site_scoped_session` in particular is worth testing rather than assuming. SAD
11C's site isolation is a row level security policy plus a session variable,
and this is the only thing that sets the variable. RF-18 found a query relying
on that policy alone reporting two forges' rows added together, which is what
this mechanism failing quietly looks like.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

from draupnir.core.infrastructure import database

pytestmark = pytest.mark.integration

SITE = "sindri-entry-points"
OTHER = "sindri-entry-points-other"


@pytest.fixture
def committed(migrated: str) -> Iterator[Engine]:
    """An engine that commits, so a session in another connection can read it."""
    made = create_engine(migrated, future=True)
    yield made
    made.dispose()


@pytest.fixture
def two_sites(committed: Engine) -> Iterator[str]:
    """Two forges, each with runs, so scoping has something to get wrong."""
    with committed.begin() as connection:
        for site in (SITE, OTHER):
            connection.execute(
                text(
                    "INSERT INTO site (id, name, location, timezone, control_plane_uri,"
                    " anchor_state) VALUES (:id, :id, 'Nuneaton', 'Europe/London',"
                    " 'https://alviss.example.internal', 'ANCHORED')"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": site},
            )
        _runs(connection, SITE, 2)
        _runs(connection, OTHER, 5)
    yield SITE


def _runs(connection: Connection, site: str, count: int) -> None:
    connection.execute(text("SELECT set_config('draupnir.site_id', :s, true)"), {"s": site})
    for index in range(count):
        connection.execute(
            text(
                "INSERT INTO run (id, site_id, name, spec_hash, kind, state)"
                " VALUES (:id, :site, :name, :hash, 'adapter', 'QUEUED')"
            ),
            {
                "id": str(uuid.uuid4()),
                "site": site,
                "name": f"{site}-{index}",
                "hash": uuid.uuid4().hex,
            },
        )


# ---------------------------------------------------------------------------
# The engine factory
# ---------------------------------------------------------------------------


def test_the_engine_factory_builds_an_engine_from_the_process_settings(
    migrated: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`create_engine()` takes no argument, which is the point of it.

    Every caller in the process gets the same configuration without passing a
    URL around, and the one place that decides it is `Settings`. A factory that
    read the environment at import time instead would freeze whatever was set
    when the module was first touched.
    """
    from draupnir.core.infrastructure.config import get_settings

    url = migrated.replace("postgresql+psycopg", "postgresql+asyncpg")
    monkeypatch.setenv("DRAUPNIR_DATABASE_URL", url)
    get_settings.cache_clear()

    engine = database.create_engine()
    try:
        assert engine.url.drivername == "postgresql+asyncpg"
        assert engine.pool is not None
    finally:
        get_settings.cache_clear()


async def test_a_scoped_session_sets_the_variable_the_policies_read(
    migrated: str, two_sites: str
) -> None:
    """SAD 11C, from the only place that sets it.

    The policy compares `site_id` against `current_setting('draupnir.site_id')`
    and nothing else in the process sets that. A session factory that yielded
    an unscoped session would leave every scoped query returning nothing at
    best, and another forge's rows at worst.
    """
    url = migrated.replace("postgresql+psycopg", "postgresql+asyncpg")

    from sqlalchemy.ext.asyncio import create_async_engine

    async_engine = create_async_engine(url, future=True)
    try:
        factory = database.session_factory(async_engine)
        async for session in database.site_scoped_session(factory, two_sites):
            found = await session.execute(text("SELECT current_setting('draupnir.site_id', true)"))
            assert found.scalar_one() == two_sites
    finally:
        await async_engine.dispose()


async def test_the_variable_dies_with_the_transaction(migrated: str, two_sites: str) -> None:
    """The scope dies with the transaction, because it is set with SET LOCAL.

    A pooled connection must not carry one forge's scope to the next request
    that borrows it. This is the failure that would be invisible in testing and catastrophic in
    deployment: a connection returned to the pool still scoped to Sindri, handed
    to a request for Brokkr, answering with Sindri's runs.
    """
    url = migrated.replace("postgresql+psycopg", "postgresql+asyncpg")

    from sqlalchemy.ext.asyncio import create_async_engine

    async_engine = create_async_engine(url, future=True)
    try:
        factory = database.session_factory(async_engine)
        async for session in database.site_scoped_session(factory, two_sites):
            assert session is not None

        # A fresh session on the same pool. The setting was transaction local,
        # so it is gone.
        async with factory() as after:
            found = await after.execute(text("SELECT current_setting('draupnir.site_id', true)"))
            assert found.scalar_one() in {None, ""}
    finally:
        await async_engine.dispose()


# ---------------------------------------------------------------------------
# The worker's command line
# ---------------------------------------------------------------------------


def test_one_tick_through_the_command_line_reports_what_it_did(
    migrated: str, two_sites: str, tmp_path: Path
) -> None:
    """`python -m draupnir.worker --once` is a runbook step and a smoke test.

    Every other worker test constructs `Worker` directly, so the argument
    parsing, the settings merge and the exit code -- the whole of what an
    operator and a unit file actually invoke -- were exercised by nothing.
    """
    from draupnir.worker.__main__ import main

    code = main(
        [
            "--once",
            "--site",
            two_sites,
            "--database-url",
            migrated,
            "--scratch",
            str(tmp_path),
            "--no-duties",
        ]
    )

    assert code == 0


def test_the_command_line_takes_precedence_over_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator overriding one setting must not have to restate the rest.

    A merge that took the environment wholesale, or the arguments wholesale,
    would make `--site` mean "and forget the database URL" -- which reads as
    the worker being unable to connect.
    """
    import argparse

    from draupnir.worker.__main__ import _settings

    monkeypatch.setenv("DRAUPNIR_SITE_ID", "from-environment")
    monkeypatch.setenv("DRAUPNIR_WORKER_INTERVAL", "30")

    merged = _settings(
        argparse.Namespace(
            site="from-the-command-line",
            database_url="",
            interval=None,
            scratch="",
            no_duties=False,
            once=True,
            ticks=None,
            log_level="info",
        )
    )

    assert merged.site_id == "from-the-command-line"
    assert merged.interval == 30.0, "an unstated argument discarded the environment's value"
