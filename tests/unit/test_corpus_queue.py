"""The chain as a queue, and what draining it does. RF-12.

`POST /v1/corpora/{iso3}/ingest` recorded an `ingest-accepted` entry and
returned 202. Nothing consumed it: the worker's stage table maps run states and
its duties covered the chain, the fabric, the vault, the anchor and retention.
A curator pressed Ingest, got a run identifier, and nothing ever happened.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry
from draupnir.hodd.stores import PosixStoreDriver, artefact_uri
from draupnir.worker import corpora

pytestmark = pytest.mark.unit

SITE = "sindri"
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
BODY = "The Human Rights Act 1998 gives further effect to rights and freedoms. " * 8
ITEM = "In what year did the Human Rights Act receive Royal Assent in the United Kingdom?"


def _entry(
    seq: int, transition: str, payload: Mapping[str, Any], subject: str = "GBR"
) -> LedgerEntry:
    return LedgerEntry(
        id=uuid.uuid4(),
        site_id=SITE,
        seq=seq,
        prev_hash=GENESIS_HASH,
        entry_hash="e" * 64,
        ts=NOW,
        actor="curator@veldris.internal",
        subject_type=corpora.CORPUS_SUBJECT,
        subject_id=subject,
        transition=transition,
        payload=dict(payload),
    )


def _accepted(seq: int, subject: str = "GBR", transition: str = corpora.INGEST_ACCEPTED) -> Any:
    return _entry(seq, transition, {"jurisdiction": subject, "run_id": str(uuid.uuid4())}, subject)


def _closed(seq: int, answers: int, transition: str = corpora.INGEST_COMPLETED) -> Any:
    return _entry(seq, transition, {"jurisdiction": "GBR", corpora.ANSWERS: answers})


# ---------------------------------------------------------------------------
# What is outstanding
# ---------------------------------------------------------------------------


def test_an_accepted_request_is_outstanding_until_something_closes_it() -> None:
    """The chain is the queue, and a worker holds nothing between ticks."""
    entries = [_accepted(4)]

    (request,) = corpora.outstanding(entries, accepted=corpora.INGEST_ACCEPTED)

    assert request.seq == 4
    assert request.jurisdiction == "GBR"


def test_a_completed_request_is_no_longer_outstanding() -> None:
    """Keyed by the accepted entry's sequence, so a re-tick does not redo it."""
    entries = [_accepted(4), _closed(5, answers=4)]

    assert corpora.outstanding(entries, accepted=corpora.INGEST_ACCEPTED) == ()


def test_a_failed_request_is_closed_too() -> None:
    """A failure is an entry.

    A chain holding only successes cannot tell "not reached yet" from "tried
    and failed", which are the two states a curator most needs told apart --
    and an unconsumed accepted entry would be retried on every tick for ever.
    """
    entries = [_accepted(4), _closed(5, answers=4, transition=corpora.INGEST_FAILED)]

    assert corpora.outstanding(entries, accepted=corpora.INGEST_ACCEPTED) == ()


def test_a_second_request_for_one_jurisdiction_is_a_second_piece_of_work() -> None:
    """Keyed by sequence, not by jurisdiction.

    A corpus is re-ingested deliberately -- new sources arrive, a licence is
    cleared -- and keying on the jurisdiction would make the second request a
    silent no-op that returned 202.
    """
    entries = [_accepted(4), _closed(5, answers=4), _accepted(6)]

    (request,) = corpora.outstanding(entries, accepted=corpora.INGEST_ACCEPTED)

    assert request.seq == 6


def test_an_outcome_closes_only_the_request_it_names() -> None:
    """Two jurisdictions accepted, one done, and the other is still waiting."""
    entries = [_accepted(4, "GBR"), _accepted(5, "JAM"), _closed(6, answers=4)]

    outstanding = corpora.outstanding(entries, accepted=corpora.INGEST_ACCEPTED)

    assert [item.jurisdiction for item in outstanding] == ["JAM"]


def test_ingests_and_curations_are_separate_queues() -> None:
    """An ingest-completed does not close a curate-accepted, and vice versa."""
    entries = [
        _accepted(4, transition=corpora.INGEST_ACCEPTED),
        _accepted(5, transition=corpora.CURATE_ACCEPTED),
        _closed(6, answers=4, transition=corpora.INGEST_COMPLETED),
    ]

    assert corpora.outstanding(entries, accepted=corpora.INGEST_ACCEPTED) == ()
    assert len(corpora.outstanding(entries, accepted=corpora.CURATE_ACCEPTED)) == 1


# ---------------------------------------------------------------------------
# Doing it
# ---------------------------------------------------------------------------


def _workspace(tmp_path: Path, *, sources: bool = True, evaluation: bool = True) -> Any:
    vault = tmp_path / "vault"
    vault.mkdir()

    incoming = tmp_path / "incoming"
    if sources:
        (incoming / "GBR").mkdir(parents=True)
        (incoming / "GBR" / "hansard.txt").write_text(BODY, encoding="utf-8")
        (incoming / "GBR" / "legislation.txt").write_text(f"{BODY} distinct", encoding="utf-8")

    sets = tmp_path / "eval"
    if evaluation:
        sets.mkdir()
        (sets / "general-core.txt").write_text(ITEM, encoding="utf-8")

    return corpora.Workspace(
        site_id=SITE,
        incoming=incoming,
        store=PosixStoreDriver(root=vault, local_site=SITE),
        evaluation_sets=sets if evaluation else None,
        scratch=tmp_path / "scratch",
    )


def test_an_ingest_publishes_the_corpus_and_seals_it(tmp_path: Path) -> None:
    """`hodd/ingest.py` was invoked by nothing in the running system."""
    workspace = _workspace(tmp_path)
    request = corpora.Request(seq=4, jurisdiction="GBR", transition=corpora.INGEST_ACCEPTED)

    outcome = corpora.perform_ingest(request, workspace)

    assert outcome.succeeded, outcome.payload
    assert outcome.transition == corpora.INGEST_COMPLETED
    assert outcome.payload[corpora.ANSWERS] == 4

    uri = artefact_uri(SITE, "corpus", "GBR/raw")
    assert workspace.store.stat(uri).exists
    assert workspace.store.is_sealed(uri)
    assert outcome.payload["files"] == 2


def test_a_second_ingest_of_a_published_corpus_closes_rather_than_loops(
    tmp_path: Path,
) -> None:
    """The one window a crash can leave open.

    A previous attempt published the artefact and died before recording the
    outcome. Retrying against a sealed artefact would be refused for ever --
    a livelock caused entirely by the seal doing its job -- so the request is
    closed as completed instead.
    """
    workspace = _workspace(tmp_path)
    first = corpora.Request(seq=4, jurisdiction="GBR", transition=corpora.INGEST_ACCEPTED)
    corpora.perform_ingest(first, workspace)

    again = corpora.perform_ingest(first, workspace)

    assert again.succeeded
    assert "already ingested" in again.payload["note"]


def test_an_ingest_with_no_sources_records_a_failure(tmp_path: Path) -> None:
    """Rather than leaving the accepted entry unconsumed.

    An unconsumed entry is retried on every tick for ever and tells the curator
    nothing. The reason names the directory, because that is what they have to
    do something about.
    """
    workspace = _workspace(tmp_path, sources=False)
    request = corpora.Request(seq=4, jurisdiction="GBR", transition=corpora.INGEST_ACCEPTED)

    outcome = corpora.perform_ingest(request, workspace)

    assert not outcome.succeeded
    assert outcome.transition == corpora.INGEST_FAILED
    assert "no sources for GBR" in outcome.payload["reason"]
    assert outcome.payload[corpora.ANSWERS] == 4


def test_a_corpus_carrying_a_secret_is_not_ingested(tmp_path: Path) -> None:
    """RF-09's scanner, on the second path that reaches an ingest."""
    workspace = _workspace(tmp_path)
    (workspace.incoming / "GBR" / "notes.txt").write_text(
        f"{BODY} AKIAIOSFODNN7EXAMPLE", encoding="utf-8"
    )
    request = corpora.Request(seq=4, jurisdiction="GBR", transition=corpora.INGEST_ACCEPTED)

    outcome = corpora.perform_ingest(request, workspace)

    assert not outcome.succeeded
    assert not workspace.store.stat(artefact_uri(SITE, "corpus", "GBR/raw")).exists


def test_curation_runs_over_the_ingested_corpus(tmp_path: Path) -> None:
    """And the curated output is vaulted, sealed and given an address.

    A curated corpus that lived only in scratch would be RF-08's defect one
    directory along: a run's specification names it by `hodd://` URI.
    """
    workspace = _workspace(tmp_path)
    corpora.perform_ingest(
        corpora.Request(seq=4, jurisdiction="GBR", transition=corpora.INGEST_ACCEPTED), workspace
    )

    outcome = corpora.perform_curation(
        corpora.Request(seq=5, jurisdiction="GBR", transition=corpora.CURATE_ACCEPTED), workspace
    )

    assert outcome.succeeded, outcome.payload
    assert outcome.payload["decontaminationConfirmed"] is True
    assert outcome.payload["stageRetention"]["dedupe"] == 1.0
    assert workspace.store.is_sealed(artefact_uri(SITE, "corpus", "GBR/curated"))


def test_curation_with_no_evaluation_sets_fails_and_names_the_setting(
    tmp_path: Path,
) -> None:
    """The refusal names `DRAUPNIR_EVALUATION_SETS`, not a path.

    An operator reading "no evaluation set was found at build/eval" has to work
    out where that path came from before they can fix anything.
    """
    workspace = _workspace(tmp_path, evaluation=False)
    corpora.perform_ingest(
        corpora.Request(seq=4, jurisdiction="GBR", transition=corpora.INGEST_ACCEPTED), workspace
    )

    outcome = corpora.perform_curation(
        corpora.Request(seq=5, jurisdiction="GBR", transition=corpora.CURATE_ACCEPTED), workspace
    )

    assert not outcome.succeeded
    assert "DRAUPNIR_EVALUATION_SETS" in outcome.payload["reason"]


def test_curating_a_corpus_nobody_ingested_fails_with_the_reason(tmp_path: Path) -> None:
    """Ingest and curate are accepted separately, and may arrive out of order."""
    workspace = _workspace(tmp_path)

    outcome = corpora.perform_curation(
        corpora.Request(seq=5, jurisdiction="GBR", transition=corpora.CURATE_ACCEPTED), workspace
    )

    assert not outcome.succeeded
    assert "has no ingested corpus" in outcome.payload["reason"]


def test_the_raw_tree_is_read_only_after_curation(tmp_path: Path) -> None:
    """AC-F3, enforced rather than asserted.

    The mode change is the control: a curation script that never consulted the
    database is refused by the filesystem.
    """
    workspace = _workspace(tmp_path)
    corpora.perform_ingest(
        corpora.Request(seq=4, jurisdiction="GBR", transition=corpora.INGEST_ACCEPTED), workspace
    )
    corpora.perform_curation(
        corpora.Request(seq=5, jurisdiction="GBR", transition=corpora.CURATE_ACCEPTED), workspace
    )

    raw = Path(workspace.store.resolve(artefact_uri(SITE, "corpus", "GBR/raw")))
    with pytest.raises(OSError):
        (raw / "hansard.txt").write_text("rewritten", encoding="utf-8")


# ---------------------------------------------------------------------------
# A crash leaves one corpus, whichever point it happened at
# ---------------------------------------------------------------------------

#: Where an ingest can die. Each is a real step of `Ingestor.ingest`, and the
#: property is the same at every one: whatever the worker does afterwards, the
#: estate ends up with exactly one registered corpus or none, never half of one.
_FAILURE_POINTS = ("shutil.copytree", "draupnir.hodd.ingest.build", "pathlib.Path.rename")


@pytest.mark.parametrize("where", _FAILURE_POINTS)
def test_a_crash_mid_ingest_leaves_one_corpus_after_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """The property `hodd.ingest` has, and the duty must not undo.

    Everything before the rename is disposable, so a crash leaves a staged tree
    nothing references and no artefact at the address. A restarted worker finds
    the request still outstanding -- the chain is the queue -- and does it
    again, and the estate ends up with one corpus rather than two halves of one.
    """
    workspace = _workspace(tmp_path)
    request = corpora.Request(seq=4, jurisdiction="GBR", transition=corpora.INGEST_ACCEPTED)
    uri = artefact_uri(SITE, "corpus", "GBR/raw")

    def crash(*args: Any, **kwargs: Any) -> Any:
        msg = f"the worker died at {where}"
        raise RuntimeError(msg)

    module, _, name = where.rpartition(".")
    monkeypatch.setattr(f"{module}.{name}", crash)

    # The tick that crashed. The duty turns it into a failure entry rather than
    # letting it out: one corpus that cannot be ingested must not stop the
    # estate.
    crashed = corpora.perform_ingest(request, workspace)
    assert not crashed.succeeded
    assert (
        where.rsplit(".", 1)[-1] in crashed.payload["reason"]
        or "died at" in (crashed.payload["reason"])
    )
    assert not workspace.store.stat(uri).exists, "a crashed ingest published a partial corpus"

    # The restart. `monkeypatch` is undone, the request is still outstanding
    # because no *completed* entry closed it, and the work is done properly.
    monkeypatch.undo()
    again = corpora.perform_ingest(request, workspace)

    assert again.succeeded, again.payload
    assert workspace.store.stat(uri).exists
    assert workspace.store.is_sealed(uri)

    # And exactly one: nothing was left staged for a reconciliation to find.
    from draupnir.hodd.ingest import Ingestor

    assert Ingestor(workspace.store).abandoned_staging() == (), (
        "a crashed ingest left a staged tree behind"
    )


def test_a_failure_is_recorded_rather_than_leaving_the_request_outstanding(
    tmp_path: Path,
) -> None:
    """The acceptance criterion, and the reason a failure is an entry.

    An accepted entry nobody closed is retried on every tick for ever, and
    tells the curator watching the board precisely nothing.
    """
    workspace = _workspace(tmp_path, sources=False)
    accepted = _accepted(4)

    (request,) = corpora.outstanding([accepted], accepted=corpora.INGEST_ACCEPTED)
    (outcome,) = corpora.perform([request], workspace)

    # The entry the loop would write, fed back into the queue.
    failure = _entry(5, outcome.transition, outcome.payload)

    assert corpora.outstanding([accepted, failure], accepted=corpora.INGEST_ACCEPTED) == ()
