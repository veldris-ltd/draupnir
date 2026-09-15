"""An approval the signing agent signs is an approval the API accepts. RF-40.

S13's approval could never succeed: it sent a placeholder signature and no
`decidedAt`. The console now asks `draupnir.gleipnir.signing_agent` to sign, and
these tests close the loop at the API: a signature the agent produces, sent to
`decideGate` with the instant it was signed over, verifies against the key
registered for the approver and releases the run. A signature over anything
other than what the API computes is refused, which is what stops the flag the
payload carries being described differently by whoever asks for a signature.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from draupnir.api.routers.approvals import POLICY_VERSION
from draupnir.core.domain.states import RunState
from draupnir.gleipnir import signing_agent
from tests import conftest
from tests.contract import test_conditional_writes as conditional

pytestmark = pytest.mark.contract

#: The caller the conditional-write doubles present, and who submitted the run.
#: They differ, so the sole approver exception the API computes is false.
APPROVER = str(conditional.CALLER["sub"])


@pytest.fixture
def awaiting() -> Iterator[conditional.Chain]:
    yield from conditional.install(RunState.AWAITING_APPROVAL)


def agent_for(approver: str) -> signing_agent.Agent:
    """An agent holding the key the session registered for this approver."""
    key = conftest.APPROVER_KEYS[approver]
    assert isinstance(key, Ed25519PrivateKey)
    return signing_agent.Agent(
        key=key, approver=approver, origins=frozenset({"http://127.0.0.1:5173"})
    )


def fields(approver: str = APPROVER, **changes: Any) -> dict[str, Any]:
    return {
        "subject": str(conditional.RUN),
        "approver": approver,
        "policyVersion": POLICY_VERSION,
        "soleApproverException": False,
        **changes,
    }


def decide(signed: dict[str, str]) -> Any:
    api = conditional.client()
    tag = api.get(f"/v1/runs/{conditional.RUN}").json()["etag"]
    return api.post(
        f"/v1/gates/{conditional.RUN}/decide",
        json={
            "decision": "approved",
            "reason": "Gate evidence reviewed in the console.",
            "signature": signed["signature"],
            "decidedAt": signed["decidedAt"],
        },
        headers=conditional.write_headers(tag),
    )


def test_an_approval_the_agent_signs_is_accepted_and_releases_the_run(
    awaiting: conditional.Chain,
) -> None:
    response = decide(agent_for(APPROVER).sign(fields()))

    assert response.status_code == 201, response.text
    assert awaiting.state is RunState.RELEASED


def test_a_signature_over_the_wrong_exception_flag_is_refused(
    awaiting: conditional.Chain,
) -> None:
    """The API computes the flag; a signature over any other value does not verify."""
    response = decide(agent_for(APPROVER).sign(fields(soleApproverException=True)))

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "approval-signature-invalid"
    assert awaiting.state is RunState.AWAITING_APPROVAL


def test_a_signature_from_another_approver_s_key_is_refused(
    awaiting: conditional.Chain,
) -> None:
    other = "approver@veldris.internal"

    response = decide(agent_for(other).sign(fields(approver=other)))

    assert response.status_code == 422, response.text
    assert awaiting.state is RunState.AWAITING_APPROVAL


def test_the_queue_row_carries_what_the_approver_signs(awaiting: conditional.Chain) -> None:
    """The console's loop: read the row, have the agent sign it, decide.

    The row's `signing` block is the only source of the fields the console
    sends the agent, so this is what an approval from S13 does, less the
    browser.
    """
    items = conditional.client().get("/v1/gates", params={"limit": 10}).json()["items"]
    signing = next(item for item in items if item["id"] == str(conditional.RUN))["signing"]

    assert signing == {
        "subject": str(conditional.RUN),
        "approver": APPROVER,
        "policyVersion": POLICY_VERSION,
        "soleApproverException": False,
    }
    response = decide(agent_for(signing["approver"]).sign(signing))

    assert response.status_code == 201, response.text
    assert awaiting.state is RunState.RELEASED
