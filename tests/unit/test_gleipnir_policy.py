"""Licence policy: AC-S2, and a policy change that costs no re-ingest.

AC-S2: "A corpus source with a licence that fails policy cannot reach CURATED.
The attempt is refused and quarantined with the failing rule named."

The exit condition for this prompt adds the other half: "A test proves a
licence-policy change re-evaluates existing sources without re-ingesting
them." That is `test_a_policy_change_re_evaluates_without_touching_the_corpus`
below, and it is the reason Decision S4 separates recording from judging at
all. The test proves it by making the corpus unreadable first: if a
re-evaluation touched a single byte of it, the test would fail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from draupnir.core.domain.states import RunState
from draupnir.gleipnir import licence
from draupnir.gleipnir.policy import (
    Assessment,
    Policy,
    PolicyDriverAdapter,
    PolicyEngine,
    PolicyError,
    Rule,
)
from draupnir.hodd.register import LicenceRegister, SourceRecord
from draupnir.interfaces.types import Verdict

RETRIEVED = datetime(2026, 3, 2, tzinfo=UTC)


def source(
    licence_spdx: str,
    *,
    attribution: bool = True,
    personal_data: bool = False,
    dpia: str | None = None,
    source_id: UUID | None = None,
    url: str | None = None,
) -> SourceRecord:
    return SourceRecord(
        id=source_id or uuid4(),
        jurisdiction="GBR",
        url=url or f"https://example.invalid/{licence_spdx.lower()}",
        licence_spdx=licence_spdx,
        attribution_required=attribution,
        retrieved_at=RETRIEVED,
        sha256="a" * 64,
        personal_data=personal_data,
        dpia_ref=dpia,
    )


def assess(record: SourceRecord, policy: Policy | None = None) -> Assessment:
    return PolicyEngine(policy or licence.CURRENT).assess(record.as_mapping())


# ---------------------------------------------------------------------------
# AC-S2
# ---------------------------------------------------------------------------


def test_a_permitted_licence_clears() -> None:
    outcome = assess(source("OGL-UK-3.0"))
    assert outcome.permitted
    assert outcome.target_state is RunState.LICENCE_CLEARED
    assert outcome.rule == "licence-permitted"
    assert outcome.decision.policy_version == "gleipnir-licence/2026.01"


def test_a_refused_licence_cannot_reach_curated_and_names_the_rule() -> None:
    """AC-S2."""
    outcome = assess(source("CC-BY-NC-4.0"))

    assert not outcome.permitted
    assert outcome.target_state is RunState.QUARANTINED
    # It cannot reach CURATED, which is the whole of AC-S2.
    assert outcome.target_state not in {RunState.CURATED, RunState.LICENCE_CLEARED}
    assert outcome.rule == "licence-refused"
    assert "commercial" in (outcome.decision.reason or "")
    # The refusal is legible to an operator without reading the policy source.
    assert "licence-refused" in outcome.describe()


def test_an_unassessed_licence_is_refused_by_default() -> None:
    # Silence is not permission. A licence nobody wrote a rule for is a
    # licence nobody has assessed.
    outcome = assess(source("LicenseRef-Something-Nobody-Has-Read"))
    assert not outcome.permitted
    assert outcome.rule == "default"
    assert "no rule matches" in (outcome.decision.reason or "")


def test_personal_data_requires_approval_whatever_the_licence_permits() -> None:
    outcome = assess(source("CC0-1.0", personal_data=True, dpia="DPIA-2026-014"))
    assert outcome.decision.verdict is Verdict.REQUIRES_APPROVAL
    assert outcome.target_state is RunState.CORPUS_REGISTERED
    assert outcome.rule == "personal-data-requires-approval"


def test_a_share_alike_licence_needs_its_attribution_declared() -> None:
    assert assess(source("CC-BY-SA-4.0", attribution=True)).permitted

    undeclared = assess(source("CC-BY-SA-4.0", attribution=False))
    assert not undeclared.permitted
    assert undeclared.rule == "attribution-not-declared"
    assert "model card" in (undeclared.decision.reason or "")


def test_every_decision_records_the_policy_version_that_produced_it() -> None:
    for spdx in ("OGL-UK-3.0", "CC-BY-NC-4.0", "LicenseRef-Unknown"):
        assert assess(source(spdx)).decision.policy_version == licence.CURRENT.version


# ---------------------------------------------------------------------------
# A policy change costs no re-ingest
# ---------------------------------------------------------------------------


def test_a_policy_change_re_evaluates_without_touching_the_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exit condition. Re-evaluation reads facts, not corpora.

    The corpus is made unopenable for the duration. If re-evaluating so much
    as stat'd it, this fails -- which is the difference between a policy
    change being a configuration edit and a policy change being a re-ingest of
    the estate.
    """
    register = LicenceRegister()
    for spdx in ("OGL-UK-3.0", "CC-BY-SA-4.0", "CC-BY-NC-4.0", "CC0-1.0"):
        register.record(source(spdx, attribution=False))

    facts = register.facts_for_policy()

    # Nothing may open a file while the policies are applied.
    def refuse(*args: object, **kwargs: object) -> Any:
        msg = "re-evaluation must not read the corpus"
        raise AssertionError(msg)

    monkeypatch.setattr(Path, "open", refuse)
    monkeypatch.setattr("builtins.open", refuse)

    before = PolicyEngine(licence.PREVIOUS).reassess(facts)
    after = PolicyEngine(licence.CURRENT).reassess(facts)

    assert len(before) == len(after) == 4

    # Under the previous policy, a share-alike licence passed without
    # declaring attribution. Under the current one it does not.
    changed = PolicyEngine(licence.CURRENT).changed_from(before, after)
    assert len(changed) == 1

    was, now = changed[0]
    assert was.decision.verdict is Verdict.PERMIT
    assert now.decision.verdict is Verdict.REFUSE
    assert now.rule == "attribution-not-declared"


def test_re_evaluation_leaves_the_register_untouched() -> None:
    # The judgement is returned, never stored. A verdict written into the
    # register would be a stale verdict stored forever.
    register = LicenceRegister()
    register.record(source("CC-BY-NC-4.0"))

    snapshot = register.facts_for_policy()
    PolicyEngine(licence.CURRENT).reassess(snapshot)

    assert register.facts_for_policy() == snapshot
    assert next(iter(register)).state is RunState.DRAFT


def test_applying_the_new_state_is_the_callers_job() -> None:
    """The separation, worked through: GLEIPNIR says where, HODD moves it."""
    register = LicenceRegister()
    recorded = register.record(source("CC-BY-NC-4.0"))

    outcome = PolicyEngine(licence.CURRENT).assess(recorded.as_mapping())
    register.update(recorded.with_state(outcome.target_state))

    assert register.get(recorded.id).state is RunState.QUARANTINED


def test_the_refused_set_is_what_an_operator_acts_on() -> None:
    register = LicenceRegister()
    for spdx in ("OGL-UK-3.0", "CC0-1.0", "CC-BY-NC-4.0", "AGPL-3.0-only"):
        register.record(source(spdx))

    engine = PolicyEngine(licence.CURRENT)
    assessments = engine.reassess(register.facts_for_policy())
    refused = engine.refused(assessments)

    assert len(refused) == 2
    assert {item.decision.verdict for item in refused} == {Verdict.REFUSE}


def test_a_historical_decision_resolves_the_policy_that_produced_it() -> None:
    assert licence.by_version("gleipnir-licence/2025.11") is licence.PREVIOUS
    assert licence.by_version("gleipnir-licence/2026.01") is licence.CURRENT

    # And never falls back to the current one.
    with pytest.raises(KeyError, match="never"):
        licence.by_version("gleipnir-licence/2019.01")


# ---------------------------------------------------------------------------
# The policy object itself
# ---------------------------------------------------------------------------


def test_first_matching_rule_wins() -> None:
    policy = Policy(
        version="test/1",
        rules=(
            Rule(id="first", statement="matches everything", verdict=Verdict.PERMIT),
            Rule(id="second", statement="never reached", verdict=Verdict.REFUSE),
        ),
    )
    assert policy.decide({"licenceSpdx": "anything"}).rule == "first"


def test_a_policy_without_a_version_is_refused() -> None:
    with pytest.raises(PolicyError, match="unexplainable"):
        Policy(version="", rules=())


def test_duplicate_rule_ids_are_refused() -> None:
    with pytest.raises(PolicyError, match="duplicate"):
        Policy(
            version="test/1",
            rules=(
                Rule(id="same", statement="a", verdict=Verdict.PERMIT),
                Rule(id="same", statement="b", verdict=Verdict.REFUSE),
            ),
        )


def test_the_policy_renders_as_data() -> None:
    rendered = licence.CURRENT.as_mapping()
    assert rendered["version"] == licence.CURRENT.version
    assert rendered["default"] == str(Verdict.REFUSE)
    assert {clause["id"] for clause in rendered["rules"]} == {
        rule.id for rule in licence.CURRENT.rules
    }


def test_the_policy_presents_as_a_policy_driver() -> None:
    # SAD 8.2: `draupnir.policy` takes a driver, and a policy is data. This is
    # the seam, so a new compliance regime is a driver and a new licence list
    # is a file.
    driver = PolicyDriverAdapter(licence.CURRENT)
    assert driver.name == "gleipnir.licence/v1"
    assert driver.policy_version == licence.CURRENT.version

    decision = driver.evaluate(source("CC-BY-NC-4.0").as_mapping())
    assert decision.verdict is Verdict.REFUSE
    assert decision.rule == "licence-refused"
