"""A corpus's licence decision, taken in one place. RF-43.

SAD 6.1 moves a run CORPUS_REGISTERED -> LICENCE_CLEARED when "GLEIPNIR licence
policy passes for every source and for the base model", and to QUARANTINED when
any source fails it. Only the Sindri demonstration procedure took that
decision, so a run submitted through the console or `draupnirctl` stayed at
DRAFT on an estate. The worker takes it now, and the procedure calls this rather
than keeping a second copy that would eventually disagree with it.

**It judges facts it is handed.** Decision S4: GLEIPNIR judges and never
executes. Sources arrive as the mappings the licence register renders, and the
base model as the facts its declaration renders; the decision comes back as
where the corpus goes and what the transition records. Recording it is the
caller's, through the orchestrator.

**Through a driver, not a policy.** The decision is asked of the
`draupnir.policy` driver the deployment installs, so a jurisdiction whose regime
differs is a different driver rather than a change here. Every decision it
returns carries the policy version that took it, and the version recorded on
the transition is theirs (RF-34).

**Three outcomes, and the third records nothing.**

- A refusal of any source, or of the base model, quarantines the corpus and
  names the rule that refused it (AC-S2). Deny by default: a verdict this does
  not recognise is a refusal.
- A decision that requires an approval -- a source holding personal data --
  leaves the corpus where it is. GLEIPNIR's own mapping puts such a source at
  CORPUS_REGISTERED, and quarantining it would record a refusal nobody made.
- Every subject permitted clears it, recording each decision.

A base model whose licence nobody declared is not permitted by its absence: the
corpus waits, saying so.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from draupnir.core.domain.states import RunState
from draupnir.interfaces.protocols import PolicyDriver
from draupnir.interfaces.types import PolicyDecision, Verdict

#: Who a quarantine is recorded as taken by. SAD 6.1 requires the actor.
ACTOR: Final = "system:gleipnir"

#: What a decision is about.
SOURCE: Final = "source"
BASE_MODEL: Final = "base_model"


@dataclass(frozen=True, slots=True)
class SubjectDecision:
    """One subject -- a source or the base model -- and what the driver decided."""

    subject: str
    id: str
    url: str
    licence: str
    decision: PolicyDecision

    @property
    def rule(self) -> str:
        """The rule that decided it. AC-S2 requires a refusal to name one."""
        return self.decision.rule or "default"

    @property
    def label(self) -> str:
        """How an operator reads it on the board."""
        return f"{self.url or self.id}: {self.licence or 'no licence declared'} ({self.rule})"

    def as_payload(self) -> dict[str, Any]:
        """The ledger shape. Each carries its own policy version (RF-34)."""
        return {
            "subject": self.subject,
            "id": self.id,
            "url": self.url,
            "licence": self.licence,
            "verdict": str(self.decision.verdict),
            "rule": self.rule,
            "policyVersion": self.decision.policy_version,
            "reason": self.decision.reason,
        }


@dataclass(frozen=True, slots=True)
class Clearance:
    """Where a corpus goes, and what the transition that takes it there records.

    `target` is `None` when nothing is to be recorded yet: the corpus waits,
    and `detail` says for what.
    """

    target: RunState | None
    detail: str
    facts: Mapping[str, Any] = field(default_factory=dict)
    payload: Mapping[str, Any] = field(default_factory=dict)
    decisions: tuple[SubjectDecision, ...] = ()


def decide(
    sources: Sequence[Mapping[str, Any]],
    base_model: Mapping[str, Any] | None,
    driver: PolicyDriver,
) -> Clearance:
    """Apply the driver's policy to every source and to the base model."""
    if not sources:
        return Clearance(
            None, "no source is registered for this corpus, so there is nothing to judge"
        )

    decisions = [_subject(SOURCE, facts, driver) for facts in sources]
    if base_model is not None:
        decisions.append(_subject(BASE_MODEL, base_model, driver))

    version = decisions[0].decision.policy_version
    recorded = [item.as_payload() for item in decisions]

    refused = [item for item in decisions if not _permits(item) and not _awaits(item)]
    if refused:
        first = refused[0]
        return Clearance(
            RunState.QUARANTINED,
            f"licence policy {version} refuses {'; '.join(item.label for item in refused)}",
            facts={"sources_failing_policy": [item.label for item in refused]},
            payload={
                "failing_source": first.url or first.id,
                "rule": first.rule,
                "actor": ACTOR,
                "policy_version": version,
                "decisions": recorded,
            },
            decisions=tuple(decisions),
        )

    awaiting = [item for item in decisions if _awaits(item)]
    if awaiting:
        return Clearance(
            None,
            f"{'; '.join(item.label for item in awaiting)} "
            f"{'requires' if len(awaiting) == 1 else 'require'} an approval under {version} "
            "before the corpus can be cleared; nothing is recorded until one is",
            decisions=tuple(decisions),
        )

    if base_model is None:
        return Clearance(
            None,
            "no licence is declared for the run's base model, so the policy cannot pass for "
            "it and the corpus is not cleared",
            decisions=tuple(decisions),
        )

    return Clearance(
        RunState.LICENCE_CLEARED,
        f"every source and the base model are permitted under {version}",
        facts={"sources_failing_policy": [], "base_model_cleared": True},
        payload={
            "policy_version": version,
            "evaluation_result": "PASS",
            "decisions": recorded,
        },
        decisions=tuple(decisions),
    )


def _subject(kind: str, facts: Mapping[str, Any], driver: PolicyDriver) -> SubjectDecision:
    return SubjectDecision(
        subject=kind,
        id=str(facts.get("id") or ""),
        url=str(facts.get("url") or ""),
        licence=str(facts.get("licenceSpdx") or ""),
        decision=driver.evaluate(dict(facts)),
    )


def _permits(item: SubjectDecision) -> bool:
    return item.decision.verdict is Verdict.PERMIT


def _awaits(item: SubjectDecision) -> bool:
    return item.decision.verdict is Verdict.REQUIRES_APPROVAL


__all__ = ["ACTOR", "BASE_MODEL", "SOURCE", "Clearance", "SubjectDecision", "decide"]
