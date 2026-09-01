"""The policy engine. GLEIPNIR judges and never executes.

SAD Decision S4: "Separating the recording of a fact from the judgement about
that fact means a licence policy change is a GLEIPNIR configuration change and
never requires re-ingesting a corpus. It also means the provenance record
stays neutral and remains valid if the policy is later found to have been
wrong."

The whole of that follows from one design choice: **a policy is evaluated
against a mapping of facts, never against a HODD record.** GLEIPNIR cannot
import `draupnir.hodd` -- the module layering forbids it -- so there is no way
to write a policy that reaches into the register, and no way for a stored
judgement to accumulate there. Re-evaluating ten thousand sources under a new
policy reads facts and writes decisions; it touches no corpus and recomputes
no hash.

A decision carries the policy version that produced it. A decision without one
is unexplainable the moment the policy moves on, and the policy will move on.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from draupnir.core.domain.states import RunState
from draupnir.interfaces.types import PolicyDecision, Verdict


class PolicyError(Exception):
    """Raised when a policy cannot be applied."""


@dataclass(frozen=True, slots=True)
class Rule:
    """One clause of a policy, matched against the facts of a subject.

    Declarative on purpose. A rule expressed as a predicate in code is a rule
    nobody outside engineering can read, and Article 53 requires the copyright
    policy to be machine readable and published -- so the rules are data that
    renders, and `copyright.py` renders them.
    """

    id: str
    statement: str
    verdict: Verdict
    #: SPDX identifiers this rule applies to. Empty matches any licence.
    licences: frozenset[str] = frozenset()
    #: Match only when the personal data determination has this value.
    personal_data: bool | None = None
    #: Match only when attribution is or is not required.
    attribution_required: bool | None = None

    def matches(self, facts: Mapping[str, Any]) -> bool:
        """Whether this rule applies to a subject with these facts."""
        if self.licences and facts.get("licenceSpdx") not in self.licences:
            return False
        if self.personal_data is not None and bool(facts.get("personalData")) != self.personal_data:
            return False
        return not (
            self.attribution_required is not None
            and bool(facts.get("attributionRequired")) != self.attribution_required
        )

    def as_mapping(self) -> dict[str, Any]:
        """The wire shape, for the machine readable policy artefact."""
        clause: dict[str, Any] = {
            "id": self.id,
            "statement": self.statement,
            "verdict": str(self.verdict),
        }
        if self.licences:
            clause["licences"] = sorted(self.licences)
        if self.personal_data is not None:
            clause["personalData"] = self.personal_data
        if self.attribution_required is not None:
            clause["attributionRequired"] = self.attribution_required
        return clause


@dataclass(frozen=True, slots=True)
class Policy:
    """An ordered set of rules, under a version.

    First match wins, and an unmatched subject is refused. Deny by default is
    the only defensible posture for a licence policy: a corpus whose licence
    nobody wrote a rule for is a corpus nobody has assessed.
    """

    version: str
    rules: tuple[Rule, ...]
    #: What happens to a subject no rule matches.
    default: Verdict = Verdict.REFUSE
    default_statement: str = (
        "no rule matches this licence, and an unassessed licence is not a permitted one"
    )

    def __post_init__(self) -> None:
        """Refuse a policy that cannot be reasoned about."""
        if not self.version:
            msg = "a policy carries a version; a decision without one is unexplainable"
            raise PolicyError(msg)
        seen = [rule.id for rule in self.rules]
        duplicates = sorted({item for item in seen if seen.count(item) > 1})
        if duplicates:
            msg = f"policy {self.version} has duplicate rule ids: {', '.join(duplicates)}"
            raise PolicyError(msg)

    def decide(self, facts: Mapping[str, Any]) -> PolicyDecision:
        """Return the decision for a subject, naming the rule that produced it."""
        for rule in self.rules:
            if rule.matches(facts):
                return PolicyDecision(
                    verdict=rule.verdict,
                    policy_version=self.version,
                    rule=rule.id,
                    reason=rule.statement,
                )
        return PolicyDecision(
            verdict=self.default,
            policy_version=self.version,
            rule="default",
            reason=self.default_statement,
        )

    def as_mapping(self) -> dict[str, Any]:
        """The machine readable form of the whole policy."""
        return {
            "version": self.version,
            "default": str(self.default),
            "defaultStatement": self.default_statement,
            "rules": [rule.as_mapping() for rule in self.rules],
        }


# ---------------------------------------------------------------------------
# Assessing a source
# ---------------------------------------------------------------------------

#: Where a source goes under each verdict. SAD 6.1: a source that fails
#: licence policy moves to QUARANTINED, which means withdrawn and retained --
#: never deleted by the transition.
STATE_FOR_VERDICT: dict[Verdict, RunState] = {
    Verdict.PERMIT: RunState.LICENCE_CLEARED,
    Verdict.REFUSE: RunState.QUARANTINED,
    Verdict.REQUIRES_APPROVAL: RunState.CORPUS_REGISTERED,
}


@dataclass(frozen=True, slots=True)
class Assessment:
    """One source, judged under one policy version."""

    source_id: str
    url: str
    decision: PolicyDecision

    @property
    def permitted(self) -> bool:
        """Whether the source may proceed towards curation."""
        return self.decision.verdict is Verdict.PERMIT

    @property
    def target_state(self) -> RunState:
        """Where this source belongs after the assessment."""
        return STATE_FOR_VERDICT[self.decision.verdict]

    @property
    def rule(self) -> str:
        """The rule that decided it. AC-S2 requires the failing rule be named."""
        return self.decision.rule or "default"

    def as_payload(self) -> dict[str, Any]:
        """The ledger payload for this assessment."""
        return {
            "source": self.source_id,
            "url": self.url,
            "verdict": str(self.decision.verdict),
            "policyVersion": self.decision.policy_version,
            "rule": self.rule,
            "reason": self.decision.reason,
        }

    def describe(self) -> str:
        """A line an operator reads on the run board."""
        return (
            f"{self.url}: {self.decision.verdict} under {self.decision.policy_version} "
            f"by rule {self.rule} -- {self.decision.reason}"
        )


class PolicyEngine:
    """Applies a policy to facts, and nothing else.

    It holds no corpus, opens no file and imports no store. Everything it needs
    arrives as a mapping, which is what makes a re-evaluation cheap and a
    judgement in the wrong module impossible.
    """

    def __init__(self, policy: Policy) -> None:
        """Bind to the policy in force."""
        self._policy = policy

    @property
    def policy(self) -> Policy:
        """The policy this engine applies."""
        return self._policy

    @property
    def version(self) -> str:
        """The version recorded alongside every decision."""
        return self._policy.version

    def assess(self, facts: Mapping[str, Any]) -> Assessment:
        """Judge one source."""
        return Assessment(
            source_id=str(facts.get("id", "")),
            url=str(facts.get("url", "")),
            decision=self._policy.decide(facts),
        )

    def reassess(self, sources: Iterable[Mapping[str, Any]]) -> tuple[Assessment, ...]:
        """Judge every source again under this policy.

        This is a licence policy change: read the facts already recorded, and
        decide again. Nothing is re-ingested, no hash is recomputed and no
        corpus is read, because none of that has changed -- only the question.
        """
        return tuple(self.assess(facts) for facts in sources)

    def refused(self, assessments: Sequence[Assessment]) -> tuple[Assessment, ...]:
        """Just the ones that did not pass, for an operator to act on."""
        return tuple(item for item in assessments if not item.permitted)

    def changed_from(
        self, previous: Sequence[Assessment], current: Sequence[Assessment]
    ) -> tuple[tuple[Assessment, Assessment], ...]:
        """Pairs whose verdict differs between two policy versions.

        What an operator actually wants after a policy change: not the ten
        thousand sources that still pass, but the four that no longer do.
        """
        before = {item.source_id: item for item in previous}
        return tuple(
            (before[item.source_id], item)
            for item in current
            if item.source_id in before
            and before[item.source_id].decision.verdict is not item.decision.verdict
        )


class PolicyDriverAdapter:
    """Presents a `Policy` as the `PolicyDriver` of SAD 8.2.

    The entry point group `draupnir.policy` takes a driver; a policy is data.
    This is the seam between them, so that a new compliance regime is a new
    driver and a new licence list is a new policy file.
    """

    def __init__(self, policy: Policy, name: str = "gleipnir.licence/v1") -> None:
        """Wrap a policy as a driver."""
        self.name = name
        self.capabilities: frozenset[str] = frozenset({"licence"})
        self.policy_version = policy.version
        self._policy = policy

    def evaluate(self, subject: dict[str, object]) -> PolicyDecision:
        """Permit, refuse, or require an approval, naming the rule applied."""
        return self._policy.decide(subject)
