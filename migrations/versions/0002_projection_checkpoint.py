"""Projection checkpoints, and the run registry declared as a projection.

The ledger is the source of truth. `run` is derived from it by
`draupnir.core.domain.projector`, and `projection_checkpoint` records how far
each projection has consumed each site's chain so that a restart resumes
rather than replays (AC-N6) and a rebuild can be told to start from zero.

The table comment is not decoration. An operator with psql who sees `run` and
assumes it is authoritative will eventually write to it, and the comment is
the only warning that reaches them there.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_COMMENT = (
    "Projection of the ledger, rebuilt by draupnir.core.domain.projector. "
    "Do not write to this table by hand: it is derived, and rebuild_projection "
    "will discard anything the chain does not account for."
)

LEDGER_COMMENT = (
    "Append only, one hash chain per site. UPDATE, DELETE and TRUNCATE are "
    "refused by trigger (SAD 11C)."
)


def upgrade() -> None:
    op.create_table(
        "projection_checkpoint",
        sa.Column("site_id", sa.Text(), sa.ForeignKey("site.id"), primary_key=True),
        sa.Column("projection", sa.Text(), primary_key=True),
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rebuilt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("last_seq >= 0", name="ck_projection_checkpoint_seq"),
    )

    # Site scoped like everything else that carries a site_id (SAD 11C
    # constraint 3). A checkpoint is per site; reading another site's progress
    # is as much a scope violation as reading its ledger.
    op.execute("ALTER TABLE projection_checkpoint ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE projection_checkpoint FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY site_isolation ON projection_checkpoint "
        "USING (site_id = current_setting('draupnir.site_id', true)) "
        "WITH CHECK (site_id = current_setting('draupnir.site_id', true))"
    )

    # Projecting reads a window of one site's chain in sequence order. The
    # unique constraint on (site_id, seq) already serves that; this index adds
    # the covering columns so the read does not return to the heap.
    op.execute(
        "CREATE INDEX ix_ledger_entry_projection ON ledger_entry "
        "(site_id, seq) INCLUDE (subject_type, subject_id, transition)"
    )

    op.execute(f"COMMENT ON TABLE run IS {_quote(RUN_COMMENT)}")
    op.execute(f"COMMENT ON TABLE ledger_entry IS {_quote(LEDGER_COMMENT)}")


def _quote(text: str) -> str:
    escaped = text.replace("'", "''")
    return f"'{escaped}'"


def downgrade() -> None:
    """Refuse. AC-Q6: migrations are forward only."""
    raise NotImplementedError("DRAUPNIR migrations are forward only (AC-Q6)")
