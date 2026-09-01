"""The copyright policy: versioned, machine readable, referenced by every release.

SAD 9A.2 assigns GLEIPNIR one Article 53 obligation: "Policy to comply with
Union copyright law, including the text and data mining reservation" -- as a
"machine readable copyright policy, versioned, referenced by every release".

Two things follow.

It is *generated*, not authored. Decision S11: "Article 53 artefacts are
generated from the pipeline record, never authored separately ... A compliance
document written by hand after the fact describes what the author remembers,
and drifts from the system on the first revision that nobody propagates." The
licence rules below are the same `Policy` object the engine evaluates, so the
published document cannot disagree with the decisions actually taken. There is
no second copy to fall out of step.

It is *referenced by version*, not embedded. A release records
`copyright_policy_uri` and the version in force at its release date. SAD 10.2
is explicit that when the guidance changes, "existing releases keep the
template version in force at their release date" -- so the artefact is
immutable once published and a new policy is a new version, never an edit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from draupnir.gleipnir.licence import CURRENT, by_version
from draupnir.gleipnir.policy import Policy
from draupnir.interfaces.types import Verdict

SCHEMA = "draupnir/copyright-policy/v1"

#: The text and data mining reservation of Article 4(3) of the Copyright in
#: the Digital Single Market Directive. Stated as the position taken, because
#: a policy that only lists licences does not answer the question the Article
#: actually asks.
TDM_RESERVATION = (
    "Where a rightsholder has expressly reserved the text and data mining "
    "exception under Article 4(3) of Directive (EU) 2019/790, by machine "
    "readable means or otherwise, that source is not ingested. The reservation "
    "is established at registration and recorded against the source in the "
    "licence register; it is not inferred at training time, because by then the "
    "corpus has already been read."
)

#: What the policy commits to doing, in the order a reader asks it.
COMMITMENTS = (
    "Every source is registered with an SPDX licence identifier, a retrieval "
    "date and a content hash before it is curated.",
    "A source whose licence is not assessed by this policy is refused. Silence is not permission.",
    "Attribution obligations are carried into the model card of every derived "
    "release, generated from the licence register rather than transcribed.",
    "A source holding personal data requires a data protection approval and a "
    "DPIA reference, whatever its copyright licence permits.",
    "The training content summary published with each release is generated from "
    "the same register, so it cannot describe a corpus other than the one used.",
    "Licence register entries and per-source hashes are retained indefinitely, "
    "including after the underlying text is deleted under retention, so that "
    "the provenance of a released model outlives its training data.",
)


class CopyrightPolicyError(Exception):
    """Raised when a copyright policy artefact cannot be produced or trusted."""


@dataclass(frozen=True, slots=True)
class CopyrightPolicy:
    """The published artefact. Immutable once released."""

    version: str
    issued_at: datetime
    licence_policy: Policy

    def as_mapping(self) -> dict[str, Any]:
        """The machine readable document.

        The licence clauses are rendered from the policy the engine evaluates.
        Publishing a summary written separately would let the two diverge, and
        the document that diverges is always the one nobody re-reads.
        """
        return {
            "schema": SCHEMA,
            "version": self.version,
            "issuedAt": self.issued_at.isoformat(),
            "instrument": "Regulation (EU) 2024/1689, Article 53(1)(c)",
            "textAndDataMiningReservation": TDM_RESERVATION,
            "commitments": list(COMMITMENTS),
            "licencePolicy": {
                "version": self.licence_policy.version,
                "default": str(self.licence_policy.default),
                "permitted": sorted(
                    {
                        licence
                        for rule in self.licence_policy.rules
                        if rule.verdict is Verdict.PERMIT
                        for licence in rule.licences
                    }
                ),
                "refused": sorted(
                    {
                        licence
                        for rule in self.licence_policy.rules
                        if rule.verdict is Verdict.REFUSE
                        for licence in rule.licences
                    }
                ),
                "requiresApproval": sorted(
                    {
                        rule.id
                        for rule in self.licence_policy.rules
                        if rule.verdict is Verdict.REQUIRES_APPROVAL
                    }
                ),
                "clauses": [rule.as_mapping() for rule in self.licence_policy.rules],
            },
        }

    def to_json(self) -> str:
        """The published rendering, stable across machines."""
        return json.dumps(self.as_mapping(), indent=2, sort_keys=True, ensure_ascii=False)

    def digest(self) -> str:
        """The hash a release records alongside the version.

        A version alone identifies which policy was in force; the digest
        proves the document has not been edited since. Both travel with the
        release, because a version that can be rewritten is a version that
        says nothing.
        """
        canonical = json.dumps(
            self.as_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def reference(self, base_uri: str) -> dict[str, str]:
        """What a release record carries: the URI, the version and the digest."""
        return {
            "uri": f"{base_uri.rstrip('/')}/copyright-policy-{self.version}.json",
            "version": self.version,
            "sha256": self.digest(),
        }


def issue(
    version: str, issued_at: datetime, *, licence_policy: Policy | None = None
) -> CopyrightPolicy:
    """Produce the artefact for a version of the licence policy."""
    if issued_at.tzinfo is None:
        msg = "the copyright policy records an issue date with an explicit offset"
        raise CopyrightPolicyError(msg)
    return CopyrightPolicy(
        version=version, issued_at=issued_at, licence_policy=licence_policy or CURRENT
    )


def for_release(licence_policy_version: str, issued_at: datetime) -> CopyrightPolicy:
    """Produce the artefact a release references, from the policy it ran under.

    SAD 10.2: existing releases keep the version in force at their release
    date. Passing the licence policy version a release actually used is what
    makes that true rather than aspirational -- re-rendering a historical
    release under the current policy would quietly restate its compliance
    position.
    """
    policy = by_version(licence_policy_version)
    return issue(
        version=f"copyright/{policy.version.split('/')[-1]}",
        issued_at=issued_at,
        licence_policy=policy,
    )
