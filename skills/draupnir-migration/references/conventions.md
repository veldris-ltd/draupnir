# Schema and migration conventions

What `migrations/` actually contains. Where this differs from the SAD, the
code is right and the specification is amended.

## The three constraints of SAD 11C

Each is in the database, not in the application, because a constraint that
lives only in the application is one a migration script can bypass.

**1. `ledger_entry` accepts INSERT only.** A statement-level trigger,
`trg_ledger_entry_append_only`, raises `restrict_violation` on UPDATE, DELETE
and TRUNCATE. There is no role that is exempt.

**2. `release` requires an `approval_id`.** A foreign key and NOT NULL, so
SKIDBLADNIR cannot publish without a GLEIPNIR approval even if the API forgets
to look.

**3. Site scope by row level security.** On `ledger_entry`, `artefact`, `run`
and `projection_checkpoint`:

```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
CREATE POLICY site_isolation ON <t>
  USING (site_id = current_setting('draupnir.site_id', true))
  WITH CHECK (site_id = current_setting('draupnir.site_id', true));
```

`FORCE` is the half that is easy to omit and impossible to notice: without it
the table owner is exempt, and the table owner is the role the application
connects as.

A session that has not set `draupnir.site_id` sees **zero rows**, not all rows
(AC-B10), and a write with no scope is refused rather than landing under some
default. The variable is set with `SET LOCAL` by the site resolver, so it
lasts exactly one transaction.

## The tables

`site`, `source`, `run`, `artefact`, `gate_result`, `approval`, `plugin`,
`release`, `retention_action`, `ledger_entry`, `projection_checkpoint`.

`site` is the registry, and it is not site scoped — it is the list of scopes.
It carries `id`, `name`, `location`, `timezone`, `control_plane_uri`,
`anchor_state` and `last_anchored_at`. `sindri` is site 1.

`run` is a **projection** of the ledger, rebuilt by
`draupnir.core.domain.projector`. Its table comment says so, because an
operator with `psql` who assumes it is authoritative will eventually write to
it and `rebuild_projection` will discard the write without saying anything.

## Enums

`run_state`, the fourteen states of SAD 6.1:

```
DRAFT, CORPUS_REGISTERED, LICENCE_CLEARED, CURATED, QUEUED, TRAINING,
TRAINED, FAILED, EVALUATING, MERGED, QUANTISED, AWAITING_APPROVAL,
RELEASED, QUARANTINED
```

`artefact_kind`, the eight:

```
corpus_raw, corpus_curated, base_model, substrate, adapter, merged,
quantised, report
```

Adding a value to a PostgreSQL enum is `ALTER TYPE ... ADD VALUE`, which
cannot run inside a transaction block before PostgreSQL 12 and cannot be
reversed at all. It belongs in its own migration.

## Hashes

Every hash column carries `CHECK (col ~ '^[0-9a-f]{64}$')`. Lowercase hex, 64
characters. A non-hex hash is refused by the database, which is where a
mistyped digest is caught rather than three screens later.

## Forward only

`downgrade` raises. Verbatim, in every migration:

```python
def downgrade() -> None:
    """Refuse. AC-Q6: migrations are forward only.

    Recovery from a bad migration is a restore plus a new forward migration,
    not a downgrade path that has never been exercised.
    """
    raise NotImplementedError("DRAUPNIR migrations are forward only (AC-Q6)")
```

`migrations/script.py.mako` already carries it, so `alembic revision` produces
it. The check exists because somebody eventually copies an older file instead.

## Locks worth knowing

| Operation | Lock | Note |
|---|---|---|
| `ADD COLUMN` nullable, no default | brief `ACCESS EXCLUSIVE` | safe |
| `ADD COLUMN NOT NULL DEFAULT` | full rewrite on PG < 11 | three migrations instead |
| `CREATE INDEX` | blocks writes | fine on a small table |
| `CREATE INDEX CONCURRENTLY` | does not block | needs `autocommit_block()`, own migration |
| `ALTER TYPE ... ADD VALUE` | — | own migration, irreversible |

## What the integration tests assert

`tests/integration/test_schema_constraints.py`, against an ephemeral
PostgreSQL from testcontainers:

- The ledger accepts an insert, and refuses update, delete and truncate.
- A site chain cannot reuse a sequence number; a non-hex hash is refused.
- A release without an approval is refused, and one naming an unknown approval
  is refused.
- A write without the site variable is refused; a write for another site is
  refused; a read sees only the scoped site.
- `FORCE` is set on every scoped table, and every scoped table carries the
  `site_isolation` policy.
- An unscoped query returns **zero rows rather than all rows**, and an
  unscoped write is refused rather than attributed somewhere.
- A personal-data source must name a DPIA; a source cannot hold a run-only
  state.

`tests/integration/test_models_match_schema.py` compares the SQLAlchemy models
against the migrated schema, so a migration that adds a column without a model
change — or the reverse — fails there rather than at runtime.
