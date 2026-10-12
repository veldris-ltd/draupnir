"""A release records what MEGINGJORD registered it as.

G8 (VLD-SAD-MEGINGJORD-001 AC-X5, AC-I1i, AC-R7). Publication now registers the
release with MEGINGJORD, with proof it was anchored, and the answer is part of
the release: MEGINGJORD's model and receipt identifiers, the anchored sequence
it was registered against, the signed receipt for that anchor, and the estate
posture MEGINGJORD stamped onto it. The `published` entry records it; this
column is the projection's copy, so the lineage document and the console can
show it without reading the chain.

Nullable, and staying so: a release published before this, or by a forge with
no federation, was registered nowhere, and a value backfilled now would claim
a registration nobody made.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, and added without a default that rewrites the table. A NOT NULL
    # column on a populated table takes an ACCESS EXCLUSIVE lock for as long as
    # the rewrite takes, and `run` is the table an operator is watching when
    # the deployment goes out.
    #
    # To make it NOT NULL: add it nullable here, backfill in a later migration,
    # and add the constraint in a third once nothing writes a null.
    op.add_column(
        "release",
        sa.Column("federation", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    """Refuse. AC-Q6: migrations are forward only.

    Recovery from a bad migration is a restore plus a new forward migration,
    not a downgrade path that has never been exercised.
    """
    raise NotImplementedError("DRAUPNIR migrations are forward only (AC-Q6)")
