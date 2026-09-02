"""The wire shapes. One place, so the OpenAPI document is the whole contract.

AC-N10: "OpenAPI specification is complete and both clients are generated from
it. No hand written client path." That is only achievable if every response a
handler can produce is described by a model here -- a handler returning a bare
dict produces `{}` in the schema, and a generated client then has an untyped
hole that somebody fills in by hand, which is the thing the criterion forbids.

So every field is annotated and described. The descriptions are not decoration:
they are what a generated client's docstrings and the console's tooltips are
made of, and writing them here is what stops them being written twice.

Naming is camelCase on the wire and snake_case in Python. `populate_by_name`
plus an alias generator does that once rather than per field.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel

from draupnir.core.domain.states import RunState


class Wire(BaseModel):
    """Base for every model that crosses the boundary."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------


class PageMeta(Wire):
    """Where the next page starts. Cursor based, never offset (AC-B3)."""

    next_cursor: str | None = Field(
        default=None,
        description="Pass as `cursor` to fetch the next page. Null on the last page.",
    )
    limit: int = Field(description="How many items this page could hold.")


# ---------------------------------------------------------------------------
# Sources and corpora
# ---------------------------------------------------------------------------


class SourceIn(Wire):
    """A corpus source being registered with its licence. SAD 8.1, curator."""

    jurisdiction: Annotated[str, Field(min_length=3, max_length=3, pattern="^[A-Z]{3}$")] = Field(
        description="ISO 3166-1 alpha-3 code of the jurisdiction this source serves."
    )
    url: Annotated[str, Field(min_length=1, max_length=2048)] = Field(
        description="Where the source was retrieved from."
    )
    licence_spdx: Annotated[str, Field(min_length=1, max_length=128)] = Field(
        description="SPDX identifier as declared. Held as a fact, not as a permission."
    )
    attribution_required: bool = Field(
        description="Whether the licence obliges attribution in derived releases."
    )
    retrieved_at: datetime = Field(
        description="When the source was retrieved. RFC 3339 with an explicit offset."
    )
    sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")] = Field(
        description="Digest of the retrieved content."
    )
    personal_data: bool = Field(
        description="Whether the source contains personal data. Requires a DPIA reference."
    )
    dpia_ref: str | None = Field(
        default=None, description="DPIA reference. Required when personalData is true."
    )
    residency_constraint: list[str] = Field(
        default_factory=list,
        description="Sites permitted to hold this corpus. Empty means unconstrained.",
    )


class SourceOut(Wire):
    """A registered source."""

    id: UUID = Field(description="UUIDv7. Sorts by creation time (AC-B8).")
    jurisdiction: str = Field(description="ISO 3166-1 alpha-3 code.")
    url: str = Field(description="Where the source was retrieved from.")
    licence_spdx: str = Field(description="SPDX identifier as declared.")
    attribution_required: bool = Field(description="Whether attribution is obliged.")
    personal_data: bool = Field(description="Whether the source contains personal data.")
    dpia_ref: str | None = Field(default=None, description="DPIA reference, where one applies.")
    retrieved_at: datetime = Field(description="When the source was retrieved.")
    sha256: str = Field(description="Digest of the retrieved content.")
    state: RunState = Field(description="Where the source has reached in its lifecycle.")


class SourcePage(Wire):
    """A page of sources."""

    items: list[SourceOut] = Field(description="This page's sources.")
    next_cursor: str | None = Field(default=None, description="Cursor for the next page.")
    limit: int = Field(description="Page size.")


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunSubmission(Wire):
    """A run specification being submitted. SAD 6.2."""

    specification: dict[str, Any] = Field(
        description=(
            "The run specification, as SAD 6.2 defines it. Validated against the JSON "
            "Schema and hashed into the run identity."
        )
    )


class RunOut(Wire):
    """A run as the projection holds it."""

    id: UUID = Field(description="UUIDv7 run identifier.")
    site_id: str = Field(description="The site this run belongs to.")
    name: str = Field(description="The specification's metadata.name.")
    jurisdiction: str = Field(description="ISO 3166-1 alpha-3 code.")
    state: RunState = Field(description="Current state, per SAD 6.1.")
    spec_hash: str = Field(description="SHA-256 of the canonical specification.")
    created_at: datetime = Field(description="When the run was submitted.")
    updated_at: datetime = Field(description="When the run last changed state.")
    retry_budget_remaining: int = Field(default=0, description="How many automatic retries remain.")


class RunPage(Wire):
    """A page of runs."""

    items: list[RunOut] = Field(description="This page's runs.")
    next_cursor: str | None = Field(default=None, description="Cursor for the next page.")
    limit: int = Field(description="Page size.")


class Accepted(Wire):
    """A long operation that has been accepted but not completed. AC-B9."""

    run_id: UUID = Field(description="The run this operation concerns.")
    status: Literal["accepted"] = Field(description="Always `accepted`.")
    detail: str = Field(description="What was accepted.")
    events: str = Field(description="Where to watch this run's event stream.")


class CancelIn(Wire):
    """Why a run is being cancelled."""

    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ] = Field(description="Recorded in the ledger against the transition.")


# ---------------------------------------------------------------------------
# Gates, approvals and releases
# ---------------------------------------------------------------------------


class GateOut(Wire):
    """One gate result, with its baseline and margin. SAD 7.1."""

    gate: str = Field(description="Gate identifier, E1 to E6.")
    suite_version: str = Field(description="The suite version that produced it.")
    value: float = Field(description="The measurement.")
    baseline_value: float | None = Field(default=None, description="What it was compared against.")
    margin: float | None = Field(default=None, description="Measurement minus baseline.")
    passed: bool = Field(description="Whether the gate was satisfied.")


class ApprovalItem(Wire):
    """One artefact awaiting a decision."""

    id: UUID = Field(description="The subject awaiting approval.")
    run_id: UUID = Field(description="The run that produced it.")
    model: str = Field(description="What the artefact is.")
    artefact_sha256: str = Field(description="The bytes being decided on.")
    gates: list[GateOut] = Field(default_factory=list, description="The gate results.")
    submitted_by: str = Field(description="Who submitted the run.")
    awaiting_since: datetime = Field(description="When it entered the queue.")


class ApprovalPage(Wire):
    """A page of the approval queue."""

    items: list[ApprovalItem] = Field(description="This page's pending approvals.")
    next_cursor: str | None = Field(default=None, description="Cursor for the next page.")
    limit: int = Field(description="Page size.")


class DecisionIn(Wire):
    """An approver's signed decision. SAD 7.1 `approval`."""

    decision: Literal["approved", "rejected"] = Field(description="The decision taken.")
    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ] = Field(description="Why. Recorded on the approval and visible in lineage.")
    signature: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(
        description=(
            "Detached signature over the approval payload, from the approver's key. "
            "Whitespace is stripped and an empty result is refused: a blank signature "
            "is a row somebody could have written, and publication verifies it."
        )
    )


class DecisionOut(Wire):
    """A recorded decision."""

    id: UUID = Field(description="The approval record.")
    subject_id: UUID = Field(description="What was decided on.")
    approver: str = Field(description="Who decided.")
    decision: Literal["approved", "rejected"] = Field(description="The decision taken.")
    reason: str = Field(description="Why.")
    sole_approver_exception: bool = Field(
        description=(
            "True where the approver also submitted the run. Computed, never supplied. "
            "Constraint C-11; visible here, in the lineage and in the model card."
        )
    )
    decided_at: datetime = Field(description="When it was decided.")


class PublishOut(Wire):
    """What a publication produced."""

    artefact_sha256: str = Field(description="The bytes that were published.")
    model: str = Field(description="The released model.")
    released_at: datetime = Field(description="When it was published.")
    formats: list[str] = Field(description="Which formats were published.")
    manifest: dict[str, Any] = Field(description="The SHA-256 manifest of the release package.")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class LedgerEntryOut(Wire):
    """One hash-chained ledger entry. SAD 7.1."""

    id: UUID = Field(description="Entry identifier.")
    site_id: str = Field(description="The site whose segment this belongs to.")
    seq: int = Field(description="Position in this site's chain.")
    prev_hash: str = Field(description="The previous entry's hash.")
    entry_hash: str = Field(description="This entry's hash.")
    ts: datetime = Field(description="When it was recorded.")
    actor: str = Field(description="Who caused it.")
    subject_type: str = Field(description="What kind of thing it concerns.")
    subject_id: UUID = Field(description="Which one.")
    transition: str = Field(description="The state transition recorded.")


class LedgerSlice(Wire):
    """A slice of the ledger, with its chain verification."""

    items: list[LedgerEntryOut] = Field(description="The entries in the slice.")
    next_cursor: str | None = Field(default=None, description="Cursor for the next page.")
    limit: int = Field(description="Page size.")
    verified: bool = Field(
        description="Whether the returned slice's hash chain verifies end to end."
    )
    divergence: str | None = Field(
        default=None, description="Where the chain first diverges, if it does."
    )


class LineageOut(Wire):
    """A lineage attestation. AC-F11."""

    artefact: str = Field(description="The artefact this chain describes.")
    complete: bool = Field(description="Whether the chain reaches licensed roots with no gaps.")
    gaps: list[str] = Field(default_factory=list, description="Every gap, where there are any.")
    licences: list[str] = Field(default_factory=list, description="Every licence in the chain.")
    corpus_hashes: list[str] = Field(
        default_factory=list, description="Every source and corpus hash in the chain."
    )
    nodes: list[dict[str, Any]] = Field(default_factory=list, description="The chain itself.")
    approval: dict[str, Any] = Field(
        default_factory=dict,
        description="The approval, carrying the sole approver exception (SAD 9.4).",
    )


# ---------------------------------------------------------------------------
# Plug-ins and operations
# ---------------------------------------------------------------------------


class PluginOut(Wire):
    """One installed plug-in and its signature status. SAD 8.1."""

    name: str = Field(description="Versioned entry point name, e.g. `hamarr.llamafactory/v1`.")
    group: str = Field(description="Which extension point it implements.")
    distribution: str = Field(description="The installed distribution.")
    version: str = Field(description="The distribution's version.")
    capabilities: list[str] = Field(description="What the driver declares it can do.")
    signature_verified: bool = Field(description="Whether its signature verified (AC-S7).")
    signer: str | None = Field(default=None, description="Which key signed it.")
    reason: str | None = Field(default=None, description="Why verification failed, where it did.")


class PluginList(Wire):
    """Every installed plug-in."""

    items: list[PluginOut] = Field(description="Installed plug-ins.")
    failures: list[str] = Field(
        default_factory=list, description="Distributions the loader refused, and why."
    )
