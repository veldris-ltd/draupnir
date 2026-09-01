"""Projection of the ledger into the run registry.

SAD 11B places the projector in the domain, beside the ledger writer and the
run registry, and this is why: projection is a pure fold. Given the same
entries in the same order it produces the same rows, on any machine, at any
time. That property is what makes `rebuild_projection` safe to run against a
live system, and what makes "the ledger is the source of truth" a fact rather
than an aspiration.

Everything the projector needs is in the entry. It never reads the table it is
about to write, so a rebuild from sequence 1 and an incremental projection of
the next entry follow exactly the same code path.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from draupnir.core.domain.ledger import LedgerEntry
from draupnir.core.domain.states import TERMINAL_STATES, RunState, Transition, find

#: The transition recorded when a run first appears. It has no source state,
#: so it is spelled with an empty left hand side and is the one entry the
#: transition table of SAD 6.1 does not describe: the table governs movement
#: between states, not the act of coming into existence.
REGISTRATION = "->DRAFT"

#: The subject type the run projection consumes. Entries about sites, plug-ins
#: and artefacts pass through untouched.
RUN_SUBJECT = "run"


class ProjectionError(Exception):
    """Raised when the ledger cannot be projected.

    A projection failure is a statement about the ledger, not about the
    projection: it means the chain records something the state machine says
    cannot have happened.
    """


@dataclass(frozen=True, slots=True)
class ProjectedRun:
    """One row of the run registry, derived entirely from the chain."""

    id: str
    site_id: str
    name: str
    spec_hash: str
    kind: str
    state: RunState
    started_at: datetime | None = None
    ended_at: datetime | None = None
    scheduler_job_id: str | None = None
    node: str | None = None
    retry_count: int = 0
    #: The sequence number of the entry that last moved this run. Not
    #: persisted; used to make the fold's progress observable in tests.
    last_seq: int = 0


def _registration(entry: LedgerEntry, payload: Mapping[str, Any]) -> ProjectedRun:
    missing = [field for field in ("name", "spec_hash", "kind") if not payload.get(field)]
    if missing:
        msg = (
            f"the registration entry for run {entry.subject_id} at seq {entry.seq} "
            f"omits {', '.join(missing)}; a run cannot be projected without them"
        )
        raise ProjectionError(msg)
    return ProjectedRun(
        id=entry.subject_id,
        site_id=entry.site_id,
        name=str(payload["name"]),
        spec_hash=str(payload["spec_hash"]),
        kind=str(payload["kind"]),
        state=RunState.DRAFT,
        last_seq=entry.seq,
    )


def _advance(
    run: ProjectedRun, entry: LedgerEntry, transition: Transition, payload: Mapping[str, Any]
) -> ProjectedRun:
    """Apply one transition to a projected run."""
    changes: dict[str, Any] = {"state": transition.target, "last_seq": entry.seq}

    # A run starts when the scheduler places it, and ends when it reaches a
    # state it cannot leave. Both are read off the transition rather than
    # inferred from the timestamps, so the two never disagree.
    if transition.target is RunState.TRAINING:
        changes["started_at"] = entry.ts
        changes["scheduler_job_id"] = _text(payload.get("scheduler_job_id"))
        changes["node"] = _text(payload.get("node"))
    if transition.target in TERMINAL_STATES:
        changes["ended_at"] = entry.ts

    # SAD 6.1: EVALUATING -> QUEUED is the requeue. It is the only transition
    # that spends retry budget, so it is the only one that counts.
    if transition.source is RunState.EVALUATING and transition.target is RunState.QUEUED:
        changes["retry_count"] = run.retry_count + 1

    return replace(run, **changes)


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def project(entries: Iterable[LedgerEntry]) -> dict[str, ProjectedRun]:
    """Fold a site's chain into its run registry.

    Entries must arrive in sequence order; the caller has already verified the
    chain, so this does not re-verify it.
    """
    runs: dict[str, ProjectedRun] = {}

    for entry in entries:
        if entry.subject_type != RUN_SUBJECT:
            continue

        payload = entry.payload if isinstance(entry.payload, Mapping) else {}

        if entry.transition == REGISTRATION:
            if entry.subject_id in runs:
                msg = f"run {entry.subject_id} is registered twice, at seq {entry.seq}"
                raise ProjectionError(msg)
            runs[entry.subject_id] = _registration(entry, payload)
            continue

        run = runs.get(entry.subject_id)
        if run is None:
            msg = f"seq {entry.seq} moves run {entry.subject_id}, which the chain never registered"
            raise ProjectionError(msg)

        source, _, target = entry.transition.partition("->")
        transition = find(RunState(source), RunState(target)) if source and target else None
        if transition is None:
            msg = (
                f"seq {entry.seq} records {entry.transition!r}, which is not a "
                "transition in SAD 6.1"
            )
            raise ProjectionError(msg)
        if run.state is not transition.source:
            msg = (
                f"seq {entry.seq} applies {entry.transition!r} to run "
                f"{entry.subject_id}, which is in {run.state}"
            )
            raise ProjectionError(msg)

        runs[entry.subject_id] = _advance(run, entry, transition, payload)

    return runs


def as_rows(runs: Iterable[ProjectedRun]) -> list[dict[str, Any]]:
    """Render projected runs as table rows, ordered so a rebuild is comparable.

    Sorted by identifier rather than by insertion, so that two rebuilds of the
    same chain produce not merely equal contents but the same sequence of rows.
    """
    return [
        {
            "id": run.id,
            "site_id": run.site_id,
            "name": run.name,
            "spec_hash": run.spec_hash,
            "kind": run.kind,
            "state": str(run.state),
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "scheduler_job_id": run.scheduler_job_id,
            "node": run.node,
            "retry_count": run.retry_count,
        }
        for run in sorted(runs, key=lambda item: item.id)
    ]
