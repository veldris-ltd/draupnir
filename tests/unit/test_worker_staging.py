"""What the worker does with the bytes a job produced. RF-08.

The worker hashed what a job wrote and recorded the digest, and the bytes
stayed in a scratch directory SAD 11.2 calls disposable. Nothing called
`stores.store_for`, nothing called `hodd.quota`, and a release approval
resolved an artefact URI the chain had never recorded. These are the tests
that would have failed then.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from draupnir.core.application.orchestrator import RunFacts
from draupnir.core.domain.states import RunState
from draupnir.hodd.stores import PosixStoreDriver
from draupnir.worker import stages

SITE = "sindri"


@dataclass
class _Full(PosixStoreDriver):
    """A vault with almost nothing left, so the quota check has teeth."""

    free: int = 1024
    total: int = 4 * 1024**4

    def free_bytes(self) -> int:
        return self.free

    def total_bytes(self) -> int:
        return self.total


def _vault(tmp_path: Path) -> PosixStoreDriver:
    root = tmp_path / "vault"
    root.mkdir()
    return PosixStoreDriver(root=root, local_site=SITE)


def _context(tmp_path: Path, store: Any) -> stages.Context:
    return stages.Context(
        orchestrator=SimpleNamespace(),  # type: ignore[arg-type]
        scheduler=None,
        scratch=tmp_path / "scratch",
        site_id=SITE,
        store=store,
    )


def _produced(context: stages.Context, run_id: UUID, content: bytes) -> Path:
    target = context.workdir(run_id) / "adapter.safetensors"
    target.write_bytes(content)
    return target


def _facts(run_id: UUID) -> RunFacts:
    return RunFacts(
        run_id=run_id,
        name="cim-gbr-v1.0",
        state=RunState.TRAINING,
        submitter="operator@veldris.internal",
        spec_hash="a" * 64,
    )


def test_a_produced_artefact_reaches_the_vault_and_is_sealed(tmp_path: Path) -> None:
    """The finding, at its narrowest.

    A digest recorded against bytes that live only in scratch is a provenance
    claim about something the next disk failure takes with it, and a
    publication re-hashing it (AC-S8) finds nothing to re-hash.
    """
    store = _vault(tmp_path)
    context = _context(tmp_path, store)
    run_id = uuid4()
    source = _produced(context, run_id, b"weights")

    staged = stages._stage(context, "adapter", run_id, source)

    assert staged.uri == f"hodd://{SITE}/adapters/{run_id}"
    held = store.stat(staged.uri)
    assert held.exists
    assert held.sha256 == staged.sha256, "the vault holds different bytes from the ones hashed"
    assert store.is_sealed(staged.uri), (
        "an unsealed artefact is open for the whole window between evaluation and "
        "approval, which is the window T8 needs"
    )


def test_staging_the_same_bytes_twice_is_not_a_refusal(tmp_path: Path) -> None:
    """A stage whose transition failed re-runs next tick.

    It finds its own bytes already at the address. Putting them again would be
    refused as an overwrite of a sealed artefact, and the run would defer for
    ever -- a livelock produced entirely by the fix.
    """
    store = _vault(tmp_path)
    context = _context(tmp_path, store)
    run_id = uuid4()
    source = _produced(context, run_id, b"weights")

    first = stages._stage(context, "adapter", run_id, source)
    second = stages._stage(context, "adapter", run_id, source)

    assert first == second


def test_different_bytes_at_one_address_are_refused(tmp_path: Path) -> None:
    """And that is the seal working, not the seal in the way.

    Two artefacts at one address is the state AC-S8 exists to make impossible.
    The run defers with the vault's own words rather than overwriting.
    """
    store = _vault(tmp_path)
    context = _context(tmp_path, store)
    run_id = uuid4()

    stages._stage(context, "adapter", run_id, _produced(context, run_id, b"weights"))

    with pytest.raises(stages.NotStagedError, match="was not stored"):
        stages._stage(context, "adapter", run_id, _produced(context, run_id, b"other weights"))


def test_a_full_vault_defers_the_run_rather_than_filling(tmp_path: Path) -> None:
    """AC-S10, at the write rather than at planning.

    A put that fills the vault has already caused the harm: writes in flight
    fail, and on a copy-on-write filesystem the vault cannot be emptied to
    recover. The refusal carries the arithmetic, because one an operator
    cannot check is one they will work around.
    """
    root = tmp_path / "vault"
    root.mkdir()
    store = _Full(root=root, local_site=SITE)
    context = _context(tmp_path, store)
    run_id = uuid4()
    source = _produced(context, run_id, b"w" * 8192)

    with pytest.raises(stages.NotStagedError) as refusal:
        stages._stage(context, "adapter", run_id, source)

    assert "deferred rather than the vault filled" in str(refusal.value)
    assert "measured" in str(refusal.value), "the refusal does not show its arithmetic"
    assert not store.stat(f"hodd://{SITE}/adapters/{run_id}").exists, (
        "the vault was written to by a check that was supposed to prevent the write"
    )


def test_a_worker_with_no_vault_says_so_rather_than_pretending(tmp_path: Path) -> None:
    """A development machine, and it is told apart from a broken one.

    The digest is still recorded, so the run trains and evaluates; the address
    is empty, so nothing can be released. That is decision S8's shape, and it
    is said in the outcome rather than discovered at publication.
    """
    context = _context(tmp_path, None)
    run_id = uuid4()
    source = _produced(context, run_id, b"weights")

    staged = stages._stage(context, "adapter", run_id, source)

    assert staged.uri == ""
    assert staged.sha256
    assert "DRAUPNIR_VAULT_ROOT" in staged.reason, (
        "the reason does not name the setting an operator would change"
    )


def test_an_unavailable_vault_defers_and_does_not_degrade(tmp_path: Path) -> None:
    """A configured vault that is not mounted is not a vault that is absent.

    Softening it to `None` would let a dropped NFS export look like a
    development machine, and every run would sail past it recording no address
    at all. The run defers, and the next tick asks again -- which is what an
    export coming back should look like.
    """
    store = PosixStoreDriver(root=tmp_path / "never-mounted", local_site=SITE)
    context = _context(tmp_path, store)
    run_id = uuid4()
    source = _produced(context, run_id, b"weights")

    with pytest.raises(stages.NotStagedError, match="did not answer"):
        stages._stage(context, "adapter", run_id, source)


# ---------------------------------------------------------------------------
# Through the stage, not only through the helper
# ---------------------------------------------------------------------------


class _Recording:
    """An orchestrator that keeps what it was asked to record."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def transition(self, run_id: Any, target: Any, **kwargs: Any) -> Any:
        del run_id
        self.payloads.append(dict(kwargs["payload"]))
        return SimpleNamespace(state=target)

    def history(self, run_id: Any) -> Any:
        del run_id
        return iter(())


class _Finished:
    """A scheduler whose job has exited zero, having written a checkpoint."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir

    def poll(self, handle: Any) -> Any:
        from draupnir.interfaces.types import JobState, JobStatus

        (self.workdir / stages.ARTEFACTS["adapter"]).write_bytes(b"weights")
        del handle
        return JobStatus(state=JobState.COMPLETED, exit_code=0, node="dvalin")

    def logs(self, handle: Any, lines: int = 50) -> str:
        del handle, lines
        return "step 1 loss 0.0"


def _observing(tmp_path: Path, store: Any, run_id: UUID) -> tuple[stages.Context, _Recording]:
    orchestrator = _Recording()
    context = stages.Context(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        scheduler=None,
        scratch=tmp_path / "scratch",
        site_id=SITE,
        store=store,
    )
    context.scheduler = _Finished(context.workdir(run_id))
    return context, orchestrator


def test_the_trained_transition_records_where_the_checkpoint_went(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So that a publication can resolve it.

    `Orchestrator._uri_for` matches an address to a digest. Without this the
    approval path refuses with "the chain records no location", which is a true
    statement about a chain that should never have been in that state.
    """
    store = _vault(tmp_path)
    run_id = uuid4()
    context, orchestrator = _observing(tmp_path, store, run_id)
    monkeypatch.setattr(stages, "placement_of", lambda *_: {"driver": "local", "job_id": "1"})

    outcome = stages.observe(context, _facts(run_id))

    assert outcome.result is stages.Result.MOVED, outcome.detail
    payload = orchestrator.payloads[-1]
    assert payload["artefact_uri"] == f"hodd://{SITE}/adapters/{run_id}"
    assert payload["artefact_sha256"] == payload["checkpoint_sha256"], (
        "the address is recorded against a digest that is not the checkpoint's"
    )
    assert store.stat(payload["artefact_uri"]).exists


def test_the_run_does_not_move_when_the_vault_would_not_take_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The order matters more than the check does.

    A transition recorded first and a put attempted afterwards leaves a chain
    saying TRAINED about a checkpoint at an address holding nothing, which is
    the one failure the provenance argument cannot survive.
    """
    root = tmp_path / "vault"
    root.mkdir()
    run_id = uuid4()
    context, orchestrator = _observing(tmp_path, _Full(root=root, local_site=SITE), run_id)
    monkeypatch.setattr(stages, "placement_of", lambda *_: {"driver": "local", "job_id": "1"})

    outcome = stages.observe(context, _facts(run_id))

    assert outcome.result is stages.Result.DEFERRED
    assert orchestrator.payloads == [], "the chain moved on a checkpoint the vault refused"
