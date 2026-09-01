"""Alembic environment for DRAUPNIR.

Migrations run against the synchronous driver: they are DDL, they run once,
and an async engine buys nothing here.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from draupnir.core.infrastructure.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url_sync)

# The schema is defined by the migrations themselves, not reflected from an ORM
# metadata object. Autogenerate is deliberately unavailable: a forward-only
# ledger schema with triggers and row level security is not something to
# round-trip through a diff.
target_metadata = None


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a connection. This is the dry run."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
