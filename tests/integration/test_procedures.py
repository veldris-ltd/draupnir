"""AC-F12: Procedures M1 to M10 end to end for one jurisdiction.

"The complete Procedure M1 to M10 sequence from VLD-INF-SINDRI-001 executes
end to end for one jurisdiction with no manual shell step."

Against real PostgreSQL, with row level security applied to an unprivileged
role, real files on disk, real child processes started through the schedule
driver, and the real state machine, licence policy, gate judge and projector.
One call runs all ten steps; nothing here runs a step between them, which is
the whole of what "no manual shell step" means.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, text

from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import RunState
from draupnir.core.infrastructure.orchestration import for_connection
from draupnir.core.infrastructure.repositories import LedgerRepository, RunProjection
from draupnir.procedures import Procedure, run
from draupnir.procedures.sindri import STEPS, restore_writable

pytestmark = pytest.mark.integration

SITE = "sindri"
JURISDICTION = "GBR"

#: The states M1 to M10 walk, in order. SAD 6.1's spine for a released run.
#: Eleven transitions for ten steps: M5 places before it trains and M6 starts
#: evaluating before it judges, and both intermediate states are real -- a run
#: that is TRAINING is a run holding an allocation.
EXPECTED_STATES: tuple[RunState, ...] = (
    RunState.CORPUS_REGISTERED,
    RunState.LICENCE_CLEARED,
    RunState.CURATED,
    RunState.QUEUED,
    RunState.TRAINING,
    RunState.TRAINED,
    RunState.EVALUATING,
    RunState.MERGED,
    RunState.QUANTISED,
    RunState.AWAITING_APPROVAL,
    RunState.RELEASED,
)


@pytest.fixture
def site(owner: Connection) -> str:
    """Register Sindri, which every scoped row needs a foreign key to."""
    owner.execute(
        text(
            "INSERT INTO site (id, name, location, timezone, control_plane_uri, anchor_state) "
            "VALUES (:id, 'Sindri', 'Belfast', 'Europe/London', "
            "'https://sindri.veldris.internal', 'ANCHORED') ON CONFLICT (id) DO NOTHING"
        ),
        {"id": SITE},
    )
    return SITE


@pytest.fixture
def workdir(tmp_path: Path) -> Iterator[Path]:
    """A working directory the procedure may make read only and still clean up."""
    root = tmp_path / "procedure"
    yield root
    # M3 drops write permission on the raw corpus, which is the control AC-F3
    # asks for and also what stops pytest removing the directory afterwards.
    restore_writable(root)


@pytest.fixture
def scheduler() -> object:
    """The local subprocess scheduler: real processes, on this machine."""
    driver = pytest.importorskip(
        "draupnir_local_subprocess", reason="the reference schedule driver is not installed"
    )
    return driver.driver


def test_the_whole_procedure_runs_in_one_call(
    owner: Connection, site: str, workdir: Path, scheduler: object
) -> None:
    """AC-F12. Ten steps, one call, and the run ends RELEASED."""
    scope = SiteScope(site)
    procedure = Procedure(
        orchestrator=for_connection(owner, scope, actor="operator@veldris.internal"),
        workdir=workdir,
        jurisdiction=JURISDICTION,
    )

    results = run(procedure, scheduler)

    assert [result.id for result in results] == [step[0] for step in STEPS]
    assert procedure.orchestrator.state_of(procedure.run_id) is RunState.RELEASED


def test_every_step_records_its_own_audit_entries(
    owner: Connection, site: str, workdir: Path, scheduler: object
) -> None:
    """The audit record is a property of the system, not of the operator.

    SAD 1.1's whole argument. Every step that moves the run appends to the
    chain, and the chain is what an auditor reads afterwards -- so a step that
    did work and recorded nothing would be a step nobody can check.
    """
    scope = SiteScope(site)
    procedure = Procedure(
        orchestrator=for_connection(owner, scope, actor="operator@veldris.internal"),
        workdir=workdir,
        jurisdiction=JURISDICTION,
    )

    results = run(procedure, scheduler)

    # M9 is the approval: it is signed by GLEIPNIR and recorded by M10's
    # transition, so it is the one step that legitimately appends nothing.
    silent = [result.id for result in results if not result.entries]
    assert silent == ["M9"], silent

    ledger = LedgerRepository(owner, scope)
    entries = [entry for entry in ledger.stream(1) if entry.subject_id == str(procedure.run_id)]
    assert entries[0].transition == "->DRAFT"
    assert [entry.transition for entry in entries[1:]] == [
        f"{source}->{target}"
        for source, target in zip(
            (RunState.DRAFT, *EXPECTED_STATES[:-1]), EXPECTED_STATES, strict=True
        )
    ]
    assert {entry.actor for entry in entries} == {"operator@veldris.internal"}


def test_the_chain_verifies_and_the_projection_agrees_with_it(
    owner: Connection, site: str, workdir: Path, scheduler: object
) -> None:
    """The registry is derived, and a rebuild reproduces it exactly."""
    scope = SiteScope(site)
    procedure = Procedure(
        orchestrator=for_connection(owner, scope, actor="operator@veldris.internal"),
        workdir=workdir,
        jurisdiction=JURISDICTION,
    )
    run(procedure, scheduler)

    assert LedgerRepository(owner, scope).verify_chain() is None

    projection = RunProjection(owner, scope)
    before = {item.id: item.state for item in projection.read()}
    projection.rebuild()
    after = {item.id: item.state for item in projection.read()}

    assert before == after
    assert after[str(procedure.run_id)] is RunState.RELEASED


def test_the_raw_corpus_is_read_only_after_curation(
    owner: Connection, site: str, workdir: Path, scheduler: object
) -> None:
    """AC-F3, second clause: a write attempt on the raw tree is refused.

    Attempted for real. A test that checked the mode bits would be checking
    that a chmod was issued; this checks that the write fails.
    """
    procedure = Procedure(
        orchestrator=for_connection(owner, SiteScope(site), actor="operator@veldris.internal"),
        workdir=workdir,
        jurisdiction=JURISDICTION,
    )
    run(procedure, scheduler)

    raw = workdir / "corpus" / "raw" / "hansard.txt"
    assert raw.is_file()
    with pytest.raises(PermissionError):
        raw.write_text("rewritten after curation", encoding="utf-8")


def test_the_specification_is_the_only_thing_that_differs_between_jurisdictions(
    owner: Connection, site: str, tmp_path: Path, scheduler: object
) -> None:
    """AC-F15: two jurisdictions differ only in the dataset block.

    Checked on the specification the procedure compiles rather than on one
    written for the test, because the claim is about what the system produces.
    """
    from draupnir.procedures.sindri import _specification

    built = {}
    for jurisdiction in ("GBR", "IRL"):
        procedure = Procedure(
            orchestrator=for_connection(owner, SiteScope(site), actor="operator@veldris.internal"),
            workdir=tmp_path / jurisdiction,
            jurisdiction=jurisdiction,
        )
        procedure.model = f"cim-{jurisdiction.lower()}-v1.0"
        procedure.artefacts["corpus_curated"] = "a" * 64
        built[jurisdiction] = _specification(procedure).as_mapping()

    gbr, irl = built["GBR"], built["IRL"]
    differing = {key for key in gbr["spec"] if gbr["spec"][key] != irl["spec"][key]}

    assert differing == {"dataset"}
    # The metadata differs too, and must: a jurisdiction is not a dataset.
    assert gbr["metadata"] != irl["metadata"]


def test_the_same_specification_over_the_same_inputs_is_reported_as_a_duplicate(
    owner: Connection, site: str, tmp_path: Path, scheduler: object
) -> None:
    """AC-F2: detected and reported, rather than silently re-running.

    Two procedures for the same jurisdiction produce the same curated corpus
    from the same declared sources, so they compile the same specification over
    the same inputs and arrive at the same run identity. The second is stopped
    at M4 -- before an allocation is spent, which is the point.
    """
    from draupnir.core.application.orchestrator import DuplicateRunError

    scope = SiteScope(site)
    first = Procedure(
        orchestrator=for_connection(owner, scope, actor="operator@veldris.internal"),
        workdir=tmp_path / "first",
        jurisdiction=JURISDICTION,
    )
    run(first, scheduler)

    second = Procedure(
        orchestrator=for_connection(owner, scope, actor="operator@veldris.internal"),
        workdir=tmp_path / "second",
        jurisdiction=JURISDICTION,
    )
    try:
        with pytest.raises(DuplicateRunError) as raised:
            run(second, scheduler)
    finally:
        restore_writable(tmp_path / "first")
        restore_writable(tmp_path / "second")

    # The report names the run that already exists, so an operator comparing
    # two runs knows where the first one is.
    assert str(first.run_id) in str(raised.value)
    assert "already recorded" in str(raised.value)
    # And the identifiers differ, because they always do: UUIDv7 sorts by
    # creation time. It is the identity that is shared.
    assert second.run_id != first.run_id
