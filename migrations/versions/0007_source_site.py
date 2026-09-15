"""A registered source records the site whose chain registered it.

RF-42. `source` is now a projection of each site's chain, like `run` and
`artefact`, and a projection is rebuilt one site at a time. A rebuild of one
site's register that could not tell its rows from another site's would either
leave rows the chain no longer holds or delete rows another site's chain does.

Nullable, because rows written before this record no site, and a value
backfilled now would name a site nothing recorded. The projection sets it on
every row it writes, including one it finds already present.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source",
        sa.Column("site_id", sa.Text(), sa.ForeignKey("site.id"), nullable=True),
    )
    op.create_index("ix_source_site_id", "source", ["site_id"])


def downgrade() -> None:
    raise NotImplementedError("DRAUPNIR migrations are forward only (AC-Q6)")
