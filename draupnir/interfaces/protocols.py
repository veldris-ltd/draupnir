"""The seven plug-in interfaces of SAD 8.2.

| Entry point group  | Interface        |
|--------------------|------------------|
| `draupnir.train`   | `TrainDriver`    |
| `draupnir.merge`   | `MergeDriver`    |
| `draupnir.eval`    | `EvalDriver`     |
| `draupnir.export`  | `ExportDriver`   |
| `draupnir.schedule`| `ScheduleDriver` |
| `draupnir.store`   | `StoreDriver`    |
| `draupnir.policy`  | `PolicyDriver`   |

Four of them plan and collect a job and therefore share a shape, which is
`JobDriver` below. The other three do not, and are not forced into it.

Every one declares `name`, a versioned entry point name per SAD 10.3, and
`capabilities`, a frozenset the core checks a specification against before an
allocation is consumed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from draupnir.interfaces.types import (
    GateOutcome,
    JobHandle,
    JobPlan,
    JobStatus,
    ObjectInfo,
    PolicyDecision,
    ProgressEvent,
    RunArtefacts,
    RunSpec,
    ValidationError,
)


@runtime_checkable
class Driver(Protocol):
    """What every plug-in declares, whichever interface it implements."""

    #: Versioned entry point name, e.g. `hamarr.llamafactory/v1` (SAD 10.3).
    name: str

    #: What this driver can do. The core refuses to plan a job whose
    #: specification requires a capability that is not in this set.
    capabilities: frozenset[str]


@runtime_checkable
class JobDriver(Driver, Protocol):
    """A driver that turns a specification into a job and reads its results.

    Shared by `TrainDriver`, `MergeDriver`, `EvalDriver` and `ExportDriver`.
    """

    def validate(self, spec: RunSpec) -> list[ValidationError]:
        """Reject an unrunnable spec before an allocation is consumed.

        Returns every problem found, not the first: an operator fixing a
        specification should need one round trip, not five.
        """
        ...

    def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
        """Produce the concrete command, environment and resource request.

        Must be pure: no side effects, no network, deterministic given spec.
        SAD Decision S5, and the conformance suite enforces it.
        """
        ...

    def parse_progress(self, line: str) -> ProgressEvent | None:
        """Translate one line of executor output into a structured event.

        Returns None for a line that carries no event. Must be pure, so that
        replaying a captured log yields the events it yielded live.
        """
        ...

    def collect(self, workdir: Path) -> RunArtefacts:
        """Return produced artefacts with their hashes. Must not mutate them."""
        ...


@runtime_checkable
class TrainDriver(JobDriver, Protocol):
    """`draupnir.train`: a training framework or method.

    Capabilities are drawn from the method vocabulary, e.g. `{"lora", "qlora",
    "full", "moe", "multinode"}`, plus the precisions the driver supports.
    """


@runtime_checkable
class MergeDriver(JobDriver, Protocol):
    """`draupnir.merge`: a reweighting or merge method.

    Capabilities name the methods, e.g. `{"ties", "dare-ties", "slerp"}`.
    BRISINGAMEN owns the sweep; a merge driver never decides whether a merge is
    acceptable, because RAUN decides (SAD 5.2).
    """


@runtime_checkable
class EvalDriver(JobDriver, Protocol):
    """`draupnir.eval`: an evaluation suite.

    Capabilities name the suites this driver can run. `collect_gates` reads the
    outcome; `collect` returns the artefacts, typically the report.
    """

    def collect_gates(self, workdir: Path, suite_version: str) -> tuple[GateOutcome, ...]:
        """Return one outcome per gate, with baseline and margin."""
        ...


@runtime_checkable
class ExportDriver(JobDriver, Protocol):
    """`draupnir.export`: a quantisation or packaging format.

    Capabilities name the formats, e.g. `{"nvfp4", "gguf-q4km", "mlx4"}`. This
    is the interface AC-N9 measures: a new one, working, in under 200 lines
    with no core file modified.
    """


@runtime_checkable
class ScheduleDriver(Driver, Protocol):
    """`draupnir.schedule`: a scheduler, or a local runner for development.

    MOTSOGNIR owns placement policy and retry; a schedule driver only knows how
    to submit, observe and cancel. It never knows what a job computes
    (SAD 5.2).
    """

    def submit(self, plan: JobPlan) -> JobHandle:
        """Submit a rendered plan and return the handle that identifies it."""
        ...

    def poll(self, handle: JobHandle) -> JobStatus:
        """Return the current status. Must be safe to call repeatedly."""
        ...

    def cancel(self, handle: JobHandle) -> JobStatus:
        """Stop the job and return its resulting status.

        AC-F13: cancelling leaves the job in a defined state, never in an
        ambiguous one. Cancelling an already finished job is not an error.
        """
        ...

    def logs(self, handle: JobHandle) -> str:
        """Return whatever output the job has produced so far."""
        ...


@runtime_checkable
class StoreDriver(Driver, Protocol):
    """`draupnir.store`: a storage backend behind `hodd://` URIs.

    SAD 7.4: a run specification addresses artefacts by `hodd://` URI, so that
    a change of physical placement leaves every specification untouched.
    Capabilities name the schemes and features, e.g. `{"posix", "versioned"}`.
    """

    def resolve(self, uri: str) -> str:
        """Return the physical location for a `hodd://` URI."""
        ...

    def stat(self, uri: str) -> ObjectInfo:
        """Return what is known about the object, without fetching it."""
        ...

    def get(self, uri: str, destination: Path) -> ObjectInfo:
        """Fetch the object to `destination` and return what was fetched."""
        ...

    def put(self, uri: str, source: Path) -> ObjectInfo:
        """Store `source` at `uri`. Refuse to overwrite a sealed artefact."""
        ...


@runtime_checkable
class PolicyDriver(Driver, Protocol):
    """`draupnir.policy`: a compliance regime.

    GLEIPNIR judges and never executes (SAD Decision S4). A policy driver
    returns a decision and the version of the policy that produced it, so that
    a decision remains explicable after the policy has moved on.
    """

    #: The version recorded alongside every decision this driver returns.
    policy_version: str

    def evaluate(self, subject: dict[str, object]) -> PolicyDecision:
        """Permit, refuse, or require an approval, naming the rule applied."""
        ...


#: The current major version of each interface. SAD 10.3 rule 2: the core
#: supports this and the immediately previous major version, and nothing else.
CURRENT_MAJOR: dict[str, int] = {
    "draupnir.train": 1,
    "draupnir.merge": 1,
    "draupnir.eval": 1,
    "draupnir.export": 1,
    "draupnir.schedule": 1,
    "draupnir.store": 1,
    "draupnir.policy": 1,
}

#: Which Protocol a plug-in in each group must satisfy.
PROTOCOL_FOR_GROUP: dict[str, type] = {
    "draupnir.train": TrainDriver,
    "draupnir.merge": MergeDriver,
    "draupnir.eval": EvalDriver,
    "draupnir.export": ExportDriver,
    "draupnir.schedule": ScheduleDriver,
    "draupnir.store": StoreDriver,
    "draupnir.policy": PolicyDriver,
}
