"""A corpus's licence decision, as the worker and the procedure both take it. RF-43.

These pin what `gleipnir.clearance.decide` makes of each combination of
decisions, through GLEIPNIR's own licence policy presented as the driver a
deployment installs, and through a driver whose verdicts a test chooses.
"""

from __future__ import annotations

from typing import Any

import pytest

from draupnir.core.domain.states import RunState
from draupnir.gleipnir import clearance
from draupnir.gleipnir.licence import CURRENT, driver
from draupnir.interfaces.types import PolicyDecision, Verdict

pytestmark = pytest.mark.unit


def source(
    licence: str, *, url: str = "https://legislation.gov.uk", **facts: Any
) -> dict[str, Any]:
    """A source as the licence register renders it for a policy."""
    return {
        "id": f"source-{licence}",
        "url": url,
        "licenceSpdx": licence,
        "attributionRequired": False,
        "personalData": False,
        **facts,
    }


BASE = {
    "id": "MIDGARD-CORE-QWEN36-35B-A3B-v1.0",
    "url": "hodd://sindri/models/core/MIDGARD-CORE-QWEN36-35B-A3B-v1.0",
    "licenceSpdx": "Apache-2.0",
    "attributionRequired": False,
    "personalData": False,
}


class Chosen:
    """A driver that returns the verdict a test names, for each licence."""

    name = "test.chosen/v1"
    capabilities = frozenset({"licence"})
    policy_version = "chosen/2026.09"

    def __init__(self, verdicts: dict[str, Verdict]) -> None:
        self.verdicts = verdicts

    def evaluate(self, subject: dict[str, object]) -> PolicyDecision:
        verdict = self.verdicts.get(str(subject.get("licenceSpdx")), Verdict.REFUSE)
        return PolicyDecision(verdict, self.policy_version, rule=f"rule-{verdict}", reason="chosen")


def test_every_subject_permitted_clears_the_corpus_recording_each_decision() -> None:
    cleared = clearance.decide([source("OGL-UK-3.0"), source("CC0-1.0")], BASE, driver)

    assert cleared.target is RunState.LICENCE_CLEARED
    assert cleared.facts == {"sources_failing_policy": [], "base_model_cleared": True}
    assert cleared.payload["policy_version"] == CURRENT.version
    assert cleared.payload["evaluation_result"] == "PASS"
    recorded = cleared.payload["decisions"]
    assert [item["subject"] for item in recorded] == ["source", "source", "base_model"]
    assert {item["policyVersion"] for item in recorded} == {CURRENT.version}
    assert all(item["verdict"] == str(Verdict.PERMIT) for item in recorded)


def test_a_refused_source_quarantines_the_corpus_and_names_the_rule() -> None:
    """AC-S2: the failing rule is named."""
    refused = clearance.decide(
        [source("OGL-UK-3.0"), source("CC-BY-NC-4.0", url="https://example.invalid/nc")],
        BASE,
        driver,
    )

    assert refused.target is RunState.QUARANTINED
    assert refused.payload["failing_source"] == "https://example.invalid/nc"
    assert refused.payload["rule"] == "licence-refused"
    assert refused.payload["actor"] == clearance.ACTOR
    assert refused.payload["policy_version"] == CURRENT.version
    assert refused.facts["sources_failing_policy"] == [
        "https://example.invalid/nc: CC-BY-NC-4.0 (licence-refused)"
    ]


def test_a_licence_no_rule_matches_is_refused_by_default() -> None:
    refused = clearance.decide([source("LicenseRef-Proprietary-Unclear")], BASE, driver)
    assert refused.target is RunState.QUARANTINED
    assert refused.payload["rule"] == "default"


def test_a_refused_base_model_quarantines_the_corpus() -> None:
    gemma = {**BASE, "id": "gemma", "url": "hodd://gemma", "licenceSpdx": "LicenseRef-Gemma"}
    refused = clearance.decide([source("OGL-UK-3.0")], gemma, driver)

    assert refused.target is RunState.QUARANTINED
    assert refused.payload["failing_source"] == "hodd://gemma"
    assert refused.payload["decisions"][-1]["subject"] == "base_model"


def test_a_source_requiring_an_approval_leaves_the_corpus_waiting_and_records_nothing() -> None:
    waiting = clearance.decide(
        [source("OGL-UK-3.0", personalData=True, dpiaRef="DPIA-1")], BASE, driver
    )

    assert waiting.target is None
    assert "requires an approval" in waiting.detail
    assert waiting.payload == {}
    assert waiting.decisions[0].rule == "personal-data-requires-approval"


def test_a_refusal_wins_over_an_approval_still_owed() -> None:
    chosen = Chosen(
        {"A": Verdict.REQUIRES_APPROVAL, "B": Verdict.REFUSE, "Apache-2.0": Verdict.PERMIT}
    )
    decided = clearance.decide([source("A"), source("B", url="https://b")], BASE, chosen)

    assert decided.target is RunState.QUARANTINED
    assert decided.payload["failing_source"] == "https://b"
    assert decided.payload["policy_version"] == "chosen/2026.09"


def test_a_verdict_this_does_not_recognise_is_a_refusal() -> None:
    class Unknown(Chosen):
        def evaluate(self, subject: dict[str, object]) -> PolicyDecision:
            # A driver on a newer interface, answering in a word this one lacks.
            return PolicyDecision("ABSTAIN", self.policy_version, rule=None)  # type: ignore[arg-type]

    decided = clearance.decide([source("OGL-UK-3.0")], BASE, Unknown({}))
    assert decided.target is RunState.QUARANTINED
    assert decided.payload["rule"] == "default"


def test_an_undeclared_base_model_is_not_permitted_by_its_absence() -> None:
    waiting = clearance.decide([source("OGL-UK-3.0")], None, driver)

    assert waiting.target is None
    assert "no licence is declared for the run's base model" in waiting.detail


def test_no_registered_source_is_nothing_to_judge() -> None:
    waiting = clearance.decide([], BASE, driver)
    assert waiting.target is None
    assert waiting.decisions == ()
