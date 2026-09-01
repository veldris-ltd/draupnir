"""Ingest is atomic, and what it produces is read only.

AC-F3: "A corpus is ingested, hashed, licence registered and curated; the raw
directory is read only afterwards and a write attempt is refused."

Atomicity is tested by crashing. Each step of the ingest is made to fail in
turn, and after each the question is the same: is there a half-registered
corpus? A test that only checks the happy path would pass against an
implementation that registers first and hashes afterwards, which is the
implementation this requirement exists to forbid.
"""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from draupnir.hodd.ingest import MANIFEST_NAME, IngestError, Ingestor
from draupnir.hodd.register import LicenceRegister, SourceRecord
from draupnir.hodd.stores import ImmutableArtefactError, PosixStoreDriver

URI = "hodd://sindri/corpora/GBR/raw"


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A small corpus to ingest."""
    source = tmp_path / "incoming"
    (source / "acts").mkdir(parents=True)
    (source / "acts" / "1998.txt").write_text("Human Rights Act 1998", encoding="utf-8")
    (source / "acts" / "2018.txt").write_text("Data Protection Act 2018", encoding="utf-8")
    (source / "README").write_text("legislation.gov.uk bulk export", encoding="utf-8")
    return source


@pytest.fixture
def store(tmp_path: Path) -> PosixStoreDriver:
    return PosixStoreDriver(root=tmp_path / "vault", local_site="sindri")


@pytest.fixture
def register() -> LicenceRegister:
    return LicenceRegister()


@pytest.fixture
def ingestor(store: PosixStoreDriver, register: LicenceRegister) -> Ingestor:
    return Ingestor(store, register, clock=lambda: datetime(2026, 3, 2, 9, tzinfo=UTC))


def source_record() -> SourceRecord:
    return SourceRecord(
        id=uuid4(),
        jurisdiction="GBR",
        url="https://www.legislation.gov.uk/ukpga",
        licence_spdx="OGL-UK-3.0",
        attribution_required=True,
        retrieved_at=datetime(2026, 3, 2, tzinfo=UTC),
        sha256="a" * 64,
        personal_data=False,
    )


# ---------------------------------------------------------------------------
# AC-F3
# ---------------------------------------------------------------------------


def test_a_corpus_is_ingested_hashed_and_registered(
    ingestor: Ingestor, corpus: Path, register: LicenceRegister
) -> None:
    result = ingestor.ingest(corpus, URI, kind="corpus_raw", source=source_record())

    assert result.manifest.file_count == 3
    assert result.manifest.size > 0
    assert len(result.digest) == 64
    assert result.source is not None
    assert len(register) == 1

    # Every file has a hash, and the hashes are of the bytes actually stored.
    entry = result.manifest.entry("acts/1998.txt")
    assert entry is not None
    assert entry.size == len("Human Rights Act 1998")


def test_the_raw_directory_is_read_only_afterwards(
    ingestor: Ingestor, corpus: Path, store: PosixStoreDriver
) -> None:
    """AC-F3, first half."""
    ingestor.ingest(corpus, URI, kind="corpus_raw")

    assert store.is_sealed(URI)
    stored = Path(store.resolve(URI))
    for path in stored.rglob("*"):
        if path.is_file():
            assert not path.stat().st_mode & stat.S_IWUSR, f"{path} is still writable"


def test_a_write_attempt_is_refused(
    ingestor: Ingestor, corpus: Path, store: PosixStoreDriver, tmp_path: Path
) -> None:
    """AC-F3, second half. Two ways in, both refused."""
    ingestor.ingest(corpus, URI, kind="corpus_raw")
    stored = Path(store.resolve(URI))

    # A curation script that never consulted the database, writing directly.
    with pytest.raises(PermissionError):
        (stored / "acts" / "1998.txt").write_text("rewritten", encoding="utf-8")

    # And through HODD itself.
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "acts").mkdir()
    (replacement / "acts" / "1998.txt").write_text("rewritten", encoding="utf-8")
    with pytest.raises(ImmutableArtefactError):
        store.put(URI, replacement)


def test_the_manifest_is_stored_beside_the_artefact(
    ingestor: Ingestor, corpus: Path, store: PosixStoreDriver
) -> None:
    # SAD 7.3: the manifest outlives the corpus, so it lives with it rather
    # than only in a database that a restore might not carry.
    ingestor.ingest(corpus, URI, kind="corpus_raw")
    assert (Path(store.resolve(URI)) / MANIFEST_NAME).is_file()


def test_the_stored_artefact_verifies_against_its_manifest(
    ingestor: Ingestor, corpus: Path
) -> None:
    ingestor.ingest(corpus, URI, kind="corpus_raw")
    assert ingestor.verify(URI) == ()
    assert ingestor.describe_divergence(URI) == "intact"


def test_altering_one_byte_is_detected(
    ingestor: Ingestor, corpus: Path, store: PosixStoreDriver
) -> None:
    """AC-S1 and AC-S8 are this check, called at different moments."""
    ingestor.ingest(corpus, URI, kind="corpus_raw")

    # Only a retention action reaches unseal; here it stands in for a restore
    # from a doctored backup, which is how an artefact actually gets edited.
    store.unseal(URI)
    target = Path(store.resolve(URI)) / "acts" / "1998.txt"
    target.write_text("Human Rights Act 1997", encoding="utf-8")

    divergences = ingestor.verify(URI)
    assert len(divergences) == 1
    assert "acts/1998.txt" in divergences[0]
    assert "SHA-256" in divergences[0]


def test_a_removed_file_is_detected(
    ingestor: Ingestor, corpus: Path, store: PosixStoreDriver
) -> None:
    ingestor.ingest(corpus, URI, kind="corpus_raw")
    store.unseal(URI)
    (Path(store.resolve(URI)) / "README").unlink()

    divergences = ingestor.verify(URI)
    assert any("README" in item and "missing" in item for item in divergences)


def test_an_added_file_is_detected(
    ingestor: Ingestor, corpus: Path, store: PosixStoreDriver
) -> None:
    ingestor.ingest(corpus, URI, kind="corpus_raw")
    store.unseal(URI)
    (Path(store.resolve(URI)) / "smuggled.txt").write_text("extra", encoding="utf-8")

    divergences = ingestor.verify(URI)
    assert any("smuggled.txt" in item for item in divergences)


# ---------------------------------------------------------------------------
# Atomicity: a crash at any point leaves no half-registered corpus
# ---------------------------------------------------------------------------


def test_a_failure_while_hashing_registers_nothing(
    ingestor: Ingestor,
    corpus: Path,
    register: LicenceRegister,
    store: PosixStoreDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import draupnir.hodd.ingest as module

    def explode(*args: object, **kwargs: object) -> object:
        msg = "the vault went away mid-hash"
        raise OSError(msg)

    monkeypatch.setattr(module, "build", explode)

    with pytest.raises(OSError, match="mid-hash"):
        ingestor.ingest(corpus, URI, kind="corpus_raw", source=source_record())

    assert len(register) == 0
    assert not Path(store.resolve(URI)).exists()
    assert ingestor.abandoned_staging() == ()


def test_a_failure_while_writing_the_manifest_registers_nothing(
    ingestor: Ingestor,
    corpus: Path,
    register: LicenceRegister,
    store: PosixStoreDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Path.write_text

    def refuse(self: Path, *args: object, **kwargs: object) -> int:
        if self.name == MANIFEST_NAME:
            msg = "no space left on device"
            raise OSError(msg)
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", refuse)

    with pytest.raises(OSError, match="no space"):
        ingestor.ingest(corpus, URI, kind="corpus_raw", source=source_record())

    assert len(register) == 0
    assert not Path(store.resolve(URI)).exists()
    assert ingestor.abandoned_staging() == ()


def test_a_failure_at_the_publish_rename_registers_nothing(
    ingestor: Ingestor,
    corpus: Path,
    register: LicenceRegister,
    store: PosixStoreDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(self: Path, target: object) -> None:
        msg = "the vault went read only"
        raise OSError(msg)

    monkeypatch.setattr(Path, "rename", refuse)

    with pytest.raises(OSError, match="read only"):
        ingestor.ingest(corpus, URI, kind="corpus_raw", source=source_record())

    assert len(register) == 0
    assert not Path(store.resolve(URI)).exists()
    assert ingestor.abandoned_staging() == ()


def test_a_failure_while_registering_leaves_a_findable_orphan(
    ingestor: Ingestor, corpus: Path, register: LicenceRegister, store: PosixStoreDriver
) -> None:
    """The one window that cannot be closed, and what is done about it.

    After the rename the artefact exists; if registration then fails, the
    bytes are sealed and nothing names them. They are reported rather than
    deleted, because an artefact HODD sealed is exactly the sort of thing a
    process should not remove on its own initiative.
    """
    source = source_record()
    register.record(source)  # already recorded, so the ingest's record() raises

    with pytest.raises(Exception, match="already recorded"):
        ingestor.ingest(corpus, URI, kind="corpus_raw", source=source)

    assert Path(store.resolve(URI)).exists()
    assert store.is_sealed(URI)
    assert ingestor.orphans(known_uris=[]) == (URI,)
    assert ingestor.orphans(known_uris=[URI]) == ()


def test_abandoned_staging_is_findable_and_discardable(
    ingestor: Ingestor, corpus: Path, store: PosixStoreDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate the process dying between staging and publish: stage, then stop.
    import shutil

    staged = ingestor._staging_for(URI)
    shutil.copytree(corpus, staged)

    assert ingestor.abandoned_staging() == (staged,)
    assert ingestor.discard_abandoned() == 1
    assert ingestor.abandoned_staging() == ()
    # And nothing resolvable ever pointed at it.
    assert not Path(store.resolve(URI)).exists()


def test_ingesting_over_an_existing_artefact_is_refused(ingestor: Ingestor, corpus: Path) -> None:
    ingestor.ingest(corpus, URI, kind="corpus_raw")
    with pytest.raises(ImmutableArtefactError):
        ingestor.ingest(corpus, URI, kind="corpus_raw")


def test_ingesting_an_empty_tree_is_refused(ingestor: Ingestor, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(Exception, match="no files"):
        ingestor.ingest(empty, URI, kind="corpus_raw")


def test_a_source_without_a_register_is_refused(store: PosixStoreDriver, corpus: Path) -> None:
    with pytest.raises(IngestError, match="no licence register"):
        Ingestor(store).ingest(corpus, URI, kind="corpus_raw", source=source_record())
