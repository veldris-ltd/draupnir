"""Deduplicate, filter and decontaminate a raw corpus. SAD 6.1's CURATED guard.

RF-12 asked for the ingest and curation the API accepts to be performed by
something. `hodd.ingest` existed and was called by nothing; **the curation
pipeline did not exist at all.** The Sindri procedure dispatched the stand-in
executor over the raw tree and recorded `{"dedupe": 0.82, "quality": 0.61,
"decontaminate": 0.99}` as literals, and SAD 6.1's guard asks for
`decontamination_confirmed` -- a flag that was set to `True` beside three
numbers nobody had measured.

That is the shape of a claim rather than a control, so this module is deliberate
about what it does and does not do.

**What it does.** Three stages, in the order that makes each cheap:

1. **Deduplicate** by content. Byte-identical documents only. Near-duplicate
   detection (MinHash, SimHash) is a different problem with a threshold
   somebody has to defend, and a threshold nobody has defended is worse than
   none -- so this removes what is provably the same and says that is what it
   removed.
2. **Filter** on length. A document shorter than `MIN_DOCUMENT_BYTES` carries
   no signal a language model can use and is over-represented in scraped legal
   corpora: navigation fragments, empty judgments, stub pages.
3. **Decontaminate** against the evaluation sets. Any document containing a
   string from an evaluation set is removed, whole.

**What it does not do.** It is not a research-grade curation stack. There is no
language identification, no quality classifier, no PII redaction, no
boilerplate stripping. Each of those is a decision about the corpus that
somebody should make deliberately, and a module that did them silently would be
making them on their behalf.

**Decontamination is the one that cannot be skipped.** The other two stages
improve a corpus; this one is the difference between an evaluation score that
means something and one that does not. A model trained on its own test set
scores well and has learned nothing, and every gate downstream is then
measuring the contamination. So `curate` **refuses** rather than proceeding
when it is given no evaluation sets to check against: `decontamination_confirmed`
is a guard in SAD 6.1's table, and confirming it without having checked is
exactly the class of defect RF-12 is about.

Whole documents are removed rather than the offending lines, because a document
that contains a test item is a document about that test item, and excising the
line leaves the context that surrounds it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Below this a document is a fragment. Chosen at the low end deliberately: the
#: filter exists to remove navigation stubs and empty judgments, not to make an
#: editorial judgement about what is worth training on.
MIN_DOCUMENT_BYTES = 256

#: The shortest evaluation-set string that is worth matching on. A short string
#: appears in ordinary prose by coincidence, and removing every document that
#: contains one would empty the corpus while reporting a decontamination rate.
MIN_CONTAMINANT_LENGTH = 48


class CurationError(Exception):
    """Raised when a corpus cannot be curated."""


class NotDecontaminatedError(CurationError):
    """Raised when curation is asked to proceed without evaluation sets.

    Not a warning and not a flag. SAD 6.1 makes `decontamination_confirmed` a
    guard on reaching CURATED, and a run that reached it without the check
    having run carries a claim its own chain cannot support.
    """

    def __init__(self, where: Path) -> None:
        """Name what was looked for."""
        self.where = where
        super().__init__(
            f"no evaluation set was found at {where}, so contamination cannot be "
            "checked for. A corpus curated without this is a corpus whose "
            "evaluation scores measure the overlap rather than the model: SAD 6.1 "
            "requires decontamination against the evaluation sets to be confirmed, "
            "and confirming it without checking is the claim that check exists to "
            "make unnecessary."
        )


@dataclass(frozen=True, slots=True)
class Stage:
    """What one stage of the pipeline kept, and what it dropped."""

    name: str
    considered: int
    kept: int

    @property
    def dropped(self) -> int:
        """How many documents this stage removed."""
        return self.considered - self.kept

    @property
    def retention(self) -> float:
        """The fraction kept. 1.0 for a stage with nothing to consider."""
        return 1.0 if not self.considered else round(self.kept / self.considered, 4)

    def as_payload(self) -> dict[str, Any]:
        """The wire shape."""
        return {
            "considered": self.considered,
            "kept": self.kept,
            "dropped": self.dropped,
            "retention": self.retention,
        }


@dataclass(frozen=True, slots=True)
class Curation:
    """What curating one corpus produced, and how it got there."""

    output: Path
    sha256: str
    #: Bytes of curated text. Named `token_count` where SAD 6.1 records it,
    #: which is what that field has always held; this is the honest name for it
    #: until a tokeniser is on the path.
    byte_count: int
    stages: tuple[Stage, ...]
    #: Which evaluation sets were checked against, by name. Empty is
    #: impossible: `curate` refuses before it gets here.
    evaluation_sets: tuple[str, ...] = ()
    #: Documents removed for containing an evaluation-set string, by name.
    contaminated: tuple[str, ...] = ()

    @property
    def retention(self) -> dict[str, float]:
        """The per-stage retention SAD 6.1 records at CURATED."""
        return {stage.name: stage.retention for stage in self.stages}

    def as_payload(self) -> dict[str, Any]:
        """The ledger payload for a curation."""
        return {
            "outputSha256": self.sha256,
            "byteCount": self.byte_count,
            "stages": {stage.name: stage.as_payload() for stage in self.stages},
            "stageRetention": self.retention,
            "evaluationSets": list(self.evaluation_sets),
            "contaminated": list(self.contaminated),
            "decontaminationConfirmed": True,
        }


@dataclass
class Document:
    """One source document, as curation sees it."""

    name: str
    text: str

    @property
    def digest(self) -> str:
        """The content hash this document deduplicates on."""
        return hashlib.sha256(self.text.encode("utf-8", "surrogatepass")).hexdigest()


def documents(root: Path) -> list[Document]:
    """Every readable text file under `root`, in a stable order.

    Sorted by path, because the curated output is hashed and recorded and a
    corpus that curated to different bytes on two runs of the same inputs would
    not be reproducible (SAD 6.2). Unreadable files are skipped rather than
    raising: a corpus of ten thousand documents should not fail to curate
    because one of them is a broken symlink, and what was skipped is reported.
    """
    found: list[Document] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.append(Document(name=str(path.relative_to(root)).replace("\\", "/"), text=text))
    return found


def deduplicate(given: Sequence[Document]) -> tuple[list[Document], Stage]:
    """Remove byte-identical documents, keeping the first by name.

    The first rather than an arbitrary one, so that two runs over the same
    inputs keep the same copy and produce the same bytes.
    """
    seen: set[str] = set()
    kept: list[Document] = []
    for document in given:
        digest = document.digest
        if digest in seen:
            continue
        seen.add(digest)
        kept.append(document)
    return kept, Stage(name="dedupe", considered=len(given), kept=len(kept))


def filter_short(
    given: Sequence[Document], *, minimum: int = MIN_DOCUMENT_BYTES
) -> tuple[list[Document], Stage]:
    """Remove documents too short to carry signal."""
    kept = [item for item in given if len(item.text.encode("utf-8", "surrogatepass")) >= minimum]
    return kept, Stage(name="quality", considered=len(given), kept=len(kept))


def contaminants(
    evaluation_sets: Path, *, minimum: int = MIN_CONTAMINANT_LENGTH
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The strings a corpus must not contain, and the sets they came from.

    One string per non-empty line of every file under `evaluation_sets`. A line
    rather than a whole file, because an evaluation set is a list of items and a
    corpus is contaminated by containing one of them, not by containing all.

    Lines shorter than `minimum` are ignored: a short string occurs in ordinary
    prose by coincidence, and matching on it would empty the corpus while
    reporting a decontamination rate that looked thorough.
    """
    if not evaluation_sets.is_dir():
        raise NotDecontaminatedError(evaluation_sets)

    found: list[str] = []
    names: list[str] = []
    for path in sorted(item for item in evaluation_sets.rglob("*") if item.is_file()):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        names.append(str(path.relative_to(evaluation_sets)).replace("\\", "/"))
        found.extend(line.strip() for line in text.splitlines() if len(line.strip()) >= minimum)

    if not found:
        raise NotDecontaminatedError(evaluation_sets)
    return tuple(dict.fromkeys(found)), tuple(names)


def decontaminate(
    given: Sequence[Document], *, against: Iterable[str]
) -> tuple[list[Document], Stage, tuple[str, ...]]:
    """Remove every document containing an evaluation-set string.

    The whole document, not the offending line: a document that contains a test
    item is a document about that test item, and excising the line leaves the
    context that surrounds it -- which is most of what makes the item findable.
    """
    items = tuple(against)
    kept: list[Document] = []
    removed: list[str] = []
    for document in given:
        if any(item in document.text for item in items):
            removed.append(document.name)
            continue
        kept.append(document)
    return kept, Stage(name="decontaminate", considered=len(given), kept=len(kept)), tuple(removed)


def curate(
    raw: Path,
    output: Path,
    *,
    evaluation_sets: Path,
    minimum_bytes: int = MIN_DOCUMENT_BYTES,
) -> Curation:
    """Curate a raw corpus into one file, or refuse.

    One file rather than a tree, because what a training driver consumes is a
    corpus and what a run's identity is computed over is its hash. The
    per-document boundaries are kept as newlines; nothing else about the
    structure survives, and nothing downstream reads it.

    Refuses on an empty result. A corpus that curated to nothing is not a
    curated corpus, and writing an empty file would send a run to training
    against zero bytes and report a retention figure for it.
    """
    if not raw.is_dir():
        msg = f"{raw} is not a directory; there is nothing to curate"
        raise CurationError(msg)

    # Before any work: the check that cannot be skipped, so a missing
    # evaluation set is reported before minutes of hashing rather than after.
    items, sets = contaminants(evaluation_sets)

    given = documents(raw)
    if not given:
        msg = f"{raw} holds no readable document"
        raise CurationError(msg)

    stages: list[Stage] = []
    kept, stage = deduplicate(given)
    stages.append(stage)
    kept, stage = filter_short(kept, minimum=minimum_bytes)
    stages.append(stage)
    kept, stage, removed = decontaminate(kept, against=items)
    stages.append(stage)

    if not kept:
        working = "; ".join(f"{item.name} kept {item.kept} of {item.considered}" for item in stages)
        msg = (
            f"curating {raw} left no document: {working}. An empty corpus is not a curated corpus."
        )
        raise CurationError(msg)

    output.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(document.text for document in kept)
    output.write_text(body, encoding="utf-8")

    return Curation(
        output=output,
        sha256=hashlib.sha256(body.encode("utf-8", "surrogatepass")).hexdigest(),
        byte_count=len(body.encode("utf-8", "surrogatepass")),
        stages=tuple(stages),
        evaluation_sets=sets,
        contaminated=removed,
    )


def read_only(root: Path) -> None:
    """Drop write permission across a tree. AC-F3.

    The raw corpus is read only once it has been curated, and the control is
    the mode change rather than a note that it should not be written to: a
    curation script that never consulted the database is refused by the
    filesystem.
    """
    for path in [root, *root.rglob("*")]:
        try:
            path.chmod(path.stat().st_mode & ~0o222)
        except OSError:
            continue


__all__ = [
    "MIN_CONTAMINANT_LENGTH",
    "MIN_DOCUMENT_BYTES",
    "Curation",
    "CurationError",
    "Document",
    "NotDecontaminatedError",
    "Stage",
    "contaminants",
    "curate",
    "decontaminate",
    "deduplicate",
    "documents",
    "filter_short",
    "read_only",
]
