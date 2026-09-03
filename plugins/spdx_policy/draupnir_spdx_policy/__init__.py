"""A PolicyDriver over SPDX identifiers: the reference `draupnir.policy`.

AC-D2: "Every plug-in interface has a reference implementation and a worked
example." `draupnir.policy` had neither, because GLEIPNIR decides its own
licence policy and never needed the extension point to do it. SAD 10.2 names
the case that does: a jurisdiction whose regime differs, arriving as a policy
driver rather than as a change to the core.

It is not GLEIPNIR. `draupnir.gleipnir.policy` is the licence regime in force,
with its rule table, its copyright policy rendering and its Article 53
obligations, and an import contract forbids a driver reaching into it. This is
the smaller thing the Protocol describes: given the facts about a subject,
permit, refuse, or require an approval, and say which rule decided.

Three properties, all of them the Protocol's rather than this driver's.

**Deny by default.** A licence no rule matches is refused, not permitted. A
corpus whose licence nobody has written a rule for is a corpus nobody has
assessed, and permitting it would make the policy's silence a permission.

**The decision carries its version.** `policy_version` is recorded alongside
every decision, so a decision taken in March is still explicable in November
when the policy has moved (SAD 9A.2). The version is a constructor argument
rather than a constant, because that is what makes a second edition possible.

**It returns a decision and never acts.** Decision S4: GLEIPNIR judges and
never executes. Nothing here writes, quarantines, or deletes -- it answers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from draupnir.interfaces.types import PolicyDecision, Verdict

NAME = "gleipnir.spdx/v1"

#: `licence` is the regime this decides. `personal-data` says it also reads the
#: personal data determination, which a caller can select on.
CAPABILITIES = frozenset({"licence", "spdx", "personal-data"})

#: The version this reference driver records. Calendar shaped, because what
#: changes about a policy is when it was decided.
POLICY_VERSION = "spdx-reference/2026.01"


@dataclass(frozen=True, slots=True)
class Rule:
    """One clause, matched against the facts of a subject.

    Data rather than a predicate in code. Article 53 requires the copyright
    policy to be machine readable and published, and a rule expressed as a
    function is a rule nobody outside engineering can read.
    """

    id: str
    statement: str
    verdict: Verdict
    #: SPDX identifiers this rule applies to. Empty matches any licence.
    licences: frozenset[str] = frozenset()
    #: Match only when the personal data determination has this value.
    personal_data: bool | None = None

    def matches(self, facts: Mapping[str, object]) -> bool:
        """Whether this rule applies to a subject with these facts."""
        if self.licences and facts.get("licenceSpdx") not in self.licences:
            return False
        return self.personal_data is None or bool(facts.get("personalData")) is self.personal_data


#: The worked example. Three rules and a default, in the order a first match
#: wins: personal data needs an approval whatever the licence says; the
#: permissive identifiers are permitted; a share-alike identifier is refused
#: because a derived model would inherit the obligation; and anything else
#: falls to the default, which refuses.
RULES: tuple[Rule, ...] = (
    Rule(
        id="personal-data-requires-approval",
        statement="a source containing personal data needs a recorded approval and a DPIA",
        verdict=Verdict.REQUIRES_APPROVAL,
        personal_data=True,
    ),
    Rule(
        id="permissive-permitted",
        statement="permissive and open government licences permit training and redistribution",
        verdict=Verdict.PERMIT,
        licences=frozenset(
            {"Apache-2.0", "MIT", "BSD-3-Clause", "CC-BY-4.0", "CC0-1.0", "OGL-UK-3.0"}
        ),
    ),
    Rule(
        id="share-alike-refused",
        statement="a share-alike obligation would attach to every derived model",
        verdict=Verdict.REFUSE,
        licences=frozenset({"CC-BY-SA-4.0", "GPL-3.0-only", "AGPL-3.0-only"}),
    ),
)


@dataclass
class SpdxPolicyDriver:
    """Decides on a subject from its SPDX identifier and personal data finding."""

    name: str = NAME
    capabilities: frozenset[str] = CAPABILITIES
    policy_version: str = POLICY_VERSION
    rules: Sequence[Rule] = RULES
    #: What happens to a subject no rule matches.
    default: Verdict = Verdict.REFUSE
    _: dict[str, str] = field(default_factory=dict, repr=False)

    def evaluate(self, subject: dict[str, object]) -> PolicyDecision:
        """Permit, refuse, or require an approval, naming the rule applied."""
        for rule in self.rules:
            if rule.matches(subject):
                return PolicyDecision(
                    verdict=rule.verdict,
                    policy_version=self.policy_version,
                    rule=rule.id,
                    reason=rule.statement,
                )
        return PolicyDecision(
            verdict=self.default,
            policy_version=self.policy_version,
            rule="default",
            reason=(
                f"no rule matches {subject.get('licenceSpdx')!r}, and an unassessed "
                "licence is not a permitted one"
            ),
        )

    def as_mapping(self) -> dict[str, object]:
        """The machine readable form, for publication under Article 53."""
        return {
            "version": self.policy_version,
            "default": str(self.default),
            "rules": [
                {
                    "id": rule.id,
                    "statement": rule.statement,
                    "verdict": str(rule.verdict),
                    "licences": sorted(rule.licences),
                    "personalData": rule.personal_data,
                }
                for rule in self.rules
            ],
        }


driver = SpdxPolicyDriver()

__all__ = ["CAPABILITIES", "NAME", "POLICY_VERSION", "RULES", "Rule", "SpdxPolicyDriver", "driver"]
