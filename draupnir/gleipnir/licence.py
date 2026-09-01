"""The licence policy in force, and the versions it has had.

A policy is data. Changing it means adding a version below, not editing code,
and the two versions here exist because the exit condition for this module is
that changing the policy re-evaluates existing sources without re-ingesting
them -- which cannot be demonstrated with only one.

The order of rules matters: first match wins, and anything unmatched is
refused. Deny by default is the only defensible posture. A corpus whose
licence nobody has written a rule for is a corpus nobody has assessed, and
treating silence as permission is how an unlicensed source reaches a released
model.

Nothing here decides whether a licence *is* what the register says it is. HODD
recorded `CC-BY-SA-4.0` because a curator established it; this decides what
follows from that, under a version that is recorded with every decision.
"""

from __future__ import annotations

from draupnir.gleipnir.policy import Policy, Rule
from draupnir.interfaces.types import Verdict

#: Licences a model may be trained on without further question. Open government
#: licences and the permissive Creative Commons family, which between them
#: cover the legislative and case law corpora of the Tier A jurisdictions.
PERMITTED = frozenset(
    {
        "OGL-UK-3.0",
        "CC0-1.0",
        "CC-BY-4.0",
        "Apache-2.0",
        "MIT",
        "BSD-3-Clause",
    }
)

#: Permitted, but the obligation travels with the artefact: every release
#: derived from one of these must carry attribution, and the model card
#: renders it from the register.
PERMITTED_WITH_ATTRIBUTION = frozenset({"CC-BY-SA-4.0", "ODbL-1.0"})

#: Refused outright. Non-commercial terms are incompatible with a commercial
#: model release, and a share-alike copyleft on training data is a position
#: nobody has taken yet.
REFUSED = frozenset({"CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "GPL-3.0-only", "AGPL-3.0-only"})


#: The policy in force. Every decision records this version, so a decision
#: taken in March remains explicable in November when the policy has moved.
CURRENT = Policy(
    version="gleipnir-licence/2026.01",
    rules=(
        Rule(
            id="personal-data-requires-approval",
            statement=(
                "a source holding personal data requires a data protection approval "
                "before it may be curated, whatever its licence permits"
            ),
            verdict=Verdict.REQUIRES_APPROVAL,
            personal_data=True,
        ),
        Rule(
            id="licence-refused",
            statement=(
                "the licence forbids commercial use or imposes copyleft on derived "
                "works, and a released model is both commercial and derived"
            ),
            verdict=Verdict.REFUSE,
            licences=REFUSED,
        ),
        Rule(
            id="licence-permitted-with-attribution",
            statement=(
                "the licence permits training where attribution is carried into the "
                "release; the model card renders it from the licence register"
            ),
            verdict=Verdict.PERMIT,
            licences=PERMITTED_WITH_ATTRIBUTION,
            attribution_required=True,
        ),
        Rule(
            id="attribution-not-declared",
            statement=(
                "the licence requires attribution and the source does not declare it, "
                "so the obligation would not reach the model card"
            ),
            verdict=Verdict.REFUSE,
            licences=PERMITTED_WITH_ATTRIBUTION,
            attribution_required=False,
        ),
        Rule(
            id="licence-permitted",
            statement="the licence permits training and redistribution of derived works",
            verdict=Verdict.PERMIT,
            licences=PERMITTED,
        ),
    ),
)


#: The previous version, kept so that a decision taken under it stays
#: explicable. SAD 10.3 rule 3 makes the same point about drivers: a
#: historical record resolves what it recorded, not what is current.
#:
#: It differs from CURRENT in one clause -- share-alike licences were
#: permitted without requiring an attribution declaration -- which is what
#: makes a re-evaluation under CURRENT move some sources and not others.
PREVIOUS = Policy(
    version="gleipnir-licence/2025.11",
    rules=(
        Rule(
            id="personal-data-requires-approval",
            statement="a source holding personal data requires a data protection approval",
            verdict=Verdict.REQUIRES_APPROVAL,
            personal_data=True,
        ),
        Rule(
            id="licence-refused",
            statement="the licence forbids commercial use",
            verdict=Verdict.REFUSE,
            licences=REFUSED,
        ),
        Rule(
            id="licence-permitted",
            statement="the licence permits training and redistribution of derived works",
            verdict=Verdict.PERMIT,
            licences=PERMITTED | PERMITTED_WITH_ATTRIBUTION,
        ),
    ),
)


#: Every version, newest first. A decision names one of these, and this is
#: where it is looked up.
VERSIONS: tuple[Policy, ...] = (CURRENT, PREVIOUS)


def by_version(version: str) -> Policy:
    """Return the policy that produced a historical decision.

    Fails loudly rather than falling back to the current one. A decision
    re-explained under a policy that did not produce it is worse than a
    decision that cannot be re-explained, because it looks like an answer.
    """
    for policy in VERSIONS:
        if policy.version == version:
            return policy
    known = ", ".join(policy.version for policy in VERSIONS)
    msg = (
        f"no licence policy version {version!r} is held; known versions are {known}. "
        "A historical decision resolves the version it recorded and is never "
        "re-explained under a newer one."
    )
    raise KeyError(msg)
