"""Gate results, folded from the chain. RF-41.

`gate_result` was written by the seed and by nothing else, while three things
read it: the approval queue's evidence table, the model detail's gate list and
`/metrics`. The worker records every gate outcome in the chain -- EVALUATING's
`gate_results`, and QUANTISED->AWAITING_APPROVAL's `format_gate_results` -- so
on a seeded stack an approver read the evidence before deciding, and on an
estate the table they read was empty.

This is the fold that makes the table a projection, beside RF-33's releases.
Like them it is pure: the same entries in the same order give the same rows,
and it never reads the table it produces.

**What a row is.** An outcome the entry recorded in full: a numeric value, a
pass or fail, the suite version that measured it, and the baseline and margin
it was judged against -- which a gate with an absolute threshold records as
null, and which are therefore required to be *recorded*, not to be non-null. An
outcome recording less than that is not a row: a row is a statement of what
was measured, and one completed with a guess would be evidence nobody took.

**Which outcome a row holds.** The table keeps one row per run, gate and suite
version. Two rules decide between outcomes that share a key:

- the latest entry wins, because a run requeued on a failing gate is evaluated
  again, and the evaluation an approver acts on is the last one;
- within one entry that re-gated several formats, the weakest outcome wins --
  a failing one before a passing one, then the smallest margin -- because the
  row is what an approver reads, and a table that showed the best of three
  formats would hide the one that nearly failed.

A merge sweep's per-point evidence is not folded: those are candidates, and the
run's own evaluation is the chosen point's re-gate, which the next entries
record.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from draupnir.core.domain.ledger import LedgerEntry

#: The payload keys a gate outcome is recorded under.
SINGLE: Final = "gate_results"
PER_FORMAT: Final = "format_gate_results"

#: Where a projected row's identifier comes from, so a rebuild reproduces it.
_NAMESPACE: Final = uuid.UUID("5f0c2f3e-6f3a-4d1e-9a53-2a1e7c4b9d41")


@dataclass(frozen=True, slots=True)
class ProjectedGateResult:
    """One gate's outcome for one run, as the entry recorded it."""

    id: UUID
    run_id: UUID
    gate: str
    suite_version: str
    value: float
    baseline_value: float | None
    margin: float | None
    passed: bool
    evaluated_at: datetime


def concerns(entry: LedgerEntry) -> bool:
    """Whether an entry records a gate outcome."""
    payload = entry.payload if isinstance(entry.payload, Mapping) else {}
    return SINGLE in payload or PER_FORMAT in payload


def fold(entries: Iterable[LedgerEntry]) -> tuple[ProjectedGateResult, ...]:
    """Fold one site's chain, oldest first, into its gate results."""
    rows: dict[tuple[UUID, str, str], ProjectedGateResult] = {}
    for entry in sorted(entries, key=lambda item: item.seq):
        if entry.subject_type != "run":
            continue
        run_id = _uuid(entry.subject_id)
        payload = entry.payload if isinstance(entry.payload, Mapping) else {}
        if run_id is None:
            continue

        evaluations: list[Mapping[str, Any]] = []
        single = payload.get(SINGLE)
        if isinstance(single, Mapping):
            evaluations.append(single)
        per_format = payload.get(PER_FORMAT)
        if isinstance(per_format, Mapping):
            evaluations.extend(item for item in per_format.values() if isinstance(item, Mapping))

        # The weakest outcome per gate within this entry, then latest-wins
        # across entries by assignment.
        weakest: dict[tuple[UUID, str, str], ProjectedGateResult] = {}
        for evaluation in evaluations:
            for row in _outcomes(entry, run_id, evaluation):
                key = (row.run_id, row.gate, row.suite_version)
                held = weakest.get(key)
                if held is None or _weakness(row) < _weakness(held):
                    weakest[key] = row
        rows.update(weakest)

    return tuple(rows.values())


def _outcomes(
    entry: LedgerEntry, run_id: UUID, evaluation: Mapping[str, Any]
) -> Iterable[ProjectedGateResult]:
    suite_version = evaluation.get("suiteVersion") or evaluation.get("suite_version")
    gates = evaluation.get("gates")
    if not isinstance(suite_version, str) or not suite_version or not isinstance(gates, Mapping):
        return
    evaluated_at = _instant(evaluation.get("evaluatedAt") or evaluation.get("evaluated_at"))
    for gate, recorded in gates.items():
        if not isinstance(recorded, Mapping):
            continue
        if "baseline" not in recorded or "margin" not in recorded:
            continue
        value = _number(recorded.get("value"))
        passed = recorded.get("passed")
        if value is None or not isinstance(passed, bool):
            continue
        yield ProjectedGateResult(
            id=uuid.uuid5(_NAMESPACE, f"{entry.site_id}:{run_id}:{gate}:{suite_version}"),
            run_id=run_id,
            gate=str(gate),
            suite_version=suite_version,
            value=value,
            baseline_value=_number(recorded.get("baseline")),
            margin=_number(recorded.get("margin")),
            passed=passed,
            evaluated_at=evaluated_at or entry.ts,
        )


def _weakness(row: ProjectedGateResult) -> tuple[bool, float]:
    """Order outcomes weakest first: failing before passing, then smallest margin."""
    return (row.passed, row.margin if row.margin is not None else math.inf)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _instant(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        found = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return found if found.tzinfo is not None else None


def _uuid(value: str) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


__all__ = ["PER_FORMAT", "SINGLE", "ProjectedGateResult", "concerns", "fold"]
