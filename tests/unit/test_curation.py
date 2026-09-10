"""Deduplicate, filter and decontaminate. RF-12.

There was no curation pipeline. The Sindri procedure dispatched the stand-in
executor over the raw tree and recorded `{"dedupe": 0.82, "quality": 0.61,
"decontaminate": 0.99}` as literals, and SAD 6.1's CURATED guard asks for
`decontamination_confirmed` -- a flag set to `True` beside three numbers nobody
had measured.

The decontamination tests are the ones that matter. A model trained on its own
test set scores well and has learned nothing, and every gate downstream is then
measuring the contamination rather than the model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from draupnir.hodd import curation

pytestmark = pytest.mark.unit

#: Long enough to survive the length filter, so that a test about
#: deduplication is not quietly a test about the filter.
BODY = "The Human Rights Act 1998 gives further effect to rights and freedoms. " * 8

#: A string an evaluation set holds, long enough to be worth matching on.
ITEM = "In what year did the Human Rights Act receive Royal Assent in the United Kingdom?"


def _corpus(root: Path, **documents: str) -> Path:
    """A raw corpus directory holding these documents."""
    root.mkdir(parents=True, exist_ok=True)
    for name, text in documents.items():
        (root / f"{name}.txt").write_text(text, encoding="utf-8")
    return root


def _evaluation_sets(root: Path, *items: str) -> Path:
    """An evaluation set directory holding these items, one per line."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "general-core.txt").write_text("\n".join(items), encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Decontamination
# ---------------------------------------------------------------------------


def test_a_document_containing_an_evaluation_item_is_removed(tmp_path: Path) -> None:
    """Whole, not line by line.

    A document that contains a test item is a document about that test item.
    Excising the line leaves the context that surrounds it, which is most of
    what makes the item findable.
    """
    raw = _corpus(
        tmp_path / "raw",
        clean=BODY,
        contaminated=f"{BODY}\n{ITEM}\nThe answer is 1998.",
    )
    sets = _evaluation_sets(tmp_path / "eval", ITEM)

    curated = curation.curate(raw, tmp_path / "out" / "corpus.bin", evaluation_sets=sets)

    assert curated.contaminated == ("contaminated.txt",)
    assert ITEM not in curated.output.read_text(encoding="utf-8")
    assert "The answer is 1998" not in curated.output.read_text(encoding="utf-8")


def test_curation_refuses_when_there_is_nothing_to_decontaminate_against(
    tmp_path: Path,
) -> None:
    """The finding, at its sharpest.

    Confirming decontamination without having checked is what the old code did
    -- 0.99, as a literal. `curate` raises rather than proceeding, because SAD
    6.1 makes this a guard and a guard that passes when it was not evaluated is
    not a guard.
    """
    raw = _corpus(tmp_path / "raw", one=BODY)

    with pytest.raises(curation.NotDecontaminatedError) as refusal:
        curation.curate(raw, tmp_path / "out" / "corpus.bin", evaluation_sets=tmp_path / "absent")

    assert "measure the overlap rather than the model" in str(refusal.value)


def test_an_empty_evaluation_directory_is_the_same_refusal(tmp_path: Path) -> None:
    """A directory that exists and holds nothing is not a check that ran."""
    raw = _corpus(tmp_path / "raw", one=BODY)
    empty = tmp_path / "eval"
    empty.mkdir()

    with pytest.raises(curation.NotDecontaminatedError):
        curation.curate(raw, tmp_path / "out" / "corpus.bin", evaluation_sets=empty)


def test_the_check_happens_before_the_work(tmp_path: Path) -> None:
    """So a missing evaluation set is reported before minutes of hashing.

    On a real corpus the three stages are the expensive part. Discovering
    afterwards that the whole thing has to be redone is the kind of ordering
    mistake that gets worked around rather than fixed.
    """
    raw = _corpus(tmp_path / "raw", one=BODY)
    output = tmp_path / "out" / "corpus.bin"

    with pytest.raises(curation.NotDecontaminatedError):
        curation.curate(raw, output, evaluation_sets=tmp_path / "absent")

    assert not output.exists(), "curation wrote an output before checking it could decontaminate"


def test_a_short_evaluation_line_is_not_matched_on(tmp_path: Path) -> None:
    """A short string occurs in ordinary prose by coincidence.

    Matching on it would empty the corpus while reporting a decontamination
    rate that looked thorough, which is worse than not checking: it is not
    checking with a number attached.
    """
    raw = _corpus(tmp_path / "raw", one=BODY)
    sets = _evaluation_sets(tmp_path / "eval", "the", "rights", ITEM)

    curated = curation.curate(raw, tmp_path / "out" / "corpus.bin", evaluation_sets=sets)

    assert curated.contaminated == ()
    assert curated.retention["decontaminate"] == 1.0


# ---------------------------------------------------------------------------
# The other two stages
# ---------------------------------------------------------------------------


def test_byte_identical_documents_are_deduplicated(tmp_path: Path) -> None:
    """And the first by name is the one kept, so two runs agree."""
    raw = _corpus(tmp_path / "raw", a=BODY, b=BODY, c=f"{BODY} and something else entirely.")
    sets = _evaluation_sets(tmp_path / "eval", ITEM)

    curated = curation.curate(raw, tmp_path / "out" / "corpus.bin", evaluation_sets=sets)

    assert curated.retention["dedupe"] == round(2 / 3, 4)
    assert curated.output.read_text(encoding="utf-8").count("something else entirely") == 1


def test_a_fragment_is_filtered(tmp_path: Path) -> None:
    """Navigation stubs and empty judgments are over-represented in scraped law."""
    raw = _corpus(tmp_path / "raw", full=BODY, stub="Back to top")
    sets = _evaluation_sets(tmp_path / "eval", ITEM)

    curated = curation.curate(raw, tmp_path / "out" / "corpus.bin", evaluation_sets=sets)

    assert curated.retention["quality"] == 0.5
    assert "Back to top" not in curated.output.read_text(encoding="utf-8")


def test_curating_the_same_corpus_twice_produces_the_same_bytes(tmp_path: Path) -> None:
    """SAD 6.2 makes the corpus digest part of a run's identity.

    A curation that produced different bytes on two runs over the same inputs
    would give the same corpus two identities, and every duplicate check
    downstream would stop working.
    """
    raw = _corpus(tmp_path / "raw", b=f"{BODY} second", a=BODY, c=f"{BODY} third")
    sets = _evaluation_sets(tmp_path / "eval", ITEM)

    first = curation.curate(raw, tmp_path / "one" / "corpus.bin", evaluation_sets=sets)
    second = curation.curate(raw, tmp_path / "two" / "corpus.bin", evaluation_sets=sets)

    assert first.sha256 == second.sha256


def test_a_corpus_curated_to_nothing_is_refused(tmp_path: Path) -> None:
    """Writing an empty file would send a run to train against zero bytes.

    And it would report a retention figure for it, which is how a corpus that
    curation destroyed reaches a board looking like a corpus.
    """
    raw = _corpus(tmp_path / "raw", stub="short", other="also short")
    sets = _evaluation_sets(tmp_path / "eval", ITEM)

    with pytest.raises(curation.CurationError) as refusal:
        curation.curate(raw, tmp_path / "out" / "corpus.bin", evaluation_sets=sets)

    assert "quality kept 0 of 2" in str(refusal.value)


def test_the_payload_records_what_was_measured(tmp_path: Path) -> None:
    """Every number in it came from counting, which is the whole point."""
    raw = _corpus(tmp_path / "raw", a=BODY, b=BODY, stub="x", clean=f"{BODY} distinct")
    sets = _evaluation_sets(tmp_path / "eval", ITEM)

    payload = curation.curate(
        raw, tmp_path / "out" / "corpus.bin", evaluation_sets=sets
    ).as_payload()

    assert payload["stages"]["dedupe"]["considered"] == 4
    assert payload["stages"]["dedupe"]["dropped"] == 1
    assert payload["evaluationSets"] == ["general-core.txt"]
    assert payload["decontaminationConfirmed"] is True
    assert payload["byteCount"] > 0
