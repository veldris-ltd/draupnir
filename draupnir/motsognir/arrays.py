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
from typing import Any

from draupnir.interfaces.types import ArraySpec
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

    def submission(self) -> ArraySpec:
        """What the scheduler is asked for: N elements, M at once."""
        return ArraySpec(size=self.size, throttle=self.concurrency)

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


# ---------------------------------------------------------------------------
# The array as the chain records it. RF-13.
# ---------------------------------------------------------------------------

#: The subject type an array's entries are recorded against.
ARRAY_SUBJECT = "array"

#: What the API records when a curator submits an array, and what the worker
#: drains. Named the way every other accepted-and-performed pair is.
ARRAY_ACCEPTED = "array-accepted"
ARRAY_SUBMITTED = "array-submitted"
ARRAY_REFUSED = "array-refused"

#: A single element resubmitted on its own. AC-F6, and S12's primary action.
ELEMENT_REQUEUE_ACCEPTED = "element-requeue-accepted"
ELEMENT_REQUEUED = "element-requeued"
ELEMENT_REQUEUE_REFUSED = "element-requeue-refused"

#: What the worker records as it observes an element move.
ELEMENT_OBSERVED = "element-observed"


def element_payload(element: Element) -> dict[str, Any]:
    """One element, for the chain and for the console."""
    return {
        "index": element.index,
        "subject": element.subject,
        "state": str(element.state),
        "attempts": element.attempts,
        "jobId": element.job_id,
        "node": element.node,
        "exitCode": element.exit_code,
    }


def plan_payload(plan: ArrayPlan, *, job_id: str = "", driver: str = "") -> dict[str, Any]:
    """The submitted array, for the chain.

    Records `slurmArray` verbatim -- `0-55%3` -- rather than the three numbers
    it is derived from. An auditor asking "was this submitted as one array"
    reads the string sbatch was given, and reconstructing it from a size and a
    concurrency is a second derivation that can disagree with the first.
    """
    return {
        "name": plan.name,
        "size": plan.size,
        "concurrency": plan.concurrency,
        "slurmArray": plan.slurm_array(),
        "arguments": list(plan.slurm_arguments()),
        "partition": str(plan.placement.partition),
        "appliances": list(plan.placement.appliances),
        "retryBudget": plan.retry_budget,
        "jobId": job_id,
        "driver": driver,
        "elements": [element_payload(item) for item in plan.elements],
    }


@dataclass(frozen=True, slots=True)
class ArrayRecord:
    """An array as the chain holds it, folded from its entries. RF-13.

    `GET /v1/arrays` did not read an array. It listed the runs at the site,
    sorted them and numbered them `0..n`, so `size` was the number of runs
    rather than fifty-six and `attempts` was `max(1, 4 - retry_budget)` -- a
    formula rather than a count. An array with no runs yet reported size zero,
    and a site with sixty runs from other work reported sixty elements.

    Folded rather than projected into a table, because the array is small and
    the chain is the record: a projection would be a second place the element
    states live, and the two would disagree the first time one was rebuilt.
    """

    name: str
    size: int
    concurrency: int
    slurm_array: str
    elements: tuple[Element, ...]
    job_id: str = ""
    partition: str = ""
    retry_budget: int = 0
    #: Which schedule driver placed it. Kept because a requeue is addressed to
    #: the same driver that holds the job, and a control plane that guessed
    #: would address `scontrol` at a cluster reached over slurmrestd.
    driver: str = ""

    @property
    def summary(self) -> dict[str, int]:
        """How many elements are in each state, for the run board."""
        counts: dict[str, int] = {}
        for element in self.elements:
            counts[str(element.state)] = counts.get(str(element.state), 0) + 1
        return dict(sorted(counts.items()))

    def element(self, index: int) -> Element:
        """One element by index, or a refusal naming the size."""
        for item in self.elements:
            if item.index == index:
                return item
        msg = f"array {self.name} has {self.size} elements; there is no index {index}"
        raise ArrayError(msg)

    def retry(self, index: int) -> Retry:
        """The resubmission of one element of this array. AC-F6.

        From the record rather than from a rebuilt plan, because what is being
        requeued is *this* element of *that* array: its subject and its attempt
        count. A rebuilt plan would produce an array that looks the same and is
        a different object, with every element's attempt count back at zero.
        """
        element = self.element(index)
        return Retry(index=element.index, subject=element.subject, attempt=element.attempts + 1)

    def submission_for(self, index: int) -> ArraySpec:
        """What the scheduler is asked for when one element is requeued.

        `--array=<index>` and nothing else. Resubmitting the array would
        restart every element, discarding the compute of the ones that
        succeeded -- for fifty-six elements against three appliances, most of a
        week.
        """
        self.element(index)
        return ArraySpec(size=self.size, throttle=1, indices=(index,))

    def submission(self) -> ArraySpec:
        """What the scheduler was asked for when the whole array was placed."""
        return ArraySpec(size=self.size, throttle=self.concurrency)


def _element_from(payload: Mapping[str, Any]) -> Element:
    """One element, rebuilt from what the chain recorded."""
    return Element(
        index=int(payload.get("index", 0)),
        subject=str(payload.get("subject") or ""),
        state=ElementState(str(payload.get("state") or ElementState.PENDING)),
        attempts=int(payload.get("attempts", 0) or 0),
        job_id=payload.get("jobId") or None,
        node=payload.get("node") or None,
        exit_code=payload.get("exitCode"),
    )


def fold(entries: Iterable[Any]) -> ArrayRecord | None:
    """The latest array at this site, with every observation applied.

    `None` where no array has been submitted, which is a truthful answer and
    was the one thing the old handler could not give: it always had a number,
    because it was counting runs.

    The *latest* array rather than every array. There is one adapter array in
    flight at a time on this estate -- fifty-six elements against three
    appliances is most of a week -- and a reader asking "how is the array
    going" means the current one. The chain keeps the ones before it.
    """
    submitted: dict[str, Any] | None = None
    observations: list[Mapping[str, Any]] = []

    for entry in entries:
        payload = entry.payload if isinstance(entry.payload, Mapping) else {}
        if entry.transition == ARRAY_SUBMITTED:
            # A later submission replaces the earlier one, and its
            # observations start again with it.
            submitted = dict(payload)
            observations = []
        elif entry.transition in {ELEMENT_OBSERVED, ELEMENT_REQUEUED} and submitted is not None:
            observations.append(payload)

    if submitted is None:
        return None

    elements = {
        int(item.get("index", 0)): _element_from(item)
        for item in submitted.get("elements", [])
        if isinstance(item, Mapping)
    }
    for observed in observations:
        element = observed.get("element")
        if isinstance(element, Mapping):
            rebuilt = _element_from(element)
            elements[rebuilt.index] = rebuilt

    ordered = tuple(elements[index] for index in sorted(elements))
    return ArrayRecord(
        name=str(submitted.get("name") or "array"),
        size=int(submitted.get("size") or len(ordered)),
        concurrency=int(submitted.get("concurrency") or 1),
        slurm_array=str(submitted.get("slurmArray") or ""),
        elements=ordered,
        job_id=str(submitted.get("jobId") or ""),
        partition=str(submitted.get("partition") or ""),
        retry_budget=int(submitted.get("retryBudget") or 0),
        driver=str(submitted.get("driver") or ""),
    )


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
