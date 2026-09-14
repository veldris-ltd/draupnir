"""SQLAlchemy models for the entities of SAD 7.1.

These describe the schema the migrations create; they do not create it.
`metadata.create_all` is never called, and `alembic` autogenerate is switched
off in `migrations/env.py`. A forward-only ledger schema carrying triggers and
row level security is not something to round-trip through a diff, and a model
that silently disagrees with a migration is worse than no model at all -- so
`tests/integration/test_models_match_schema.py` compares the two against a
live database and fails when they drift.

The models live in the infrastructure layer because SQLAlchemy is technology.
Nothing in `draupnir.core.domain` may import this module, and `.importlinter`
enforces that.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from draupnir.core.domain.states import RunState

#: The native PostgreSQL enums, created by migration 0001. `create_type=False`
#: because the migration owns them; the model only needs to name them.
RUN_STATE = ENUM(*(state.value for state in RunState), name="run_state", create_type=False)
ARTEFACT_KIND = ENUM(
    "corpus_raw",
    "corpus_curated",
    "base_model",
    "substrate",
    "adapter",
    "merged",
    "quantised",
    "report",
    name="artefact_kind",
    create_type=False,
)

HEX64 = "^[0-9a-f]{64}$"


class Base(DeclarativeBase):
    """Declarative base for every DRAUPNIR table."""


class Site(Base):
    """The forge registry. SAD 7.1, and Sindri is site 0 (SAD 11A.0)."""

    __tablename__ = "site"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    control_plane_uri: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="UNANCHORED")
    last_anchored_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Source(Base):
    """The licence register. HODD records; GLEIPNIR judges (Decision S4)."""

    __tablename__ = "source"
    __table_args__ = (
        CheckConstraint(
            "personal_data = false OR dpia_ref IS NOT NULL",
            name="ck_source_personal_data_requires_dpia",
        ),
        CheckConstraint(
            "state IN ('DRAFT', 'CORPUS_REGISTERED', 'LICENCE_CLEARED', 'CURATED', 'QUARANTINED')",
            name="ck_source_corpus_states_only",
        ),
        Index("ix_source_jurisdiction", "jurisdiction"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    jurisdiction: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    licence_spdx: Mapped[str] = mapped_column(Text, nullable=False)
    attribution_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    personal_data: Mapped[bool] = mapped_column(Boolean, nullable=False)
    dpia_ref: Mapped[str | None] = mapped_column(Text)
    residency_constraint: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    state: Mapped[str] = mapped_column(RUN_STATE, nullable=False)


class Run(Base):
    """The run registry.

    This table is a **projection**. Every column is derived from the ledger by
    `draupnir.core.domain.projector`, and `rebuild_projection` reproduces it
    from sequence 1. Writing to it outside the projector puts the registry and
    the chain into disagreement, which is the one failure the ledger exists to
    make impossible.
    """

    __tablename__ = "run"
    __table_args__ = (Index("ix_run_site_state", "site_id", "state"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("site.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    spec_hash: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(RUN_STATE, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    scheduler_job_id: Mapped[str | None] = mapped_column(Text)
    node: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class Artefact(Base):
    """Weights, adapters, corpora and reports. `locality` records which forges hold a copy."""

    __tablename__ = "artefact"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("site.id"), nullable=False)
    locality: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    kind: Mapped[str] = mapped_column(ARTEFACT_KIND, nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sha256_manifest: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_from_run: Mapped[UUID | None] = mapped_column(ForeignKey("run.id"))
    immutable_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class GateResult(Base):
    """One row per gate per artefact. SAD 7.1."""

    __tablename__ = "gate_result"
    __table_args__ = (
        UniqueConstraint("run_id", "gate", "suite_version", name="uq_gate_result_run_gate"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("run.id"), nullable=False)
    gate: Mapped[str] = mapped_column(Text, nullable=False)
    suite_version: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    baseline_value: Mapped[Decimal | None] = mapped_column(Numeric)
    margin: Mapped[Decimal | None] = mapped_column(Numeric)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class Approval(Base):
    """A signed decision. The exception flag is surfaced in lineage, not hidden."""

    __tablename__ = "approval"
    __table_args__ = (
        CheckConstraint("decision IN ('APPROVED', 'REJECTED')", name="ck_approval_decision"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    subject_id: Mapped[UUID] = mapped_column(nullable=False)
    approver: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    sole_approver_exception: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    decided_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class Plugin(Base):
    """The extension registry."""

    __tablename__ = "plugin"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    interface: Mapped[str] = mapped_column(Text, nullable=False)
    signature_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class Release(Base):
    """The release record. `approval_id` is NOT NULL: SAD 11C constraint 2."""

    __tablename__ = "release"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    artefact_id: Mapped[UUID] = mapped_column(ForeignKey("artefact.id"), nullable=False)
    approval_id: Mapped[UUID] = mapped_column(ForeignKey("approval.id"), nullable=False)
    model_card_uri: Mapped[str] = mapped_column(Text, nullable=False)
    sbom_uri: Mapped[str] = mapped_column(Text, nullable=False)
    lineage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    # The two EU AI Act Article 53 artefacts, SAD 9A.
    training_summary_uri: Mapped[str] = mapped_column(Text, nullable=False)
    copyright_policy_uri: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    anchored_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # The GLEIPNIR licence policy the corpus was cleared under (RF-34, SAD 10.2).
    # Null for a release that recorded none, which renders no copyright policy.
    licence_policy_version: Mapped[str | None] = mapped_column(Text)


class RetentionAction(Base):
    """Deletion is an approved, ledgered action. SAD 7.3."""

    __tablename__ = "retention_action"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    subject_id: Mapped[UUID] = mapped_column(nullable=False)
    policy: Mapped[str] = mapped_column(Text, nullable=False)
    due_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    manifests_retained: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class LedgerEntryRow(Base):
    """The append-only chain. One chain per site.

    The table refuses UPDATE, DELETE and TRUNCATE by trigger, so SQLAlchemy's
    unit of work must never be asked to flush a change to a loaded instance.
    `LedgerRepository` is the only supported writer, and it inserts.
    """

    __tablename__ = "ledger_entry"
    __table_args__ = (
        UniqueConstraint("site_id", "seq", name="uq_ledger_entry_site_seq"),
        UniqueConstraint("site_id", "entry_hash", name="uq_ledger_entry_site_hash"),
        CheckConstraint("seq > 0", name="ck_ledger_entry_seq_positive"),
        CheckConstraint(f"prev_hash ~ '{HEX64}'", name="ck_ledger_entry_prev_hash_hex"),
        CheckConstraint(f"entry_hash ~ '{HEX64}'", name="ck_ledger_entry_hash_hex"),
        Index("ix_ledger_entry_subject", "subject_type", "subject_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("site.id"), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prev_hash: Mapped[str] = mapped_column(Text, nullable=False)
    entry_hash: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[str] = mapped_column(Text, nullable=False)
    transition: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)


class ProjectionCheckpoint(Base):
    """How far each projection has consumed each site's chain.

    Kept per site and per projection so that a rebuild of one does not disturb
    the other, and so that an incremental projection knows where to resume
    after a restart (AC-N6).
    """

    __tablename__ = "projection_checkpoint"

    site_id: Mapped[str] = mapped_column(ForeignKey("site.id"), primary_key=True)
    projection: Mapped[str] = mapped_column(Text, primary_key=True)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    rebuilt_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class IdempotencyKey(Base):
    """Reservations and stored responses, one row per outstanding request.

    RF-14. This was a dictionary in one API process, so a key reserved by one
    of SAD 5.1's two-to-four processes was unknown to the others and every
    reservation was lost on restart.

    `status` is `NULL` while the first request is still running, and that
    column is the whole of the in-flight answer: a store that recorded only
    completed responses could not tell a second caller to wait, which is the
    case the key exists for.

    Written by `api.idempotency_store.DatabaseIdempotencyStore` and swept by
    the worker. Nothing loads it through the unit of work.
    """

    __tablename__ = "idempotency_key"
    __table_args__ = (
        CheckConstraint("length(key) > 0", name="ck_idempotency_key_present"),
        CheckConstraint(
            "status IS NULL OR (status >= 100 AND status < 600)",
            name="ck_idempotency_key_status_range",
        ),
        Index("ix_idempotency_key_created_at", "created_at"),
    )

    site_id: Mapped[str] = mapped_column(Text, primary_key=True)
    #: The actor as well as the site: two operators using the same obvious key
    #: -- `retry-1` -- must not collide.
    actor: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[Any | None] = mapped_column(JSONB)
    location: Mapped[str | None] = mapped_column(Text)


class DutyMeasurement(Base):
    """The latest reading from each of the worker's periodic duties.

    RF-18. `/metrics` had no DRAUPNIR collector at all, and two of SAD 11.3's
    signals cannot be read at scrape time: verifying the chain is the hourly
    duty's whole cost, and asking an NFS mount how full it is can block
    uninterruptibly -- which would make Prometheus mark the control plane down
    because the vault was slow.

    So the worker writes what it measured and a scrape reads it. One row per
    site per duty, overwritten in place: a current reading, not a history.
    Anything that mattered is in the chain already, because an alarming duty
    records an entry.

    Written by the worker's maintenance pass and read by
    `api.metrics.SiteCollector`. Nothing loads it through the unit of work.
    """

    __tablename__ = "duty_measurement"
    __table_args__ = (CheckConstraint("length(duty) > 0", name="ck_duty_measurement_duty_present"),)

    site_id: Mapped[str] = mapped_column(Text, ForeignKey("site.id"), primary_key=True)
    #: `Duty`'s value, which is what SAD 11.3's signal column calls it.
    duty: Mapped[str] = mapped_column(Text, primary_key=True)
    measured_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    #: Whether the reading crossed the duty's threshold. A gauge cannot say
    #: where the line is; the thresholds live with the duty that applies them.
    alarm: Mapped[bool] = mapped_column(Boolean, nullable=False)
    measurements: Mapped[Any] = mapped_column(JSONB, nullable=False)
