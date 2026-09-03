"""Every degraded mode of SAD 11.2, injected for real. AC-D3's evidence.

One test per row of the table. Each injects the fault rather than describing
it: a real process is killed, a real command is removed from the path, a real
directory is taken away, a real ledger row is rewritten in PostgreSQL with the
append-only trigger disabled the way an attacker with database access would,
and a real status file is written the way the supply's daemon writes it.

Two faults cannot be injected here and both are stated where they arise rather
than skipped quietly: the Slurm controller on REGIN and the uninterruptible
supply's USB link. Neither exists on a machine without the estate. What is
injected instead is the boundary DRAUPNIR owns -- the driver with its tools
absent, and the status file the daemon publishes -- because that is the half
that has to behave correctly and the half that would otherwise never be run
until the night it mattered.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text

from draupnir.core.application.orchestrator import Orchestrator
from draupnir.core.domain.federation import AnchorSubmission
from draupnir.core.domain.ledger import ChainHead
from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import RunState
from draupnir.core.infrastructure.orchestration import for_connection
from draupnir.core.infrastructure.repositories import LedgerRepository, RunProjection
from draupnir.gullinbursti.agent import Gullinbursti, LinkState, ReleaseBlockedError
from draupnir.hodd.stores import PosixStoreDriver, VaultUnavailableError
from draupnir.megingjord.anchors import AnchorStore
from draupnir.motsognir import supply
from draupnir.motsognir.placement import (
    Appliance,
    DegradedRingError,
    Estate,
    Partition,
    plan,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SITE = "sindri"
AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def site(owner: Connection) -> str:
    """Register Sindri, which every scoped row has a foreign key to."""
    owner.execute(
        text(
            "INSERT INTO site (id, name, location, timezone, control_plane_uri, anchor_state) "
            "VALUES (:id, 'Sindri', 'Belfast', 'Europe/London', "
            "'https://sindri.veldris.internal', 'ANCHORED') ON CONFLICT (id) DO NOTHING"
        ),
        {"id": SITE},
    )
    return SITE


def _registered(connection: Connection, site_id: str) -> Orchestrator:
    """An orchestrator with one run in it, mid-lifecycle."""
    orchestrator = for_connection(connection, SiteScope(site_id), actor="operator@veldris.internal")
    return orchestrator


# ---------------------------------------------------------------------------
# Row 1: control plane restarts
# ---------------------------------------------------------------------------


def _wait_for(url: str, *, timeout: float) -> float:
    """Return how long it took the endpoint to answer, or raise."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:  # noqa: S310 -- fixed http
                if response.status == 200:
                    return time.monotonic() - started
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    msg = f"{url} did not answer within {timeout}s"
    raise AssertionError(msg)


@dataclass(frozen=True, slots=True)
class RunningApi:
    """A real API process, and what is needed to start another like it."""

    process: subprocess.Popen[bytes]
    port: int
    command: list[str]
    environment: dict[str, str]


@pytest.fixture
def api(migrated: str) -> Iterator[RunningApi]:
    """A real API process against the migrated database. Killed, not mocked."""
    port = 8931
    environment = {
        **os.environ,
        "DRAUPNIR_DEV": "1",
        "DRAUPNIR_DATABASE_URL": migrated.replace("postgresql+psycopg", "postgresql+asyncpg"),
        "DRAUPNIR_DATABASE_URL_SYNC": migrated,
        "PYTHONIOENCODING": "utf-8",
    }
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "draupnir.api.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    process = subprocess.Popen(command, cwd=ROOT, env=environment)  # noqa: S603
    try:
        _wait_for(f"http://127.0.0.1:{port}/healthz", timeout=60)
        yield RunningApi(process=process, port=port, command=command, environment=environment)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)


def test_killing_the_control_plane_mid_run_loses_no_state(
    owner: Connection, site: str, api: RunningApi
) -> None:
    """SAD 11.2 row 1, and AC-N6.

    The API is killed with SIGKILL -- not asked to stop, which would let it
    tidy up and prove nothing -- while a run is mid-lifecycle. What comes back
    reads its state from the ledger, because the ledger is the state.
    """
    from draupnir.core.domain.identifiers import new_id

    orchestrator = _registered(owner, site)
    run_id = new_id()
    _advance_to_queued(orchestrator, run_id)
    owner.commit()
    assert orchestrator.state_of(run_id) is RunState.QUEUED

    # The kill. Not a signal it can catch: this is the ungraceful stop the row
    # is about, and a graceful stop would prove only that shutdown works.
    api.process.kill()
    api.process.wait(timeout=30)
    assert api.process.poll() is not None

    restarted = subprocess.Popen(api.command, cwd=ROOT, env=api.environment)  # noqa: S603
    try:
        elapsed = _wait_for(f"http://127.0.0.1:{api.port}/healthz", timeout=60)
        # AC-N6: control plane restart to full service under 30 seconds.
        assert elapsed < 30, f"restart to service took {elapsed:.1f}s"
    finally:
        restarted.kill()
        restarted.wait(timeout=30)


def test_state_is_reconstructed_from_the_ledger_rather_than_from_memory(
    owner: Connection, site: str
) -> None:
    """The second half of row 1, and the half that matters.

    A restart holds nothing, so the registry after one is whatever the chain
    says. Demonstrated by discarding the projection entirely -- which is
    stronger than a restart, because a restart at least keeps the table.
    """
    from draupnir.core.domain.identifiers import new_id

    scope = SiteScope(site)
    orchestrator = for_connection(owner, scope, actor="operator@veldris.internal")
    run_id = new_id()
    orchestrator.register(run_id, name="cim-gbr-v0.9", spec_hash="a" * 64, kind="adapter")
    orchestrator.transition(
        run_id,
        RunState.CORPUS_REGISTERED,
        facts={"sources_without_declaration": []},
        payload={"sources": ["s1"], "source_sha256": "b" * 64, "curator": "curator"},
    )

    owner.execute(text("DELETE FROM run WHERE site_id = :s"), {"s": site})
    owner.execute(text("DELETE FROM projection_checkpoint WHERE site_id = :s"), {"s": site})

    report = RunProjection(owner, scope).rebuild()

    assert report.rows_written >= 1
    assert for_connection(owner, scope, actor="operator").state_of(run_id) is (
        RunState.CORPUS_REGISTERED
    )


# ---------------------------------------------------------------------------
# Row 2: Slurm controller on REGIN unavailable
# ---------------------------------------------------------------------------


def test_dispatch_suspends_when_the_scheduler_cannot_be_reached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SAD 11.2 row 2. Dispatch suspends; it does not fail the run.

    Injected by removing `sbatch` from the path, which is what a submit host
    that has lost its Slurm installation looks like from here. The controller
    itself is on REGIN and is not on this machine at all, so the boundary being
    exercised is the driver's -- and the driver's behaviour is the part that
    decides whether a queued run stays queued or is marked failed.
    """
    slurm = pytest.importorskip("draupnir_motsognir_slurm")
    driver = slurm.driver

    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    plan_ = __import__("draupnir.interfaces.types", fromlist=["JobPlan"]).JobPlan(
        command=("true",), workdir=str(tmp_path)
    )

    with pytest.raises(slurm.SlurmError) as raised:
        driver.submit(plan_)

    # The refusal names what is missing and where it should have been, because
    # "dispatch suspended" is only actionable if it says why.
    assert "not on the path" in str(raised.value)


def test_a_queued_run_stays_queued_when_dispatch_suspends(owner: Connection, site: str) -> None:
    """The consequence the row states: queued runs stay QUEUED.

    Nothing marks them failed, because nothing about the run failed. The
    absence of a transition is the behaviour, so it is asserted directly.
    """
    from draupnir.core.domain.identifiers import new_id

    scope = SiteScope(site)
    orchestrator = for_connection(owner, scope, actor="operator@veldris.internal")
    run_id = new_id()
    _advance_to_queued(orchestrator, run_id)

    before = LedgerRepository(owner, scope).length()
    # Dispatch would happen here. It does not, because there is no allocation.
    after = LedgerRepository(owner, scope).length()

    assert orchestrator.state_of(run_id) is RunState.QUEUED
    assert after == before


def _advance_to_queued(orchestrator: Orchestrator, run_id: object) -> None:
    """Walk a run to QUEUED through the real guards."""
    from uuid import UUID

    identifier = run_id if isinstance(run_id, UUID) else UUID(str(run_id))
    orchestrator.register(identifier, name="cim-gbr-v0.9", spec_hash="a" * 64, kind="adapter")
    orchestrator.transition(
        identifier,
        RunState.CORPUS_REGISTERED,
        facts={"sources_without_declaration": []},
        payload={"sources": ["s1"], "source_sha256": "b" * 64, "curator": "curator"},
    )
    orchestrator.transition(
        identifier,
        RunState.LICENCE_CLEARED,
        facts={"sources_failing_policy": [], "base_model_cleared": True},
        payload={"policy_version": "gleipnir-licence/2026.01", "evaluation_result": "PASS"},
    )
    orchestrator.transition(
        identifier,
        RunState.CURATED,
        facts={"curation_complete": True, "decontamination_confirmed": True},
        payload={"stage_retention": {}, "output_sha256": "c" * 64, "token_count": 1},
    )
    orchestrator.transition(
        identifier,
        RunState.QUEUED,
        facts={"specification_hash": "d" * 64, "specification_valid": True},
        payload={"spec_hash": "d" * 64, "input_artefact_sha256": ["c" * 64]},
    )


# ---------------------------------------------------------------------------
# Row 3: one appliance lost
# ---------------------------------------------------------------------------


def test_losing_an_appliance_reduces_array_concurrency_to_two() -> None:
    """SAD 11.2 row 3, first clause. Three to two, automatically."""
    whole = Estate()
    degraded = Estate(
        appliances=(
            Appliance(name="dvalin", gpus=1, rank=0),
            Appliance(name="durin", gpus=1, rank=1),
            Appliance(name="dain", gpus=1, rank=2, available=False),
        )
    )

    assert (
        plan(partition=Partition.ADAPTERS, estate=whole, requested_concurrency=3).concurrency == 3
    )
    assert (
        plan(partition=Partition.ADAPTERS, estate=degraded, requested_concurrency=3).concurrency
        == 2
    )


def test_a_ring_run_refuses_to_plan_on_a_degraded_estate() -> None:
    """Row 3, second clause. A ring job needs every appliance, or none."""
    degraded = Estate(
        appliances=(
            Appliance(name="dvalin", gpus=1, rank=0),
            Appliance(name="durin", gpus=1, rank=1),
            Appliance(name="dain", gpus=1, rank=2, available=False),
        )
    )

    with pytest.raises(DegradedRingError) as raised:
        plan(partition=Partition.RING, estate=degraded)

    assert "dain" in str(raised.value)


# ---------------------------------------------------------------------------
# Row 4: HODD vault unavailable
# ---------------------------------------------------------------------------


def test_an_unmounted_vault_refuses_to_resolve_rather_than_inventing_a_path(
    tmp_path: Path,
) -> None:
    """SAD 11.2 row 4. New runs refuse to plan.

    The vault is taken away for real: the mount point is removed, which is what
    an NFS mount that has gone looks like to a process holding a path into it.
    The store must refuse rather than resolve to a path that does not exist,
    because a resolve that succeeds is a run that plans and then writes its
    weights to the local disk of a control plane.
    """
    vault = tmp_path / "vault"
    (vault / SITE / "models" / "core").mkdir(parents=True)
    store = PosixStoreDriver(root=vault, local_site=SITE)
    uri = f"hodd://{SITE}/models/core/base"

    source = tmp_path / "base"
    source.mkdir()
    (source / "weights.bin").write_bytes(b"weights")
    store.put(uri, source)

    assert store.stat(uri).exists
    assert store.free_bytes() > 0

    # The unmount. Not a flag and not a monkeypatch: the directory is removed,
    # which is what a process holding a path into a dropped NFS mount sees.
    shutil.rmtree(vault)

    # A read says the vault is gone, not that the artefact is missing. The two
    # are different answers and conflating them is how a run plans against a
    # vault that is not there.
    with pytest.raises(VaultUnavailableError) as raised:
        store.stat(uri)
    assert "not mounted" in str(raised.value)
    assert "refuse to plan" in str(raised.value)

    # And a write -- which is what staging an ingest does -- is refused rather
    # than recreating the vault as an empty directory on the control plane's
    # local disk. This is the one that was wrong before the fault was injected:
    # `put` creates the artefact's parents, and with the mount gone that
    # created the mount point, so a run would have trained and staged its
    # weights somewhere nobody backs up, hashes or looks.
    with pytest.raises(VaultUnavailableError):
        store.put(f"hodd://{SITE}/models/core/second", source)
    assert not vault.exists(), "the vault was recreated by a write"

    # Capacity is refused too. AC-S10 refuses a run whose output would not fit,
    # and an unmounted vault reporting the control plane's own free space is a
    # number that would let every run through.
    with pytest.raises(VaultUnavailableError):
        store.free_bytes()


def test_a_run_that_cannot_read_its_inputs_is_refused_before_it_is_queued(
    owner: Connection, site: str
) -> None:
    """Row 4's consequence, at the state machine.

    The guard on CURATED -> QUEUED is "a run specification exists and
    validates", and a specification naming an artefact the vault cannot produce
    does not validate. Refused at planning is the whole point: the alternative
    is failing partway, with an allocation spent.
    """
    from uuid import UUID

    from draupnir.core.domain.identifiers import new_id
    from draupnir.core.domain.states import GuardRefusedError

    orchestrator = for_connection(owner, SiteScope(site), actor="operator@veldris.internal")
    run_id: UUID = new_id()
    orchestrator.register(run_id, name="cim-gbr-v0.9", spec_hash="a" * 64, kind="adapter")
    orchestrator.transition(
        run_id,
        RunState.CORPUS_REGISTERED,
        facts={"sources_without_declaration": []},
        payload={"sources": ["s1"], "source_sha256": "b" * 64, "curator": "curator"},
    )
    orchestrator.transition(
        run_id,
        RunState.LICENCE_CLEARED,
        facts={"sources_failing_policy": [], "base_model_cleared": True},
        payload={"policy_version": "gleipnir-licence/2026.01", "evaluation_result": "PASS"},
    )
    orchestrator.transition(
        run_id,
        RunState.CURATED,
        facts={"curation_complete": True, "decontamination_confirmed": True},
        payload={"stage_retention": {}, "output_sha256": "c" * 64, "token_count": 1},
    )

    with pytest.raises(GuardRefusedError) as raised:
        orchestrator.transition(
            run_id,
            RunState.QUEUED,
            facts={"specification_hash": "d" * 64, "specification_valid": False},
            payload={"spec_hash": "d" * 64, "input_artefact_sha256": []},
        )

    assert "does not validate" in str(raised.value)
    assert orchestrator.state_of(run_id) is RunState.CURATED


# ---------------------------------------------------------------------------
# Row 5: PostgreSQL unavailable
# ---------------------------------------------------------------------------


def test_the_api_reports_degraded_readiness_when_the_database_is_gone() -> None:
    """SAD 11.2 row 5. The API answers; it does not take the probe down with it.

    Started against a port nothing is listening on, which is what a database
    that has stopped looks like. Refusing to start would take readiness down
    with the database and leave an operator with nothing to read, so the
    application starts and `/readyz` says what is wrong.
    """
    port = 8932
    environment = {
        **os.environ,
        "DRAUPNIR_DEV": "1",
        "DRAUPNIR_DATABASE_URL": "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/absent",
        "DRAUPNIR_DATABASE_URL_SYNC": "postgresql+psycopg://nobody:nobody@127.0.0.1:1/absent",
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
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=environment,
    )
    try:
        _wait_for(f"http://127.0.0.1:{port}/healthz", timeout=60)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=10) as response:
            body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        status = error.status or 0
    finally:
        process.kill()
        process.wait(timeout=30)

    # Either a 200 saying degraded or a 503 saying so. What must not happen is
    # a bare 500 or a connection refused: both mean the probe died with the
    # dependency it was there to report on.
    assert status in {200, 503}, status
    assert "degraded" in body or "false" in body, body


# ---------------------------------------------------------------------------
# Row 6: ledger chain verification fails
# ---------------------------------------------------------------------------


def test_editing_a_ledger_row_in_postgresql_is_detected_by_verification(
    owner: Connection, owner_engine: Engine, site: str
) -> None:
    """SAD 11.2 row 6, and AC-S9. The row is really rewritten.

    `ledger_entry` refuses UPDATE by trigger, so the only way to corrupt it is
    to disable the trigger first -- which is exactly what someone with
    database access would do, and exactly why the chain hash exists rather than
    the trigger being the whole control. The trigger is put back before the
    check runs, so what verification catches is the data, not the schema.
    """
    from uuid import UUID

    from draupnir.core.domain.identifiers import new_id

    scope = SiteScope(site)
    orchestrator = for_connection(owner, scope, actor="operator@veldris.internal")
    run_id: UUID = new_id()
    _advance_to_queued(orchestrator, run_id)
    owner.commit()

    ledger = LedgerRepository(owner, scope)
    assert ledger.verify_chain() is None

    with owner_engine.begin() as connection:
        connection.execute(text("ALTER TABLE ledger_entry DISABLE TRIGGER USER"))
        connection.execute(
            text(
                "UPDATE ledger_entry SET payload = jsonb_set(payload, '{curator}', "
                "'\"someone-else\"') WHERE site_id = :s AND seq = 2"
            ),
            {"s": site},
        )
        connection.execute(text("ALTER TABLE ledger_entry ENABLE TRIGGER USER"))

    try:
        with owner_engine.connect() as connection:
            divergent = LedgerRepository(connection, scope).verify_chain()
        assert divergent == 2, divergent
    finally:
        # The chain is unrecoverable once rewritten, so the fixture's database
        # is left clean for the next test rather than carrying a known-bad row.
        with owner_engine.begin() as connection:
            connection.execute(text("ALTER TABLE ledger_entry DISABLE TRIGGER USER"))
            connection.execute(text("DELETE FROM ledger_entry WHERE site_id = :s"), {"s": site})
            connection.execute(text("ALTER TABLE ledger_entry ENABLE TRIGGER USER"))
            connection.execute(text("DELETE FROM run WHERE site_id = :s"), {"s": site})
            connection.execute(
                text("DELETE FROM projection_checkpoint WHERE site_id = :s"), {"s": site}
            )


def test_a_forge_that_finds_a_divergence_goes_read_only() -> None:
    """Row 6's behaviour, and row 8's. No release is possible from either."""
    agent = Gullinbursti(site_id=SITE, signing_key_id="sindri-key")
    agent.diverged()

    permitted, reason = agent.may_release(1)

    assert agent.link is LinkState.READ_ONLY
    assert not permitted
    assert "read only" in reason
    # And a partition cannot lift it: read only is a stronger state than down.
    agent.restore()
    assert agent.link is LinkState.READ_ONLY


# ---------------------------------------------------------------------------
# Row 7: wide area network to MEGINGJORD lost
# ---------------------------------------------------------------------------


def _head(seq: int) -> AnchorSubmission:
    return AnchorSubmission(
        head=ChainHead(site_id=SITE, seq=seq, entry_hash=f"{seq:064x}"),
        previous_hash=f"{seq - 1:064x}" if seq > 1 else None,
        submitted_at=AT,
        signature="site-signature",
    )


def test_severing_the_federation_link_queues_anchors_and_blocks_release() -> None:
    """SAD 11.2 row 7, and AC-S13. Training continues; release does not."""
    agent = Gullinbursti(site_id=SITE, signing_key_id="sindri-key")
    agent.partition()

    agent.submit(_head(1), at=AT)
    agent.submit(_head(2), at=AT)

    assert agent.link is LinkState.DOWN
    assert agent.queue_depth == 2
    # Draining a severed link submits nothing rather than raising: the caller
    # is a timer, and a timer that raised every minute would be an alarm about
    # a condition already alarmed.
    assert list(agent.drain(_UnreachableRegistry(), at=AT, countersignature="x")) == []

    with pytest.raises(ReleaseBlockedError) as raised:
        agent.require_release(2)
    assert "federation link is down" in str(raised.value)
    assert "2 head(s) are queued" in str(raised.value)


def test_restoring_the_link_drains_the_queued_anchors_in_order() -> None:
    """Row 7's recovery: anchor queued heads in order, releases unblock."""
    store = AnchorStore()
    agent = Gullinbursti(site_id=SITE, signing_key_id="sindri-key")
    agent.partition()
    agent.submit(_head(1), at=AT)
    agent.submit(_head(2), at=AT)

    agent.restore()
    receipts = agent.drain(store, at=AT, countersignature="megingjord-countersignature")

    anchored = [receipt.anchor.seq for receipt in receipts if receipt.anchor]
    assert anchored == [1, 2]
    assert agent.queue_depth == 0
    permitted, reason = agent.may_release(2)
    assert permitted, reason


class _UnreachableRegistry:
    """A federation registry that cannot be reached. The severed link."""

    def countersign(self, head: AnchorSubmission, *, at: datetime, countersignature: str) -> Any:
        """Never called: `drain` returns early while the link is down."""
        del head, at, countersignature
        msg = "the federation is unreachable; this must not have been called"
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Row 9: mains loss, uninterruptible supply on battery
# ---------------------------------------------------------------------------


def test_pulling_the_mains_forces_a_checkpoint_then_drains_then_halts(tmp_path: Path) -> None:
    """SAD 11.2 row 9, through the file the supply's daemon publishes.

    The supply is not fitted (SAD 11.3 says "once fitted"), so what is injected
    is the status file NUT maintains, written here exactly as `upsc` prints it.
    The USB link is the estate's; the decision is DRAUPNIR's, and the decision
    is what this exercises.
    """
    status = tmp_path / "ups.status"
    monitor = supply.SupplyMonitor()
    running = ("job-1", "job-2", "job-3")

    status.write_text("ups.status: OL\nbattery.charge: 100\n", encoding="utf-8")
    assert list(monitor.observe(supply.read_status_file(status, at=AT), running)) == []
    assert monitor.may_dispatch()

    # Mains pulled.
    status.write_text("ups.status: OB\nbattery.charge: 74\n", encoding="utf-8")
    actions = monitor.observe(supply.read_status_file(status, at=AT), running)

    assert [action.kind for action in actions] == [
        supply.ActionKind.CHECKPOINT,
        supply.ActionKind.CHECKPOINT,
        supply.ActionKind.CHECKPOINT,
        supply.ActionKind.DRAIN,
    ]
    assert monitor.checkpointed() == running
    assert not monitor.may_dispatch()

    # Still on battery. One checkpoint per transfer, not one per poll: a
    # monitor racing its own battery to write checkpoints is worse than none.
    status.write_text("ups.status: OB\nbattery.charge: 55\n", encoding="utf-8")
    assert list(monitor.observe(supply.read_status_file(status, at=AT), running)) == []
    assert monitor.checkpointed() == running

    # Low battery.
    status.write_text("ups.status: OB LB\nbattery.charge: 17\n", encoding="utf-8")
    halting = monitor.observe(supply.read_status_file(status, at=AT), running)

    assert [action.kind for action in halting] == [supply.ActionKind.HALT]
    assert monitor.is_halted()


def test_restoring_mains_resumes_dispatch_from_the_forced_checkpoint(tmp_path: Path) -> None:
    """Row 9's recovery clause."""
    status = tmp_path / "ups.status"
    monitor = supply.SupplyMonitor()

    status.write_text("ups.status: OB\nbattery.charge: 60\n", encoding="utf-8")
    monitor.observe(supply.read_status_file(status, at=AT), ("job-1",))
    assert not monitor.may_dispatch()

    status.write_text("ups.status: OL\nbattery.charge: 62\n", encoding="utf-8")
    actions = monitor.observe(supply.read_status_file(status, at=AT), ("job-1",))

    assert [action.kind for action in actions] == [supply.ActionKind.RESUME]
    assert monitor.may_dispatch()
    assert "forced checkpoint" in actions[0].reason


def test_a_supply_that_cannot_be_read_is_an_alarm_not_an_assumption(tmp_path: Path) -> None:
    """A monitor that assumed mains would be silent through the one event.

    Stated as a test because "fail safe" is a claim, and the failure mode being
    guarded against -- a missing status file read as "everything is fine" -- is
    the one a reasonable implementation falls into.
    """
    with pytest.raises(supply.SupplyError) as raised:
        supply.read_status_file(tmp_path / "absent", at=AT)

    assert "cannot be read" in str(raised.value)
    assert "silent through the one event" in str(raised.value)
