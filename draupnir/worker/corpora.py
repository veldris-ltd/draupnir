"""The corpus work the API accepts, performed. RF-12.

`POST /v1/corpora/{iso3}/ingest` documents itself as "Stage, hash, publish, seal
and register" and returns 202. What it did was record an `ingest-accepted` entry
and stop. `curateCorpus` was the same shape. Nothing consumed either: the
worker's stage table maps run states, and its duties covered the chain, the
fabric, the vault, the anchor and retention -- none of them corpora.

So a curator registered sources, pressed Ingest, got a 202 and a run identifier,
and nothing ever happened. `hodd/ingest.py` -- atomic, and each of whose four
failure points is tested by crashing there -- was invoked by nothing in the
running system.

**The chain is the queue.** An accepted entry is outstanding until an entry
naming its sequence number says otherwise, and both outcomes are recorded: a
failure is an entry too. A worker holds nothing between ticks (SAD 11.2 row 1),
so a restarted worker finds the same queue by reading the same chain, and two
workers racing on one corpus both refuse the second attempt because the first
one's entry is already there.

**Keyed by the accepted entry's sequence**, not by the jurisdiction. A corpus
can be re-ingested deliberately -- new sources arrive, a licence is cleared --
and keying on the jurisdiction would make the second request a no-op. The
sequence names *this* request, so a re-tick after a crash finds the same one
outstanding and a fresh request is a fresh piece of work.

**Where the bytes come from.** An incoming directory the estate drops sources
into, one per jurisdiction. Not a fetch: retrieving a corpus is outbound traffic
to a host no allow list entry covers, and threat T11 makes that a decision the
broker takes rather than one a duty takes for itself. On an air-gapped forge the
curator copies the files in, which is what VLD-INF-SINDRI-001 describes and what
this reads.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from draupnir.core.domain.ledger import LedgerEntry
from draupnir.hodd import curation
from draupnir.hodd.ingest import Ingestor
from draupnir.hodd.stores import ImmutableArtefactError, PosixStoreDriver, artefact_uri
from draupnir.worker import accepted

logger = structlog.get_logger(__name__)

#: The subject every corpus entry is recorded against: the jurisdiction.
CORPUS_SUBJECT = "corpus"

#: What the API records when it takes the request, and what this drains.
INGEST_ACCEPTED = "ingest-accepted"
CURATE_ACCEPTED = "curate-accepted"

#: What this records when the work is done, either way. Both outcomes, because
#: a chain holding only successes cannot tell "not reached yet" from "tried and
#: failed", and those are the two states a curator most needs told apart.
INGEST_COMPLETED = "ingest-completed"
INGEST_FAILED = "ingest-failed"
CURATE_COMPLETED = "curate-completed"
CURATE_FAILED = "curate-failed"

#: Which outcome closes which request.
CLOSES: Mapping[str, tuple[str, ...]] = {
    INGEST_ACCEPTED: (INGEST_COMPLETED, INGEST_FAILED),
    CURATE_ACCEPTED: (CURATE_COMPLETED, CURATE_FAILED),
}

#: Re-exported. The queue mechanism is shared with the array queue (RF-13), and
#: a reader of this module should not have to know that to find the field name.
ANSWERS = accepted.ANSWERS


class CorpusWorkError(Exception):
    """Raised when accepted corpus work cannot be performed.

    Carried into a failure entry rather than out of the tick: one corpus that
    cannot be ingested must not stop the estate, and the reason belongs in the
    chain where the curator who asked for it will look.
    """


@dataclass(frozen=True, slots=True)
class Request:
    """One accepted piece of corpus work, waiting to be done."""

    seq: int
    jurisdiction: str
    transition: str
    #: The identifier the API handed the curator, so the outcome can be joined
    #: to the 202 they were given.
    run_id: str = ""

    def as_payload(self, **extra: Any) -> dict[str, Any]:
        """The common half of an outcome entry."""
        return {"jurisdiction": self.jurisdiction, "runId": self.run_id, ANSWERS: self.seq, **extra}


@dataclass(frozen=True, slots=True)
class Outcome:
    """What was done about one request."""

    request: Request
    transition: str
    payload: Mapping[str, Any]

    @property
    def succeeded(self) -> bool:
        """Whether the work was performed."""
        return self.transition in {INGEST_COMPLETED, CURATE_COMPLETED}


@dataclass
class Workspace:
    """Where corpus work reads from and writes to.

    Every path is configuration rather than convention, because each of them is
    somewhere an operator puts files and a convention is a thing they have to be
    told. A workspace with no vault is a development machine, and the duties
    say so rather than inventing a directory.
    """

    site_id: str = "sindri"
    #: Where an operator drops a jurisdiction's retrieved sources, one
    #: directory per ISO 3166-1 alpha-3 code.
    incoming: Path | None = None
    #: The HODD vault the corpus is ingested into.
    store: PosixStoreDriver | None = None
    #: Where the evaluation sets are, for decontamination. Absent means
    #: curation refuses, which is the point: see `hodd.curation`.
    evaluation_sets: Path | None = None
    #: Where curation does its work. Derived per jurisdiction.
    scratch: Path = field(default_factory=lambda: Path("build") / "curation")

    def sources_for(self, jurisdiction: str) -> Path:
        """Where this jurisdiction's retrieved sources were dropped."""
        if self.incoming is None:
            msg = (
                "this worker has no incoming directory configured "
                "(DRAUPNIR_INCOMING_ROOT), so there is nowhere for a curator to have "
                "put the sources and nothing to ingest."
            )
            raise CorpusWorkError(msg)
        return self.incoming / jurisdiction

    def require_store(self) -> PosixStoreDriver:
        """The vault, or a refusal naming what is missing."""
        if self.store is None:
            msg = (
                "this worker has no artefact store configured (DRAUPNIR_VAULT_ROOT), "
                "so an ingested corpus would have nowhere to live."
            )
            raise CorpusWorkError(msg)
        return self.store

    def require_evaluation_sets(self) -> Path:
        """Where decontamination checks against, or a refusal.

        Refused here rather than deferred to `curation.curate`, so that the
        message names the setting rather than the directory: an operator
        reading "no evaluation set was found at build/eval" has to work out
        where that path came from.
        """
        if self.evaluation_sets is None:
            msg = (
                "this worker has no evaluation sets configured "
                "(DRAUPNIR_EVALUATION_SETS), so a corpus cannot be decontaminated "
                "against them -- and a corpus curated without that check is one "
                "whose evaluation scores measure the overlap rather than the model "
                "(SAD 6.1)."
            )
            raise CorpusWorkError(msg)
        return self.evaluation_sets


# ---------------------------------------------------------------------------
# Reading the queue
# ---------------------------------------------------------------------------


def outstanding(entries: Iterable[LedgerEntry], *, accepted: str) -> tuple[Request, ...]:
    """Every accepted request of this kind that no outcome has closed.

    Through `worker.accepted`, which is the one implementation of "the chain is
    the queue" and is shared with the array queue (RF-13). The translation into
    a corpus `Request` is here because what a jurisdiction is called is this
    module's business and not the queue's.
    """
    from draupnir.worker.accepted import outstanding as pending

    return tuple(
        Request(
            seq=item.seq,
            jurisdiction=str(item.payload.get("jurisdiction") or item.subject),
            transition=accepted,
            run_id=str(item.payload.get("run_id") or item.payload.get("runId") or ""),
        )
        for item in pending(entries, accepted=accepted, closed_by=CLOSES[accepted])
    )


# ---------------------------------------------------------------------------
# Doing the work
# ---------------------------------------------------------------------------


def perform_ingest(request: Request, workspace: Workspace) -> Outcome:
    """Stage, hash, publish, seal and register one jurisdiction's sources.

    Through `hodd.ingest`, which is atomic: everything before the rename is
    disposable and a crash leaves a staged tree nothing references. This must
    not undo that, so it does no partial work of its own -- it hands the whole
    tree over and records what came back.

    An artefact already at the address is *not* an error to retry. It is the
    previous attempt having succeeded between the put and the record, which is
    the one window a crash can leave open, so the outcome is recorded and the
    request closed.
    """
    try:
        store = workspace.require_store()
        sources = workspace.sources_for(request.jurisdiction)
        if not sources.is_dir():
            msg = (
                f"no sources for {request.jurisdiction} at {sources}. A curator has "
                "registered them and not yet dropped the files there."
            )
            raise CorpusWorkError(msg)

        uri = artefact_uri(workspace.site_id, "corpus", f"{request.jurisdiction}/raw")
        ingested = Ingestor(store, scan=_scanner()).ingest(
            sources, uri, kind="corpus_raw", facts={"jurisdiction": request.jurisdiction}
        )
    except ImmutableArtefactError as held:
        # Not a failure to retry: the artefact is already at the address, which
        # is the previous attempt having succeeded between the publish and the
        # record. That is the one window a crash can leave open, and closing
        # the request is the right answer to it -- retrying for ever against a
        # sealed artefact is the livelock the seal would otherwise cause.
        return Outcome(
            request,
            INGEST_COMPLETED,
            request.as_payload(
                uri=held.uri,
                note=(
                    "already ingested: a previous attempt published this artefact and "
                    "did not record the outcome"
                ),
            ),
        )
    # Broad, and here rather than at each call. One corpus that cannot be
    # ingested must not stop the estate, and every reason -- a missing
    # directory, a planted secret, a full vault, a driver raising something
    # nobody anticipated -- belongs in the chain where the curator who asked
    # for it will look, rather than in a traceback in the worker's log.
    except Exception as refusal:
        logger.warning(
            "corpus.ingest.failed",
            jurisdiction=request.jurisdiction,
            seq=request.seq,
            reason=str(refusal),
        )
        return Outcome(request, INGEST_FAILED, request.as_payload(reason=str(refusal)))

    return Outcome(
        request,
        INGEST_COMPLETED,
        request.as_payload(
            uri=ingested.uri,
            manifestSha256=ingested.digest,
            bytesWritten=ingested.bytes_written,
            files=ingested.manifest.file_count,
        ),
    )


def perform_curation(request: Request, workspace: Workspace) -> Outcome:
    """Deduplicate, filter and decontaminate one jurisdiction's raw corpus.

    The raw tree goes read only afterwards (AC-F3), and the curated output is
    ingested into the vault like any other artefact -- so it is sealed, has a
    manifest, and has an address a run's specification can name.
    """
    try:
        store = workspace.require_store()
        evaluation_sets = workspace.require_evaluation_sets()
        raw_uri = artefact_uri(workspace.site_id, "corpus", f"{request.jurisdiction}/raw")
        raw = Path(store.resolve(raw_uri))
        if not raw.is_dir():
            msg = (
                f"{request.jurisdiction} has no ingested corpus at {raw_uri}, so there "
                "is nothing to curate. Ingest is accepted separately and may not have "
                "run yet."
            )
            raise CorpusWorkError(msg)

        workdir = workspace.scratch / request.jurisdiction
        curated = curation.curate(raw, workdir / "corpus.bin", evaluation_sets=evaluation_sets)

        # Into the vault, as a tree with its own manifest, like every other
        # artefact. A curated corpus that lived only in scratch would be the
        # defect RF-08 was about, one directory along.
        curated_uri = artefact_uri(workspace.site_id, "corpus", f"{request.jurisdiction}/curated")
        Ingestor(store, scan=_scanner()).ingest(
            workdir,
            curated_uri,
            kind="corpus_curated",
            facts={"jurisdiction": request.jurisdiction, **curated.as_payload()},
        )
        curation.read_only(raw)
    # Broad for the same reason as above: the reason goes to the curator.
    except Exception as refusal:
        logger.warning(
            "corpus.curate.failed",
            jurisdiction=request.jurisdiction,
            seq=request.seq,
            reason=str(refusal),
        )
        return Outcome(request, CURATE_FAILED, request.as_payload(reason=str(refusal)))

    return Outcome(
        request,
        CURATE_COMPLETED,
        request.as_payload(uri=curated_uri, **curated.as_payload()),
    )


def _scanner() -> Any:
    """The secret scanner an ingest runs before the point of no return.

    RF-09 put it on the ingest path and this is the second caller. Imported
    here rather than at module scope only because HODD and SVALINN are siblings
    and the worker is the layer allowed to know both.
    """
    from draupnir.svalinn.scanning import scan_before_registration

    return scan_before_registration


def perform(requests: Sequence[Request], workspace: Workspace) -> tuple[Outcome, ...]:
    """Do every outstanding request, in the order they were accepted.

    In order, because two requests for one jurisdiction are an ingest and then
    a re-ingest, and doing the second first would leave the corpus at the
    earlier state while the chain said otherwise.
    """
    done: list[Outcome] = []
    for request in requests:
        if request.transition == INGEST_ACCEPTED:
            done.append(perform_ingest(request, workspace))
        else:
            done.append(perform_curation(request, workspace))
    return tuple(done)


__all__ = [
    "ANSWERS",
    "CORPUS_SUBJECT",
    "CURATE_ACCEPTED",
    "CURATE_COMPLETED",
    "CURATE_FAILED",
    "INGEST_ACCEPTED",
    "INGEST_COMPLETED",
    "INGEST_FAILED",
    "CorpusWorkError",
    "Outcome",
    "Request",
    "Workspace",
    "outstanding",
    "perform",
    "perform_curation",
    "perform_ingest",
]
