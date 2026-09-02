"""Array management: N elements, exactly M concurrent, retried one at a time.

AC-F5: "A fifty six element adapter array is submitted as one action and
executes exactly three concurrently, one per appliance."

AC-F6: "A failed array element is retried individually without disturbing the
other elements."

Both come down to the same Slurm feature and the same care about it. An array
is `--array=0-(N-1)%M`, where the `%M` is a throttle rather than a count: N
elements exist from the moment of submission, and M of them run at a time.
Getting that wrong in the other direction -- submitting M jobs and topping
them up -- would make the estate's utilisation depend on the control plane
being awake, which SAD 11.2 explicitly does not assume.

Retrying an element individually is `--array=<index>` on a fresh submission,
not a resubmission of the array. A resubmission would restart every element,
discarding the compute of the fifty five that succeeded.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from draupnir.motsognir.placement import Placement


class ArrayError(Exception):
    """Raised when an array cannot be described or advanced."""


class ElementState(StrEnum):
    """Where one element of an array has reached.

    Distinct from `JobState`, which is the scheduler's view of one submission.
    An element that failed and is awaiting a retry is neither running nor
    finished, and conflating the two loses the retry budget.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    #: Failed, within budget, waiting for its backoff to elapse.
    AWAITING_RETRY = "AWAITING_RETRY"
    #: Failed and out of budget. Terminal.
    EXHAUSTED = "EXHAUSTED"
    CANCELLED = "CANCELLED"


TERMINAL: frozenset[ElementState] = frozenset(
    {ElementState.COMPLETED, ElementState.EXHAUSTED, ElementState.CANCELLED}
)


@dataclass(frozen=True, slots=True)
class Element:
    """One element of an array, and what has happened to it."""

    index: int
    #: What this element is for -- a jurisdiction, for a CIM-56 adapter array.
    subject: str
    state: ElementState = ElementState.PENDING
    attempts: int = 0
    job_id: str | None = None
    node: str | None = None
    exit_code: int | None = None

    @property
    def settled(self) -> bool:
        """Whether this element needs nothing further."""
        return self.state in TERMINAL


@dataclass(frozen=True, slots=True)
class ArrayPlan:
    """An array as it is submitted: how many, how many at once, and to where."""

    elements: tuple[Element, ...]
    placement: Placement
    retry_budget: int = 0
    name: str = "cim-array"

    def __post_init__(self) -> None:
        """Refuse an array that cannot be submitted."""
        if not self.elements:
            msg = "an array has at least one element"
            raise ArrayError(msg)
        indices = [element.index for element in self.elements]
        if sorted(indices) != list(range(len(indices))):
            msg = (
                f"array indices must be contiguous from 0; got "
                f"{sorted(indices)[:5]}{'...' if len(indices) > 5 else ''}. Slurm "
                "addresses elements by index, and a gap addresses nothing."
            )
            raise ArrayError(msg)

    @property
    def size(self) -> int:
        """N: how many elements the array holds."""
        return len(self.elements)

    @property
    def concurrency(self) -> int:
        """M: how many run at once. One per available appliance."""
        return self.placement.concurrency

    def slurm_array(self) -> str:
        """The `--array` value: `0-(N-1)%M`.

        The `%M` is a throttle, not a count. All N elements exist at
        submission and Slurm runs M of them; the control plane does not top up
        a queue, so utilisation does not depend on it being awake (SAD 11.2).
        """
        return f"0-{self.size - 1}%{self.concurrency}"

    def slurm_arguments(self) -> tuple[str, ...]:
        """Everything the submission needs, in the order sbatch takes it."""
        return (
            f"--job-name={self.name}",
            f"--array={self.slurm_array()}",
            f"--partition={self.placement.partition}",
            f"--nodes={self.placement.nodes_per_element}",
        )

    def element(self, index: int) -> Element:
        """One element by index."""
        try:
            return self.elements[index]
        except IndexError as error:
            msg = f"array {self.name} has {self.size} elements; there is no index {index}"
            raise ArrayError(msg) from error

    def by_state(self, state: ElementState) -> tuple[Element, ...]:
        """Every element in one state."""
        return tuple(item for item in self.elements if item.state is state)

    @property
    def running(self) -> tuple[Element, ...]:
        """Elements the scheduler is currently executing."""
        return self.by_state(ElementState.RUNNING)

    @property
    def settled(self) -> bool:
        """Whether every element needs nothing further."""
        return all(item.settled for item in self.elements)

    @property
    def progress(self) -> tuple[int, int]:
        """How many elements have completed, out of how many."""
        return len(self.by_state(ElementState.COMPLETED)), self.size

    def with_element(self, element: Element) -> ArrayPlan:
        """Return the array with one element replaced.

        Replacing one rather than mutating the array is what makes a retry
        individual: the other fifty five are the same objects they were.
        """
        return replace(
            self,
            elements=tuple(
                element if item.index == element.index else item for item in self.elements
            ),
        )


def build(
    subjects: Sequence[str],
    placement: Placement,
    *,
    retry_budget: int = 0,
    name: str = "cim-array",
) -> ArrayPlan:
    """Build an array over `subjects`, one element each, in the order given."""
    return ArrayPlan(
        elements=tuple(
            Element(index=index, subject=subject) for index, subject in enumerate(subjects)
        ),
        placement=placement,
        retry_budget=retry_budget,
        name=name,
    )


# ---------------------------------------------------------------------------
# Advancing an array
# ---------------------------------------------------------------------------


def observe(
    plan: ArrayPlan,
    index: int,
    *,
    state: ElementState,
    exit_code: int | None = None,
    job_id: str | None = None,
    node: str | None = None,
) -> ArrayPlan:
    """Record what the scheduler reports about one element.

    A failure within budget becomes `AWAITING_RETRY` rather than `FAILED`, so
    that "failed" in the run board means "will not be tried again". An
    operator reading fifty six elements needs that distinction more than they
    need the scheduler's vocabulary.
    """
    element = plan.element(index)

    if state is ElementState.FAILED:
        attempts = element.attempts + 1
        within_budget = attempts <= plan.retry_budget
        return plan.with_element(
            replace(
                element,
                state=ElementState.AWAITING_RETRY if within_budget else ElementState.EXHAUSTED,
                attempts=attempts,
                exit_code=exit_code,
                job_id=job_id or element.job_id,
                node=node or element.node,
            )
        )

    return plan.with_element(
        replace(
            element,
            state=state,
            exit_code=exit_code if exit_code is not None else element.exit_code,
            job_id=job_id or element.job_id,
            node=node or element.node,
        )
    )


@dataclass(frozen=True, slots=True)
class Retry:
    """One element to resubmit, on its own."""

    index: int
    subject: str
    attempt: int
    #: `--array=<index>`: a fresh submission of one element, never of the array.
    slurm_array: str = field(default="")

    def slurm_arguments(self, plan: ArrayPlan) -> tuple[str, ...]:
        """The submission for exactly this element. AC-F6."""
        return (
            f"--job-name={plan.name}-retry-{self.index}",
            f"--array={self.index}",
            f"--partition={plan.placement.partition}",
            f"--nodes={plan.placement.nodes_per_element}",
        )


def due_for_retry(plan: ArrayPlan) -> tuple[Retry, ...]:
    """Every element awaiting a retry, in index order.

    Resubmitting these does not touch the array. Slurm would restart every
    element if the array were resubmitted, discarding the compute of the ones
    that succeeded, which for a fifty six element run is most of a week.
    """
    return tuple(
        Retry(index=item.index, subject=item.subject, attempt=item.attempts + 1)
        for item in plan.elements
        if item.state is ElementState.AWAITING_RETRY
    )


def summarise(plan: ArrayPlan) -> Mapping[str, int]:
    """How many elements are in each state, for the run board."""
    counts: dict[str, int] = {}
    for element in plan.elements:
        counts[str(element.state)] = counts.get(str(element.state), 0) + 1
    return dict(sorted(counts.items()))


def cancel(plan: ArrayPlan, indices: Iterable[int] | None = None) -> ArrayPlan:
    """Cancel elements, leaving each in a defined state. AC-F13.

    An element that has already completed keeps its result: rewriting a
    finished element as cancelled would be a less true record, and AC-F13 asks
    for a defined state rather than a uniform one.
    """
    selected = set(indices) if indices is not None else {item.index for item in plan.elements}
    updated = plan
    for element in plan.elements:
        if element.index in selected and not element.settled:
            updated = updated.with_element(replace(element, state=ElementState.CANCELLED))
    return updated
