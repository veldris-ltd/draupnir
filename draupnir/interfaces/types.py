"""The vocabulary the seven Protocols are written in.

These types live in `draupnir.interfaces` rather than in the core on purpose. A
third party writing a driver depends on this package and nothing else; if the
vocabulary lived in the core, every driver would depend on the core and the
extension points would be extension points in name only.

Everything here is frozen. A driver receives a specification and returns a
plan; neither may be edited in flight, because SAD 6.2 makes the specification
the unit of reproduction.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Self

#: The entry point groups of SAD 8.2, in the order that table lists them.
GROUPS: Final = (
    "draupnir.train",
    "draupnir.merge",
    "draupnir.eval",
    "draupnir.export",
    "draupnir.schedule",
    "draupnir.store",
    "draupnir.policy",
)


def canonical_json(payload: Any) -> bytes:
    """Serialise deterministically: sorted keys, tight separators, UTF-8.

    Deliberately a separate implementation from the ledger's canonical form in
    `draupnir.core.domain.ledger`. That one is a persisted format whose bytes
    must never change; this one is a comparison aid for `render` purity.
    Sharing them would let a change to either silently alter the other.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Specification, SAD 6.2
# ---------------------------------------------------------------------------


class Tier(StrEnum):
    """Jurisdiction tier, SAD 11A."""

    A = "A"
    B = "B"


@dataclass(frozen=True, slots=True)
class SpecMetadata:
    """The `metadata` block of a run specification."""

    name: str
    jurisdiction: str
    tier: Tier


@dataclass(frozen=True, slots=True)
class ArtefactRef:
    """A `hodd://` reference with the hash the specification expects there.

    The expected hash is what makes a replay honest: resolving the URI to
    different bytes than the run recorded is a failure, not a silent upgrade.
    """

    artefact: str
    expect_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """The `dataset` block."""

    artefact: str
    expect_sha256: str | None = None
    cutoff_percentile: int | None = None


@dataclass(frozen=True, slots=True)
class TrainSpec:
    """The `train` block. `params` is driver-defined and passed through."""

    driver: str
    method: str
    params: Mapping[str, Any] = field(default_factory=dict)
    precision: str = "bf16"


@dataclass(frozen=True, slots=True)
class PlacementSpec:
    """The `placement` block."""

    driver: str
    partition: str
    nodes: int = 1
    max_concurrent: int = 1
    retry_budget: int = 0


@dataclass(frozen=True, slots=True)
class EvaluateSpec:
    """The `evaluate` block."""

    driver: str
    suites: tuple[str, ...] = ()
    gates: tuple[str, ...] = ()
    baseline: str | None = None


@dataclass(frozen=True, slots=True)
class ReleaseSpec:
    """The `release` block."""

    route: str
    formats: tuple[str, ...] = ()
    approval: str = "required"


@dataclass(frozen=True, slots=True)
class RunSpec:
    """A run specification, SAD 6.2.

    Parsing here is structural only. Validation against the JSON Schema, and
    the specification compiler that hashes it into the run identity, belong to
    the core and arrive with Prompt 1; `spec_hash` below is the same digest
    over the same canonical bytes, provided now because a driver's `validate`
    and `render` are meaningless without something to be given.
    """

    api_version: str
    kind: str
    metadata: SpecMetadata
    base: ArtefactRef
    dataset: DatasetSpec
    train: TrainSpec
    placement: PlacementSpec
    evaluate: EvaluateSpec
    release: ReleaseSpec

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Build a specification from parsed YAML or JSON."""
        try:
            metadata = data["metadata"]
            spec = data["spec"]
            base = spec["base"]
            dataset = spec["dataset"]
            train = spec["train"]
            placement = spec["placement"]
            evaluate = spec["evaluate"]
            release = spec["release"]
        except KeyError as missing:
            msg = f"run specification is missing the {missing} block (SAD 6.2)"
            raise ValueError(msg) from missing

        return cls(
            api_version=str(data.get("apiVersion", "draupnir/v1")),
            kind=str(data.get("kind", "AdapterRun")),
            metadata=SpecMetadata(
                name=str(metadata["name"]),
                jurisdiction=str(metadata["jurisdiction"]),
                tier=Tier(str(metadata["tier"])),
            ),
            base=ArtefactRef(
                artefact=str(base["artefact"]), expect_sha256=base.get("expectSha256")
            ),
            dataset=DatasetSpec(
                artefact=str(dataset["artefact"]),
                expect_sha256=dataset.get("expectSha256"),
                cutoff_percentile=dataset.get("cutoffPercentile"),
            ),
            train=TrainSpec(
                driver=str(train["driver"]),
                method=str(train["method"]),
                params=dict(train.get("params", {})),
                precision=str(train.get("precision", "bf16")),
            ),
            placement=PlacementSpec(
                driver=str(placement["driver"]),
                partition=str(placement["partition"]),
                nodes=int(placement.get("nodes", 1)),
                max_concurrent=int(placement.get("maxConcurrent", 1)),
                retry_budget=int(placement.get("retryBudget", 0)),
            ),
            evaluate=EvaluateSpec(
                driver=str(evaluate["driver"]),
                suites=tuple(evaluate.get("suites", ())),
                gates=tuple(evaluate.get("gates", ())),
                baseline=evaluate.get("baseline"),
            ),
            release=ReleaseSpec(
                route=str(release["route"]),
                formats=tuple(release.get("formats", ())),
                approval=str(release.get("approval", "required")),
            ),
        )

    def as_mapping(self) -> dict[str, Any]:
        """Return the specification in its wire shape, for hashing or display."""
        return {
            "apiVersion": self.api_version,
            "kind": self.kind,
            "metadata": {
                "name": self.metadata.name,
                "jurisdiction": self.metadata.jurisdiction,
                "tier": str(self.metadata.tier),
            },
            "spec": {
                "base": {
                    "artefact": self.base.artefact,
                    "expectSha256": self.base.expect_sha256,
                },
                "dataset": {
                    "artefact": self.dataset.artefact,
                    "expectSha256": self.dataset.expect_sha256,
                    "cutoffPercentile": self.dataset.cutoff_percentile,
                },
                "train": {
                    "driver": self.train.driver,
                    "method": self.train.method,
                    "params": dict(self.train.params),
                    "precision": self.train.precision,
                },
                "placement": {
                    "driver": self.placement.driver,
                    "partition": self.placement.partition,
                    "nodes": self.placement.nodes,
                    "maxConcurrent": self.placement.max_concurrent,
                    "retryBudget": self.placement.retry_budget,
                },
                "evaluate": {
                    "driver": self.evaluate.driver,
                    "suites": list(self.evaluate.suites),
                    "gates": list(self.evaluate.gates),
                    "baseline": self.evaluate.baseline,
                },
                "release": {
                    "route": self.release.route,
                    "formats": list(self.release.formats),
                    "approval": self.release.approval,
                },
            },
        }

    def canonical(self) -> bytes:
        """Return the canonical bytes of this specification."""
        return canonical_json(self.as_mapping())

    def spec_hash(self) -> str:
        """Return the SHA-256 of the canonical form, as SAD 6.2 requires."""
        import hashlib

        return hashlib.sha256(self.canonical()).hexdigest()

    def driver_for(self, group: str) -> str | None:
        """Return the driver name this specification names for `group`."""
        return {
            "draupnir.train": self.train.driver,
            "draupnir.merge": self.train.driver,
            "draupnir.eval": self.evaluate.driver,
            "draupnir.schedule": self.placement.driver,
        }.get(group)

    def capabilities_for(self, group: str) -> frozenset[str]:
        """Return the capabilities a driver in `group` must declare for this run.

        SAD 10.3 rule 4: "A plug-in declares its capabilities. The core refuses
        to plan a job whose specification requires a capability the driver has
        not declared." This is that requirement made concrete, and it is
        deliberately a small, readable table rather than an inference: an
        operator reading a refusal must be able to see where the demand came
        from.
        """
        if group == "draupnir.train":
            required = {self.train.method}
            if self.placement.nodes > 1:
                required.add("multinode")
            if self.train.precision:
                required.add(self.train.precision)
            return frozenset(required)
        if group == "draupnir.export":
            return frozenset(self.release.formats)
        if group == "draupnir.eval":
            return frozenset(self.evaluate.suites)
        if group == "draupnir.schedule":
            required = {"multinode"} if self.placement.nodes > 1 else set()
            if self.placement.max_concurrent > 1:
                required.add("array")
            return frozenset(required)
        return frozenset()


# ---------------------------------------------------------------------------
# Planning and execution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValidationError:
    """One reason a specification cannot be run by a given driver."""

    field: str
    message: str
    code: str = "invalid"

    def __str__(self) -> str:
        """Render for a problem document or a CLI line."""
        return f"{self.field}: {self.message} [{self.code}]"


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    """What a job asks the scheduler for."""

    partition: str
    nodes: int = 1
    gpus_per_node: int = 0
    time_limit_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class JobPlan:
    """The concrete command, environment and resource request for one job.

    Produced by `render`, which SAD Decision S5 requires to be pure. Two calls
    with the same specification must produce plans with identical `canonical()`
    bytes; the conformance suite asserts exactly that.
    """

    command: tuple[str, ...]
    environment: Mapping[str, str] = field(default_factory=dict)
    workdir: str = "."
    resources: ResourceRequest = field(default_factory=lambda: ResourceRequest(partition="default"))
    expected_artefacts: tuple[str, ...] = ()

    def as_mapping(self) -> dict[str, Any]:
        """Return the plan in a shape that serialises deterministically."""
        return {
            "command": list(self.command),
            "environment": dict(self.environment),
            "workdir": self.workdir,
            "resources": {
                "partition": self.resources.partition,
                "nodes": self.resources.nodes,
                "gpusPerNode": self.resources.gpus_per_node,
                "timeLimitMinutes": self.resources.time_limit_minutes,
            },
            "expectedArtefacts": list(self.expected_artefacts),
        }

    def canonical(self) -> bytes:
        """Return the bytes two renders of the same spec must agree on."""
        return canonical_json(self.as_mapping())


class ProgressKind(StrEnum):
    """What one line of executor output turned out to mean."""

    STEP = "step"
    LOSS = "loss"
    CHECKPOINT = "checkpoint"
    METRIC = "metric"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One structured event parsed from a line of executor output.

    Carries no timestamp. `parse_progress` is a pure translation of a line;
    the caller stamps the event, so that replaying a captured log produces the
    same events it did the first time.
    """

    kind: ProgressKind
    step: int | None = None
    total: int | None = None
    value: float | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ProducedArtefact:
    """One artefact a run produced, with the hash that identifies it."""

    path: str
    kind: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class RunArtefacts:
    """Everything a run produced. `collect` must not mutate any of it."""

    artefacts: tuple[ProducedArtefact, ...] = ()
    metrics: Mapping[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


class JobState(StrEnum):
    """Scheduler-side job state, distinct from the run states of SAD 6.1."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_JOB_STATES: Final = frozenset({JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED})


@dataclass(frozen=True, slots=True)
class JobHandle:
    """What a scheduler gives back, and what it needs to find the job again."""

    driver: str
    job_id: str
    node: str | None = None


@dataclass(frozen=True, slots=True)
class JobStatus:
    """The observable state of a submitted job."""

    state: JobState
    exit_code: int | None = None
    node: str | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# Evaluation, storage and policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """One gate result, matching the `gate_result` entity of SAD 7.1."""

    gate: str
    suite_version: str
    value: float
    baseline_value: float | None = None
    margin: float | None = None
    passed: bool = False


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    """What a store knows about one addressed object."""

    uri: str
    exists: bool
    sha256: str | None = None
    size: int | None = None


class Verdict(StrEnum):
    """A policy outcome. GLEIPNIR permits or refuses; it never executes."""

    PERMIT = "PERMIT"
    REFUSE = "REFUSE"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """One policy evaluation, recorded in the ledger with its policy version."""

    verdict: Verdict
    policy_version: str
    rule: str | None = None
    reason: str | None = None


def sequence_of(values: Sequence[str]) -> tuple[str, ...]:
    """Normalise a sequence of names to a tuple, for frozen dataclass fields."""
    return tuple(values)
