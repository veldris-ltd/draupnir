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
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

import draupnir_local_subprocess
from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import RunState
from draupnir.core.infrastructure.orchestration import for_connection
from draupnir.procedures.sindri import STEPS, Procedure
from draupnir.worker.duties import Duty, Timetable
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


def _select_when_waiting(engine: Engine, site: str, run_id: Any) -> None:
    """Choose a merge point when the worker is waiting for one, as an operator would.

    The worker merges and re-gates every point of the sweep and then waits at
    MERGED (RF-27): choosing among the points RAUN passed is S15's primary
    action, and not the worker's. A test that drives a run to approval has to
    make that choice, the way an operator does, or the run waits for ever.
    """
    from draupnir.brisingamen import sweep as sweeps

    with engine.begin() as connection:
        operator = for_connection(connection, SiteScope(site), actor="operator@veldris.internal")
        recorded = sweeps.fold(operator.history(run_id))
        if recorded is None or recorded.selected is not None or not recorded.passing:
            return
        chosen = recorded.passing[0]
        operator.record(
            subject_type=sweeps.SWEEP_SUBJECT,
            subject_id=str(run_id),
            transition=sweeps.SELECTED,
            payload={
                "parameters": dict(chosen.parameters),
                "label": chosen.label,
                "configHash": chosen.config_hash(),
                "artefactSha256": chosen.artefact_sha256,
                "criterion": None,
            },
        )


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
            # The development executor, asked for by name. RF-10: the worker
            # renders the driver each specification names, and this
            # specification names LLaMA-Factory -- which is not installed here,
            # needs a GPU, and would take hours. What is under test is the
            # lifecycle, not the trainer.
            #
            # Asked for rather than fallen back to, which is the whole of the
            # finding: the worker used to dispatch a stand-in for every run on
            # every estate, and nothing said so.
            stand_in=True,
        ),
        scheduler=draupnir_local_subprocess.driver,
        engine=engine,
    )

    reached: RunState | None = None
    for _ in range(TICKS):
        worker.run_once()
        _select_when_waiting(engine, site, procedure.run_id)
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

    # AC-F8 through the worker (RF-27). Every point merged and re-gated, one
    # chosen, and the run quantised from exactly that point -- where it used to
    # merge once and record how many points the sweep had.
    from draupnir.brisingamen import sweep as sweeps

    assert sweeps.EVALUATED in transitions
    assert sweeps.SELECTED in transitions
    with engine.connect() as connection:
        transaction = connection.begin()
        history = for_connection(
            connection, SiteScope(site), actor="auditor@veldris.internal"
        ).history(procedure.run_id)
        transaction.rollback()

    recorded = sweeps.fold(history)
    assert recorded is not None
    assert recorded.complete, "a point of the sweep was never merged and re-gated"
    assert recorded.selected_point is not None
    quantised = next(
        entry
        for entry in history
        if entry.transition == f"{RunState.MERGED}->{RunState.QUANTISED}"
    )
    assert isinstance(quantised.payload, dict)
    assert quantised.payload["merge_config_hash"] == recorded.selected_point.config_hash()
    assert quantised.payload["merged_sha256"] == recorded.selected_point.artefact_sha256


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
            site_id=site,
            scratch=tmp_path / "worker",
            interval=0.05,
            perform_duties=False,
            stand_in=True,
        ),
        scheduler=draupnir_local_subprocess.driver,
        engine=engine,
    )
    for _ in range(TICKS):
        report = worker.run_once()
        _select_when_waiting(engine, site, procedure.run_id)
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

    # The chain duty raises nothing; the *anchor* duty does, and correctly.
    # RF-07 made it a duty that actually anchors, and this forge has no
    # federation link configured — so it alarms, saying that until the chain is
    # anchored somewhere a truncation of its end verifies as an intact chain
    # (SAD 11A.3). This assertion used to be `report.alarms == ()`, which held
    # only because the anchor duty was a no-op.
    assert [item.duty for item in report.alarms] == [Duty.ANCHOR]
    assert "not anchored anywhere" in report.alarms[0].detail

    # And a second tick within the hour does not repeat it.
    again = worker.run_once()
    assert Duty.CHAIN not in {finding.duty for finding in again.findings}


def test_a_declared_ring_is_written_to_the_chain_once(
    engine: Engine, site: str, tmp_path: Path
) -> None:
    """RF-E21. A forge that quietly became a two-node ring is not comparable.

    Three ranks and two ranks are different collectives, with different step
    times and different numerics, so "was this a two-node ring" is a question
    asked months later about a specific release. It has to be answerable from
    the chain rather than from whoever remembers the recabling.

    Once, not once per tick: the declaration comes from `draupnir.env`, so it
    changes when an operator changes it and restarts the units, and that is the
    event worth a row.
    """
    from draupnir.motsognir.placement import estate_for
    from draupnir.worker.loop import RING_DECLARED

    worker = Worker(
        WorkerSettings(site_id=site, scratch=tmp_path / "worker", interval=0.05),
        scheduler=draupnir_local_subprocess.driver,
        engine=engine,
        estate=estate_for(site, "gb10", ring_members=("durin", "dain")),
    )

    worker.run_once()
    worker.run_once()

    with engine.connect() as connection:
        found = connection.execute(
            text(
                "SELECT payload FROM ledger_entry WHERE site_id = :site "
                "AND transition = :transition"
            ),
            {"site": site, "transition": RING_DECLARED},
        ).fetchall()

    assert len(found) == 1, "the ring declaration was written more than once"
    payload = found[0][0]
    assert payload["members"] == ["durin", "dain"]
    assert payload["ringSize"] == 2
    assert payload["estateSize"] == 3


def test_an_undeclared_ring_writes_no_row(engine: Engine, site: str, tmp_path: Path) -> None:
    """A row per worker start on every ordinary forge would be noise.

    And noise in a ledger is expensive: it is the one place where a reader
    assumes every row was worth writing.
    """
    from draupnir.worker.loop import RING_DECLARED

    def rows() -> int:
        with engine.connect() as connection:
            return int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM ledger_entry WHERE site_id = :site "
                        "AND transition = :transition"
                    ),
                    {"site": site, "transition": RING_DECLARED},
                ).scalar_one()
            )

    # A delta rather than an absolute: these tests share one forge and one
    # database, so another test's declaration is a row this one would otherwise
    # be blamed for.
    before = rows()

    worker = Worker(
        WorkerSettings(site_id=site, scratch=tmp_path / "worker", interval=0.05),
        scheduler=draupnir_local_subprocess.driver,
        engine=engine,
    )
    worker.run_once()

    assert rows() == before


# ---------------------------------------------------------------------------
# Anchoring, through a tick. RF-07.
# ---------------------------------------------------------------------------


class _StandInRegistry:
    """MEGINGJORD, in process and without a network.

    Wraps the real `AnchorStore` rather than reimplementing it, because the
    property under test is that a worker tick reaches a registry at all --
    `Gullinbursti.drain` cannot tell this from `RemoteRegistry` over HTTP, and
    a path only exercised during an outage is a path that does not work.

    It supplies its own countersignature, which is the half `AnchorStore`
    leaves to its caller and a real registry would never leave to a submitter:
    a countersignature the submitting site chose is not a countersignature.
    """

    def __init__(self) -> None:
        from draupnir.megingjord.anchors import AnchorStore

        self.store = AnchorStore()
        self.submitted: list[int] = []

    def countersign(self, head: object, *, at: object, countersignature: str) -> object:
        del countersignature
        self.submitted.append(head.seq)  # type: ignore[attr-defined]
        return self.store.countersign(
            head,  # type: ignore[arg-type]
            at=at,  # type: ignore[arg-type]
            countersignature="megingjord-" + "c" * 52,
        )


class _RefusingRegistry:
    """A registry that answers, and refuses. The partitioned forge of row 7."""

    def countersign(self, head: object, *, at: object, countersignature: str) -> object:
        from draupnir.core.domain.federation import AnchorOutcome, Receipt

        del head, at, countersignature
        return Receipt(
            outcome=AnchorOutcome.REJECTED,
            anchor=None,
            reason="the WireGuard tunnel to Veldris_NXT is down",
        )


@pytest.fixture
def unanchored_site(engine: Engine) -> str:
    """A forge of its own, with a chain and no anchor in it.

    Its own, because anchoring is cumulative: a test that anchored this site
    earlier in the module leaves a countersignature behind, and a refusal at a
    forge anchored ninety seconds ago correctly does *not* alarm. Sharing the
    site would make this test pass or fail on the order it ran in.

    With a chain rather than empty, because an empty chain has no head to
    anchor and no end to truncate -- which the duty says, and does not alarm
    about.
    """
    name = "sindri-unanchored"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO site (id, name, location, timezone, control_plane_uri, "
                "anchor_state) VALUES (:id, 'Unanchored', 'Belfast', 'Europe/London', "
                "'https://sindri.veldris.internal', 'ANCHORED') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": name},
        )
    with engine.begin() as connection:
        from draupnir.core.domain.sites import SiteScope
        from draupnir.core.infrastructure.orchestration import for_connection

        for_connection(connection, SiteScope(name), actor="test").record(
            subject_type="site",
            subject_id=name,
            transition="site.commissioned",
            payload={"note": "so the chain has a head to anchor"},
        )
    return name


@pytest.fixture
def signing_key(tmp_path: Path) -> Path:
    """A site key on disk. The worker loads it once and derives its own key id."""
    from draupnir.svalinn.signing import generate_key_pair

    private, _public = generate_key_pair()
    path = tmp_path / "site-signing.pem"
    path.write_bytes(private)
    return path


def test_a_tick_anchors_the_chain_and_the_duty_stops_alarming(
    engine: Engine, site: str, tmp_path: Path, signing_key: Path
) -> None:
    """RF-07's acceptance criterion, end to end.

    The duty used to be `freshness(last_anchored_at)` gated on a column nothing
    wrote, so it alarmed on every tick of every worker that ever ran -- while
    its own message said publication is refused while the anchor is stale, and
    nothing refused anything. SAD 11A.3 makes the anchor what detects
    truncation; nothing detected truncation.
    """
    from draupnir.core.domain.sites import SiteScope
    from draupnir.core.infrastructure.orchestration import for_connection
    from draupnir.worker.loop import ANCHOR_SUBMITTED

    registry = _StandInRegistry()
    worker = Worker(
        WorkerSettings(
            site_id=site,
            scratch=tmp_path / "worker",
            interval=0.05,
            registry_url="https://megingjord.veldris.internal",
            signing_key=signing_key,
        ),
        scheduler=draupnir_local_subprocess.driver,
        engine=engine,
        registry=registry,
    )

    report = worker.run_once()

    anchor = next(item for item in report.findings if item.duty is Duty.ANCHOR)
    assert not anchor.alarm, anchor.detail
    assert registry.submitted, "the tick reached the registry with nothing"

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT payload FROM ledger_entry WHERE site_id = :site "
                "AND transition = :transition ORDER BY seq"
            ),
            {"site": site, "transition": ANCHOR_SUBMITTED},
        ).fetchall()
        assert rows, "the anchoring outcome was not recorded in the chain"
        assert rows[-1][0]["anchored_through"] > 0

        # And the two readers that gate publication can see it. `AC-S13` reads
        # `anchored_through`; the freshness duty reads the instant. Both come
        # out of these entries rather than off `site.last_anchored_at`, which
        # nothing has ever written.
        orchestrator = for_connection(connection, SiteScope(site), actor="test")
        assert orchestrator._anchored_through() > 0
        assert orchestrator.last_anchored_at() is not None


def test_a_refusing_registry_alarms_and_says_training_continues(
    engine: Engine, unanchored_site: str, tmp_path: Path, signing_key: Path
) -> None:
    """Decision S8, through the tick rather than through the agent alone.

    A partitioned forge trains and evaluates and does not release. An operator
    reading "the registry did not anchor" without that sentence would
    reasonably stop submitting work, so the sentence is part of the alarm.
    """
    from draupnir.core.domain.sites import SiteScope
    from draupnir.core.infrastructure.orchestration import for_connection
    from draupnir.worker.loop import ANCHOR_SUBMITTED

    site = unanchored_site
    worker = Worker(
        WorkerSettings(
            site_id=site,
            scratch=tmp_path / "worker",
            interval=0.05,
            registry_url="https://megingjord.veldris.internal",
            signing_key=signing_key,
        ),
        scheduler=draupnir_local_subprocess.driver,
        engine=engine,
        registry=_RefusingRegistry(),
    )

    report = worker.run_once()

    anchor = next(item for item in report.findings if item.duty is Duty.ANCHOR)
    assert anchor.alarm, "a forge that has never anchored and cannot anchor is not fine"
    assert "never anchored" in anchor.detail, (
        "the alarm does not say how long the chain's end has been unprotected"
    )
    assert "Training and evaluation continue" in anchor.detail
    assert "release is unavailable" in anchor.detail
    assert "tunnel to Veldris_NXT is down" in anchor.detail, (
        "the alarm does not carry the registry's own reason"
    )

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT payload FROM ledger_entry WHERE site_id = :site "
                "AND transition = :transition"
            ),
            {"site": site, "transition": ANCHOR_SUBMITTED},
        ).fetchall()
        # Recorded, and recorded as a refusal. A chain holding only successes
        # cannot tell "never tried" from "tried and was refused", which are the
        # two states an operator most needs told apart during an outage.
        assert rows, "the refusal was not recorded"
        assert rows[-1][0]["anchored_through"] == 0

        orchestrator = for_connection(connection, SiteScope(site), actor="test")
        assert orchestrator._anchored_through() == 0, "a refusal was read as a countersignature"
        assert orchestrator.last_anchored_at() is None


# ---------------------------------------------------------------------------
# The corpus queue, drained by a tick. RF-12.
# ---------------------------------------------------------------------------

_BODY = "The Human Rights Act 1998 gives further effect to rights and freedoms. " * 8
_ITEM = "In what year did the Human Rights Act receive Royal Assent in the United Kingdom?"


@pytest.fixture
def curated_estate(tmp_path: Path) -> dict[str, Path]:
    """A vault, an incoming directory with sources in it, and evaluation sets.

    What an air-gapped forge looks like: the curator has copied the retrieved
    sources onto the incoming mount, and the evaluation sets are on the vault
    where the estate put them at commissioning.
    """
    from draupnir.hodd.reconcile import initialise
    from draupnir.hodd.stores import PosixStoreDriver

    vault = tmp_path / "vault"
    vault.mkdir()
    initialise(PosixStoreDriver(root=vault, local_site="sindri-worker-test"))

    incoming = tmp_path / "incoming" / "GBR"
    incoming.mkdir(parents=True)
    (incoming / "hansard.txt").write_text(_BODY, encoding="utf-8")
    (incoming / "legislation.txt").write_text(f"{_BODY} distinct", encoding="utf-8")
    (incoming / "duplicate.txt").write_text(_BODY, encoding="utf-8")

    sets = tmp_path / "eval"
    sets.mkdir()
    (sets / "general-core.txt").write_text(_ITEM, encoding="utf-8")

    return {"vault": vault, "incoming": tmp_path / "incoming", "eval": sets}


def _accept(engine: Engine, site: str, transition: str) -> None:
    """Record what the API records when a curator presses the button."""
    from draupnir.core.domain.sites import SiteScope
    from draupnir.core.infrastructure.orchestration import for_connection

    with engine.begin() as connection:
        for_connection(connection, SiteScope(site), actor="curator@veldris.internal").record(
            subject_type="corpus",
            subject_id="GBR",
            transition=transition,
            payload={"jurisdiction": "GBR", "run_id": str(uuid.uuid4())},
        )


def _corpus_transitions(engine: Engine, site: str) -> list[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT transition FROM ledger_entry WHERE site_id = :site "
                "AND subject_type = 'corpus' ORDER BY seq"
            ),
            {"site": site},
        ).fetchall()
    return [row[0] for row in rows]


def _corpus_worker(engine: Engine, site: str, estate: dict[str, Path], tmp_path: Path) -> Worker:
    return Worker(
        WorkerSettings(
            site_id=site,
            scratch=tmp_path / "worker",
            interval=0.05,
            vault_root=estate["vault"],
            incoming_root=estate["incoming"],
            evaluation_sets=estate["eval"],
            stand_in=True,
        ),
        scheduler=draupnir_local_subprocess.driver,
        engine=engine,
    )


def test_accepted_corpus_work_is_performed_by_a_tick(
    engine: Engine, site: str, tmp_path: Path, curated_estate: dict[str, Path]
) -> None:
    """The finding, end to end.

    A curator pressed Ingest, got a 202 and a run identifier, and nothing ever
    happened: the accepted entry was consumed by nothing. Now a tick drains it,
    the corpus reaches the vault sealed, and curation deduplicates, filters and
    decontaminates it against the evaluation sets.
    """
    from draupnir.hodd.stores import PosixStoreDriver, artefact_uri
    from draupnir.worker import corpora

    _accept(engine, site, corpora.INGEST_ACCEPTED)
    _accept(engine, site, corpora.CURATE_ACCEPTED)

    worker = _corpus_worker(engine, site, curated_estate, tmp_path)
    worker.run_once()

    transitions = _corpus_transitions(engine, site)
    assert corpora.INGEST_COMPLETED in transitions, transitions
    assert corpora.CURATE_COMPLETED in transitions, transitions

    store = PosixStoreDriver(root=curated_estate["vault"], local_site=site)
    raw = artefact_uri(site, "corpus", "GBR/raw")
    curated = artefact_uri(site, "corpus", "GBR/curated")

    assert store.stat(raw).exists
    assert store.is_sealed(raw), "the ingested corpus is not sealed"
    assert store.stat(curated).exists
    assert store.is_sealed(curated)


def test_a_second_tick_does_not_do_the_work_again(
    engine: Engine, site: str, tmp_path: Path, curated_estate: dict[str, Path]
) -> None:
    """The chain is the queue, and an outcome closes the request that named it.

    Without that the duty would re-ingest on every tick, be refused by the seal
    every time, and write a failure entry a minute for ever.
    """
    from draupnir.worker import corpora

    _accept(engine, site, corpora.INGEST_ACCEPTED)

    worker = _corpus_worker(engine, site, curated_estate, tmp_path)
    worker.run_once()
    after_one = _corpus_transitions(engine, site)

    worker.timetable = Timetable()  # every duty due again, as a restart would find it
    worker.run_once()

    assert _corpus_transitions(engine, site) == after_one, "a second tick did the work again"


def test_work_that_cannot_be_done_is_recorded_as_failed(
    engine: Engine, site: str, tmp_path: Path, curated_estate: dict[str, Path]
) -> None:
    """And it alarms.

    A curator waiting on an ingest that will never complete is exactly who an
    alarm is for, and an accepted entry nobody closed would be retried every
    minute while telling them nothing.
    """
    from draupnir.worker import corpora

    _accept(engine, site, corpora.CURATE_ACCEPTED)

    worker = _corpus_worker(engine, site, curated_estate, tmp_path)
    report = worker.run_once()

    assert corpora.CURATE_FAILED in _corpus_transitions(engine, site)
    alarms = [item for item in report.alarms if item.duty is Duty.CORPORA]
    assert alarms, "a corpus request failed and nothing alarmed"
    assert "GBR" in alarms[0].detail


# ---------------------------------------------------------------------------
# The array queue, drained by a tick. RF-13.
# ---------------------------------------------------------------------------


class _ArrayScheduler:
    """A scheduler that records the array it was given and can requeue."""

    def __init__(self) -> None:
        self.submissions: list[Any] = []
        self.requeued: list[tuple[str, int]] = []

    def submit(self, plan: Any, name: str = "") -> Any:
        from draupnir.interfaces.types import JobHandle

        del name
        self.submissions.append(plan)
        return JobHandle(driver="motsognir.slurm/v1", job_id="9001")

    def poll(self, handle: Any) -> Any:
        from draupnir.interfaces.types import JobState, JobStatus

        del handle
        return JobStatus(state=JobState.RUNNING)

    def logs(self, handle: Any, lines: int = 50) -> str:
        del handle, lines
        return ""

    def requeue(self, handle: Any, index: int) -> Any:
        from draupnir.interfaces.types import JobState, JobStatus

        self.requeued.append((handle.job_id, index))
        return JobStatus(state=JobState.PENDING)


def _accept_array(engine: Engine, site: str, transition: str, **payload: Any) -> None:
    """Record what the API records when an operator presses the button."""
    from draupnir.core.domain.sites import SiteScope
    from draupnir.core.infrastructure.orchestration import for_connection
    from draupnir.motsognir import arrays

    with engine.begin() as connection:
        for_connection(connection, SiteScope(site), actor="operator@veldris.internal").record(
            subject_type=arrays.ARRAY_SUBJECT,
            subject_id="cim-56-adapters",
            transition=transition,
            payload={"name": "cim-56-adapters", "run_id": str(uuid.uuid4()), **payload},
        )


def _array_transitions(engine: Engine, site: str) -> list[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT transition FROM ledger_entry WHERE site_id = :site "
                "AND subject_type = 'array' ORDER BY seq"
            ),
            {"site": site},
        ).fetchall()
    return [row[0] for row in rows]


def _array_worker(engine: Engine, site: str, tmp_path: Path, scheduler: Any) -> Worker:
    from draupnir.motsognir.placement import estate_for

    return Worker(
        WorkerSettings(site_id=site, scratch=tmp_path / "worker", interval=0.05, stand_in=True),
        scheduler=scheduler,
        engine=engine,
        estate=estate_for(site, "gb10"),
    )


def test_an_accepted_array_is_submitted_as_one_array(
    engine: Engine, site: str, tmp_path: Path
) -> None:
    """The finding, end to end.

    `arrays.py` described `--array=0-55%3` at length and nothing submitted it.
    One submission holds all fifty-six and the scheduler runs three, so
    utilisation does not depend on the control plane being awake (SAD 11.2).
    """
    from draupnir.hamarr import tiers
    from draupnir.motsognir import arrays

    scheduler = _ArrayScheduler()
    _accept_array(engine, site, arrays.ARRAY_ACCEPTED, subjects=list(tiers.ALL), retryBudget=2)

    _array_worker(engine, site, tmp_path, scheduler).run_once()

    assert arrays.ARRAY_SUBMITTED in _array_transitions(engine, site)
    assert len(scheduler.submissions) == 1, "the array was submitted more than once"
    assert scheduler.submissions[0].resources.array.directive() == "0-55%3"


def test_the_array_read_back_reports_fifty_six(engine: Engine, site: str, tmp_path: Path) -> None:
    """`size == 56` regardless of how many runs exist at the site.

    The acceptance criterion, and the defect: `size` was `len(runs)`, so this
    site -- which the other tests in this module have filled with runs -- would
    have reported whatever that number happened to be.
    """
    from draupnir.core.domain.sites import SiteScope
    from draupnir.core.infrastructure.repositories import LedgerRepository
    from draupnir.hamarr import tiers
    from draupnir.motsognir import arrays

    _accept_array(engine, site, arrays.ARRAY_ACCEPTED, subjects=list(tiers.ALL))
    _array_worker(engine, site, tmp_path, _ArrayScheduler()).run_once()

    with engine.connect() as connection:
        entries = LedgerRepository(connection, SiteScope(site)).entries_of_type(
            arrays.ARRAY_SUBJECT
        )

    record = arrays.fold(entries)
    assert record is not None
    assert record.size == 56
    assert record.slurm_array == "0-55%3"
    assert record.job_id == "9001"


def test_requeueing_one_element_touches_one_element(
    engine: Engine, site: str, tmp_path: Path
) -> None:
    """AC-F6, through a tick. S12's primary action had no operation at all."""
    from draupnir.hamarr import tiers
    from draupnir.motsognir import arrays

    scheduler = _ArrayScheduler()
    _accept_array(engine, site, arrays.ARRAY_ACCEPTED, subjects=list(tiers.ALL))
    worker = _array_worker(engine, site, tmp_path, scheduler)
    worker.run_once()

    # Element 17 fails first. A requeue of an element that is still pending is
    # refused (RF-27) -- it is already on the queue -- so the requeue this test
    # is about is the one an operator actually makes: of an element that
    # stopped without completing.
    _accept_array(
        engine,
        site,
        arrays.ELEMENT_OBSERVED,
        element={
            "index": 17,
            "subject": tiers.ALL[17],
            "state": "FAILED",
            "attempts": 1,
            "jobId": "9001_17",
            "node": "dvalin",
            "exitCode": 1,
        },
    )
    _accept_array(engine, site, arrays.ELEMENT_REQUEUE_ACCEPTED, index=17)
    worker.timetable = Timetable()
    worker.run_once()

    assert arrays.ELEMENT_REQUEUED in _array_transitions(engine, site)
    assert scheduler.requeued == [("9001", 17)]
    assert len(scheduler.submissions) == 1, (
        "a requeue submitted a new job, which severs the element from its array"
    )
