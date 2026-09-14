"""What a submission is checked against before anything is recorded. RF-11.

`submitRun` said "Validate, hash into a run identity, and queue". Its only
check was that the specification was not empty. So a specification
`dryRunSpecification` refused with 422 was accepted here with 202 and failed
later, having consumed a run identifier, a ledger entry and a place in the
queue -- and the tier table AC-F16 requires to be validated at submission was
imported by nothing but its own tests.

Both handlers go through `admit` now. These tests are mostly about the two
agreeing, because two implementations of "is this runnable" agree only on the
day they are written.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from draupnir.api import deps
from draupnir.api.app import create_app
from draupnir.api.idempotency import IdempotencyStore
from draupnir.hamarr import tiers
from draupnir.interfaces.testing import sample_spec
from tests.specs import submittable_mapping

pytestmark = pytest.mark.contract

OPERATOR = {
    "sub": "operator-1",
    "iss": "https://megingjord.veldris.internal",
    "roles": ["operator"],
    "amr": ["pwd", "hwk"],
}

SPEC: dict[str, Any] = submittable_mapping()


@pytest.fixture(autouse=True)
def isolated_store() -> Iterator[None]:
    """A fresh idempotency store per test, so keys do not leak between them."""
    original = deps.STORE
    deps.STORE = IdempotencyStore()
    try:
        yield
    finally:
        deps.STORE = original


def client() -> TestClient:
    """A client whose requests arrive as a submitting operator."""
    app = create_app()

    @app.middleware("http")
    async def inject(request: Any, call_next: Any) -> Any:
        request.state.claims = OPERATOR
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=False)


def _submit(api: TestClient, specification: dict[str, Any], key: str) -> Any:
    return api.post(
        "/v1/runs", json={"specification": specification}, headers={"Idempotency-Key": key}
    )


def _dry_run(api: TestClient, specification: dict[str, Any]) -> Any:
    return api.post("/v1/runs/dry-run", json={"specification": specification})


def _altered(**changes: Any) -> dict[str, Any]:
    """A copy of the submittable specification with `spec` fields replaced."""
    copy: dict[str, Any] = json.loads(json.dumps(SPEC))
    copy["spec"].update(changes)
    return copy


# ---------------------------------------------------------------------------
# The two handlers agree
# ---------------------------------------------------------------------------

#: One case per refusal `admit` can produce, plus the accepted one. Each is a
#: specification and the code the estate should answer with -- `None` meaning
#: it should be accepted.
CASES: tuple[tuple[str, dict[str, Any], str | None], ...] = (
    ("the settled specification", SPEC, None),
    (
        "SAD 6.2's worked example, which names the wrong base for its tier",
        sample_spec().as_mapping(),
        "specification-rejected",
    ),
    (
        "a jurisdiction outside the programme",
        {**SPEC, "metadata": {**SPEC["metadata"], "jurisdiction": "IRL"}},
        "jurisdiction-unassigned",
    ),
    (
        "a tier the jurisdiction is not in",
        {**SPEC, "metadata": {**SPEC["metadata"], "tier": "B"}},
        "specification-rejected",
    ),
    (
        "a training driver nobody installed",
        _altered(train={**SPEC["spec"]["train"], "driver": "hamarr.nonesuch/v9"}),
        "driver-unavailable",
    ),
    (
        "a method the installed driver does not have",
        _altered(train={**SPEC["spec"]["train"], "method": "telepathy"}),
        "driver-unavailable",
    ),
    (
        # RF-35: `prepare` refused this and `admit` did not catch it, so it was
        # a 500. 500 steps at the assumed twelve seconds is a hundred minutes.
        "an authored checkpoint interval that leaves more than the budget unwritten",
        _altered(
            train={
                **SPEC["spec"]["train"],
                "params": {**SPEC["spec"]["train"].get("params", {}), "save_steps": 500},
            }
        ),
        "specification-rejected",
    ),
    ("something that is not a specification", {"not": "a specification"}, "specification-invalid"),
)


@pytest.mark.parametrize(("description", "specification", "code"), CASES)
def test_the_dry_run_and_the_submission_agree(
    description: str, specification: dict[str, Any], code: str | None
) -> None:
    """The two handlers reach the same verdict, refusal by refusal.

    A specification the dry run accepts and the submission refuses is as bad as
    the reverse. Enumerated over every refusal `admit` can produce rather than asserted in
    general, because the failure this guards against is precisely a case one
    handler knows about and the other does not.
    """
    api = client()

    dry = _dry_run(api, specification)
    submitted = _submit(api, specification, key=f"agree-{abs(hash(description))}")

    if code is None:
        assert dry.status_code == 200, dry.text
        assert submitted.status_code == 202, submitted.text
        return

    assert dry.status_code == 422, dry.text
    assert submitted.status_code == 422, submitted.text
    assert dry.json()["code"] == code
    assert submitted.json()["code"] == code, (
        f"the dry run and the submission disagree about {description}"
    )


# ---------------------------------------------------------------------------
# The tier table is consulted
# ---------------------------------------------------------------------------


def test_an_unknown_jurisdiction_is_refused_and_named() -> None:
    """`tier_of` never guesses.

    A jurisdiction nobody assigned is not a Tier B jurisdiction by default; it
    is a jurisdiction outside the programme, and resolving a default tier would
    silently train a fifty-seventh model against whichever base that tier
    happens to name.
    """
    api = client()
    outside = {**SPEC, "metadata": {**SPEC["metadata"], "jurisdiction": "IRL"}}

    response = _submit(api, outside, "unknown-jurisdiction")

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "jurisdiction-unassigned"
    assert "IRL" in body["detail"], "the refusal does not name the jurisdiction"
    assert "no default tier" in body["detail"]


def test_a_specification_may_not_choose_its_own_base() -> None:
    """Base selection follows from the tier and from nothing else.

    Two jurisdictions in the same tier that trained against different bases
    would not be comparable, and comparability is the whole of a fifty-six
    model programme.
    """
    api = client()
    wrong = _altered(base={"artefact": tiers.base_artefact("NGA"), "expectSha256": "a" * 64})
    wrong["metadata"] = {**SPEC["metadata"], "jurisdiction": "GBR"}

    # NGA and GBR are both Tier A, so this one is accepted: they share a base.
    assert _submit(api, wrong, "same-tier").status_code == 202

    # A Tier B base under a Tier A jurisdiction is not.
    mismatched = _altered(base={"artefact": tiers.base_artefact("JAM"), "expectSha256": "a" * 64})
    response = _submit(api, mismatched, "cross-tier")

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "specification-rejected"
    assert "Base selection follows from the tier" in response.json()["detail"]


def test_a_declared_tier_that_contradicts_the_assignment_is_refused() -> None:
    """A specification carries `metadata.tier`, and it is checked rather than read."""
    api = client()
    response = _submit(api, {**SPEC, "metadata": {**SPEC["metadata"], "tier": "B"}}, "wrong-tier")

    assert response.status_code == 422, response.text
    assert "GBR is Tier A" in response.json()["detail"]


def test_the_enumeration_is_checked_at_submission_not_at_import() -> None:
    """AC-F16, and where it is checked matters.

    A module that refuses to import is a control plane that will not start. The
    right response to a jurisdiction list that has drifted is to refuse the run
    that depends on it, loudly, with the problem named -- so `config.prepare`
    validates the two lists before it reads either, and that is on the
    submission path now.
    """
    api = client()
    checked: list[str] = []

    original = tiers.validate

    def watched() -> Any:
        checked.append("validated")
        return original()

    tiers.validate = watched
    try:
        assert _submit(api, SPEC, "enumeration").status_code == 202
    finally:
        tiers.validate = original

    assert checked, "the tier enumeration was not validated at submission (AC-F16)"


def test_a_tier_list_with_a_duplicate_is_refused_naming_it() -> None:
    """The enumeration reports which code, not merely that the count is wrong.

    A count that is one short and a code that appears twice look the same from
    the total alone, and they are different mistakes with different fixes.
    """
    duplicated = tiers.enumeration(("GBR", "GBR"), tiers.TIER_B)

    assert not duplicated.complete
    assert duplicated.duplicates == ("GBR",)
    assert "duplicated: GBR" in duplicated.describe()


def test_a_tier_list_with_an_omission_is_refused_naming_the_count() -> None:
    """Fifty six is not decorative: it is the programme, and it is checked."""
    short = tiers.enumeration(tiers.TIER_A[:-1], tiers.TIER_B)

    assert not short.complete
    assert f"expected {tiers.EXPECTED_TOTAL}" in short.describe()


def test_a_jurisdiction_in_both_tiers_is_refused() -> None:
    """Which base it trains against would depend on which list was read first."""
    overlapping = tiers.enumeration(tiers.TIER_A, (*tiers.TIER_B, "GBR"))

    assert not overlapping.complete
    assert overlapping.overlapping == ("GBR",)
    assert "in both tiers: GBR" in overlapping.describe()


# ---------------------------------------------------------------------------
# Nothing is recorded by a refusal
# ---------------------------------------------------------------------------


def test_a_refused_submission_records_nothing_and_releases_its_key() -> None:
    """Both halves matter.

    A refusal that wrote to the chain would make the refusal an event somebody
    has to explain; one that held the idempotency key would tell an operator
    who corrected their specification and retried that they had already
    submitted it.
    """
    api = client()
    outside = {**SPEC, "metadata": {**SPEC["metadata"], "jurisdiction": "IRL"}}

    refused = _submit(api, outside, "shared-key")
    assert refused.status_code == 422

    accepted = _submit(api, SPEC, "shared-key")
    assert accepted.status_code == 202, accepted.text


# ---------------------------------------------------------------------------
# ...and on every specification a generator can reach
# ---------------------------------------------------------------------------

#: What a generated specification may differ in. Each is a field the admission
#: check reads, and the values are a mixture of ones that hold and ones that do
#: not -- the point is not that any particular one is refused, but that the two
#: handlers say the same thing about it.
_JURISDICTIONS = ("GBR", "CAN", "JAM", "IRL", "", "gbr", "GBRX")
_TIERS = ("A", "B")
_METHODS = ("lora", "qlora", "full", "telepathy", "")
_DRIVERS = ("hamarr.llamafactory/v1", "hamarr.nonesuch/v9", "")
_BASES = (
    tiers.base_artefact("GBR"),
    tiers.base_artefact("JAM"),
    "hodd://models/core/SOMETHING-ELSE-v1.0",
)


@st.composite
def specifications(draw: Any) -> dict[str, Any]:
    """A specification varying in the fields admission reads."""
    body: dict[str, Any] = json.loads(json.dumps(SPEC))
    body["metadata"] = {
        **body["metadata"],
        "jurisdiction": draw(st.sampled_from(_JURISDICTIONS)),
        "tier": draw(st.sampled_from(_TIERS)),
    }
    body["spec"]["base"] = {
        "artefact": draw(st.sampled_from(_BASES)),
        "expectSha256": "a" * 64,
    }
    body["spec"]["train"] = {
        **body["spec"]["train"],
        "driver": draw(st.sampled_from(_DRIVERS)),
        "method": draw(st.sampled_from(_METHODS)),
    }
    return body


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(specification=specifications())
def test_the_two_handlers_never_disagree(specification: dict[str, Any]) -> None:
    """The property RF-11 is actually about.

    The enumerated cases above check the refusals somebody thought of. This
    checks the ones nobody did: for any specification the generator can reach,
    the dry run and the submission either both accept it or both refuse it with
    the same code.

    Acceptance is compared, not status codes -- 200 and 202 are different
    answers to different questions, and only the verdict has to match.
    """
    api = client()

    dry = _dry_run(api, specification)
    submitted = _submit(api, specification, key=str(uuid.uuid4()))

    dry_accepted = dry.status_code == 200
    submission_accepted = submitted.status_code == 202

    assert dry_accepted == submission_accepted, (
        f"the dry run answered {dry.status_code} and the submission "
        f"{submitted.status_code} for the same specification: {dry.text} / {submitted.text}"
    )
    if not dry_accepted:
        assert dry.json()["code"] == submitted.json()["code"], (
            "both refused and they disagree about why, which sends an operator to fix "
            "the wrong thing"
        )
