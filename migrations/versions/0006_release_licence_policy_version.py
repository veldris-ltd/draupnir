"""A release records the licence policy version its corpus was judged under.

RF-34. SAD 10.2: existing releases keep the version in force at their release
date. Nothing recorded which GLEIPNIR licence policy a release's corpus was
cleared under, so its copyright policy document was rendered under whichever
version was in force when somebody downloaded it -- a document that names the
wrong policy while looking like an answer.

Nullable, because releases published before this recorded none, and a value
backfilled now would be exactly the substitution the column exists to prevent.
A release that records none has its copyright policy refused, not rendered
under today's.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("release", sa.Column("licence_policy_version", sa.Text(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("DRAUPNIR migrations are forward only (AC-Q6)")
