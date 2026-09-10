"""Idempotency keys, in the database rather than in one process's memory.

RF-14. `deps.STORE` was an `IdempotencyStore()` backed by a `dict`, and there
was no table. SAD 5.1 specifies two to four API processes, so a key reserved in
process A was unknown to process B: the documented behaviour -- "a second click
while the first request is still running is refused rather than acting twice" --
failed precisely under the concurrency the control exists for, and every
reservation was lost on restart.

`POST /v1/runs` was partly protected by AC-F2's run identity, which is computed
from the specification and refuses a duplicate whichever process sees it.
Nothing else was: `registerSource`, `ingestCorpus`, `curateCorpus`,
`decideGate`, `cancelRun`, `retryRun` and `publishRelease` had no protection at
all against a double click across two workers behind one proxy.

**The reservation is the row.** A store that recorded only completed responses
could not answer the in-flight case, which is the case that matters: the
reservation happens before the work starts, so the second request finds it and
is refused. `INSERT ... ON CONFLICT` makes that atomic across processes --
whichever transaction commits first owns the key, and the other one is told so
by the database rather than by a check that raced.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_COMMENT = (
    "Idempotency reservations and stored responses (AC-B1, SAD 11E.2). A row is "
    "written before the work starts, so a replay that arrives while the first "
    "request is still running is refused rather than acting twice. Swept by the "
    "worker; do not delete by hand while a request may still be in flight."
)


def upgrade() -> None:
    """Create the table, its expiry index and its site isolation."""
    op.create_table(
        "idempotency_key",
        sa.Column("site_id", sa.Text(), nullable=False),
        # The actor as well as the site. Two operators using the same obvious
        # key -- `retry-1` -- must not collide, and a key from one site must
        # not resolve at another.
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        # What the request was. A key that arrives with a different body is a
        # client bug, and replaying the first response would tell the caller a
        # request they did not make had succeeded.
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # NULL while the first request is still running. This column is the
        # whole of the in-flight answer.
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("body", postgresql.JSONB(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("site_id", "actor", "key", name="pk_idempotency_key"),
        sa.CheckConstraint("length(key) > 0", name="ck_idempotency_key_present"),
        sa.CheckConstraint(
            "status IS NULL OR (status >= 100 AND status < 600)",
            name="ck_idempotency_key_status_range",
        ),
        comment=TABLE_COMMENT,
    )

    # The sweep's predicate. Without it, expiring a day's keys is a sequential
    # scan of every key ever issued, which is the shape of a maintenance job
    # that gets disabled the first time it is noticed.
    op.create_index("ix_idempotency_key_created_at", "idempotency_key", ["created_at"])

    # SAD 11C constraint 3, as every other site-scoped table has it. FORCE so
    # that the owning role is subject to the policy too: without it the
    # application's own connection would read every site's keys, and a key from
    # one forge would resolve at another.
    op.execute("ALTER TABLE idempotency_key ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE idempotency_key FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY site_isolation ON idempotency_key "
        "USING (site_id = current_setting('draupnir.site_id', true)) "
        "WITH CHECK (site_id = current_setting('draupnir.site_id', true))"
    )


def downgrade() -> None:
    """Refuse. AC-Q6: migrations are forward only.

    Recovery from a bad migration is a restore plus a new forward migration,
    not a downgrade path that has never been exercised.
    """
    raise NotImplementedError("DRAUPNIR migrations are forward only (AC-Q6)")
