"""AC-F16: the two tier lists enumerate all fifty six, validated at submission.

The acceptance criterion is a counting exercise, and the reason it is a Must is
that getting it wrong is silent. An omitted jurisdiction is a model nobody
notices is missing until a delivery review; a duplicated one is two runs
competing for one output path; a jurisdiction in both tiers trains against
whichever base the lookup happens to reach first.
"""

from __future__ import annotations

import pytest

from draupnir.hamarr import tiers
from draupnir.hamarr.tiers import BaseModel, TierError
from draupnir.interfaces.types import Tier


def test_the_two_lists_enumerate_all_fifty_six() -> None:
    """AC-F16, the whole of it."""
    result = tiers.enumeration()

    assert result.complete, result.describe()
    assert result.total == 56
    assert len(tiers.TIER_A) == 9
    assert len(tiers.TIER_B) == 47


def test_validation_passes_at_submission() -> None:
    """`validate` is what submission calls, and it does not raise here."""
    assert tiers.validate().complete


def test_the_nine_tier_a_jurisdictions_are_the_ones_the_sad_fixes() -> None:
    """The nine SAD Q2 names.

    United Kingdom, Cyprus, Malta, India, Canada, Australia, Nigeria, South
    Africa, Singapore.
    """
    assert set(tiers.TIER_A) == {
        "GBR",
        "CYP",
        "MLT",
        "IND",
        "CAN",
        "AUS",
        "NGA",
        "ZAF",
        "SGP",
    }


def test_an_omission_is_detected() -> None:
    """Fifty five jurisdictions is not the programme."""
    result = tiers.enumeration(tiers.TIER_A, tiers.TIER_B[:-1])

    assert not result.complete
    assert "55 jurisdictions, expected 56" in result.describe()


def test_a_duplicate_is_detected() -> None:
    """A jurisdiction listed twice is caught even though the count still works."""
    result = tiers.enumeration(tiers.TIER_A, (*tiers.TIER_B, "KEN", "GHA"))

    assert not result.complete
    assert result.duplicates == ("GHA", "KEN")


def test_a_jurisdiction_in_both_tiers_is_detected() -> None:
    """The failure that would otherwise pick a base by dictionary order."""
    result = tiers.enumeration((*tiers.TIER_A, "KEN"), tiers.TIER_B)

    assert not result.complete
    assert result.overlapping == ("KEN",)


def test_a_malformed_code_is_detected() -> None:
    """The map is keyed by ISO 3166-1 alpha-3, and only by that."""
    result = tiers.enumeration(tiers.TIER_A, (*tiers.TIER_B[:-1], "Kenya"))

    assert not result.complete
    assert result.malformed == ("Kenya",)


def test_validate_raises_when_the_lists_have_drifted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Submission refuses rather than assigning a base from a broken table."""
    monkeypatch.setattr(tiers, "TIER_B", tiers.TIER_B[:-1])

    with pytest.raises(TierError, match="do not enumerate CIM-56"):
        tiers.validate()


# -- base selection ---------------------------------------------------------


def test_tier_a_trains_against_the_dense_base() -> None:
    for jurisdiction in tiers.TIER_A:
        assert tiers.base_for(jurisdiction) is BaseModel.DENSE_27B


def test_tier_b_trains_against_the_mixture_of_experts_base() -> None:
    for jurisdiction in tiers.TIER_B:
        assert tiers.base_for(jurisdiction) is BaseModel.MOE_35B_A3B


def test_an_unknown_jurisdiction_raises_rather_than_defaulting() -> None:
    """There is no default tier, because a default trains a fifty seventh model."""
    with pytest.raises(TierError, match="not a CIM-56 jurisdiction"):
        tiers.tier_of("USA")

    with pytest.raises(TierError):
        tiers.base_for("USA")


def test_a_specification_declaring_the_wrong_tier_is_refused() -> None:
    """Tier drives base selection, so a wrong declaration trains a wrong base."""
    assert tiers.assert_declared_tier("GBR", Tier.A) is Tier.A

    with pytest.raises(TierError, match="GBR is Tier A"):
        tiers.assert_declared_tier("GBR", Tier.B)


def test_every_jurisdiction_resolves_to_exactly_one_tier_and_one_base() -> None:
    """The property AC-F16 exists to protect, stated directly."""
    assigned = {code: tiers.tier_of(code) for code in tiers.ALL}

    assert len(assigned) == 56
    assert set(assigned.values()) == {Tier.A, Tier.B}
    assert len(tiers.jurisdictions_in(Tier.A)) == 9
    assert len(tiers.jurisdictions_in(Tier.B)) == 47


def test_the_base_artefact_is_addressed_by_hodd_uri() -> None:
    """SAD 7.4: artefacts are addressed by URI, not by physical placement."""
    assert tiers.base_artefact("GBR") == ("hodd://sindri/models/core/MIDGARD-CORE-GEMMA3-27B-v1.0")
    assert tiers.base_artefact("KEN", site="brokkr") == (
        "hodd://brokkr/models/core/MIDGARD-CORE-QWEN36-35B-A3B-v1.0"
    )


def test_the_description_names_every_problem_it_found() -> None:
    """An operator told the table is wrong needs to be told how."""
    result = tiers.enumeration((*tiers.TIER_A, "KEN"), (*tiers.TIER_B[:-1], "Kenya"))

    described = result.describe()

    assert "in both tiers: KEN" in described
    assert "duplicated: KEN" in described
    assert "not an ISO 3166-1 alpha-3 code: Kenya" in described


def test_a_suspended_member_is_still_in_the_programme() -> None:
    """Gabon, suspended from the Councils since August 2023, still gets a model.

    A programme decision of 2 September 2026, pinned here because it is not
    derivable from the membership list and because the way it would be lost is
    somebody quietly dropping a line. Reversing it makes the programme fifty
    five and changes AC-F16; that should take an argument, not an edit.
    """
    assert "GAB" in tiers.ALL
    assert tiers.tier_of("GAB") is Tier.B
    assert tiers.base_for("GAB") is BaseModel.MOE_35B_A3B
    assert tiers.EXPECTED_TOTAL == 56
