"""The worker's periodic measurements, where a scrape can read them.

RF-18. `/metrics` returned `prometheus_client.generate_latest()` with nothing
registered, so it exposed four Python garbage-collector counters and no
DRAUPNIR signal at all. SAD 11.3 gives eight signals a source and a surface;
the ones that reach a structlog finding did so, and nothing reached a scrape.

Most of those signals can be read out of tables that already exist: run state
and queue depth from `run`, gate pass rates and margins from `gate_result`,
anchor freshness from `site.last_anchored_at`. Two cannot.

**Ledger chain integrity** is a verification, not a column. Re-linking the
chain is the worker's hourly duty precisely because it is too expensive to do
on demand, and doing it inside a scrape would make Prometheus's interval the
rate at which this site rehashes its ledger.

**Vault capacity** is a filesystem call on an NFS mount. A hung mount blocks
uninterruptibly, and a scrape that blocks is a scrape target that Prometheus
marks down -- reporting the control plane as gone because the vault is slow.
RF-17 made the same argument about readiness and put that check in a thread
with a timeout; here the answer is better still, because the worker already
takes this measurement every fifteen minutes.

So the worker writes what it measured and the API reads it. One row per site
per duty, overwritten each time: this is the latest reading, not a history.

**Not the ledger, deliberately.** The chain records state transitions, and a
vault sitting at forty per cent is not one. SAD 11.3's recording rule is why
duties log what they find and record only alarms -- a duty appending "nothing
wrong" every fifteen minutes is the noise that makes an audit log unreadable,
and it would grow the chain by thirty-five thousand entries a year per forge
to say nothing happened.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_COMMENT = (
    "The latest reading from each of the worker's periodic duties (SAD 11.3, "
    "RF-18). One row per site per duty, overwritten in place: this is a current "
    "measurement for a scrape to read, not a history. The history of anything "
    "that mattered is in the chain, because an alarming duty records an entry. "
    "Safe to delete: the next tick rewrites it."
)


def upgrade() -> None:
    """Create the table and its site isolation."""
    op.create_table(
        "duty_measurement",
        sa.Column("site_id", sa.Text(), sa.ForeignKey("site.id"), nullable=False),
        # `Duty`'s value, which SAD 11.3's signal column is what names them:
        # `vault-capacity`, `ledger-chain-integrity`, `anchor-freshness`.
        sa.Column("duty", sa.Text(), nullable=False),
        sa.Column("measured_at", sa.TIMESTAMP(timezone=True), nullable=False),
        # Whether this reading crossed the duty's threshold. A gauge on its own
        # cannot say where the line is -- 85 per cent of a vault is an alarm and
        # 85 per cent of an anchor interval is not -- and the thresholds live in
        # `worker/duties.py` where the duty that applies them is.
        sa.Column("alarm", sa.Boolean(), nullable=False),
        # `Finding.measurements`, verbatim. JSONB rather than columns because
        # each duty measures something different and a table with a column per
        # duty's per measurement is a migration every time a duty learns to
        # measure one more thing.
        sa.Column("measurements", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("site_id", "duty", name="pk_duty_measurement"),
        sa.CheckConstraint("length(duty) > 0", name="ck_duty_measurement_duty_present"),
        comment=TABLE_COMMENT,
    )

    # SAD 11C constraint 3, as every other site-scoped table has it. FORCE so
    # the owning role is subject to it too: without that, a scrape at one forge
    # would report another forge's vault.
    op.execute("ALTER TABLE duty_measurement ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE duty_measurement FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY site_isolation ON duty_measurement "
        "USING (site_id = current_setting('draupnir.site_id', true)) "
        "WITH CHECK (site_id = current_setting('draupnir.site_id', true))"
    )


def downgrade() -> None:
    """Refuse. AC-Q6: migrations are forward only.

    Recovery from a bad migration is a restore plus a new forward migration,
    not a downgrade path that has never been exercised.
    """
    raise NotImplementedError("DRAUPNIR migrations are forward only (AC-Q6)")
