"""A release is admitted, and the four ways it is refused. RF-05, AC-S8.

The publication handler used to read one entry from the chain and, if it
existed, record a `published` entry. Its own docstring named four controls --
AC-S8's re-hash, AC-F9's per-format evidence, the approval signature, AC-S13's
anchor -- and it called none of them, while `skidbladnir.publish` implemented
all four and was reached by nothing.

The contract tests cover the refusals that need no store. This is the other
half, and it needs the real thing: an artefact in a real vault whose bytes hash
to the gated digest, gate evidence for every format that was built, and a
countersigned anchor at or beyond the release's sequence. Without a fixture
that can produce an *admitted* publication, "everything is refused" would pass
just as well against a handler that refuses unconditionally.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text

from draupnir.core.domain.federation import ANCHOR_SUBMITTED
from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import RunState
from draupnir.core.infrastructure.orchestration import for_connection
from draupnir.hodd.stores import PosixStoreDriver, artefact_uri

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SITE = "sindri-release-test"
PORT = 8936
BASE = f"http://127.0.0.1:{PORT}"
DEV_ACTOR = "dev@veldris.internal"

#: The format published. One rather than three, because AC-F9 is about the
#: relation between what was built and what was evaluated, and one built format
#: with evidence proves it as well as three do.
FORMAT = "nvfp4"


# ---------------------------------------------------------------------------
# The estate this runs against
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real HODD vault, with the marker that makes it one.

    Written here rather than by the API, deliberately: `reconcile.initialise`
    exists so that a directory becomes the vault when somebody commissions it,
    and a process that wrote the marker on finding it missing would turn the
    state that check exists to detect into the state it certifies. A test is
    the commissioning operator.
    """
    from draupnir.hodd.reconcile import initialise

    root = tmp_path_factory.mktemp("vault")
    initialise(PosixStoreDriver(root=root, local_site=SITE))
    return root


@pytest.fixture(scope="module")
def store(vault: Path) -> PosixStoreDriver:
    """The driver this test puts artefacts through, and the API reads through."""
    return PosixStoreDriver(root=vault, local_site=SITE)


@pytest.fixture(scope="module")
def api(request: pytest.FixtureRequest, vault: Path) -> Iterator[str]:
    """An API process that can reach the vault.

    `DRAUPNIR_VAULT_ROOT` is what makes `hodd.stores.store_for` hand back the
    POSIX driver rather than reaching for MinIO -- which is how Sindri is
    configured, and which is why the publication path goes through that factory
    rather than building an object-store client inline (RF-08).
    """
    migrated = request.getfixturevalue("migrated")
    environment = {
        **os.environ,
        "DRAUPNIR_DEV": "1",
        "DRAUPNIR_DATABASE_URL": migrated.replace("postgresql+psycopg", "postgresql+asyncpg"),
        "DRAUPNIR_DATABASE_URL_SYNC": migrated,
        "DRAUPNIR_SITE_ID": SITE,
        "DRAUPNIR_VAULT_ROOT": str(vault),
        "PYTHONIOENCODING": "utf-8",
    }
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "draupnir.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=environment,
    )
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{BASE}/healthz", timeout=1) as response:  # noqa: S310
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.1)
        else:
            pytest.fail("the API did not start")
        yield BASE
    finally:
        process.kill()
        process.wait(timeout=30)


@pytest.fixture
def site(owner: Connection) -> Iterator[str]:
    """Register this test's forge and commit it: the API is another process."""
    owner.execute(
        text(
            "INSERT INTO site (id, name, location, timezone, control_plane_uri, "
            "anchor_state) VALUES (:id, 'Release test', 'Belfast', 'Europe/London', "
            "'https://sindri.veldris.internal', 'ANCHORED') ON CONFLICT (id) DO NOTHING"
        ),
        {"id": SITE},
    )
    owner.commit()
    yield SITE


# ---------------------------------------------------------------------------
# Talking to it
# ---------------------------------------------------------------------------


def _tag(state: dict[str, Any]) -> str:
    """The entity tag the handler will compute for this state.

    Derived with the real function rather than hard-coded: a tag written into a
    test stops matching the moment the derivation changes, and the test then
    fails for a reason that has nothing to do with what it checks.
    """
    from draupnir.api import concurrency

    return concurrency.etag(state)


def _publish(artefact: str) -> tuple[int, dict[str, Any]]:
    """POST the publication, as the console does."""
    request = urllib.request.Request(  # noqa: S310 -- fixed http, fixed host
        f"{BASE}/v1/releases/{artefact}/publish",
        data=b"",
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": uuid.uuid4().hex,
            "If-Match": _tag({"artefact": artefact}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.status or 0, json.loads(error.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Building a chain that can be published from
# ---------------------------------------------------------------------------

#: The lifecycle of SAD 6.1, walked through the real guards. Everything before
#: quantisation is scenery; the last two entries are the ones a publication
#: reads.
_SPINE: tuple[tuple[RunState, dict[str, Any], dict[str, Any]], ...] = (
    (
        RunState.CORPUS_REGISTERED,
        {"sources_without_declaration": []},
        {"sources": ["s1"], "source_sha256": "b" * 64, "curator": DEV_ACTOR},
    ),
    (
        RunState.LICENCE_CLEARED,
        {"sources_failing_policy": [], "base_model_cleared": True},
        {"policy_version": "gleipnir-licence/2026.01", "evaluation_result": "PASS"},
    ),
    (
        RunState.CURATED,
        {"curation_complete": True, "decontamination_confirmed": True},
        {"stage_retention": {}, "output_sha256": "c" * 64, "token_count": 1},
    ),
    (
        RunState.QUEUED,
        {"specification_hash": "d" * 64, "specification_valid": True},
        {"spec_hash": "d" * 64, "input_artefact_sha256": ["c" * 64]},
    ),
    (
        RunState.TRAINING,
        {"scheduler_job_id": "job-1"},
        {"scheduler_job_id": "job-1", "node": "dvalin", "placement": {"partition": "adapters"}},
    ),
    (
        RunState.TRAINED,
        {"exit_code": 0, "checkpoint_sha256": "e" * 64},
        {"checkpoint_sha256": "e" * 64, "steps": 10, "final_loss": 1.0},
    ),
    (
        RunState.EVALUATING,
        {"suite_version": "2026.01"},
        {"suite_version": "2026.01", "baseline": "run://base"},
    ),
    (RunState.MERGED, {"failing_gates": []}, {"gate_results": {"E1": {"passed": True}}}),
)


def _evidence(digest: str, *, passed: bool = True) -> dict[str, Any]:
    """One format's gate result, in the shape `_evidence_from` reads back."""
    return {
        FORMAT: {
            "artefactSha256": digest,
            "artefactKind": "quantised",
            "format": FORMAT,
            "suite": "release",
            "suiteVersion": "2026.01",
            "evaluatedAt": "2026-03-02T08:00:00+00:00",
            "passed": passed,
        }
    }


class Release:
    """One run walked to a publishable state, and what it published."""

    def __init__(self, run_id: UUID, digest: str, uri: str) -> None:
        self.run_id = run_id
        self.digest = digest
        self.uri = uri


def _stage(
    store: PosixStoreDriver, tmp_path: Path, run_id: UUID, payload: bytes
) -> tuple[str, str]:
    """Put bytes in the vault the way the worker does, and return (digest, uri).

    Sealed, because that is what the worker does at the put rather than at the
    approval: AC-S8 re-hashes at publication to *detect* a post-gate change, and
    a seal placed only once a release is approved leaves the whole window
    between evaluation and approval open (RF-08).
    """
    source = tmp_path / f"{FORMAT}.bin"
    source.write_bytes(payload)
    uri = artefact_uri(SITE, "quantised", str(run_id), f"{FORMAT}.bin")
    store.put(uri, source)
    store.seal(uri)
    return hashlib.sha256(payload).hexdigest(), uri


def _release(
    owner_engine: Engine,
    store: PosixStoreDriver,
    tmp_path: Path,
    *,
    evidence_digest: str | None = None,
    formats_built: tuple[str, ...] = (FORMAT,),
    approve: bool = True,
    anchor: bool | int = True,
) -> Release:
    """Walk a run to a published-from state, with every knob a refusal needs.

    The defaults produce the admitted case. Each parameter withdraws exactly one
    of the four controls, so a test that expects a refusal is a test that
    changed one thing.
    """
    run_id = uuid.uuid4()
    payload = f"the gated bytes of {run_id}".encode()

    with owner_engine.begin() as connection:
        orchestrator = for_connection(connection, SiteScope(SITE), actor=DEV_ACTOR)
        orchestrator.register(
            run_id,
            name=f"cim-gbr-{run_id.hex[:8]}",
            spec_hash="d" * 64,
            kind="adapter",
            identity=uuid.uuid4().hex * 2,
            payload={"retry_budget": 2},
        )
        for target, facts, entry in _SPINE:
            orchestrator.transition(run_id, target, facts=facts, payload=entry)

    digest, uri = _stage(store, tmp_path, run_id, payload)
    gated = evidence_digest or digest

    with owner_engine.begin() as connection:
        orchestrator = for_connection(connection, SiteScope(SITE), actor=DEV_ACTOR)
        orchestrator.transition(
            run_id,
            RunState.QUANTISED,
            facts={"failing_gates": []},
            payload={
                "merge_config_hash": "f" * 64,
                "sweep_result": {"points": 5},
                "formats_built": {FORMAT: digest},
                # Where the bytes went, as the worker records it (RF-08). Without
                # this the publication path refuses at the first control: the
                # chain records no location, so AC-S8's re-hash has nothing to
                # hash.
                "artefacts": [{"uri": uri, "sha256": digest}],
            },
        )
        orchestrator.transition(
            run_id,
            RunState.AWAITING_APPROVAL,
            facts={"formats_regated": list(formats_built), "formats_failing": []},
            payload={
                "format_gate_results": _evidence(gated),
                "formats": list(formats_built),
            },
        )

        if approve:
            applied = orchestrator.transition(
                run_id,
                RunState.RELEASED,
                facts={
                    "approver_has_role": True,
                    "decision": "APPROVED",
                    "signature": "sig",
                    "signature_verified": True,
                },
                payload={
                    "approver": "akuma@veldris.internal",
                    "signature": "sig",
                    "signature_verified": True,
                    "decided_at": "2026-03-02T09:00:00+00:00",
                    "decision": "approved",
                    "artefact_sha256": digest,
                    "model": "cim-gbr-v1.0",
                    "formats": list(formats_built),
                },
            )
            through = applied.entry.seq if anchor is True else anchor
            if anchor is not False:
                orchestrator.record(
                    subject_type="site",
                    subject_id=SITE,
                    transition=ANCHOR_SUBMITTED,
                    payload={
                        "seq": int(through),
                        "outcome": "countersigned",
                        "countersignature": "c" * 64,
                        "reason": "",
                        "anchored_through": int(through),
                        "anchored_at": "2026-03-02T09:05:00+00:00",
                    },
                )

    return Release(run_id, digest, uri)


# ---------------------------------------------------------------------------
# The admitted path
# ---------------------------------------------------------------------------


def test_a_release_with_every_control_satisfied_is_published(
    api: str,
    owner: Connection,
    owner_engine: Engine,
    site: str,
    store: PosixStoreDriver,
    tmp_path: Path,
) -> None:
    """The fixture RF-05 was missing, and the only one that proves the rest.

    "Everything is refused" passes just as well against a handler that refuses
    unconditionally. This is the case where the bytes in the vault hash to the
    digest the gates passed, every built format has passing evidence, the
    approval is signed and names those bytes, and the federation has
    countersigned at or beyond the release's sequence.
    """
    del api, site
    release = _release(owner_engine, store, tmp_path)

    status, body = _publish(release.digest)

    assert status == 202, body
    assert body["artefactSha256"] == release.digest

    recorded = owner.execute(
        text("SELECT transition FROM ledger_entry WHERE site_id = :site AND subject_id = :subject"),
        {"site": SITE, "subject": release.digest},
    ).fetchall()
    assert recorded, "an admitted publication recorded nothing"


def test_publishing_twice_replays_rather_than_recording_again(
    api: str, owner_engine: Engine, site: str, store: PosixStoreDriver, tmp_path: Path
) -> None:
    """The idempotency key is what makes a lost response quiet.

    A console that retries a request whose response was lost must not produce a
    second publication event, because an auditor counting publications would
    then be counting network conditions.
    """
    del api, site
    release = _release(owner_engine, store, tmp_path)

    first, body = _publish(release.digest)
    second, again = _publish(release.digest)

    assert first == 202, body
    assert second == 202, again
    assert again["artefactSha256"] == body["artefactSha256"]


# ---------------------------------------------------------------------------
# The four refusals, one control withdrawn at a time
# ---------------------------------------------------------------------------


def test_bytes_that_changed_after_the_gate_are_refused(
    api: str,
    owner: Connection,
    owner_engine: Engine,
    site: str,
    store: PosixStoreDriver,
    tmp_path: Path,
) -> None:
    """AC-S8 and threat T8: the reason the hash is computed rather than looked up.

    The evidence names one digest and the vault holds another. A lookup answers
    "what did we record about this artefact"; the question at publication is
    "what do we know about these bytes".
    """
    del api, site
    release = _release(owner_engine, store, tmp_path, evidence_digest="a" * 64)

    status, body = _publish(release.digest)

    assert status == 409, body
    assert body["code"] == "artefact-mismatch", body
    assert entries_for(owner, release.digest) == [], "a refused publication wrote to the chain"


def test_a_format_that_was_built_and_never_evaluated_is_refused(
    api: str, owner_engine: Engine, site: str, store: PosixStoreDriver, tmp_path: Path
) -> None:
    """AC-F9, driven by what was built rather than by what was evaluated.

    Iterating the evidence confirms that everything evaluated passed -- which is
    true of an empty set, and of a set missing the one format nobody ran. So the
    check is over the built list, and a second format with no evidence refuses
    the release even though the first one's evidence is perfect.
    """
    del api, site
    release = _release(owner_engine, store, tmp_path, formats_built=(FORMAT, "gguf-q4km"))

    status, body = _publish(release.digest)

    assert status == 409, body
    assert body["code"] == "release-inadmissible", body
    assert "gguf-q4km" in body["detail"], "the refusal does not name the format with no evidence"


def test_an_artefact_with_no_approval_is_refused(
    api: str, owner_engine: Engine, site: str, store: PosixStoreDriver, tmp_path: Path
) -> None:
    """SAD 5.2 and AC-S5.

    Approval is the permission, and it is not implied by a run having reached
    the end of its evaluation.
    """
    del api, site
    release = _release(owner_engine, store, tmp_path, approve=False)

    status, body = _publish(release.digest)

    assert status == 409, body
    assert body["code"] == "release-unapproved", body


def test_a_release_ahead_of_the_countersigned_anchor_is_refused(
    api: str, owner_engine: Engine, site: str, store: PosixStoreDriver, tmp_path: Path
) -> None:
    """AC-S13, and Decision S8's shape.

    A release published against a chain the federation has not countersigned to
    is an artefact in the registry whose provenance no other site can attest.
    The forge keeps training; it does not publish. RF-07 is what makes this
    reachable at all -- until the anchor duty recorded outcomes, this number was
    zero for every estate and the refusal was indistinguishable from a bug.
    """
    del api, site
    release = _release(owner_engine, store, tmp_path, anchor=1)

    status, body = _publish(release.digest)

    assert status == 409, body
    assert body["code"] == "anchor-behind", body


def test_a_release_at_a_forge_that_has_never_anchored_is_refused(
    api: str, owner_engine: Engine, site: str, store: PosixStoreDriver, tmp_path: Path
) -> None:
    """Zero refuses everything, correctly.

    An estate with no federation link has no countersigned chain head. This is
    every forge today, which is why the refusal has to be legible rather than
    merely correct.
    """
    del api, site
    release = _release(owner_engine, store, tmp_path, anchor=False)

    status, body = _publish(release.digest)

    assert status == 409, body
    assert body["code"] == "anchor-behind", body


def entries_for(owner: Connection, subject_id: str) -> list[Any]:
    """Every entry about one subject, for asserting that none was written."""
    return list(
        owner.execute(
            text(
                "SELECT transition FROM ledger_entry WHERE site_id = :site "
                "AND subject_id = :subject"
            ),
            {"site": SITE, "subject": subject_id},
        ).fetchall()
    )
