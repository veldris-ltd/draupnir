---
name: draupnir-migration
description: >
  Write an Alembic migration for DRAUPNIR — forward only, row-level security on
  anything site scoped, no destructive DDL, and the constraints of SAD 11C
  enforced by the database rather than by the application. Use when changing
  the schema: "add a table", "add a column", "new index", "migration",
  "alter the schema".
---

# draupnir-migration

Write a migration that applies cleanly, is checkable without a database, and
cannot quietly leave a table unscoped.

## Why a scaffold rather than a description

Three of the rules here fail late and expensively:

- A migration is **forward only** (AC-Q6). A `downgrade` that has never been
  exercised is not a recovery plan; it is a second untested migration you run
  during an incident.
- A table with a `site_id` and **no policy looks scoped and is not**. Every
  read against it returns every site's rows, and no test fails, because the
  test fixture only ever has one site in it.
- `ENABLE ROW LEVEL SECURITY` without **`FORCE`** leaves the owning role
  exempt — and the owning role is the connection an application uses. The
  policy is then decorative in the one place it has to hold.

None of these produce an error. They produce a system that works until it is
audited.

## Use it

```bash
python skills/draupnir-migration/scripts/new_migration.py --slug lease_table --shape table --table lease --site-scoped --message "Leases on scarce partitions."
```

Three shapes:

| `--shape` | Produces |
|---|---|
| `table` | `create_table`, plus the RLS triple when `--site-scoped` |
| `column` | one **nullable** `add_column`, with the two-step note for NOT NULL |
| `index` | `create_index` inside the migration's transaction |

The revision number is the next one after the current head, and the scaffold
refuses to write when `migrations/versions/` has more than one head — two
people branching from the same revision is the failure that is easiest to
create and hardest to see.

## Check what is already there

```bash
python skills/draupnir-migration/scripts/new_migration.py --check
```

Runs over every migration in `migrations/versions/`, and needs no database.
The scaffold runs the same checks over its own output before writing it, so a
generated migration and a hand-written one are held to the same bar.

What it checks:

- `downgrade` refuses, with the AC-Q6 message.
- `revision` and `down_revision` are present, and there is exactly one head.
- `upgrade` contains no `DROP TABLE`, `DROP COLUMN`, `drop_table`,
  `drop_column` or `TRUNCATE`.
- Every table created with a `site_id` is put under row level security, with
  `ENABLE`, `FORCE`, and a `site_isolation` policy.

The scoped-table check parses the file rather than matching it, because a
regex over the whole file cannot tell which `create_table` a `site_id` belongs
to — and `site`, which carries an `id` and is the foreign key target, is
exactly the table it gets wrong.

## What you then write

The DDL. The scaffold gives you the shape and the constraints that must
travel with it; the columns, the checks and the indexes are your feature.

Put every invariant the database can hold in the database. SAD 11C: a
constraint that lives only in the application is a constraint a migration
script can bypass. `release` requires an `approval_id` by foreign key and NOT
NULL, not because the API remembers to check.

## Refusals

**Do not write a working `downgrade`.** Not even a correct one. Recovery is a
restore plus a new forward migration.

**Do not drop anything in the same release that stops writing it.** Two
releases: stop writing, ship, then drop once nothing reads it.

**Do not add a NOT NULL column to a populated table in one step.** The rewrite
takes `ACCESS EXCLUSIVE` for its whole duration, and `run` is the table an
operator is watching when the deployment goes out. Nullable, backfill,
constrain — three migrations.

**Do not use `CREATE INDEX CONCURRENTLY` inline.** Alembic runs a migration in
a transaction and concurrent index builds cannot run in one. It needs
`op.get_context().autocommit_block()` and its own migration.

**Do not edit an applied migration.** Its hash is what `alembic_version`
records; edit it and the next deployment disagrees with the last one about
what has been run.

## References

- `references/conventions.md` — the three constraints of SAD 11C, the tables
  that carry a site scope, the enum values, and what the integration tests
  actually assert.

## Verified

`tests/contract/test_skills.py` generates all three shapes into a temporary
`versions/` directory alongside copies of the real migrations, then runs
`--check` over the result and over the two real migrations. If the conventions
move and this skill does not, both halves fail together.
