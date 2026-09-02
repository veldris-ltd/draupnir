"""AC-S8 and threat T8: gate results bind to bytes, never to a path.

T8 is "artefact tampered between passing a gate and being released", and its
mitigation is that evidence names the hash. The first test below is the one
that keeps it true over time: it walks `Evidence`'s fields and fails if anybody
adds a location. That is how this control decays -- not by someone removing the
hash, but by someone adding a URI for a console's convenience and the next
person resolving it.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from draupnir.core.domain.evidence import (
    ArtefactMismatchError,
    Evidence,
    EvidenceError,
    EvidenceLog,
    UngatedArtefactError,
)
from draupnir.interfaces.types import GateOutcome

GATED = "a" * 64
TAMPERED = "b" * 64
BASELINE = "c" * 64
AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def outcome(gate: str, *, passed: bool = True, value: float = 0.8) -> GateOutcome:
    """One gate result."""
    return GateOutcome(
        gate=gate,
        suite_version="2026.01",
        value=value,
        baseline_value=0.75,
        margin=round(value - 0.75, 6),
        passed=passed,
    )


def evidence(
    sha256: str = GATED, *, passed: bool = True, kind: str = "merged", fmt: str | None = None
) -> Evidence:
    """Evidence for one artefact."""
    return Evidence(
        artefact_sha256=sha256,
        artefact_kind=kind,
        outcomes=(outcome("E1"), outcome("E2")),
        passed=passed,
        suite="general-core",
        suite_version="2026.01",
        evaluated_at=AT,
        baseline_sha256=BASELINE,
        format=fmt,
        measurements={"E1": 0.8, "E2": 0.82},
    )


# -- the structural control -------------------------------------------------


def test_evidence_carries_no_location() -> None:
    """Nothing on `Evidence` names where the bytes are. AC-S8, structurally.

    A path is a name for wherever the bytes happen to be now. Evidence bound to
    a path stays true after the bytes are replaced, which is precisely threat
    T8.
    """
    forbidden = ("uri", "path", "url", "bucket", "key", "location", "filename", "prefix")

    for field in dataclasses.fields(Evidence):
        lowered = field.name.lower()
        assert not any(word in lowered for word in forbidden), (
            f"Evidence.{field.name} names a location. Gate results bind to the "
            "artefact hash, not its path (AC-S8, threat T8). Whatever this field "
            "was for, resolving it instead of the hash is the failure the control "
            "exists to prevent."
        )


def test_evidence_binds_to_exactly_one_set_of_bytes() -> None:
    result = evidence()

    assert result.binds(GATED)
    assert not result.binds(TAMPERED)


def test_a_malformed_hash_is_refused() -> None:
    """Evidence bound to a malformed hash binds to nothing and fails open."""
    with pytest.raises(EvidenceError, match="is not a SHA-256"):
        Evidence(
            artefact_sha256="not-a-hash",
            artefact_kind="merged",
            outcomes=(),
            passed=True,
            suite="general-core",
            suite_version="2026.01",
            evaluated_at=AT,
        )


def test_an_unknown_artefact_kind_is_refused() -> None:
    with pytest.raises(EvidenceError, match="not an evaluable artefact kind"):
        Evidence(
            artefact_sha256=GATED,
            artefact_kind="corpus_raw",
            outcomes=(),
            passed=True,
            suite="general-core",
            suite_version="2026.01",
            evaluated_at=AT,
        )


def test_a_naive_timestamp_is_refused() -> None:
    """SAD 11E.2."""
    with pytest.raises(EvidenceError, match="explicit offset"):
        Evidence(
            artefact_sha256=GATED,
            artefact_kind="merged",
            outcomes=(),
            passed=True,
            suite="general-core",
            suite_version="2026.01",
            evaluated_at=datetime(2026, 3, 2, 9, 0),  # noqa: DTZ001
        )


# -- AC-S8: the refusal -----------------------------------------------------


def test_verifying_the_gated_artefact_passes() -> None:
    evidence().verify(GATED)


def test_verifying_a_tampered_artefact_raises_and_names_both_hashes() -> None:
    """AC-S8: modifying an artefact after its gates pass fails re-verification."""
    with pytest.raises(ArtefactMismatchError) as raised:
        evidence().verify(TAMPERED)

    assert raised.value.expected == GATED
    assert raised.value.observed == TAMPERED
    assert "is not the one the gates passed" in str(raised.value)
    assert "Re-gate it" in str(raised.value)


def test_the_message_distinguishes_tampering_from_a_legitimate_rebuild() -> None:
    """An operator seeing this needs to know which of the two it is."""
    message = str(ArtefactMismatchError(GATED, TAMPERED, "quantised"))

    assert "modified since evaluation" in message
    assert "different build that has never been evaluated" in message


# -- the log ----------------------------------------------------------------


def test_the_log_answers_what_is_known_about_these_bytes() -> None:
    log = EvidenceLog().with_evidence(evidence())

    assert log.for_artefact(GATED) is not None
    assert log.for_artefact(TAMPERED) is None


def test_re_evaluating_the_same_bytes_replaces_rather_than_accumulates() -> None:
    """The question "what do we know about these bytes" keeps one answer."""
    first = evidence()
    second = dataclasses.replace(first, evaluated_at=AT + timedelta(hours=1), passed=False)

    log = EvidenceLog().with_evidence(first).with_evidence(second)

    assert len(log.entries) == 1
    assert log.require(GATED).passed is False


def test_requiring_evidence_that_does_not_exist_raises() -> None:
    """An artefact with no evidence has bypassed evaluation, not merely failed."""
    with pytest.raises(UngatedArtefactError, match="bypassed evaluation"):
        EvidenceLog().require(GATED, "quantised")


def test_the_log_reports_which_artefacts_failed() -> None:
    log = (
        EvidenceLog()
        .with_evidence(evidence(GATED, passed=True))
        .with_evidence(evidence(TAMPERED, passed=False))
    )

    assert [item.artefact_sha256 for item in log.failing] == [TAMPERED]


def test_failing_gates_are_named_on_the_evidence() -> None:
    result = dataclasses.replace(
        evidence(), outcomes=(outcome("E1"), outcome("E4", passed=False)), passed=False
    )

    assert result.failing == ("E4",)


def test_the_payload_carries_the_hash_and_no_location() -> None:
    payload = evidence().as_payload()

    assert payload["artefactSha256"] == GATED
    assert payload["baselineSha256"] == BASELINE
    assert set(payload) & {"uri", "path", "location"} == set()
