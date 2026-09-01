"""Initial schema: the core entities of SAD 7.1 and the constraints of SAD 11C.

Three constraints are enforced here rather than in application code, because a
constraint that lives only in the application is a constraint that a migration
script can bypass:

  1. ``ledger_entry`` accepts INSERT only    -- trigger rejecting UPDATE/DELETE
  2. ``release`` requires an ``approval_id`` -- foreign key, NOT NULL
  3. Site scope on every scoped query        -- row level security policy plus
                                                a session variable

Revision ID: 0001
Revises:
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_STATES = (
    "DRAFT",
    "CORPUS_REGISTERED",
    "LICENCE_CLEARED",
    "CURATED",
    "QUEUED",
    "TRAINING",
    "TRAINED",
    "FAILED",
    "EVALUATING",
    "MERGED",
    "QUANTISED",
    "AWAITING_APPROVAL",
    "RELEASED",
    "QUARANTINED",
)

ARTEFACT_KINDS = (
    "corpus_raw",
    "corpus_curated",
    "base_model",
    "substrate",
    "adapter",
    "merged",
    "quantised",
    "report",
)

#: Tables carrying a site scope, per the bold attributes of SAD 7.1.
SITE_SCOPED_TABLES = ("ledger_entry", "artefact", "run")

APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION draupnir_ledger_is_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'ledger_entry is append only: % is refused (SAD 11C)', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$function$;
"""

APPEND_ONLY_TRIGGER = """
CREATE TRIGGER trg_ledger_entry_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger_entry
FOR EACH STATEMENT
EXECUTE FUNCTION draupnir_ledger_is_append_only();
"""

HEX64 = "^[0-9a-f]{64}$"


def upgrade() -> None:
    run_state = postgresql.ENUM(*RUN_STATES, name="run_state", create_type=False)
    artefact_kind = postgresql.ENUM(*ARTEFACT_KINDS, name="artefact_kind", create_type=False)
    run_state.create(op.get_bind(), checkfirst=True)
    artefact_kind.create(op.get_bind(), checkfirst=True)

    # -- site: the forge registry. Sindri is site 1. ------------------------
    op.create_table(
        "site",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("control_plane_uri", sa.Text(), nullable=False),
        sa.Column("anchor_state", sa.Text(), nullable=False, server_default="UNANCHORED"),
        sa.Column("last_anchored_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # -- source: the licence register. GLEIPNIR judges what this records. ---
    op.create_table(
        "source",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("jurisdiction", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("licence_spdx", sa.Text(), nullable=False),
        sa.Column("attribution_required", sa.Boolean(), nullable=False),
        sa.Column("retrieved_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("personal_data", sa.Boolean(), nullable=False),
        sa.Column("dpia_ref", sa.Text(), nullable=True),
        # SAD 11C: the site identifiers permitted to hold this corpus. Empty
        # means unconstrained; checked at planning, not at execution.
        sa.Column(
            "residency_constraint",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("state", run_state, nullable=False),
        sa.CheckConstraint(
            "personal_data = false OR dpia_ref IS NOT NULL",
            name="ck_source_personal_data_requires_dpia",
        ),
        sa.CheckConstraint(
            "state IN ('DRAFT', 'CORPUS_REGISTERED', 'LICENCE_CLEARED', 'CURATED', 'QUARANTINED')",
            name="ck_source_corpus_states_only",
        ),
    )
    op.create_index("ix_source_jurisdiction", "source", ["jurisdiction"])

    # -- run: state mirrors the machine in SAD section 6. -------------------
    op.create_table(
        "run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", sa.Text(), sa.ForeignKey("site.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("spec_hash", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", run_state, nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("scheduler_job_id", sa.Text(), nullable=True),
        sa.Column("node", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_run_site_state", "run", ["site_id", "state"])

    # -- artefact: locality records which forges hold a copy. ---------------
    op.create_table(
        "artefact",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", sa.Text(), sa.ForeignKey("site.id"), nullable=False),
        sa.Column("locality", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("kind", artefact_kind, nullable=False),
        sa.Column("uri", sa.Text(), nullable=False, unique=True),
        sa.Column("sha256_manifest", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_from_run",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run.id"),
            nullable=True,
        ),
        sa.Column("immutable_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # -- gate_result: one row per gate per artefact. ------------------------
    op.create_table(
        "gate_result",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("gate", sa.Text(), nullable=False),
        sa.Column("suite_version", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("baseline_value", sa.Numeric(), nullable=True),
        sa.Column("margin", sa.Numeric(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("evaluated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "gate", "suite_version", name="uq_gate_result_run_gate"),
    )

    # -- approval: signed with the approver's key. --------------------------
    op.create_table(
        "approval",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approver", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("signature", sa.Text(), nullable=False),
        # Set whenever the approver also submitted the run, and surfaced in
        # lineage rather than silently tolerated.
        sa.Column("sole_approver_exception", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('APPROVED', 'REJECTED')", name="ck_approval_decision"),
    )

    # -- plugin: the extension registry. ------------------------------------
    op.create_table(
        "plugin",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("version", sa.Text(), primary_key=True),
        sa.Column("interface", sa.Text(), nullable=False),
        sa.Column("signature_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
    )

    # -- release: SAD 11C constraint 2. approval_id is NOT NULL. ------------
    op.create_table(
        "release",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "artefact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artefact.id"),
            nullable=False,
        ),
        sa.Column(
            "approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("approval.id"),
            nullable=False,
        ),
        sa.Column("model_card_uri", sa.Text(), nullable=False),
        sa.Column("sbom_uri", sa.Text(), nullable=False),
        sa.Column("lineage_uri", sa.Text(), nullable=False),
        # The two EU AI Act Article 53 artefacts, SAD 9A.
        sa.Column("training_summary_uri", sa.Text(), nullable=False),
        sa.Column("copyright_policy_uri", sa.Text(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("anchored_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # -- retention_action: deletion is an approved, ledgered action. --------
    op.create_table(
        "retention_action",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy", sa.Text(), nullable=False),
        sa.Column("due_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("manifests_retained", sa.Boolean(), nullable=False, server_default="true"),
    )

    # -- ledger_entry: append only, one chain per site. ---------------------
    op.create_table(
        "ledger_entry",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", sa.Text(), sa.ForeignKey("site.id"), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("prev_hash", sa.Text(), nullable=False),
        sa.Column("entry_hash", sa.Text(), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("transition", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("site_id", "seq", name="uq_ledger_entry_site_seq"),
        sa.UniqueConstraint("site_id", "entry_hash", name="uq_ledger_entry_site_hash"),
        sa.CheckConstraint("seq > 0", name="ck_ledger_entry_seq_positive"),
        sa.CheckConstraint(f"prev_hash ~ '{HEX64}'", name="ck_ledger_entry_prev_hash_hex"),
        sa.CheckConstraint(f"entry_hash ~ '{HEX64}'", name="ck_ledger_entry_hash_hex"),
    )
    op.create_index("ix_ledger_entry_subject", "ledger_entry", ["subject_type", "subject_id"])

    # ----------------------------------------------------------------------
    # SAD 11C constraint 1: ledger_entry accepts INSERT only.
    # ----------------------------------------------------------------------
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(APPEND_ONLY_TRIGGER)

    # ----------------------------------------------------------------------
    # SAD 11C constraint 3: site scope by row level security. The policy reads
    # draupnir.site_id, which the site resolver sets with SET LOCAL. FORCE is
    # required so that the owning role is subject to the policy too.
    # ----------------------------------------------------------------------
    for table in SITE_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY site_isolation ON {table} "
            "USING (site_id = current_setting('draupnir.site_id', true)) "
            "WITH CHECK (site_id = current_setting('draupnir.site_id', true))"
        )


def downgrade() -> None:
    """Refuse. AC-Q6: migrations are forward only.

    Recovery from a bad migration is a restore plus a new forward migration,
    not a downgrade path that has never been exercised.
    """
    raise NotImplementedError("DRAUPNIR migrations are forward only (AC-Q6)")
