"""The fifty six jurisdictions, their tiers, and the base each tier trains on.

CIM-56 is the Commonwealth Intelligence Models: one model per member state of
the Commonwealth of Nations, which has fifty six members. The SAD fixes the
Tier A list of nine and says the remaining forty seven are Tier B; it does not
enumerate them, so the set below is the Commonwealth membership by ISO 3166-1
alpha-3 code.

The list below was derived twice, independently, and the two agree: once as
"the fifty six", and once region by region -- Africa 21, Asia 8, Caribbean and
Americas 13, Europe 3, Pacific 11. The regional grouping is kept in the source
because it is the derivation that actually catches an error: a missing
jurisdiction is invisible in a flat list of fifty six and obvious in a region
whose count is one short.

If a jurisdiction is wrong the model for it is wrong, and the failure would be
silent -- so `validate` refuses at submission rather than trusting the list,
and AC-F16 is the check that the two tiers together enumerate all fifty six
with no duplicate and no omission.

Suspended members are in scope. Gabon has been suspended from the Councils of
the Commonwealth since the coup of August 2023, which raised the question of
whether it receives a model; the programme answered on 2 September 2026 that it
does. So the count is membership, not standing: suspension is not removal, and
a member whose participation is suspended is still one of the fifty six.

That is a delivery decision rather than a fact about the Commonwealth, which is
why it is written down here. Reversing it would make the programme fifty five,
Tier B forty six, and would change `EXPECTED_TOTAL` and AC-F16 with it -- so it
is a decision to be taken again explicitly, not one to be arrived at by
somebody quietly dropping a line from a list.

Tier assignment drives base selection, which is the only reason this module is
in the training path at all: Tier A gets the 27B dense base, Tier B the
35B-A3B mixture of experts base.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from draupnir.interfaces.types import Tier


class TierError(Exception):
    """Raised when a jurisdiction or a tier assignment cannot be resolved."""


class BaseModel(StrEnum):
    """The two substrates. SAD 1: one shared substrate, fifty six derived models."""

    #: Tier A. Dense, 27B, for the jurisdictions with the deepest corpora.
    DENSE_27B = "MIDGARD-CORE-GEMMA3-27B-v1.0"
    #: Tier B. Mixture of experts, 35B total with 3B active.
    MOE_35B_A3B = "MIDGARD-CORE-QWEN36-35B-A3B-v1.0"


#: Tier A: nine jurisdictions, fixed by revision 1.1 of the SAD and named in
#: the build specification.
TIER_A: tuple[str, ...] = (
    # Europe, all 3.
    "CYP",  # Cyprus
    "GBR",  # United Kingdom
    "MLT",  # Malta
    # Africa, 2 of 21.
    "NGA",  # Nigeria
    "ZAF",  # South Africa
    # Asia, 2 of 8.
    "IND",  # India
    "SGP",  # Singapore
    # Caribbean and Americas, 1 of 13.
    "CAN",  # Canada
    # Pacific, 1 of 11.
    "AUS",  # Australia
)

#: Tier B: the remaining forty seven Commonwealth member states.
TIER_B: tuple[str, ...] = (
    # Africa, 19 of the region's 21. Nigeria and South Africa are Tier A.
    "BWA",  # Botswana
    "CMR",  # Cameroon
    "GAB",  # Gabon -- suspended from the Councils, in scope regardless. See above.
    "GHA",  # Ghana
    "GMB",  # The Gambia
    "KEN",  # Kenya
    "LSO",  # Lesotho
    "MOZ",  # Mozambique
    "MUS",  # Mauritius
    "MWI",  # Malawi
    "NAM",  # Namibia
    "RWA",  # Rwanda
    "SLE",  # Sierra Leone
    "SWZ",  # Eswatini
    "SYC",  # Seychelles
    "TGO",  # Togo
    "TZA",  # Tanzania
    "UGA",  # Uganda
    "ZMB",  # Zambia
    # Asia, 6 of the region's 8. India and Singapore are Tier A.
    "BGD",  # Bangladesh
    "BRN",  # Brunei Darussalam
    "LKA",  # Sri Lanka
    "MDV",  # Maldives
    "MYS",  # Malaysia
    "PAK",  # Pakistan
    # Caribbean and Americas, 12 of the region's 13. Canada is Tier A.
    "ATG",  # Antigua and Barbuda
    "BHS",  # The Bahamas
    "BLZ",  # Belize
    "BRB",  # Barbados
    "DMA",  # Dominica
    "GRD",  # Grenada
    "GUY",  # Guyana
    "JAM",  # Jamaica
    "KNA",  # Saint Kitts and Nevis
    "LCA",  # Saint Lucia
    "TTO",  # Trinidad and Tobago
    "VCT",  # Saint Vincent and the Grenadines
    # Europe, 0 of the region's 3. Cyprus, Malta and the United Kingdom are
    # all Tier A.
    # Pacific, 10 of the region's 11. Australia is Tier A.
    "FJI",  # Fiji
    "KIR",  # Kiribati
    "NRU",  # Nauru
    "NZL",  # New Zealand
    "PNG",  # Papua New Guinea
    "SLB",  # Solomon Islands
    "TON",  # Tonga
    "TUV",  # Tuvalu
    "VUT",  # Vanuatu
    "WSM",  # Samoa
)

#: The programme is CIM-56. The number is not decorative: it is checked.
EXPECTED_TOTAL = 56

BASE_FOR_TIER: dict[Tier, BaseModel] = {
    Tier.A: BaseModel.DENSE_27B,
    Tier.B: BaseModel.MOE_35B_A3B,
}


@dataclass(frozen=True, slots=True)
class Enumeration:
    """The result of checking the two lists against the programme. AC-F16."""

    total: int
    duplicates: tuple[str, ...]
    overlapping: tuple[str, ...]
    malformed: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """Whether the lists enumerate the programme exactly."""
        return (
            self.total == EXPECTED_TOTAL
            and not self.duplicates
            and not self.overlapping
            and not self.malformed
        )

    def describe(self) -> str:
        """Why the enumeration is not complete."""
        if self.complete:
            return f"{self.total} jurisdictions, no duplicate and no omission"
        problems = []
        if self.total != EXPECTED_TOTAL:
            problems.append(f"{self.total} jurisdictions, expected {EXPECTED_TOTAL}")
        if self.duplicates:
            problems.append(f"duplicated: {', '.join(self.duplicates)}")
        if self.overlapping:
            problems.append(f"in both tiers: {', '.join(self.overlapping)}")
        if self.malformed:
            problems.append(f"not an ISO 3166-1 alpha-3 code: {', '.join(self.malformed)}")
        return "; ".join(problems)


def enumeration(tier_a: Iterable[str] = TIER_A, tier_b: Iterable[str] = TIER_B) -> Enumeration:
    """Check that the two tiers enumerate the programme. AC-F16."""
    listed = [*tier_a, *tier_b]
    seen: dict[str, int] = {}
    for code in listed:
        seen[code] = seen.get(code, 0) + 1

    return Enumeration(
        total=len(set(listed)),
        duplicates=tuple(sorted(code for code, count in seen.items() if count > 1)),
        overlapping=tuple(sorted(set(tier_a) & set(tier_b))),
        malformed=tuple(
            sorted(code for code in set(listed) if len(code) != 3 or not code.isupper())
        ),
    )


def validate() -> Enumeration:
    """Raise unless the tiers enumerate all fifty six. Called at submission.

    At submission rather than at import: a module that refuses to import is a
    control plane that will not start, and the right response to a bad
    jurisdiction list is to refuse the run that depends on it, loudly, with the
    problem named.
    """
    # Passed explicitly rather than left to the default arguments, which bind
    # the tuples at import. A table that drifted after import would otherwise
    # be validated against the one that no longer exists.
    result = enumeration(TIER_A, TIER_B)
    if not result.complete:
        msg = (
            f"the tier lists do not enumerate CIM-56: {result.describe()}. "
            "The programme is fifty six models, one per Commonwealth member "
            "state (AC-F16)."
        )
        raise TierError(msg)
    return result


ALL: tuple[str, ...] = tuple(sorted((*TIER_A, *TIER_B)))
_TIER_BY_CODE: dict[str, Tier] = {
    **dict.fromkeys(TIER_A, Tier.A),
    **dict.fromkeys(TIER_B, Tier.B),
}


def tier_of(jurisdiction: str) -> Tier:
    """Return a jurisdiction's tier, or raise naming what is known.

    Never guesses. A jurisdiction nobody assigned is not a Tier B jurisdiction
    by default; it is a jurisdiction that is not in the programme, and treating
    it as Tier B would silently train a fifty seventh model.
    """
    try:
        return _TIER_BY_CODE[jurisdiction]
    except KeyError as error:
        msg = (
            f"{jurisdiction!r} is not a CIM-56 jurisdiction. The programme covers "
            f"{len(ALL)} Commonwealth member states; there is no default tier."
        )
        raise TierError(msg) from error


def base_for(jurisdiction: str) -> BaseModel:
    """Return the base model a jurisdiction trains against.

    Tier A takes the 27B dense base and Tier B the 35B-A3B mixture of experts.
    Selection follows from the tier and from nothing else, so that two
    jurisdictions in the same tier cannot silently diverge.
    """
    return BASE_FOR_TIER[tier_of(jurisdiction)]


def base_artefact(jurisdiction: str, *, site: str = "sindri") -> str:
    """The `hodd://` address of the base model for a jurisdiction."""
    return f"hodd://{site}/models/core/{base_for(jurisdiction)}"


def jurisdictions_in(tier: Tier) -> tuple[str, ...]:
    """Every jurisdiction in a tier, sorted."""
    return tuple(sorted(code for code, assigned in _TIER_BY_CODE.items() if assigned is tier))


def assert_declared_tier(jurisdiction: str, declared: Tier) -> Tier:
    """Raise unless a specification's declared tier matches the assignment.

    A specification carries `metadata.tier`, and a specification that declares
    Tier A for a Tier B jurisdiction would train the wrong base. Checked at
    submission, before an allocation is consumed.
    """
    actual = tier_of(jurisdiction)
    if actual is not declared:
        msg = (
            f"{jurisdiction} is Tier {actual}, and the specification declares "
            f"Tier {declared}. Tier drives base selection: Tier {actual} trains "
            f"against {BASE_FOR_TIER[actual]}, not {BASE_FOR_TIER[declared]}."
        )
        raise TierError(msg)
    return actual
