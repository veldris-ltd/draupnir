"""A run reaches approval with nobody asking it to.

The reconciliation recorded this as NOT BUILT: SAD 5.1 lists a worker among the
deployable units and there was not one, so a curated run sat in QUEUED until an
operator ran `make procedure`. This is the evidence that it is built.

The curator's half is done first and by hand, because it is a human's:
registering sources, clearing licences, curating and compiling a specification
are Procedures M1 to M4 and each of them is somebody's decision. From QUEUED
onwards nobody is asked anything -- the worker ticks, and the run arrives at
AWAITING_APPROVAL, which is where SAD 6.1 and Decision S6 stop it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

import draupnir_local_subprocess
from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import RunState
from draupnir.core.infrastructure.orchestration import for_connection
from draupnir.procedures.sindri import STEPS, Procedure
from draupnir.worker.duties import Duty
from draupnir.worker.loop import Worker, WorkerSettings
from draupnir.worker.stages import Result

pytestmark = pytest.mark.integration

#: A forge of its own, so that a worker committing transitions cannot move a
#: run another test left at Sindri. A site is an installation, never a node
#: (Decision S12), and a test estate is an installation like any other.
SITE = "sindri-worker-test"

#: How many ticks the run is given to cross six states. Each tick moves a run
#: once and a placed job needs at least one further tick to be observed, so the
#: floor is about eight; the rest is slack for a slow container.
TICKS = 40


@pytest.fixture
def engine(migrated: str) -> Iterator[Engine]:
    """An engine that commits. The worker's writes have to outlive its tick."""
    made = create_engine(migrated, future=True)
    yield made
    made.dispose()


@pytest.fixture
def site(engine: Engine) -> str:
    """Register the test forge and commit it."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO site (id, name, location, timezone, control_plane_uri, "
                "anchor_state) VALUES (:id, 'Worker test', 'Belfast', 'Europe/London', "
                "'https://worker.veldris.internal', 'ANCHORED') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": SITE},
        )
    return SITE


def _curate(connection: Connection, workdir: Path) -> Procedure:
    """Do the curator's half: M1 to M4, ending at QUEUED."""
    procedure = Procedure(
        orchestrator=for_connection(connection, SiteScope(SITE), actor="curator@veldris.internal"),
        workdir=workdir,
        jurisdiction="GBR",
        corpus_seed=uuid.uuid4().hex,
    )
    procedure.model = f"cim-{procedure.jurisdiction.lower()}-v1.0"
    procedure.orchestrator.register(
        procedure.run_id,
        name=procedure.model,
        spec_hash="0" * 64,
        kind="adapter",
        payload={"jurisdiction": procedure.jurisdiction, "procedure": "M1-M4"},
    )
    for _identifier, _title, _automates, step in STEPS[:4]:
        step(procedure, draupnir_local_subprocess.driver)
    return procedure


def test_the_worker_drives_a_queued_run_to_approval(
    engine: Engine, site: str, tmp_path: Path
) -> None:
    """QUEUED to AWAITING_APPROVAL, with no call from anybody. AC-F12, SAD 5.1."""
    with engine.connect() as connection:
        transaction = connection.begin()
        procedure = _curate(connection, tmp_path / "GBR")
        assert procedure.orchestrator.state_of(procedure.run_id) is RunState.QUEUED
        transaction.commit()

    worker = Worker(
        WorkerSettings(
            site_id=site,
            scratch=tmp_path / "worker",
            interval=0.05,
            perform_duties=False,
        ),
        scheduler=draupnir_local_subprocess.driver,
        engine=engine,
    )

    reached: RunState | None = None
    for _ in range(TICKS):
        worker.run_once()
        with engine.connect() as connection:
            transaction = connection.begin()
            reached = for_connection(
                connection, SiteScope(site), actor="auditor@veldris.internal"
            ).state_of(procedure.run_id)
            transaction.rollback()
        if reached in {RunState.AWAITING_APPROVAL, RunState.FAILED}:
            break

    assert reached is RunState.AWAITING_APPROVAL

    # Every state SAD 6.1 puts between QUEUED and approval was actually
    # occupied. A run that jumped one would have skipped a guard.
    with engine.connect() as connection:
        transaction = connection.begin()
        orchestrator = for_connection(connection, SiteScope(site), actor="auditor@veldris.internal")
        transitions = [entry.transition for entry in orchestrator.history(procedure.run_id)]
        transaction.rollback()

    for expected in (
        f"{RunState.QUEUED}->{RunState.TRAINING}",
        f"{RunState.TRAINING}->{RunState.TRAINED}",
        f"{RunState.TRAINED}->{RunState.EVALUATING}",
        f"{RunState.EVALUATING}->{RunState.MERGED}",
        f"{RunState.MERGED}->{RunState.QUANTISED}",
        f"{RunState.QUANTISED}->{RunState.AWAITING_APPROVAL}",
    ):
        assert expected in transitions


def test_a_second_tick_over_an_awaiting_run_changes_nothing(
    engine: Engine, site: str, tmp_path: Path
) -> None:
    """Idempotence. A worker that ticked twice must not act twice."""
    with engine.connect() as connection:
        transaction = connection.begin()
        procedure = _curate(connection, tmp_path / "GBR")
        transaction.commit()

    worker = Worker(
        WorkerSettings(
            site_id=site, scratch=tmp_path / "worker", interval=0.05, perform_duties=False
        ),
        scheduler=draupnir_local_subprocess.driver,
        engine=engine,
    )
    for _ in range(TICKS):
        report = worker.run_once()
        states = {outcome.state for outcome in report.moved}
        if RunState.AWAITING_APPROVAL in states:
            break

    with engine.connect() as connection:
        transaction = connection.begin()
        before = len(
            for_connection(connection, SiteScope(site), actor="auditor@veldris.internal").history(
                procedure.run_id
            )
        )
        transaction.rollback()

    quiet = worker.run_once()
    assert all(outcome.result is not Result.MOVED for outcome in quiet.outcomes)

    with engine.connect() as connection:
        transaction = connection.begin()
        after = len(
            for_connection(connection, SiteScope(site), actor="auditor@veldris.internal").history(
                procedure.run_id
            )
        )
        transaction.rollback()

    assert after == before


def test_the_duties_verify_the_chain_and_record_no_entry_when_it_holds(
    engine: Engine, site: str, tmp_path: Path
) -> None:
    """SAD 11.3: alarm on divergence. A verifying chain is a log line, not an entry."""
    worker = Worker(
        WorkerSettings(site_id=site, scratch=tmp_path / "worker", interval=0.05),
        scheduler=draupnir_local_subprocess.driver,
        engine=engine,
    )
    report = worker.run_once()
    performed = {finding.duty for finding in report.findings}
    assert Duty.CHAIN in performed
    chain = next(finding for finding in report.findings if finding.duty is Duty.CHAIN)
    assert not chain.alarm
    assert report.alarms == ()

    # And a second tick within the hour does not repeat it.
    again = worker.run_once()
    assert Duty.CHAIN not in {finding.duty for finding in again.findings}
