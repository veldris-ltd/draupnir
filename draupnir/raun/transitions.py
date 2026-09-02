"""Turning evidence into the facts the state machine's guards ask for.

The guards of SAD 6.1 are in the core and stay there. RAUN does not decide
whether a run advances; it reports what it measured, in the vocabulary the
guard expects, and the guard decides. This module is that translation and
nothing else -- no threshold, no budget arithmetic, no verdict.

Keeping it here rather than at the call site matters for AC-F9. "Every
quantised output is automatically re-gated. There is no path from quantisation
to approval that skips evaluation" is a claim about a set: every format that
was built must appear in the evidence. `quantisation_facts` is where the built
formats and the evidenced formats are reconciled, so a format that was quietly
not evaluated shows up as a failing format rather than as an absence nobody
counted.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from draupnir.core.domain.evidence import Evidence, EvidenceLog


class TransitionFactError(Exception):
    """Raised when evidence cannot be rendered into guard facts."""


def evaluation_facts(evidence: Evidence, *, retry_budget_remaining: int) -> dict[str, Any]:
    """Facts for EVALUATING to MERGED, and for EVALUATING back to QUEUED.

    Both guards read `failing_gates`, so one set of facts serves the pass and
    the requeue. AC-F7 is the requeue path: a failure returns the run to QUEUED
    while budget remains, and the budget is the core's arithmetic, not RAUN's.
    """
    return {
        "suite_version": evidence.suite_version,
        "failing_gates": list(evidence.failing),
        "retry_budget_remaining": retry_budget_remaining,
        "artefact_sha256": evidence.artefact_sha256,
        "baseline_reference": evidence.baseline_sha256,
        "requeue_reason": (
            f"gate(s) {', '.join(evidence.failing)} failed" if evidence.failing else ""
        ),
        **evidence.as_payload(),
    }


def merge_facts(evidence: Evidence, *, merge_config_hash: str, sweep_result: Any) -> dict[str, Any]:
    """Facts for MERGED to QUANTISED: the re-gate of the merged artefact."""
    return {
        "failing_gates": list(evidence.failing),
        "merge_config_hash": merge_config_hash,
        "sweep_result": sweep_result,
        "artefact_sha256": evidence.artefact_sha256,
    }


def quantisation_facts(log: EvidenceLog, *, built_formats: Iterable[str]) -> dict[str, Any]:
    """Facts for QUANTISED to AWAITING_APPROVAL. AC-F9.

    A format that was built and has no evidence is reported as failing, not
    omitted. The difference matters: an omitted format is invisible to the
    guard, which would then approve a set of builds that is smaller than the
    set that exists -- which is exactly the path from quantisation to approval
    that skips evaluation.
    """
    built = tuple(sorted(set(built_formats)))
    if not built:
        msg = (
            "no quantised build was named. The guard needs the set that was built "
            "in order to check that every one of them was evaluated; an empty set "
            "would pass vacuously."
        )
        raise TransitionFactError(msg)

    evidenced = {item.format: item for item in log.of_kind("quantised") if item.format is not None}

    failing: list[str] = []
    for name in built:
        result = evidenced.get(name)
        if result is None or not result.passed:
            failing.append(name)

    return {
        "formats_regated": list(built),
        "formats_failing": failing,
        "formats_unevaluated": [name for name in built if name not in evidenced],
        "artefactShas": {name: item.artefact_sha256 for name, item in sorted(evidenced.items())},
    }


def approval_facts(
    log: EvidenceLog, *, release_sha256: str, approver: str, signature: str
) -> dict[str, Any]:
    """Facts for AWAITING_APPROVAL to RELEASED.

    Carries the gated hash so that the approval itself records which bytes were
    approved. An approval that names only a run is an approval that survives
    the artefact being rebuilt underneath it.
    """
    return {
        "approver_identity": approver,
        "signature": signature,
        "artefact_sha256": release_sha256,
        "gated": log.for_artefact(release_sha256) is not None,
    }
