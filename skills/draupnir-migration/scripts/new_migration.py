"""Write a DRAUPNIR migration, and check one.

Two jobs in one file on purpose: the checks the scaffold satisfies are the
checks `--check` runs, so a hand-written migration is held to exactly what a
generated one already does. `tests/contract/test_skills.py` generates one and
runs the checks over it, and runs them over the two real migrations as well --
if the conventions move, both halves fail together.

The checks need no database. Every one of them is about the shape of the
script, and the shape is what a review misses: a migration that drops a column
reviews as a small diff and is unrecoverable at 3am.
"""

from __future__ import annotations

import argparse
import ast
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parents[3]
VERSIONS = REPO_ROOT / "migrations" / "versions"

SHAPES: Final = ("table", "column", "index")

#: The tables that carry a site scope, per the bold attributes of SAD 7.1. A
#: new table holding a `site_id` joins them, and one that does not must not
#: carry a site_id at all -- a site column with no policy is a column that
#: looks scoped and is not.
SITE_SCOPED: Final = ("ledger_entry", "artefact", "run", "projection_checkpoint")

#: DDL that cannot be undone by a forward migration, and therefore cannot be
#: undone at all here (AC-Q6). Removing something is a two-release job: stop
#: writing it, ship, then drop it.
DESTRUCTIVE: Final[tuple[tuple[str, str], ...]] = (
    (r"\bop\.drop_table\b", "op.drop_table"),
    (r"\bop\.drop_column\b", "op.drop_column"),
    (r"\bDROP\s+TABLE\b", "DROP TABLE"),
    (r"\bDROP\s+COLUMN\b", "DROP COLUMN"),
    (r"\bTRUNCATE\b", "TRUNCATE"),
)

REVISION = re.compile(r'^revision: str = "(?P<id>[^"]+)"', re.MULTILINE)
DOWN_REVISION = re.compile(r"^down_revision: str \| None = (?P<id>.+)$", re.MULTILINE)
SLUG = re.compile(r"^[a-z][a-z0-9_]*$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

#: The forward-only refusal, verbatim. Compared as a substring rather than
#: parsed, because what matters is that `downgrade` raises and says why.
FORWARD_ONLY = 'raise NotImplementedError("DRAUPNIR migrations are forward only (AC-Q6)")'


class MigrationError(ValueError):
    """The arguments would produce a migration that cannot be applied."""


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def checks(source: str, *, path: Path | None = None) -> list[str]:
    """Return every way `source` is not a DRAUPNIR migration.

    A list rather than the first failure, for the same reason a driver's
    `validate` returns a list: one round trip, not five.
    """
    problems: list[str] = []
    where = f"{path.name}: " if path else ""

    if "def upgrade() -> None:" not in source:
        problems.append(f"{where}no `upgrade()`")

    if FORWARD_ONLY not in source:
        problems.append(
            f"{where}`downgrade` must refuse: {FORWARD_ONLY}. Migrations are forward "
            "only (AC-Q6). Recovery from a bad migration is a restore plus a new "
            "forward migration, not a downgrade path nobody has ever exercised."
        )

    if REVISION.search(source) is None:
        problems.append(f"{where}no `revision` identifier")
    if DOWN_REVISION.search(source) is None:
        problems.append(f"{where}no `down_revision`")

    upgrade = source.partition("def upgrade() -> None:")[2].partition("\ndef ")[0]
    for pattern, name in DESTRUCTIVE:
        if re.search(pattern, upgrade):
            problems.append(
                f"{where}`upgrade` contains {name}, which a forward-only migration "
                "cannot undo. Removing a column is two releases: stop writing it and "
                "ship, then drop it once nothing reads it."
            )

    problems.extend(_rls_problems(source, where))
    return problems


def scoped_tables(source: str) -> set[str]:
    """The tables this migration creates with a `site_id` column.

    Parsed rather than matched, because a regex over the whole file cannot tell
    which `create_table` a `site_id` belongs to, and `site` -- which carries an
    `id` and a foreign key target, not a scope -- is exactly the table it gets
    wrong.
    """
    found: set[str] = set()
    for call in ast.walk(ast.parse(source)):
        if not isinstance(call, ast.Call):
            continue
        if not (isinstance(call.func, ast.Attribute) and call.func.attr == "create_table"):
            continue
        if not (call.args and isinstance(call.args[0], ast.Constant)):
            continue
        table = call.args[0].value
        columns = {
            inner.args[0].value
            for inner in call.args[1:]
            if isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "Column"
            and inner.args
            and isinstance(inner.args[0], ast.Constant)
        }
        if "site_id" in columns:
            found.add(str(table))
    return found


def _covered(source: str) -> set[str]:
    """The tables this migration puts under row level security.

    Two spellings are in use and both are right: 0002 names its one table in
    the statement, and 0001 loops over a tuple. So the literal names are taken
    from the statements, and when the statement is a template the names come
    from the tuple the loop reads.
    """
    covered = set(re.findall(r"ALTER TABLE ([a-z_]+) ENABLE ROW LEVEL SECURITY", source))
    if "ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" not in source:
        return covered

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Tuple | ast.List):
            continue
        names = {target.id for target in node.targets if isinstance(target, ast.Name)}
        if not any("SCOPED" in name or name.endswith("_TABLES") for name in names):
            continue
        covered |= {
            element.value
            for element in node.value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
    return covered


def _rls_problems(source: str, where: str) -> list[str]:
    """Every scoped table created here must be scoped by the database.

    SAD 11C constraint 3, and AC-B10. `ENABLE` alone leaves the owning role
    exempt, which makes the policy decorative for exactly the connection the
    application uses.
    """
    problems: list[str] = []
    scoped = scoped_tables(source)
    if not scoped:
        return problems

    covered = _covered(source)
    for table in sorted(scoped - covered):
        problems.append(
            f"{where}{table} carries a site_id and is not put under row level security. "
            "A table that looks scoped and is not is worse than one that is plainly "
            "unscoped (SAD 11C constraint 3, AC-B10)."
        )

    for required, why in (
        ("ENABLE ROW LEVEL SECURITY", "the policy is never turned on"),
        (
            "FORCE ROW LEVEL SECURITY",
            "without FORCE the table owner is exempt and the policy is decorative",
        ),
        ("CREATE POLICY site_isolation ON", "there is no policy to enforce"),
    ):
        if required not in source:
            problems.append(f"{where}creates a site-scoped table and never {required}: {why}")

    return problems


def head(versions: Path = VERSIONS) -> str | None:
    """The current head revision, or None when there is no migration yet.

    Read from the files rather than from Alembic, so this works before the
    package is installed and without a configuration file.
    """
    revisions: dict[str, str | None] = {}
    for script in sorted(versions.glob("[0-9]*.py")):
        source = script.read_text(encoding="utf-8")
        match = REVISION.search(source)
        down = DOWN_REVISION.search(source)
        if match is None or down is None:
            continue
        revisions[match["id"]] = down["id"].strip().strip('"')

    if not revisions:
        return None
    parents = {value for value in revisions.values() if value not in {None, "None"}}
    heads = sorted(set(revisions) - parents)
    if len(heads) != 1:
        msg = (
            f"migrations/versions has {len(heads)} heads ({', '.join(heads) or 'none'}). "
            "Two people branched from the same revision; merge them before adding a "
            "third, or the next `alembic upgrade head` cannot choose."
        )
        raise MigrationError(msg)
    return heads[0]


# ---------------------------------------------------------------------------
# The scaffold
# ---------------------------------------------------------------------------

HEADER = '''\
"""{message}

{rationale}

Revision ID: {revision}
Revises: {down_revision}
Create Date: {created}
"""

from __future__ import annotations

from collections.abc import Sequence

{sa_import}from alembic import op
{pg_import}
revision: str = "{revision}"
down_revision: str | None = {down_literal}
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
'''

FOOTER = '''

def downgrade() -> None:
    """Refuse. AC-Q6: migrations are forward only.

    Recovery from a bad migration is a restore plus a new forward migration,
    not a downgrade path that has never been exercised.
    """
    raise NotImplementedError("DRAUPNIR migrations are forward only (AC-Q6)")
'''

TABLE = """

def upgrade() -> None:
    op.create_table(
        "{table}",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),{site_column}
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
{scope}"""

SITE_COLUMN = """
        sa.Column("site_id", sa.Text(), sa.ForeignKey("site.id"), nullable=False),"""

SCOPE = """
    # SAD 11C constraint 3. FORCE as well as ENABLE: without it the owning role
    # is exempt, which is exactly the connection an application uses, so the
    # policy would be decorative in the one place it has to hold. The session
    # variable is set by the site resolver with SET LOCAL; a session that has
    # not set it sees zero rows rather than all of them (AC-B10).
    op.execute("ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY site_isolation ON {table} "
        "USING (site_id = current_setting('draupnir.site_id', true)) "
        "WITH CHECK (site_id = current_setting('draupnir.site_id', true))"
    )
"""

COLUMN = """

def upgrade() -> None:
    # Nullable, and added without a default that rewrites the table. A NOT NULL
    # column on a populated table takes an ACCESS EXCLUSIVE lock for as long as
    # the rewrite takes, and `run` is the table an operator is watching when
    # the deployment goes out.
    #
    # To make it NOT NULL: add it nullable here, backfill in a later migration,
    # and add the constraint in a third once nothing writes a null.
    op.add_column(
        "{table}",
        sa.Column("{column}", {type_expression}, nullable=True),
    )
"""

INDEX = """

def upgrade() -> None:
    # Alembic runs a migration inside a transaction, and CREATE INDEX
    # CONCURRENTLY cannot run in one. On a table small enough for a plain
    # CREATE INDEX this is right; on `ledger_entry` it is not, and the index
    # goes in its own migration with `op.get_context().autocommit_block()`.
    op.create_index("ix_{table}_{column}", "{table}", ["{column}"])
"""

TYPES: Final[dict[str, str]] = {
    "text": "sa.Text()",
    "boolean": "sa.Boolean()",
    "integer": "sa.Integer()",
    "bigint": "sa.BigInteger()",
    "timestamp": "sa.TIMESTAMP(timezone=True)",
    "uuid": "postgresql.UUID(as_uuid=True)",
    "jsonb": "postgresql.JSONB()",
}

RATIONALE: Final[dict[str, str]] = {
    "table": (
        "A new table. Everything it needs the database to enforce is enforced by the\n"
        "database: a constraint that lives only in the application is one a migration\n"
        "script can bypass (SAD 11C)."
    ),
    "column": (
        "One nullable column. Made NOT NULL, if at all, in a later migration after a\n"
        "backfill -- a rewrite under ACCESS EXCLUSIVE is not something to discover\n"
        "during a deployment."
    ),
    "index": (
        "One index. Inside the migration's transaction, so it is only right for a\n"
        "table small enough to lock briefly."
    ),
}


def scaffold(
    *,
    slug: str,
    shape: str,
    table: str,
    message: str,
    column: str | None = None,
    column_type: str = "text",
    site_scoped: bool = False,
    versions: Path = VERSIONS,
    revision: str | None = None,
    down_revision: str | None = None,
) -> Path:
    """Write the migration and return its path."""
    if shape not in SHAPES:
        msg = f"{shape!r} is not a shape. One of: {', '.join(SHAPES)}."
        raise MigrationError(msg)
    if not SLUG.match(slug):
        msg = f"{slug!r} is not a slug. Lowercase with underscores, e.g. `lease_table`."
        raise MigrationError(msg)
    if not IDENTIFIER.match(table):
        msg = f"{table!r} is not a table name. Lowercase with underscores."
        raise MigrationError(msg)
    if shape in {"column", "index"}:
        if column is None:
            msg = f"a {shape} migration needs --column."
            raise MigrationError(msg)
        if not IDENTIFIER.match(column):
            msg = f"{column!r} is not a column name. Lowercase with underscores."
            raise MigrationError(msg)
    if column_type not in TYPES:
        msg = f"{column_type!r} is not a known type. One of: {', '.join(sorted(TYPES))}."
        raise MigrationError(msg)
    if shape == "table" and table in SITE_SCOPED and not site_scoped:
        msg = (
            f"{table} is one of the site-scoped tables, so it needs --site-scoped. A "
            "table carrying a site_id without a policy looks scoped and is not."
        )
        raise MigrationError(msg)

    parent = down_revision if down_revision is not None else head(versions)
    if revision is None:
        revision = f"{int(parent or '0000') + 1:04d}" if (parent or "0").isdigit() else "0001"

    # Imported only where used: an unused `sa` is an F401, and a migration that
    # does not lint is one nobody can commit without editing, which is the
    # thing this scaffold exists to avoid.
    sa_import = "import sqlalchemy as sa\n" if shape in {"table", "column"} else ""
    pg_import = (
        "from sqlalchemy.dialects import postgresql\n"
        if shape == "table" or (shape == "column" and column_type in {"uuid", "jsonb"})
        else ""
    )

    body = HEADER.format(
        message=message,
        rationale=RATIONALE[shape],
        revision=revision,
        down_revision=parent or "",
        down_literal=f'"{parent}"' if parent else "None",
        created=datetime.now(UTC).date().isoformat(),
        sa_import=sa_import,
        pg_import=pg_import,
    )

    if shape == "table":
        body += TABLE.format(
            table=table,
            site_column=SITE_COLUMN if site_scoped else "",
            scope=SCOPE.format(table=table) if site_scoped else "",
        )
    elif shape == "column":
        body += COLUMN.format(table=table, column=column, type_expression=TYPES[column_type])
    else:
        body += INDEX.format(table=table, column=column)

    body += FOOTER

    problems = checks(body)
    if problems:
        # The scaffold is held to its own gate. If this ever fires, the
        # templates and the checks have drifted, which is the failure this
        # skill exists to make impossible.
        msg = "the generated migration does not pass its own checks:\n  " + "\n  ".join(problems)
        raise MigrationError(msg)

    versions.mkdir(parents=True, exist_ok=True)
    target = versions / f"{revision}_{slug}.py"
    target.write_text(body, encoding="utf-8", newline="\n")
    return target


def main(argv: list[str] | None = None) -> int:
    """Write a migration, or check existing ones."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path, nargs="*", help="check these files and exit")
    parser.add_argument("--slug", help="file name suffix, e.g. lease_table")
    parser.add_argument("--shape", choices=SHAPES)
    parser.add_argument("--table")
    parser.add_argument("--column")
    parser.add_argument("--type", dest="column_type", default="text", choices=sorted(TYPES))
    parser.add_argument("--site-scoped", action="store_true")
    parser.add_argument("--message", help="the migration's one-line docstring")
    parser.add_argument("--versions", type=Path, default=VERSIONS)
    args = parser.parse_args(argv)

    if args.check is not None:
        targets = args.check or sorted(args.versions.glob("[0-9]*.py"))
        problems: list[str] = []
        for target in targets:
            problems.extend(checks(target.read_text(encoding="utf-8"), path=target))
        for problem in problems:
            print(problem)
        if problems:
            return 1
        print(f"{len(targets)} migration(s) conform")
        return 0

    missing = [name for name in ("slug", "shape", "table", "message") if not getattr(args, name)]
    if missing:
        parser.error(f"--{', --'.join(missing)} required unless --check is given")

    try:
        written = scaffold(
            slug=args.slug,
            shape=args.shape,
            table=args.table,
            message=args.message,
            column=args.column,
            column_type=args.column_type,
            site_scoped=args.site_scoped,
            versions=args.versions,
        )
    except MigrationError as error:
        parser.error(str(error))

    print(f"wrote {written}")
    print("\nNext: `make migrate-dry` to render the SQL, then `make migrate`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
