"""The ORM models describe the schema the migrations actually create.

`metadata.create_all` is never called and autogenerate is switched off, so
nothing forces `models.py` and `migrations/` to agree. A model that has
drifted is worse than no model: it type-checks, it reads plausibly, and it
produces a query the database rejects at three in the morning.

This compares the two against a live PostgreSQL. It is deliberately structural
-- tables, columns, nullability, primary keys -- rather than a full type
comparison, because the dialect's rendering of a type is not the thing that
breaks; a column that is not there is.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, inspect

from draupnir.core.infrastructure.models import Base

pytestmark = pytest.mark.integration

#: Tables the migrations create that no model describes, and why.
UNMODELLED = {
    "alembic_version",  # Alembic's own bookkeeping.
}


def test_every_modelled_table_exists(owner_engine: Engine) -> None:
    present = set(inspect(owner_engine).get_table_names())
    modelled = set(Base.metadata.tables)
    missing = modelled - present
    assert not missing, f"models describe tables the migrations do not create: {sorted(missing)}"


def test_every_migrated_table_is_modelled(owner_engine: Engine) -> None:
    present = set(inspect(owner_engine).get_table_names()) - UNMODELLED
    modelled = set(Base.metadata.tables)
    unmodelled = present - modelled
    assert not unmodelled, f"the migrations create tables no model describes: {sorted(unmodelled)}"


def test_every_column_matches(owner_engine: Engine) -> None:
    inspector = inspect(owner_engine)
    problems: list[str] = []

    for name, table in sorted(Base.metadata.tables.items()):
        actual = {column["name"]: column for column in inspector.get_columns(name)}
        modelled = {column.name: column for column in table.columns}

        for column in sorted(set(modelled) - set(actual)):
            problems.append(f"{name}.{column}: modelled but not migrated")
        for column in sorted(set(actual) - set(modelled)):
            problems.append(f"{name}.{column}: migrated but not modelled")

        for column in sorted(set(modelled) & set(actual)):
            if modelled[column].nullable != actual[column]["nullable"]:
                problems.append(
                    f"{name}.{column}: model says nullable={modelled[column].nullable}, "
                    f"the database says nullable={actual[column]['nullable']}"
                )

    assert not problems, "the models and the migrations disagree:\n  " + "\n  ".join(problems)


def test_every_primary_key_matches(owner_engine: Engine) -> None:
    inspector = inspect(owner_engine)
    problems: list[str] = []

    for name, table in sorted(Base.metadata.tables.items()):
        actual = tuple(inspector.get_pk_constraint(name)["constrained_columns"])
        modelled = tuple(column.name for column in table.primary_key.columns)
        if set(actual) != set(modelled):
            problems.append(f"{name}: model key {modelled}, database key {actual}")

    assert not problems, "primary keys disagree:\n  " + "\n  ".join(problems)


def test_the_run_table_is_documented_as_a_projection(owner_engine: Engine) -> None:
    # An operator with psql who assumes `run` is authoritative will write to
    # it. The comment is the only warning that reaches them there.
    comment = inspect(owner_engine).get_table_comment("run")["text"]
    assert comment is not None
    assert "Projection" in comment
    assert "rebuild_projection" in comment


def test_the_ledger_table_is_documented_as_append_only(owner_engine: Engine) -> None:
    comment = inspect(owner_engine).get_table_comment("ledger_entry")["text"]
    assert comment is not None
    assert "Append only" in comment
