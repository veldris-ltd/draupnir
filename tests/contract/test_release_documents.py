"""S17's primary action: downloading a document of the release package. RF-27.

The UX inventory names "Download the package" as what S17 is for, and there was
no operation behind it. `getRelease` returned five addresses, the console
printed them as code, and nothing served what was at them.

What is asserted here is the shape of the promise rather than the prose of the
documents: each one downloads as an attachment, the same download is the same
bytes, an absence is stated rather than filled, and the documents agree with
each other and with the lineage they are generated from.
"""

from __future__ import annotations

import hashlib
import json
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
    GateOut,
    LineageOut,
    ModelDetailOut,
    ReleasePackageOut,
)
from draupnir.gleipnir.licence import CURRENT

pytestmark = pytest.mark.contract

VIEWER = {
    "sub": "viewer-1",
    "iss": "https://megingjord.veldris.internal",
    "roles": ["viewer"],
    "amr": ["pwd", "hwk"],
}

ARTEFACT = "a" * 64
SPEC_HASH = "c" * 64
RUN_ID = UUID("019cf270-ba80-76c9-84ca-7374e16c7630")
PUBLISHED = datetime(2026, 3, 7, 4, 41, tzinfo=UTC)

MEDIA = {
    "model-card": "text/markdown",
    "sbom": "application/vnd.cyclonedx+json",
    "lineage": "application/json",
    "training-summary": "application/json",
    "copyright-policy": "application/json",
}

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


class Published(EmptyReadModel):
    """One published release, its lineage over two sources, and its model."""

    def __init__(self, *, published_at: datetime | None = PUBLISHED, sources: bool = True) -> None:
        self.published_at = published_at
        self.with_sources = sources

    async def release(self, site_id: str, artefact: str) -> ReleasePackageOut | None:
        del site_id
        return ReleasePackageOut(
            artefact=artefact,
            model="cim-gbr-v0.1",
            model_card_uri="hodd://sindri/releases/r1/model-card.md",
            sbom_uri="hodd://sindri/releases/r1/sbom.cdx.json",
            lineage_uri="hodd://sindri/releases/r1/lineage.json",
            training_summary_uri="hodd://sindri/releases/r1/article-53-training-summary.md",
            copyright_policy_uri="hodd://sindri/releases/r1/article-53-copyright-policy.md",
            signature="ed25519:stub",
            published_at=self.published_at,
            anchored_at=None,
            approver="a.stewart",
            sole_approver_exception=True,
        )

    async def lineage(self, site_id: str, artefact: str) -> LineageOut | None:
        del site_id
        sources = (
            [
                {
                    "kind": "source",
                    "label": "https://www.legislation.gov.uk",
                    "digest": "e" * 64,
                    "fact": "OGL-UK-3.0",
                    "gap": None,
                    "licence": "OGL-UK-3.0",
                    "jurisdiction": "GBR",
                    "attributionRequired": True,
                    "personalData": False,
                },
                {
                    "kind": "source",
                    "label": "https://caselaw.nationalarchives.gov.uk",
                    "digest": "f" * 64,
                    "fact": "CC-BY-4.0",
                    "gap": None,
                    "licence": "CC-BY-4.0",
                    "jurisdiction": "GBR",
                    "attributionRequired": False,
                    "personalData": True,
                },
            ]
            if self.with_sources
            else []
        )
        return LineageOut(
            artefact=artefact,
            complete=self.with_sources,
            gaps=[]
            if self.with_sources
            else ["no corpus source is recorded for this jurisdiction"],
            licences=sorted(node["licence"] for node in sources),
            corpus_hashes=[node["digest"] for node in sources],
            nodes=[
                {
                    "kind": "release",
                    "label": "cim-gbr-v0.1",
                    "digest": artefact,
                    "fact": "quantised at hodd://sindri/nvfp4",
                    "gap": None,
                },
                {
                    "kind": "run",
                    "label": "cim-gbr-v0.1",
                    "digest": SPEC_HASH,
                    "fact": "specification hash",
                    "gap": None,
                },
                *sources,
            ],
            approval={
                "approver": "a.stewart",
                "decision": "APPROVED",
                "decided_at": PUBLISHED.isoformat(),
                "sole_approver_exception": True,
            },
        )

    async def model(self, site_id: str, artefact: str) -> ModelDetailOut | None:
        del site_id
        return ModelDetailOut(
            artefact=artefact,
            name="cim-gbr-v0.1",
            jurisdiction="GBR",
            run_id=RUN_ID,
            state="RELEASED",
            spec_hash=SPEC_HASH,
            artefacts=[
                ArtefactOut(sha256="d" * 64, uri="hodd://sindri/adapter", kind="adapter", size=1),
                ArtefactOut(sha256=artefact, uri="hodd://sindri/nvfp4", kind="quantised", size=2),
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


def _install(model: EmptyReadModel) -> Iterator[None]:
    original = deps.READER
    deps.set_reader(model)
    try:
        yield
    finally:
        deps.set_reader(original)


@pytest.fixture
def published() -> Iterator[None]:
    yield from _install(Published())


@pytest.fixture
def unpublished() -> Iterator[None]:
    yield from _install(Published(published_at=None))


@pytest.fixture
def sourceless() -> Iterator[None]:
    yield from _install(Published(sources=False))


def download(document: str, claims: dict[str, Any] | object | None = _DEFAULT) -> Any:
    return client(claims).get(f"/v1/releases/{ARTEFACT}/documents/{document}")


# ---------------------------------------------------------------------------
# The download
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("published")
@pytest.mark.parametrize("document", sorted(MEDIA))
def test_every_document_downloads_as_an_attachment(document: str) -> None:
    response = download(document)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(MEDIA[document])
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment; filename=")
    assert "cim-gbr-v0.1" in disposition


@pytest.mark.usefixtures("published")
@pytest.mark.parametrize("document", sorted(MEDIA))
def test_the_same_download_is_the_same_bytes(document: str) -> None:
    """Dated by the release, never by the request.

    A digest is what somebody checks a downloaded document against, and a
    document dated by its download would have a different one every time.
    """
    first = download(document)
    second = download(document)

    assert first.content == second.content
    assert first.headers["etag"] == f'"{hashlib.sha256(first.content).hexdigest()}"'


@pytest.mark.usefixtures("published")
def test_a_viewer_may_download_and_an_unauthenticated_caller_may_not() -> None:
    assert download("model-card").status_code == 200
    assert download("model-card", claims=None).status_code == 401


# ---------------------------------------------------------------------------
# What the documents say
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("published")
def test_the_card_carries_the_sole_approver_exception_and_states_its_absences() -> None:
    """AC-S15 puts the exception on the card; Decision S11 keeps absences visible."""
    card = download("model-card").text

    assert "# cim-gbr-v0.1" in card
    assert "soleApproverException" in card
    assert "not recorded" in card, "a fact the record lacks was left out rather than stated"
    assert "GBR" in card


@pytest.mark.usefixtures("published")
def test_the_sbom_describes_the_released_artefact_and_its_sources() -> None:
    document = download("sbom").json()

    assert document["bomFormat"] == "CycloneDX"
    assert document["metadata"]["component"]["hashes"][0]["content"] == ARTEFACT
    hashes = {item["hashes"][0]["content"] for item in document["components"]}
    assert hashes == {"e" * 64, "f" * 64}
    assert SPEC_HASH not in hashes, "a specification hash is how a model was made, not a part of it"


@pytest.mark.usefixtures("published")
def test_the_summary_carries_the_registers_answers_rather_than_absences() -> None:
    """The lineage nodes carry attribution and personal data (RF-27).

    Without them every source's determination would be reported as not
    recorded, which is a false absence -- as wrong as a false presence.
    """
    summary = download("training-summary").json()

    assert summary["data_sources"]["licences"] == ["CC-BY-4.0", "OGL-UK-3.0"]
    assert summary["data_sources"]["attributionRequired"] == ["OGL-UK-3.0"]
    assert summary["data_processing"]["personalDataPresent"] is True
    assert summary["notRecorded"] == []


@pytest.mark.usefixtures("published")
def test_the_summary_and_the_lineage_agree_about_the_licences() -> None:
    """AC-F10's internal consistency, between two downloads."""
    summary = download("training-summary").json()
    attestation = download("lineage").json()

    assert summary["data_sources"]["licences"] == attestation["payload"]["licences"]


@pytest.mark.usefixtures("published")
def test_the_downloaded_attestation_is_the_exported_one_dated_by_the_release() -> None:
    """One builder for both (RF-27), so they cannot differ in anything but the date."""
    downloaded = download("lineage").json()
    exported = client().get(f"/v1/lineage/{ARTEFACT}/attestation").json()

    assert downloaded["issuedAt"].startswith("2026-03-07T04:41")
    assert downloaded["signature"] == f"sha256:{downloaded['payloadSha256']}"
    for field in ("artefact", "complete", "gaps"):
        assert downloaded[field] == exported[field]
    assert downloaded["payload"]["nodes"] == exported["payload"]["nodes"]


@pytest.mark.usefixtures("published")
def test_the_copyright_policy_names_the_version_it_was_rendered_under() -> None:
    policy = json.loads(download("copyright-policy").text)

    assert policy["version"].startswith("copyright/")
    assert policy["licencePolicy"]["version"] == CURRENT.version


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("published")
def test_a_document_that_is_not_in_the_package_is_a_problem() -> None:
    response = download("weights")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_a_release_that_does_not_exist_has_nothing_to_download() -> None:
    response = download("model-card")

    assert response.status_code == 404
    assert response.json()["code"] == "release-not-found"


@pytest.mark.usefixtures("unpublished")
def test_an_unpublished_release_is_refused_rather_than_dated_by_the_download() -> None:
    response = download("model-card")

    assert response.status_code == 409
    assert response.json()["code"] == "release-unpublished"


@pytest.mark.usefixtures("sourceless")
def test_a_summary_over_no_sources_is_refused_and_the_card_says_so() -> None:
    """A summary over nothing asserts the model was trained on nothing."""
    refused = download("training-summary")
    card = download("model-card")

    assert refused.status_code == 409
    assert refused.json()["code"] == "training-content-unrecorded"
    assert card.status_code == 200
    assert "personalDataPresent" in card.text


def test_the_operation_documents_every_media_type() -> None:
    """So a generated client knows a download is not JSON."""
    document = create_app().openapi()
    operation = document["paths"]["/v1/releases/{artefact}/documents/{document}"]["get"]

    declared = set(operation["responses"]["200"]["content"])
    assert {media.split(";")[0] for media in declared} == set(MEDIA.values())
    assert (
        "Requires: `admin`, `approver`, `curator`, `operator`, `viewer`" in operation["description"]
    )
