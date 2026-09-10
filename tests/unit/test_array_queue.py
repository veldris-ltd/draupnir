"""The array, submitted as one array and retried one element at a time. RF-13.

`draupnir/motsognir/arrays.py` and `draupnir/motsognir/retry.py` -- the
`--array=0-55%3` submission and the single-element retry the README describes at
length -- were orphans. `GET /v1/arrays` did not read an array: it listed the
runs at the site, sorted them and numbered them `0..n`. S12's stated primary
action, "Requeue a single element", had no API operation.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry
from draupnir.hamarr import tiers
from draupnir.interfaces.types import ArraySpec, JobHandle, JobState, JobStatus
from draupnir.motsognir import arrays
from draupnir.motsognir.placement import Estate, Partition, PlacementError, estate_for
from draupnir.motsognir.placement import plan as place
from draupnir.worker import array_queue

pytestmark = pytest.mark.unit

SITE = "sindri"
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
NAME = "cim-56-adapters"


def _entry(seq: int, transition: str, payload: Mapping[str, Any]) -> LedgerEntry:
    return LedgerEntry(
        id=uuid.uuid4(),
        site_id=SITE,
        seq=seq,
        prev_hash=GENESIS_HASH,
        entry_hash="e" * 64,
        ts=NOW,
        actor="operator@veldris.internal",
        subject_type=arrays.ARRAY_SUBJECT,
        subject_id=NAME,
        transition=transition,
        payload=dict(payload),
    )


def _estate(down: int = 0) -> Estate:
    """The three-appliance forge, with `down` of them unavailable."""
    built = estate_for(SITE, "gb10")
    if not down:
        return built
    return built.without(*[item.name for item in built.appliances[:down]])


class _Recording:
    """A scheduler that keeps every submission and every requeue."""

    def __init__(self, *, requeues: bool = True) -> None:
        self.submissions: list[Any] = []
        self.requeued: list[tuple[str, int]] = []
        self._requeues = requeues

    def submit(self, plan: Any, name: str = "") -> JobHandle:
        self.submissions.append((plan, name))
        return JobHandle(driver="motsognir.slurm/v1", job_id="4001")

    def poll(self, handle: JobHandle) -> JobStatus:
        del handle
        return JobStatus(state=JobState.RUNNING)

    def requeue(self, handle: JobHandle, index: int) -> JobStatus:
        if not self._requeues:
            msg = (
                "slurmrestd v0.0.40 exposes no requeue, so element retry is not "
                "available over this transport. Requeue it from REGIN with "
                "`scontrol requeue <job>_<index>`."
            )
            raise RuntimeError(msg)
        self.requeued.append((handle.job_id, index))
        return JobStatus(state=JobState.PENDING, node="dvalin")


def _request(seq: int, transition: str, **payload: Any) -> Any:
    from draupnir.worker.accepted import Request

    return Request(seq=seq, subject=NAME, transition=transition, payload=payload)


# ---------------------------------------------------------------------------
# One submission, not fifty-six
# ---------------------------------------------------------------------------


def test_fifty_six_elements_are_one_submission() -> None:
    """The acceptance criterion, and the whole point of an array.

    The scheduler holds all N and runs M of them, so utilisation does not
    depend on the control plane being awake (SAD 11.2). Fifty-six separate
    submissions would be fifty-six independent schedules, `%3` would mean
    nothing, and a control plane that stopped would stop the estate.
    """
    scheduler = _Recording()
    submitter = array_queue.Submitter(scheduler=scheduler, estate=_estate())

    outcome = array_queue.submit_array(
        _request(4, arrays.ARRAY_ACCEPTED, subjects=list(tiers.ALL), name=NAME), submitter
    )

    assert outcome.succeeded, outcome.payload
    assert len(scheduler.submissions) == 1, "fifty-six elements produced more than one submission"

    plan, _name = scheduler.submissions[0]
    assert plan.resources.array == ArraySpec(size=56, throttle=3)
    assert plan.resources.array.directive() == "0-55%3"
    assert outcome.payload["slurmArray"] == "0-55%3"
    assert outcome.payload["size"] == 56
    assert len(outcome.payload["elements"]) == 56


def test_the_elements_are_the_jurisdictions_in_order() -> None:
    """An element is for a jurisdiction, and which one is a matter of record.

    Slurm addresses elements by index, so element seventeen has to mean the
    same jurisdiction to everyone reading it -- the board, the requeue, and the
    auditor a year later.
    """
    scheduler = _Recording()
    outcome = array_queue.submit_array(
        _request(4, arrays.ARRAY_ACCEPTED, subjects=list(tiers.ALL), name=NAME),
        array_queue.Submitter(scheduler=scheduler, estate=_estate()),
    )

    subjects = [item["subject"] for item in outcome.payload["elements"]]
    assert subjects == list(tiers.ALL)
    assert outcome.payload["elements"][0]["index"] == 0


# ---------------------------------------------------------------------------
# Losing an appliance
# ---------------------------------------------------------------------------


def test_losing_an_appliance_reduces_concurrency_and_does_not_refuse() -> None:
    """`arrays.py` implemented this and nothing called it.

    An array is N independent elements: one appliance fewer is fewer at a time
    and nothing else. Refusing would idle the two that are up.
    """
    scheduler = _Recording()
    outcome = array_queue.submit_array(
        _request(4, arrays.ARRAY_ACCEPTED, subjects=list(tiers.ALL), name=NAME),
        array_queue.Submitter(scheduler=scheduler, estate=_estate(down=1)),
    )

    assert outcome.succeeded, outcome.payload
    assert outcome.payload["slurmArray"] == "0-55%2"
    assert outcome.payload["concurrency"] == 2


def test_the_same_loss_refuses_a_ring_run() -> None:
    """And that difference is the point.

    A ring job is one job that needs the whole ring: two thirds of a ring is
    not a slower ring, it is a different collective with different numerics.
    """
    with pytest.raises(PlacementError):
        place(partition=Partition.RING, estate=_estate(down=1))

    # The array over the same estate is placed, not refused. Fifty-six asked
    # for and two given: the cap is the estate, so a lost appliance is fewer at
    # a time rather than a refusal.
    placed = place(partition=Partition.ADAPTERS, estate=_estate(down=1), requested_concurrency=56)
    assert placed.concurrency == 2


def test_an_array_over_an_estate_with_nothing_available_is_refused_not_raised() -> None:
    """One array that cannot be placed must not stop the estate.

    The reason goes into the entry, where the operator who asked for it looks.
    """
    scheduler = _Recording()
    outcome = array_queue.submit_array(
        _request(4, arrays.ARRAY_ACCEPTED, subjects=list(tiers.ALL), name=NAME),
        array_queue.Submitter(scheduler=scheduler, estate=_estate(down=3)),
    )

    assert not outcome.succeeded
    assert outcome.transition == arrays.ARRAY_REFUSED
    assert scheduler.submissions == []
    assert "could not be placed" in outcome.payload["reason"]


# ---------------------------------------------------------------------------
# Requeueing one element
# ---------------------------------------------------------------------------


def _submitted(scheduler: _Recording) -> tuple[LedgerEntry, ...]:
    """A chain holding one submitted fifty-six element array."""
    outcome = array_queue.submit_array(
        _request(4, arrays.ARRAY_ACCEPTED, subjects=list(tiers.ALL), name=NAME),
        array_queue.Submitter(scheduler=scheduler, estate=_estate()),
    )
    return (
        _entry(4, arrays.ARRAY_ACCEPTED, {"name": NAME}),
        _entry(5, outcome.transition, outcome.payload),
    )


def test_requeueing_element_seventeen_touches_element_seventeen() -> None:
    """AC-F6, and the acceptance criterion.

    Through the driver's `requeue`, which is `scontrol requeue <job>_<index>`.
    A fresh submission of `--array=17` looks equivalent and is not: it produces
    a new job identifier, severing the element from its array and losing both
    the throttle and the accounting record that ties the fifty-six together.
    """
    scheduler = _Recording()
    entries = _submitted(scheduler)
    before = len(scheduler.submissions)

    outcome = array_queue.requeue_element(
        _request(6, arrays.ELEMENT_REQUEUE_ACCEPTED, index=17),
        array_queue.Submitter(scheduler=scheduler, estate=_estate(), entries=entries),
    )

    assert outcome.succeeded, outcome.payload
    assert scheduler.requeued == [("4001", 17)]
    assert len(scheduler.submissions) == before, (
        "a requeue submitted a new job, which severs the element from its array"
    )
    assert outcome.payload["elementJobId"] == "4001_17"
    assert outcome.payload["mechanism"] == "scontrol requeue"


def test_a_requeue_leaves_the_other_fifty_five_alone() -> None:
    """Read from the record: only element seventeen moved."""
    scheduler = _Recording()
    entries = _submitted(scheduler)

    outcome = array_queue.requeue_element(
        _request(6, arrays.ELEMENT_REQUEUE_ACCEPTED, index=17),
        array_queue.Submitter(scheduler=scheduler, estate=_estate(), entries=entries),
    )

    after = arrays.fold((*entries, _entry(6, outcome.transition, outcome.payload)))
    assert after is not None
    assert after.element(17).attempts == 1
    assert [item.attempts for item in after.elements if item.index != 17] == [0] * 55
    assert after.size == 56


def test_a_transport_that_cannot_requeue_refuses_and_says_what_to_run() -> None:
    """The expected outcome at Sindri, and it is a record rather than a silence.

    slurmrestd v0.0.40 exposes submit, read and cancel and no requeue. Both
    approximations are worse than a refusal, so the driver refuses -- and the
    entry carries its message, which names `scontrol requeue <job>_<index>` on
    REGIN. An operator is told what to do rather than left with a button that
    appeared to work.
    """
    scheduler = _Recording(requeues=False)
    entries = _submitted(scheduler)

    outcome = array_queue.requeue_element(
        _request(6, arrays.ELEMENT_REQUEUE_ACCEPTED, index=17),
        array_queue.Submitter(scheduler=scheduler, estate=_estate(), entries=entries),
    )

    assert not outcome.succeeded
    assert outcome.transition == arrays.ELEMENT_REQUEUE_REFUSED
    assert "scontrol requeue" in outcome.payload["reason"]
    assert scheduler.requeued == []


def test_requeueing_an_element_of_an_array_nobody_submitted_is_refused() -> None:
    """Accepted and submitted are different things, and may arrive out of order."""
    scheduler = _Recording()

    outcome = array_queue.requeue_element(
        _request(6, arrays.ELEMENT_REQUEUE_ACCEPTED, index=17),
        array_queue.Submitter(scheduler=scheduler, estate=_estate()),
    )

    assert not outcome.succeeded
    assert "submitted no array" in outcome.payload["reason"]


def test_requeueing_an_index_the_array_does_not_have_is_refused() -> None:
    """Slurm addresses elements by index, and an index it lacks addresses nothing."""
    scheduler = _Recording()
    entries = _submitted(scheduler)

    outcome = array_queue.requeue_element(
        _request(6, arrays.ELEMENT_REQUEUE_ACCEPTED, index=99),
        array_queue.Submitter(scheduler=scheduler, estate=_estate(), entries=entries),
    )

    assert not outcome.succeeded
    assert "there is no index 99" in outcome.payload["reason"]


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


def test_a_site_that_submitted_no_array_has_none() -> None:
    """The answer the old handler could not give, because it counted runs."""
    assert arrays.fold(()) is None
    assert arrays.fold((_entry(4, arrays.ARRAY_ACCEPTED, {"name": NAME}),)) is None


def test_the_record_reports_the_size_it_was_submitted_with() -> None:
    """`size == 56` regardless of how many runs exist at the site.

    That is the acceptance criterion, and it was the defect: `size` was
    `len(runs)`, so a site with sixty runs from other work reported a
    sixty-element array and one with none reported an empty one.
    """
    record = arrays.fold(_submitted(_Recording()))

    assert record is not None
    assert record.size == 56
    assert record.slurm_array == "0-55%3"
    assert record.job_id == "4001"


def test_a_later_submission_replaces_the_earlier_one() -> None:
    """And its observations start again with it.

    Applying the first array's element states to the second would report
    progress against work that has not been done.
    """
    scheduler = _Recording()
    first = _submitted(scheduler)
    second = array_queue.submit_array(
        _request(7, arrays.ARRAY_ACCEPTED, subjects=["GBR", "JAM"], name=NAME),
        array_queue.Submitter(scheduler=scheduler, estate=_estate()),
    )

    record = arrays.fold((*first, _entry(8, second.transition, second.payload)))

    assert record is not None
    assert record.size == 2
    assert [item.subject for item in record.elements] == ["GBR", "JAM"]


def test_an_element_submission_names_only_that_element() -> None:
    """`ArraySpec.indices`: a submission of part of an array, not a smaller one."""
    record = arrays.fold(_submitted(_Recording()))

    assert record is not None
    assert record.submission_for(17).directive() == "17"
    assert record.submission_for(17).size == 56, (
        "the element's submission forgot how big the array is"
    )
    assert record.submission().directive() == "0-55%3"
