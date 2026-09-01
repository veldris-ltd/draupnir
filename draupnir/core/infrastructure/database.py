"""SQLAlchemy engine and session factories.

SAD 7.2: PostgreSQL 16 on ANDVARI holds the ledger, the registers, run state
and RBAC.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from draupnir.core.infrastructure.config import get_settings


def create_engine() -> AsyncEngine:
    """Return an async engine built from the process settings."""
    settings = get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True, future=True)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to `engine`."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def site_scoped_session(
    factory: async_sessionmaker[AsyncSession], site_id: str
) -> AsyncIterator[AsyncSession]:
    """Yield a session with the row level security site variable set.

    SAD 11C: site scope on every scoped query is enforced by a row level
    security policy plus a session variable set by the site resolver. The
    variable is set with SET LOCAL, so it lives and dies with the transaction.
    """
    from sqlalchemy import text

    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('draupnir.site_id', :site_id, true)"),
            {"site_id": site_id},
        )
        yield session
