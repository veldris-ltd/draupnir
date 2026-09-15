"""The fold that projects `gate_result` from the chain. RF-41.

`gate_result` was written by the seed and by nothing else, so an estate's
approval queue showed an empty evidence table. These tests pin what the fold
makes of the outcomes the worker records, and what it refuses to complete.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from draupnir.core.domain import gate_results
from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry

pytestmark = pytest.mark.unit

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
RUN = uuid.UUID("019cf270-ba80-76c9-84ca-7374e16c7633")
SUITE = "raun-suite/2026.02"


def entry(seq: int, transition: str, payload: dict[str, Any]) -> LedgerEntry:
    return LedgerEntry(
        id=uuid.uuid4(),
        site_id="sindri",
        seq=seq,
        prev_hash=GENESIS_HASH,
        entry_hash="b" * 64,
        ts=AT + timedelta(minutes=seq),
        actor="system:worker",
        subject_type="run",
        subject_id=str(RUN),
        transition=transition,
        payload=payload,
    )


def evidence(
    gates: dict[str, tuple[float, float | None, float | None, bool]],
    *,
    suite_version: str = SUITE,
    evaluated_at: str = "2026-03-02T08:00:00+00:00",
) -> dict[str, Any]:
    """An `Evidence.as_payload()`, as the worker records one."""
    return {
        "artefactSha256": "a" * 64,
        "artefactKind": "adapter",
        "suite": "general-core",
        "suiteVersion": suite_version,
        "evaluatedAt": evaluated_at,
        "passed": all(passed for _, _, _, passed in gates.values()),
        "gates": {
            gate: {"value": value, "baseline": baseline, "margin": margin, "passed": passed}
            for gate, (value, baseline, margin, passed) in gates.items()
        },
    }


def test_the_worker_s_evaluation_becomes_one_row_per_gate() -> None:
    rows = gate_results.fold(
        [
            entry(
                1,
                "EVALUATING->MERGED",
                {
                    "gate_results": evidence(
                        {"E1": (0.74, 0.72, 0.02, True), "E2": (0.61, None, None, True)}
                    )
                },
            )
        ]
    )

    by_gate = {row.gate: row for row in rows}
    assert set(by_gate) == {"E1", "E2"}
    assert by_gate["E1"].value == pytest.approx(0.74)
    assert by_gate["E1"].baseline_value == pytest.approx(0.72)
    assert by_gate["E1"].margin == pytest.approx(0.02)
    assert by_gate["E1"].suite_version == SUITE
    assert by_gate["E1"].evaluated_at == datetime(2026, 3, 2, 8, 0, tzinfo=UTC)
    # An absolute gate records no baseline: recorded as null, and still a row.
    assert by_gate["E2"].baseline_value is None
    assert by_gate["E2"].run_id == RUN


def test_an_outcome_recording_less_than_the_measurement_is_not_a_row() -> None:
    """The seed's `{"passed": True}` was a pass nobody measured."""
    unmeasured = entry(1, "EVALUATING->MERGED", {"gate_results": {"E1": {"passed": True}}})
    no_baseline_key = entry(
        2,
        "EVALUATING->MERGED",
        {
            "gate_results": {
                "suiteVersion": SUITE,
                "gates": {"E1": {"value": 0.7, "margin": 0.01, "passed": True}},
            }
        },
    )
    no_suite = entry(
        3,
        "EVALUATING->MERGED",
        {"gate_results": evidence({"E1": (0.74, 0.72, 0.02, True)}, suite_version="")},
    )

    assert gate_results.fold([unmeasured, no_baseline_key, no_suite]) == ()


def test_the_latest_evaluation_of_a_gate_wins() -> None:
    """A run requeued on a failing gate is evaluated again; the last one is what counts."""
    failed = entry(
        1, "EVALUATING->QUEUED", {"gate_results": evidence({"E3": (0.60, 0.72, -0.12, False)})}
    )
    passed = entry(
        2, "EVALUATING->MERGED", {"gate_results": evidence({"E3": (0.75, 0.72, 0.03, True)})}
    )

    (row,) = gate_results.fold([passed, failed])

    assert row.passed is True
    assert row.margin == pytest.approx(0.03)


def test_the_weakest_format_holds_the_row_for_a_re_gate() -> None:
    """An approver reads the row; the best of three formats would hide the near miss."""
    regate = entry(
        1,
        "QUANTISED->AWAITING_APPROVAL",
        {
            "format_gate_results": {
                "nvfp4": evidence({"E1": (0.80, 0.72, 0.08, True)}),
                "gguf-q4km": evidence({"E1": (0.73, 0.72, 0.01, True)}),
                "mlx4": evidence({"E1": (0.78, 0.72, 0.06, True)}),
            }
        },
    )

    (row,) = gate_results.fold([regate])

    assert row.margin == pytest.approx(0.01)


def test_a_failing_format_holds_the_row_over_any_passing_one() -> None:
    regate = entry(
        1,
        "QUANTISED->AWAITING_APPROVAL",
        {
            "format_gate_results": {
                "nvfp4": evidence({"E1": (0.70, 0.72, -0.02, False)}),
                "mlx4": evidence({"E1": (0.60, 0.72, -0.12, True)}),
            }
        },
    )

    (row,) = gate_results.fold([regate])

    assert row.passed is False


def test_a_rebuild_gives_every_row_the_identifier_it_had() -> None:
    chain = [
        entry(1, "EVALUATING->MERGED", {"gate_results": evidence({"E1": (0.74, 0.72, 0.02, True)})})
    ]

    assert [row.id for row in gate_results.fold(chain)] == [
        row.id for row in gate_results.fold(chain)
    ]


def test_only_entries_that_record_gates_concern_the_projection() -> None:
    assert gate_results.concerns(entry(1, "EVALUATING->MERGED", {"gate_results": {}}))
    assert gate_results.concerns(
        entry(2, "QUANTISED->AWAITING_APPROVAL", {"format_gate_results": {}})
    )
    assert not gate_results.concerns(entry(3, "TRAINED->EVALUATING", {"suite_version": SUITE}))
