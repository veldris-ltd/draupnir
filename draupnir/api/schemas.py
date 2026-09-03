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
    jurisdiction: str | None = Field(
        default=None,
        description=(
            "ISO 3166-1 alpha-3 code, where the run name encodes one. Null rather than a "
            "guess: the run table is a projection of the ledger and carries no jurisdiction "
            "column, and a wrong flag beside a run is worse than an absent one."
        ),
    )
    state: RunState = Field(description="Current state, per SAD 6.1.")
    spec_hash: str = Field(description="SHA-256 of the canonical specification.")
    kind: str = Field(default="adapter", description="`adapter`, `merge` or `quantise`.")
    node: str | None = Field(default=None, description="The appliance the run is placed on.")
    scheduler_job_id: str | None = Field(
        default=None, description="The scheduler's identifier for the job."
    )
    created_at: datetime | None = Field(
        default=None,
        description=(
            "When the run started. Null for a run that has not started: the projection "
            "records when work began, and a run in DRAFT has not begun. A placeholder "
            "instant would render as a date in year 1, which is worse than an absence."
        ),
    )
    updated_at: datetime | None = Field(
        default=None, description="When the run last changed state, where it has."
    )
    retry_budget_remaining: int = Field(default=0, description="How many automatic retries remain.")


class RunPage(Wire):
    """A page of runs."""

    items: list[RunOut] = Field(description="This page's runs.")
    next_cursor: str | None = Field(default=None, description="Cursor for the next page.")
    limit: int = Field(description="Page size.")


class Accepted(Wire):
    """A long operation that has been accepted but not completed. AC-B9."""

    run_id: UUID = Field(description="The run this operation concerns.")
    run_identity: str | None = Field(
        default=None,
        description=(
            "SHA-256 of the specification and its resolved input artefact hashes (AC-F1). "
            "Two submissions of the same specification are two runs with one identity: the "
            "identifier says when, the identity says what."
        ),
    )
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
    subject_id: str = Field(
        description=(
            "Which one. A string rather than a UUID: the ledger records sites and "
            "plug-ins as well as runs, sources and artefacts, and a site is `sindri` "
            "while a plug-in is `hamarr.llamafactory/v1`. Declaring this a UUID made "
            "every read of a real chain fail on the first site entry."
        )
    )
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


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


class DryRunOut(Wire):
    """The job plan a specification renders to, with nothing allocated. AC-F14.

    The point of the screen this feeds is that a specification error costs
    nothing. An allocation on this estate is the scarce resource, so the
    console makes the dry run the primary action and submission the secondary
    one, and this response is what makes that possible: the exact command,
    environment and resources the scheduler would be given.
    """

    run_identity: str = Field(
        description="The identity this specification would submit under (AC-F1)."
    )
    spec_hash: str = Field(description="SHA-256 of the canonical specification.")
    input_artefact_sha256: list[str] = Field(
        description="The resolved digests the identity was computed over."
    )
    driver: str = Field(description="Which driver rendered the plan.")
    command: list[str] = Field(description="The command, exactly as it would be run.")
    environment: dict[str, str] = Field(
        default_factory=dict, description="Environment the job would receive."
    )
    resources: dict[str, Any] = Field(
        default_factory=dict, description="What the scheduler would be asked for."
    )
    allocation_consumed: Literal[False] = Field(
        default=False,
        description="Always false. A dry run that consumed an allocation would not be one.",
    )
    warnings: list[str] = Field(
        default_factory=list, description="Anything the driver flagged without refusing."
    )


# ---------------------------------------------------------------------------
# Sites, models and search
# ---------------------------------------------------------------------------


class SiteOut(Wire):
    """One forge in the Forge Matrix. SAD 11A, Decision S12: a site, not a node."""

    id: str = Field(description="Site identifier, e.g. `sindri`.")
    name: str = Field(description="Human name.")
    location: str = Field(description="Where it is.")
    timezone: str = Field(description="IANA zone, for rendering local times.")
    control_plane_uri: str = Field(description="Its ALVISS endpoint.")
    anchor_state: str = Field(
        description=(
            "`ANCHORED`, `UNANCHORED` or `PARTITIONED`. A partitioned site continues to "
            "train and cannot release (Decision S8)."
        )
    )
    last_anchored_at: datetime | None = Field(
        default=None, description="When its chain head was last countersigned."
    )


class SitePage(Wire):
    """Every registered site. The list of scopes, not an aggregate of them."""

    items: list[SiteOut] = Field(description="The registered sites.")


class ModelOut(Wire):
    """One model artefact, released or not."""

    artefact: str = Field(description="SHA-256 manifest digest, the lineage key.")
    uri: str = Field(description="Content addressed URI (SAD 7.4).")
    name: str = Field(description="What it is called.")
    jurisdiction: str | None = Field(default=None, description="ISO 3166-1 alpha-3, where known.")
    kind: str = Field(description="`adapter`, `weights` or `merged`.")
    released: bool = Field(description="Whether a release record exists.")
    published_at: datetime | None = Field(default=None, description="When it was published.")
    anchored: bool = Field(description="Whether the release was anchored in the federation.")
    sole_approver_exception: bool = Field(
        default=False,
        description="Whether it was approved by a sole approver (SAD 9.4). Disclosed, not hidden.",
    )
    approver: str | None = Field(default=None, description="Who approved it.")


class ModelPage(Wire):
    """A page of models."""

    items: list[ModelOut] = Field(description="This page's models.")
    next_cursor: str | None = Field(default=None, description="Cursor for the next page.")
    limit: int = Field(description="Page size.")


class SearchHit(Wire):
    """One thing the command palette found."""

    kind: str = Field(description="`run`, `source`, `model` or `ledger`.")
    id: str = Field(description="Its identifier, for the link.")
    label: str = Field(description="What to show.")
    detail: str = Field(description="A second line of context.")


class SearchPage(Wire):
    """What a search returned, scoped to one site like every other read."""

    items: list[SearchHit] = Field(description="The hits.")
    query: str = Field(description="What was searched for.")
    limit: int = Field(description="How many hits could be returned.")


# ---------------------------------------------------------------------------
# Curation, retention and the corpus lifecycle
# ---------------------------------------------------------------------------


class CorpusOut(Wire):
    """One jurisdiction's corpus, as the curation screen shows it. S05."""

    jurisdiction: str = Field(description="ISO 3166-1 alpha-3 code.")
    sources: int = Field(description="How many sources are registered for it.")
    curated: int = Field(description="How many have reached CURATED.")
    quarantined: int = Field(description="How many were refused by the licence gate.")
    awaiting: int = Field(description="How many are still short of CURATED.")
    personal_data_sources: int = Field(description="How many declare personal data.")
    missing_dpia: int = Field(
        description=(
            "How many declare personal data with no DPIA reference. Counted separately "
            "because it is a defect rather than a state: the register should not hold one."
        )
    )
    licences: list[str] = Field(description="Every distinct licence in this corpus.")
    latest_retrieval: datetime | None = Field(
        default=None, description="When the most recent source was retrieved."
    )


class CorpusPage(Wire):
    """The corpora at this site, by jurisdiction."""

    items: list[CorpusOut] = Field(description="One entry per jurisdiction.")


class RetentionOut(Wire):
    """One retention action, due or executed. S06, SAD 7.3."""

    id: UUID = Field(description="The action.")
    subject_id: UUID = Field(description="What it applies to.")
    subject: str = Field(description="What that subject is, in words.")
    policy: str = Field(description="The retention policy that scheduled it.")
    due_at: datetime = Field(description="When the retention period expires.")
    approved_by: str | None = Field(
        default=None,
        description=(
            "Who approved the deletion. Null means unapproved: SAD 7.3 makes deletion an "
            "approved, ledgered action rather than a timer firing."
        ),
    )
    executed_at: datetime | None = Field(default=None, description="When it was carried out.")
    manifests_retained: bool = Field(
        description=(
            "Whether the manifests survive the deletion. They must: a lineage that loses "
            "its hashes when a corpus is deleted cannot be verified afterwards."
        )
    )
    days_remaining: int = Field(description="Days until due. Negative when overdue.")


class RetentionPage(Wire):
    """Retention actions at this site, soonest first."""

    items: list[RetentionOut] = Field(description="The scheduled actions.")
    overdue: int = Field(description="How many are past their due date and not executed.")


# ---------------------------------------------------------------------------
# Arrays and sweeps
# ---------------------------------------------------------------------------


class ArrayElementOut(Wire):
    """One element of an adapter array. S12, SAD 5.2 MOTSOGNIR."""

    index: int = Field(description="Position in the array.")
    subject: str = Field(description="What this element is for, e.g. a jurisdiction.")
    state: str = Field(description="PENDING, RUNNING, COMPLETED, FAILED, AWAITING_RETRY…")
    attempts: int = Field(description="How many times it has been submitted.")
    run_id: UUID | None = Field(default=None, description="The run this element became.")
    node: str | None = Field(default=None, description="Where it is placed.")


class ArrayOut(Wire):
    """The adapter array and its element states."""

    name: str = Field(description="What the array is producing.")
    size: int = Field(description="How many elements.")
    elements: list[ArrayElementOut] = Field(description="Every element, in index order.")
    summary: dict[str, int] = Field(description="How many elements are in each state.")


class SweepPointOut(Wire):
    """One merge point of a sweep, against every gate. S15."""

    label: str = Field(description="What distinguishes this point, e.g. `weight=0.4`.")
    parameters: dict[str, float] = Field(description="The merge configuration.")
    artefact_sha256: str | None = Field(default=None, description="What it produced.")
    evaluated: bool = Field(description="Whether it has gate results yet.")
    passed: bool = Field(description="Whether it cleared every blocking gate.")
    scores: dict[str, float] = Field(
        default_factory=dict, description="Gate identifier to measurement."
    )


class SweepOut(Wire):
    """A reweighting sweep, as a trade rather than a table."""

    run_id: UUID = Field(description="The run this sweep belongs to.")
    model: str = Field(description="What is being reweighted.")
    gates: list[str] = Field(description="The gates every point was measured against.")
    floors: dict[str, float] = Field(
        default_factory=dict, description="The floor each gate must clear."
    )
    points: list[SweepPointOut] = Field(description="Every merge point.")
    selected: str | None = Field(default=None, description="The label of the chosen point.")
    trade: str = Field(
        description=(
            "The trade in words, generated from the data. A matrix of twenty numbers does "
            "not by itself tell an operator that the higher scoring points fail a different "
            "gate (UX 9.6)."
        )
    )


# ---------------------------------------------------------------------------
# Model detail, release package and attestation
# ---------------------------------------------------------------------------


class ArtefactOut(Wire):
    """One artefact a run produced."""

    sha256: str = Field(description="Content digest, and the lineage key.")
    uri: str = Field(description="Content addressed URI (SAD 7.4).")
    kind: str = Field(description="`adapter`, `merged`, `quantised`, `report`…")
    size: int = Field(description="Bytes.")
    locality: list[str] = Field(
        default_factory=list, description="Which sites hold a copy (SAD 11A)."
    )
    immutable_at: datetime | None = Field(
        default=None, description="When it was sealed. Null means still mutable."
    )


class ModelDetailOut(Wire):
    """One model, its artefacts and its gate results. S14."""

    artefact: str = Field(description="The primary artefact's digest.")
    name: str = Field(description="What it is called.")
    jurisdiction: str | None = Field(default=None, description="ISO 3166-1 alpha-3, where known.")
    run_id: UUID | None = Field(default=None, description="The run that produced it.")
    state: RunState | None = Field(default=None, description="That run's state.")
    spec_hash: str | None = Field(default=None, description="The specification it was built from.")
    artefacts: list[ArtefactOut] = Field(description="Every artefact of this model.")
    gates: list[GateOut] = Field(default_factory=list, description="Its gate results.")
    released: bool = Field(description="Whether a release record exists.")


class ReleasePackageOut(Wire):
    """A release package: card, SBOM, lineage and the Article 53 artefacts. S17."""

    artefact: str = Field(description="The released artefact's digest.")
    model: str = Field(description="What was released.")
    model_card_uri: str = Field(description="The model card.")
    sbom_uri: str = Field(description="The software bill of materials.")
    lineage_uri: str = Field(description="The lineage attestation.")
    training_summary_uri: str = Field(
        description="EU AI Act Article 53 training data summary (SAD 9A)."
    )
    copyright_policy_uri: str = Field(description="Article 53 copyright policy (SAD 9A).")
    signature: str = Field(description="The release signature.")
    published_at: datetime | None = Field(default=None, description="When it was published.")
    anchored_at: datetime | None = Field(
        default=None, description="When its chain head was countersigned."
    )
    approver: str = Field(description="Who approved it.")
    sole_approver_exception: bool = Field(
        description="Whether it was approved by a sole approver. Disclosed, not hidden."
    )


class AttestationOut(Wire):
    """A signed lineage bundle, for export. S28, AC-F11."""

    artefact: str = Field(description="What this attests to.")
    complete: bool = Field(description="Whether the chain reaches licensed roots with no gaps.")
    gaps: list[str] = Field(default_factory=list, description="Every gap, where there are any.")
    issued_at: datetime = Field(description="When the bundle was produced.")
    site_id: str = Field(description="Which site issued it.")
    payload: dict[str, Any] = Field(description="The canonical bundle that was signed.")
    payload_sha256: str = Field(description="Digest of the canonical bundle.")
    signature: str | None = Field(
        default=None,
        description=(
            "The signature, when the chain is complete. An incomplete chain is exported "
            "unsigned and says so: signing an attestation over a gap would certify the gap."
        ),
    )


# ---------------------------------------------------------------------------
# Policy, roles and ledger detail
# ---------------------------------------------------------------------------


class PolicyRuleOut(Wire):
    """One clause of a policy bundle."""

    id: str = Field(description="Rule identifier.")
    statement: str = Field(description="What it says, in words.")
    verdict: str = Field(description="What it decides: permit, refuse, requires approval.")
    licences: list[str] = Field(default_factory=list, description="SPDX identifiers it applies to.")
    personal_data: bool | None = Field(default=None, description="Matches this determination.")
    attribution_required: bool | None = Field(default=None, description="Matches this obligation.")


class PolicyBundleOut(Wire):
    """One version of the licence policy. S24."""

    version: str = Field(description="The version every decision under it records.")
    rules: list[PolicyRuleOut] = Field(description="The clauses, in match order. First match wins.")
    default_verdict: str = Field(
        description=(
            "What happens to a subject no rule matches. Refuse: a corpus whose licence "
            "nobody wrote a rule for is a corpus nobody has assessed."
        )
    )
    default_statement: str = Field(description="Why, in words.")


class PolicyOut(Wire):
    """The policy in force, and the one before it, so the change is readable."""

    current: PolicyBundleOut = Field(description="The bundle decisions are made under now.")
    previous: PolicyBundleOut | None = Field(
        default=None, description="The bundle before it, where there is one."
    )


class RoleOut(Wire):
    """One role and what it may do. S25, SAD 9.4."""

    role: str = Field(description="The role name.")
    permissions: list[str] = Field(description="Every permission it grants.")


class RoutePermissionOut(Wire):
    """One route and the permission it requires."""

    method: str = Field(description="HTTP method.")
    path: str = Field(description="The route.")
    permission: str | None = Field(
        default=None, description="What it requires. Null for an unauthenticated route."
    )
    reason: str | None = Field(default=None, description="Why it is unauthenticated, where it is.")


class RolesOut(Wire):
    """The role table and the route table, from the declarations the guard enforces."""

    roles: list[RoleOut] = Field(description="Every role and its permissions.")
    routes: list[RoutePermissionOut] = Field(description="Every route and what it requires.")
    separation: str = Field(
        description=(
            "The separation of duty this table encodes. Decision S6: no role both submits "
            "and approves."
        )
    )


class LedgerEntryDetailOut(Wire):
    """One ledger entry with its payload and its chain links. S27."""

    id: UUID = Field(description="Entry identifier.")
    site_id: str = Field(description="Whose segment this belongs to.")
    seq: int = Field(description="Position in this site's chain.")
    prev_hash: str = Field(description="The previous entry's hash.")
    entry_hash: str = Field(description="This entry's hash.")
    ts: datetime = Field(description="When it was recorded.")
    actor: str = Field(description="Who caused it.")
    subject_type: str = Field(description="What kind of thing it concerns.")
    subject_id: str = Field(description="Which one.")
    transition: str = Field(description="The state transition recorded.")
    payload: dict[str, Any] = Field(description="The signed payload.")
    recomputed_hash: str = Field(description="The hash recomputed here from prev_hash and payload.")
    verified: bool = Field(
        description=(
            "Whether the recomputed hash equals the recorded one. Computed on read rather "
            "than trusted: an entry viewer that displays a stored hash proves nothing."
        )
    )
