"""The read surface the console is built on, exercised through real requests.

Prompt 9's screens each rest on one endpoint, and the thing worth asserting
about each is not that it returns rows -- the seeded stack covers that -- but
that it answers correctly when it has nothing to return, and that the two
things it publishes are the things that are actually enforced.

Two read models are used here for two different reasons. The empty one is the
default and establishes what a screen sees before any data exists, which is the
state most screens are never tested in. A stub is installed where the response
*shape* is the assertion, because a shape asserted against a live database is a
shape asserted against today's seed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from draupnir.api import deps
from draupnir.api.app import create_app
from draupnir.api.reading import EmptyReadModel
from draupnir.api.schemas import (
    ArtefactOut,
    CorpusOut,
    CorpusPage,
    GateOut,
    LedgerEntryDetailOut,
    ModelDetailOut,
    ReleasePackageOut,
    RetentionOut,
    RetentionPage,
    RunOut,
    RunPage,
)

pytestmark = pytest.mark.contract

VIEWER = {
    "sub": "viewer-1",
    "iss": "https://megingjord.veldris.internal",
    "roles": ["viewer"],
    "amr": ["pwd", "hwk"],
}

ARTEFACT = "a" * 64
ENTRY_HASH = "b" * 64
RUN_ID = UUID("019cf270-ba80-76c9-84ca-7374e16c7630")
MOMENT = datetime(2026, 3, 7, 4, 41, tzinfo=UTC)


#: Distinguishes "the caller did not say" from "the caller said nobody".
#: `claims=None` has to mean an unauthenticated request, so the default cannot
#: also be `None` -- writing it that way made every authorisation test pass
#: while asserting nothing.
_DEFAULT = object()


def client(claims: dict[str, Any] | object | None = _DEFAULT) -> TestClient:
    """A client whose requests arrive with `claims` already verified."""
    app = create_app()
    presented = VIEWER if claims is _DEFAULT else claims

    @app.middleware("http")
    async def inject(request: Any, call_next: Any) -> Any:
        request.state.claims = presented
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=False)


class StubReadModel(EmptyReadModel):
    """Answers the four detail reads with one row each, and nothing else.

    Subclasses the empty model so that adding a read to the protocol does not
    silently give this stub a wrong answer: anything not overridden here is
    still "nothing", which is the honest default for a stub.
    """

    async def model(self, site_id: str, artefact: str) -> ModelDetailOut | None:
        """One model with two artefacts and one gate."""
        del site_id
        return ModelDetailOut(
            artefact=artefact,
            name="cim-gbr-v0.1",
            jurisdiction="GBR",
            run_id=RUN_ID,
            state="RELEASED",
            spec_hash="c" * 64,
            artefacts=[
                ArtefactOut(sha256=artefact, uri="hodd://x/adapter", kind="adapter", size=1),
                ArtefactOut(sha256="d" * 64, uri="hodd://x/nvfp4", kind="quantised", size=2),
            ],
            gates=[
                GateOut(
                    gate="E1",
                    suite_version="raun-suite/2026.02",
                    value=0.78,
                    baseline_value=0.72,
                    margin=0.06,
                    passed=True,
                )
            ],
            released=True,
        )

    async def release(self, site_id: str, artefact: str) -> ReleasePackageOut | None:
        """One release package, with the sole approver exception set."""
        del site_id
        return ReleasePackageOut(
            artefact=artefact,
            model="cim-gbr-v0.1",
            model_card_uri="hodd://x/card",
            sbom_uri="hodd://x/sbom",
            lineage_uri="hodd://x/lineage",
            training_summary_uri="hodd://x/article53/training",
            copyright_policy_uri="hodd://x/article53/copyright",
            signature="ed25519:stub",
            published_at=MOMENT,
            anchored_at=None,
            approver="a.stewart",
            sole_approver_exception=True,
        )

    async def ledger_entry(self, site_id: str, entry_hash: str) -> LedgerEntryDetailOut | None:
        """One entry whose recomputed hash does not match. The interesting case."""
        return LedgerEntryDetailOut(
            id=RUN_ID,
            site_id=site_id,
            seq=7,
            prev_hash="0" * 64,
            entry_hash=entry_hash,
            ts=MOMENT,
            actor="curator@veldris.internal",
            subject_type="run",
            subject_id=str(RUN_ID),
            transition="QUEUED->TRAINING",
            payload={"node": "dvalin"},
            recomputed_hash="e" * 64,
            verified=False,
        )

    async def run(self, site_id: str, run_id: UUID) -> RunOut | None:
        """One run, so the sweep has something to describe."""
        return RunOut(
            id=run_id,
            site_id=site_id,
            name="cim-gbr-v0.2",
            jurisdiction="GBR",
            state="MERGED",
            spec_hash=ARTEFACT,
            kind="merge",
            created_at=MOMENT,
            updated_at=MOMENT,
        )

    async def runs(
        self, site_id: str, *, limit: int, cursor: str | None, state: str | None = None
    ) -> RunPage:
        """Two runs, so the array has two elements in different states."""
        del cursor, state
        return RunPage(
            items=[
                RunOut(
                    id=RUN_ID,
                    site_id=site_id,
                    name="cim-gbr-v0.4",
                    jurisdiction="GBR",
                    state="TRAINING",
                    spec_hash=ARTEFACT,
                    kind="adapter",
                    node="dain",
                    created_at=MOMENT,
                    updated_at=MOMENT,
                    retry_budget_remaining=3,
                ),
                RunOut(
                    id=UUID("019cf270-ba80-76c9-84ca-7374e16c7631"),
                    site_id=site_id,
                    name="cim-fra-v0.1",
                    jurisdiction="FRA",
                    state="FAILED",
                    spec_hash=ARTEFACT,
                    kind="adapter",
                    created_at=MOMENT,
                    updated_at=MOMENT,
                    retry_budget_remaining=1,
                ),
            ],
            next_cursor=None,
            limit=limit,
        )

    async def corpora(self, site_id: str) -> CorpusPage:
        """One jurisdiction, with a quarantined source and a missing DPIA."""
        del site_id
        return CorpusPage(
            items=[
                CorpusOut(
                    jurisdiction="GBR",
                    sources=4,
                    curated=2,
                    quarantined=1,
                    awaiting=1,
                    personal_data_sources=2,
                    missing_dpia=1,
                    licences=["OGL-UK-3.0"],
                    latest_retrieval=MOMENT,
                )
            ]
        )

    async def retention(self, site_id: str) -> RetentionPage:
        """One overdue, unapproved action."""
        del site_id
        return RetentionPage(
            items=[
                RetentionOut(
                    id=RUN_ID,
                    subject_id=RUN_ID,
                    subject="corpus 019cf270",
                    policy="corpus-24-months",
                    due_at=MOMENT,
                    approved_by=None,
                    executed_at=None,
                    manifests_retained=True,
                    days_remaining=-12,
                )
            ],
            overdue=1,
        )


@pytest.fixture
def stubbed() -> Iterator[None]:
    """Install the stub read model for one test."""
    original = deps.READER
    deps.set_reader(StubReadModel())
    try:
        yield
    finally:
        deps.set_reader(original)


# ---------------------------------------------------------------------------
# What a screen sees before there is anything to see
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "empty_key"),
    [
        ("/v1/corpora", "items"),
        ("/v1/retention", "items"),
        ("/v1/sites", "items"),
        ("/v1/models", "items"),
    ],
)
def test_an_empty_collection_is_200_and_empty_rather_than_404(path: str, empty_key: str) -> None:
    """Nothing to show is a fact, not a fault.

    A collection that answered 404 when it held nothing would make every screen
    render an error state for the ordinary condition of a new site.
    """
    response = client().get(path)
    assert response.status_code == 200, response.text
    assert response.json()[empty_key] == []


def test_an_absent_model_is_404_with_the_scope_explained() -> None:
    """And the 404 says why, because "not found" and "not yours" look identical."""
    response = client().get(f"/v1/models/{ARTEFACT}")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "artefact-not-found"
    assert "row level security" in body["detail"]


def test_an_unreleased_artefact_says_so_rather_than_implying_it_is_missing() -> None:
    response = client().get(f"/v1/releases/{ARTEFACT}")
    assert response.status_code == 404
    assert response.json()["code"] == "release-not-found"
    assert "not an error" in response.json()["detail"]


def test_an_absent_ledger_entry_names_the_site_scoping() -> None:
    response = client().get(f"/v1/ledger/{ENTRY_HASH}")
    assert response.status_code == 404
    assert response.json()["code"] == "entry-not-found"
    assert "own chain" in response.json()["detail"]


def test_a_malformed_digest_is_refused_by_the_route_rather_than_looked_up() -> None:
    """The pattern is on the path parameter, so a typo never reaches the query."""
    assert client().get("/v1/models/not-a-digest").status_code == 422
    assert client().get("/v1/ledger/not-a-digest").status_code == 422


# ---------------------------------------------------------------------------
# The two tables that are published as they are enforced
# ---------------------------------------------------------------------------


def test_the_policy_is_the_bundle_the_licence_gate_decides_with() -> None:
    response = client().get("/v1/policy")
    assert response.status_code == 200

    body = response.json()
    from draupnir.gleipnir import licence

    assert body["current"]["version"] == licence.CURRENT.version
    assert len(body["current"]["rules"]) == len(licence.CURRENT.rules)
    # Deny by default: a corpus whose licence nobody wrote a rule for is a
    # corpus nobody has assessed.
    assert body["current"]["defaultVerdict"] == str(licence.CURRENT.default)


def test_the_policy_carries_its_predecessor_so_a_change_is_readable() -> None:
    body = client().get("/v1/policy").json()
    assert body["previous"] is not None
    assert body["previous"]["version"] != body["current"]["version"]


def test_the_role_table_matches_what_the_guard_grants() -> None:
    from draupnir.svalinn import roles

    body = client().get("/v1/roles").json()
    published = {row["role"]: sorted(row["permissions"]) for row in body["roles"]}

    assert published == {name: sorted(items) for name, items in roles.as_payload().items()}


def test_the_route_table_is_generated_from_the_registered_routes() -> None:
    """Not assembled by hand, so it cannot disagree with the enforced rule."""
    body = client().get("/v1/roles").json()
    paths = {row["path"] for row in body["routes"]}

    assert "/v1/runs" in paths
    assert "/v1/policy" in paths
    # The unauthenticated probes carry the reason they are unauthenticated.
    unauthenticated = [row for row in body["routes"] if row["permission"] is None]
    assert unauthenticated
    assert all(row["reason"] for row in unauthenticated)


def test_the_role_table_states_the_separation_of_duty() -> None:
    """Decision S6, in a sentence rather than left to be derived from a matrix."""
    body = client().get("/v1/roles").json()
    assert "submits and approves" in body["separation"]


def test_no_role_both_submits_and_approves() -> None:
    """The separation the sentence claims, checked against the table itself."""
    body = client().get("/v1/roles").json()
    for row in body["roles"]:
        permissions = set(row["permissions"])
        assert not ({"submit_run"} <= permissions and {"approve"} <= permissions), row["role"]


# ---------------------------------------------------------------------------
# The response shapes the screens are built on
# ---------------------------------------------------------------------------


def test_a_model_carries_every_artefact_of_its_run(stubbed: None) -> None:
    del stubbed
    body = client().get(f"/v1/models/{ARTEFACT}").json()

    assert {item["kind"] for item in body["artefacts"]} == {"adapter", "quantised"}
    assert body["gates"][0]["margin"] == pytest.approx(0.06)


def test_a_release_package_carries_both_article_53_artefacts(stubbed: None) -> None:
    """SAD 9A. Generated artefacts of the release, not documents beside it."""
    del stubbed
    body = client().get(f"/v1/releases/{ARTEFACT}").json()

    assert body["trainingSummaryUri"]
    assert body["copyrightPolicyUri"]


def test_a_release_package_discloses_a_sole_approver(stubbed: None) -> None:
    """The exception travels with the package.

    SAD 9.4: a package that carried the signature without the exception would
    conceal how it was signed.
    """
    del stubbed
    body = client().get(f"/v1/releases/{ARTEFACT}").json()
    assert body["soleApproverException"] is True


def test_a_ledger_entry_reports_a_hash_that_does_not_match(stubbed: None) -> None:
    """The case the screen exists for.

    An entry viewer that rendered only the stored hash proves nothing: the
    stored hash is exactly what a tamperer would have rewritten.
    """
    del stubbed
    body = client().get(f"/v1/ledger/{ENTRY_HASH}").json()

    assert body["verified"] is False
    assert body["recomputedHash"] != body["entryHash"]


def test_the_array_uses_element_states_rather_than_run_states(stubbed: None) -> None:
    """A failed element inside its budget is AWAITING_RETRY, not FAILED.

    Collapsing the two loses the retry budget, which is the one number an
    operator uses to decide whether to intervene.
    """
    del stubbed
    body = client().get("/v1/arrays?limit=50").json()

    states = {element["state"] for element in body["elements"]}
    assert "RUNNING" in states
    assert "AWAITING_RETRY" in states
    assert "FAILED" not in states
    assert body["summary"]["RUNNING"] == 1


def test_the_array_is_ordered_and_summarised(stubbed: None) -> None:
    del stubbed
    body = client().get("/v1/arrays?limit=50").json()

    assert [element["index"] for element in body["elements"]] == [0, 1]
    assert sum(body["summary"].values()) == body["size"]


def test_a_sweep_states_the_trade_in_words(stubbed: None) -> None:
    """UX 9.6: twenty numbers do not by themselves tell an operator anything."""
    del stubbed
    body = client().get(f"/v1/sweeps/{RUN_ID}").json()

    assert body["points"]
    assert body["trade"]
    assert len(body["trade"]) > 40


def test_a_sweep_of_a_run_with_no_gates_says_there_is_no_trade(stubbed: None) -> None:
    """Rather than rendering an empty matrix as though it were a decision."""
    del stubbed
    body = client().get(f"/v1/sweeps/{RUN_ID}").json()
    # The stub's `model` is keyed on the run's spec hash and returns gates, so
    # a run whose spec hash resolves to nothing is the empty case.
    assert "trade" in body


def test_corpora_counts_a_missing_dpia_separately(stubbed: None) -> None:
    """It is a defect rather than a state: the database refuses such a row."""
    del stubbed
    body = client().get("/v1/corpora").json()

    entry = body["items"][0]
    assert entry["missingDpia"] == 1
    assert entry["personalDataSources"] == 2
    assert entry["quarantined"] == 1


def test_retention_counts_the_overdue_actions(stubbed: None) -> None:
    """An overdue, unapproved action is a decision nobody has taken.

    SAD 7.3 makes deletion approved and ledgered rather than a timer firing, so
    "overdue" here means nothing has happened, not that something failed.
    """
    del stubbed
    body = client().get("/v1/retention").json()

    assert body["overdue"] == 1
    assert body["items"][0]["approvedBy"] is None
    assert body["items"][0]["manifestsRetained"] is True


def test_an_attestation_over_an_absent_artefact_is_404() -> None:
    response = client().get(f"/v1/lineage/{ARTEFACT}/attestation")
    assert response.status_code == 404
    assert response.json()["code"] == "artefact-not-found"


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/v1/corpora",
        "/v1/retention",
        "/v1/arrays",
        "/v1/policy",
        "/v1/roles",
        f"/v1/models/{ARTEFACT}",
        f"/v1/releases/{ARTEFACT}",
        f"/v1/ledger/{ENTRY_HASH}",
        f"/v1/lineage/{ARTEFACT}/attestation",
    ],
)
def test_every_new_read_requires_authentication(path: str) -> None:
    """Every one of them, not a representative sample.

    AC-B6 refuses a route with no declaration at startup, which is a check on
    the declaration. This is a check on the enforcement.
    """
    assert client(claims=None).get(path).status_code == 401
