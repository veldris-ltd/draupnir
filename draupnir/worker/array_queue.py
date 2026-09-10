"""Submitting the array, and requeueing one element of it. RF-13.

`draupnir/motsognir/arrays.py` and `draupnir/motsognir/retry.py` -- the
`--array=0-55%3` submission and the single-element `--array=<index>` retry the
README describes at length -- were orphans. `GET /v1/arrays` did not read an
array: it listed the runs at the site, sorted them and numbered them `0..n`, so
`size` was the number of runs rather than fifty-six and `attempts` was
`max(1, 4 - retry_budget)`, a formula rather than a count. S12's stated primary
action, "Requeue a single element", had no API operation at all.

**One submission, not fifty-six.** The whole point of an array is that the
scheduler holds all N elements and runs M of them; the control plane does not
top up a queue, so utilisation does not depend on it being awake (SAD 11.2).
Fifty-six separate submissions would be fifty-six jobs the scheduler schedules
independently, `%3` would mean nothing, and a control plane that stopped would
stop the estate.

**A retry is one element.** Resubmitting the array would restart every element,
discarding the compute of the ones that succeeded -- for a fifty-six element
adapter run against three appliances, most of a week. So a retry is
`--array=<index>`, on its own, and the other fifty-five are untouched (AC-F6).

**Losing an appliance reduces concurrency rather than refusing.** That is what
`placement.plan` already does for the adapter partition and refuses to do for
the ring partition, and the difference is the point: an array is N independent
elements and a ring job is one job that needs the whole ring.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from draupnir.core.domain.ledger import LedgerEntry
from draupnir.interfaces.types import JobPlan, ResourceRequest
from draupnir.motsognir import arrays, execution
from draupnir.motsognir.placement import Estate, Partition, PlacementError
from draupnir.motsognir.placement import plan as place
from draupnir.worker import accepted

logger = structlog.get_logger(__name__)

#: What an array element runs. A single command for every element, which is how
#: an array works: the scheduler sets the element index in the environment and
#: the same script reads it. What it does with that is the element script's
#: business and lives on the appliance, not here.
ELEMENT_COMMAND = "draupnir-array-element"

#: Which outcome closes which request.
CLOSES: Mapping[str, tuple[str, ...]] = {
    arrays.ARRAY_ACCEPTED: (arrays.ARRAY_SUBMITTED, arrays.ARRAY_REFUSED),
    arrays.ELEMENT_REQUEUE_ACCEPTED: (
        arrays.ELEMENT_REQUEUED,
        arrays.ELEMENT_REQUEUE_REFUSED,
    ),
}


class ArrayWorkError(Exception):
    """Raised when accepted array work cannot be performed.

    Carried into a refusal entry rather than out of the tick: one array that
    cannot be submitted must not stop the estate, and the reason belongs where
    the operator who asked for it will look.
    """


@dataclass(frozen=True, slots=True)
class Outcome:
    """What was done about one request."""

    subject: str
    transition: str
    payload: Mapping[str, Any]

    @property
    def succeeded(self) -> bool:
        """Whether the work was performed."""
        return self.transition in {arrays.ARRAY_SUBMITTED, arrays.ELEMENT_REQUEUED}


@dataclass
class Submitter:
    """What array work is performed against."""

    scheduler: Any
    estate: Estate = field(default_factory=Estate)
    #: The chain, for reading the array a requeue is about.
    entries: tuple[LedgerEntry, ...] = ()

    def record(self) -> arrays.ArrayRecord | None:
        """The array this site currently holds, or `None`."""
        return arrays.fold(self.entries)


def outstanding(entries: Iterable[LedgerEntry], *, accepted_transition: str) -> tuple[Any, ...]:
    """Every accepted array request no outcome has closed."""
    return accepted.outstanding(
        entries, accepted=accepted_transition, closed_by=CLOSES[accepted_transition]
    )


def submit_array(request: Any, submitter: Submitter) -> Outcome:
    """Submit one array, as one array.

    The concurrency comes from the estate rather than from the request: SAD 6.2
    asks for three concurrent adapter elements against three appliances, and
    asking for more would queue work behind itself while asking for fewer would
    idle a machine. An appliance that is down reduces it and does not refuse
    the array.
    """
    subjects = [str(item) for item in request.payload.get("subjects", []) if str(item)]
    name = str(request.payload.get("name") or "cim-array")
    budget = int(request.payload.get("retryBudget") or 0)

    try:
        if not subjects:
            msg = "an array with no subjects is not an array; nothing was submitted"
            raise ArrayWorkError(msg)

        placement = place(
            partition=Partition.ADAPTERS,
            estate=submitter.estate,
            requested_concurrency=len(subjects),
        )
        plan = arrays.build(subjects, placement, retry_budget=budget, name=name)

        # One submission. The `%M` in `0-55%3` is a throttle the scheduler
        # applies; N jobs would be N schedules and the throttle would mean
        # nothing.
        handle = execution.submit(submitter.scheduler, _plan_job(plan), name=name)
    except PlacementError as refusal:
        return _refused(request, name, f"the array could not be placed: {refusal}")
    # Broad, and deliberately: one array that cannot be submitted must not stop
    # the estate, and every reason belongs in the entry rather than in a
    # traceback nobody reads.
    except Exception as refusal:
        return _refused(request, name, str(refusal))

    logger.info(
        "array.submitted",
        name=name,
        size=plan.size,
        slurmArray=plan.slurm_array(),
        jobId=handle.job_id,
    )
    return Outcome(
        subject=name,
        transition=arrays.ARRAY_SUBMITTED,
        payload={
            **request.answering(),
            **arrays.plan_payload(plan, job_id=handle.job_id, driver=handle.driver),
        },
    )


def requeue_element(request: Any, submitter: Submitter) -> Outcome:
    """Requeue one element, leaving the other fifty-five alone. AC-F6, S12.

    **Through the driver's `requeue`, not through a resubmission.** The
    mechanism is `scontrol requeue <job>_<index>`: it puts *that* element back
    on the queue. A fresh submission of `--array=<index>` looks equivalent and
    is not -- it produces a new job identifier, which severs the element from
    its array and loses both the `%M` throttle and the accounting record that
    ties the fifty-six together. `motsognir.slurm/v1` does the former;
    `motsognir.slurmrest/v1` refuses in as many words, because slurmrestd
    v0.0.40 exposes no requeue and both approximations are worse than a
    refusal (AC-F6, RF-E24).

    So a transport that cannot requeue produces a refusal entry carrying the
    driver's own message, which names `scontrol requeue <job>_<index>` on
    REGIN. That is the honest outcome for this estate today: the operator is
    told exactly what to do, and the control plane does not quietly do
    something else and call it a requeue.

    The array is read back from the chain rather than rebuilt, because what is
    being requeued is *this* element of *that* array -- its job, its subject
    and its attempt count. A rebuilt array would look the same and be a
    different object, with every element's attempt count back at zero.
    """
    index = request.payload.get("index")
    try:
        record = submitter.record()
        if record is None:
            msg = "this site has submitted no array, so there is no element to requeue"
            raise ArrayWorkError(msg)
        if not isinstance(index, int):
            msg = f"an element is requeued by index; {index!r} is not one"
            raise ArrayWorkError(msg)
        if not record.job_id:
            msg = (
                f"array {record.name} has no scheduler job recorded, so there is no "
                "element to put back on the queue. It was accepted and not submitted."
            )
            raise ArrayWorkError(msg)

        element = record.element(index)
        # Placed first, so that a requeue onto an estate with nothing available
        # is refused before the element is disturbed rather than after.
        place(
            partition=Partition.ADAPTERS,
            estate=submitter.estate,
            requested_concurrency=1,
        )
        handle = execution.handle_for(record.driver or "", record.job_id, element.node)
        status = submitter.scheduler.requeue(handle, index)
    except Exception as refusal:
        logger.warning("array.requeue.refused", index=index, reason=str(refusal))
        return Outcome(
            subject=str(request.subject),
            transition=arrays.ELEMENT_REQUEUE_REFUSED,
            payload={**request.answering(), "index": index, "reason": str(refusal)},
        )

    # The element, not the array. `with_element` is the shape that makes a
    # retry individual: the other fifty-five are the same objects they were.
    moved = arrays.Element(
        index=element.index,
        subject=element.subject,
        state=arrays.ElementState.PENDING,
        attempts=element.attempts + 1,
        job_id=f"{record.job_id}_{index}",
        node=getattr(status, "node", None) or element.node,
    )
    logger.info("array.requeued", name=record.name, index=index, element=moved.job_id)
    return Outcome(
        subject=record.name,
        transition=arrays.ELEMENT_REQUEUED,
        payload={
            **request.answering(),
            "index": index,
            "elementJobId": moved.job_id,
            "attempt": moved.attempts,
            "mechanism": "scontrol requeue",
            "element": arrays.element_payload(moved),
        },
    )


def perform(requests: Sequence[Any], submitter: Submitter) -> tuple[Outcome, ...]:
    """Do every outstanding array request, in the order they were accepted."""
    done: list[Outcome] = []
    for request in requests:
        if request.transition == arrays.ARRAY_ACCEPTED:
            done.append(submit_array(request, submitter))
        else:
            done.append(requeue_element(request, submitter))
    return tuple(done)


def _refused(request: Any, name: str, reason: str) -> Outcome:
    logger.warning("array.refused", name=name, reason=reason)
    return Outcome(
        subject=name,
        transition=arrays.ARRAY_REFUSED,
        payload={**request.answering(), "name": name, "reason": reason},
    )


def _plan_job(plan: arrays.ArrayPlan) -> JobPlan:
    """The job an array submission places.

    The `ArraySpec` rather than a rendered string: the driver renders
    `--array=` from it, so MOTSOGNIR states what the array is once and the
    driver states how its scheduler spells it. Two renderings would be two
    answers to "was this submitted as one array".
    """
    return JobPlan(
        command=(ELEMENT_COMMAND,),
        environment={"DRAUPNIR_ARRAY_NAME": plan.name},
        resources=ResourceRequest(
            partition=str(plan.placement.partition),
            nodes=plan.placement.nodes_per_element,
            gres=plan.placement.gres,
            array=plan.submission(),
        ),
        expected_artefacts=(),
    )


__all__ = [
    "CLOSES",
    "ArrayWorkError",
    "Outcome",
    "Submitter",
    "outstanding",
    "perform",
    "requeue_element",
    "submit_array",
]
