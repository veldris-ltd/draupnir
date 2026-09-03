"""AC-F1 and AC-F14, over the wire.

AC-F1: "A run specification file is submitted through `draupnirctl` and through
the web console, and both produce an identical run identity, being the hash of
the specification plus its input artefact hashes."

The two clients agree because neither of them computes the identity. Both post
the same specification to `POST /v1/runs` and the API returns the identity it
recorded, so agreement is a property of there being one implementation rather
than of two implementations being kept in step. These tests establish that the
API's half holds: the same bytes give the same identity, different work gives a
different one, and a specification whose inputs are unresolved is refused
rather than recorded under an identity that means nothing.

AC-F14: "A dry run renders the exact job plan without consuming an allocation."
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from draupnir.api.app import create_app
from draupnir.api.routers import plugins
from draupnir.interfaces.testing import sample_spec

pytestmark = pytest.mark.contract

OPERATOR = {
    "sub": "operator-1",
    "iss": "https://megingjord.veldris.internal",
    "roles": ["operator"],
    "amr": ["pwd", "hwk"],
}

SPEC: dict[str, Any] = sample_spec().as_mapping()
# The reference training driver needs a checkpoint interval, and the shared
# fixture does not carry one. Added here rather than in the fixture: the
# fixture is the *interface's* sample and belongs to no driver.
SPEC["spec"]["train"]["params"] = {**SPEC["spec"]["train"]["params"], "save_steps": 500}


@pytest.fixture
def developer_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Load the reference drivers so a plan can be rendered.

    The loader refuses an unsigned plug-in (AC-S7) and no signing authority
    exists on a developer machine, so without this the dry run correctly
    answers 422 `driver-unavailable`. Setting the flag is not weakening the
    test: the refusal itself is covered in the plug-in tests, and what is under
    test here is what a dry run returns when a driver *is* available.
    """
    monkeypatch.setenv("DRAUPNIR_DEV", "1")
    plugins.reset_registry()
    yield
    plugins.reset_registry()


def client() -> TestClient:
    """A client whose requests arrive with operator claims already verified."""
    app = create_app()

    @app.middleware("http")
    async def inject(request: Any, call_next: Any) -> Any:
        request.state.claims = OPERATOR
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=False)


def submit(api: TestClient, specification: dict[str, Any], key: str) -> dict[str, Any]:
    response = api.post(
        "/v1/runs",
        json={"specification": specification},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


# ---------------------------------------------------------------------------
# AC-F1
# ---------------------------------------------------------------------------


def test_two_clients_submitting_the_same_file_get_the_same_identity() -> None:
    """The criterion, stated as directly as a test can state it.

    The two submissions below are what the console and the CLI each send: the
    same specification, serialised independently. They are two runs -- two
    identifiers, deliberately, because a re-run is a run -- with one identity
    between them.
    """
    api = client()

    # The console posts the specification it holds as an object.
    from_console = submit(api, SPEC, "console-submission")

    # The CLI reads a file and posts what it parsed. Round-tripped through
    # JSON here for exactly the reason it matters: a client that reordered
    # keys or reformatted numbers must still arrive at the same identity.
    from_cli = submit(api, json.loads(json.dumps(SPEC)), "cli-submission")

    assert from_console["runIdentity"] == from_cli["runIdentity"]
    assert from_console["runId"] != from_cli["runId"]


def test_the_identity_survives_a_reordered_specification() -> None:
    """Key order is a property of the serialiser, not of the work."""
    api = client()
    reordered = dict(reversed(list(SPEC.items())))

    first = submit(api, SPEC, "ordered")
    second = submit(api, reordered, "reordered")

    assert first["runIdentity"] == second["runIdentity"]


def test_a_different_corpus_gives_a_different_identity() -> None:
    """Two runs over the same base and different corpora are different work."""
    api = client()
    other = json.loads(json.dumps(SPEC))
    other["spec"]["dataset"]["expectSha256"] = "d" * 64

    assert submit(api, SPEC, "base")["runIdentity"] != submit(api, other, "other")["runIdentity"]


def test_an_unresolved_input_is_refused_rather_than_recorded() -> None:
    """An identity over an unresolved reference looks reproducible and is not.

    A specification that names `hodd://models/core/…` without the digest it
    resolved to is not reproducible, and recording it under an identity would
    make it look as though it were.
    """
    api = client()
    unresolved = json.loads(json.dumps(SPEC))
    unresolved["spec"]["base"]["expectSha256"] = "not-a-digest"

    response = api.post(
        "/v1/runs",
        json={"specification": unresolved},
        headers={"Idempotency-Key": "unresolved"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "input-hash-unresolved"
    assert "reproducible" in body["detail"]


def test_a_payload_that_is_not_a_specification_is_refused() -> None:
    api = client()
    response = api.post(
        "/v1/runs",
        json={"specification": {"not": "a specification"}},
        headers={"Idempotency-Key": "nonsense"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "specification-invalid"


def test_a_refused_submission_releases_its_idempotency_key() -> None:
    """A request that did not act must not hold the key that says it did.

    Otherwise the operator corrects the specification, retries with the same
    key, and is told the work was already done.
    """
    api = client()
    bad = json.loads(json.dumps(SPEC))
    bad["spec"]["base"]["expectSha256"] = "not-a-digest"

    api.post("/v1/runs", json={"specification": bad}, headers={"Idempotency-Key": "shared"})
    good = api.post("/v1/runs", json={"specification": SPEC}, headers={"Idempotency-Key": "shared"})

    assert good.status_code == 202, good.text


# ---------------------------------------------------------------------------
# AC-F14
# ---------------------------------------------------------------------------


def test_a_dry_run_renders_a_plan_and_consumes_nothing(developer_mode: None) -> None:
    del developer_mode
    api = client()
    response = api.post("/v1/runs/dry-run", json={"specification": SPEC})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["allocationConsumed"] is False
    assert body["command"], "a dry run that renders no command has rendered nothing"
    assert body["driver"]


def test_a_dry_run_needs_no_idempotency_key(developer_mode: None) -> None:
    """It records nothing, so there is nothing for a repeat to duplicate.

    Requiring a key here would be ceremony, and ceremony on the action that is
    meant to be free is how the free action stops being taken.
    """
    del developer_mode
    api = client()
    assert api.post("/v1/runs/dry-run", json={"specification": SPEC}).status_code == 200


def test_a_dry_run_reports_the_identity_the_submission_would_use(developer_mode: None) -> None:
    """So an operator can compare the plan with the run it later becomes."""
    del developer_mode
    api = client()
    planned = api.post("/v1/runs/dry-run", json={"specification": SPEC}).json()
    submitted = submit(api, SPEC, "after-dry-run")

    assert planned["runIdentity"] == submitted["runIdentity"]


def test_a_specification_no_driver_can_run_is_refused_before_anything_is_rendered(
    developer_mode: None,
) -> None:
    """Named, with what is available, rather than raised.

    SAD 10.3 rule 4 makes capability matching the first gate: a driver declares
    what it can do and the core refuses to plan a job that needs something not
    declared. So an unsupported method never reaches the driver at all, and the
    refusal says which drivers exist -- which is what an operator needs, since
    the fix is either a different method or a driver that is not installed.

    The endpoint still calls `validate` before `render`, for the case this does
    not cover: a driver whose validation is stricter than its declared
    capabilities. Without that ordering an operator reads the driver author's
    `KeyError` instead of their own missing field.
    """
    del developer_mode
    api = client()
    unsupported = json.loads(json.dumps(SPEC))
    unsupported["spec"]["train"]["method"] = "not-a-method"

    response = api.post("/v1/runs/dry-run", json={"specification": unsupported})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "driver-unavailable"
    assert "not-a-method" in response.json()["detail"]


def test_a_dry_run_is_deterministic(developer_mode: None) -> None:
    """Decision S5 makes `render` pure, and the plan shown must be the plan run."""
    del developer_mode
    api = client()
    first = api.post("/v1/runs/dry-run", json={"specification": SPEC}).json()
    second = api.post("/v1/runs/dry-run", json={"specification": SPEC}).json()

    assert first["command"] == second["command"]
    assert first["environment"] == second["environment"]
    assert first["resources"] == second["resources"]
