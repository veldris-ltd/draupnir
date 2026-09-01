"""`hodd://` resolution, and the property SAD 7.4 exists to provide.

"Artefacts are addressed by a `hodd://` URI resolved by HODD to a physical
location. This keeps run specifications portable across a storage change,
which matters because gap G2 and the Phase 2 NVMe over Fabrics option both
alter physical placement."

The test that matters is `test_relocating_the_vault_does_not_invalidate_a_uri`:
the same URI, recorded in a run specification, resolves against a vault that
has moved and then against an object store that did not exist when it was
written. If that ever stops being true, every historical run specification
becomes unreplayable at once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from draupnir.hodd.stores import (
    Address,
    ImmutableArtefactError,
    ObjectStoreDriver,
    PosixStoreDriver,
    StoreError,
    driver_for,
    parse,
    readable_size,
)

URI = "hodd://sindri/corpora/GBR/curated"


@pytest.fixture
def artefact(tmp_path: Path) -> Path:
    source = tmp_path / "incoming"
    source.mkdir()
    (source / "corpus.txt").write_text("the text", encoding="utf-8")
    return source


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------


def test_an_authority_naming_the_site_is_site_scoped() -> None:
    assert parse(URI, local_site="sindri") == Address("sindri", "corpora/GBR/curated")


def test_an_authority_that_is_not_a_site_is_part_of_the_path() -> None:
    # SAD 6.2 writes `hodd://models/core/...`; SAD 7.4 says the authority is
    # the site. Both spellings resolve.
    assert parse("hodd://models/core/MIDGARD-v1.0", local_site="sindri") == Address(
        "sindri", "models/core/MIDGARD-v1.0"
    )


def test_an_omitted_authority_resolves_to_the_local_site() -> None:
    assert parse("hodd:///corpora/GBR/raw", local_site="brokkr") == Address(
        "brokkr", "corpora/GBR/raw"
    )


def test_a_foreign_scheme_is_refused() -> None:
    with pytest.raises(StoreError, match="not a hodd"):
        parse("s3://bucket/key", local_site="sindri")


def test_an_address_naming_nothing_is_refused() -> None:
    with pytest.raises(StoreError, match="names no artefact"):
        parse("hodd://", local_site="sindri")


def test_a_relative_segment_is_refused() -> None:
    # Either a mistake or an attempt to reach outside the vault.
    with pytest.raises(StoreError, match="relative path"):
        parse("hodd://sindri/corpora/../../etc/passwd", local_site="sindri")


def test_a_path_escaping_the_vault_is_refused(tmp_path: Path) -> None:
    store = PosixStoreDriver(root=tmp_path / "vault", local_site="sindri")
    with pytest.raises(StoreError):
        store.resolve("hodd://sindri/../../../etc/passwd")


def test_an_address_renders_back_fully_qualified() -> None:
    assert str(parse("hodd://models/x", local_site="sindri")) == "hodd://sindri/models/x"


# ---------------------------------------------------------------------------
# The property SAD 7.4 buys
# ---------------------------------------------------------------------------


def test_relocating_the_vault_does_not_invalidate_a_uri(tmp_path: Path, artefact: Path) -> None:
    """A run specification records a URI, never a path."""
    first = PosixStoreDriver(root=tmp_path / "vault-a", local_site="sindri")
    first.put(URI, artefact)
    assert first.stat(URI).exists

    # Phase 2: the vault moves to different hardware. The URI is unchanged.
    import shutil

    shutil.copytree(tmp_path / "vault-a", tmp_path / "vault-b")
    second = PosixStoreDriver(root=tmp_path / "vault-b", local_site="sindri")

    assert second.stat(URI).exists
    assert second.resolve(URI) != first.resolve(URI)


def test_the_same_uri_resolves_against_a_different_kind_of_store(
    tmp_path: Path,
) -> None:
    """NVMe over Fabrics, or MinIO, or anything else: a new driver, not a new URI."""
    posix = PosixStoreDriver(root=tmp_path / "vault", local_site="sindri")
    objects = ObjectStoreDriver(bucket="draupnir", client=object(), local_site="sindri")

    assert posix.resolve(URI).endswith(str(Path("sindri") / "corpora" / "GBR" / "curated"))
    assert objects.resolve(URI) == "s3://draupnir/sindri/corpora/GBR/curated"


def test_a_second_forges_artefacts_resolve_through_its_own_driver(
    tmp_path: Path,
) -> None:
    drivers: dict[str, Any] = {
        "sindri": PosixStoreDriver(root=tmp_path / "sindri", local_site="sindri"),
        "brokkr": PosixStoreDriver(root=tmp_path / "brokkr", local_site="brokkr"),
    }
    chosen = driver_for("hodd://brokkr/adapters/x", drivers, local_site="sindri")
    assert chosen is drivers["brokkr"]


def test_an_unknown_authority_is_a_path_and_never_another_site(
    tmp_path: Path,
) -> None:
    """The cost of reconciling SAD 6.2 with SAD 7.4, stated rather than hidden.

    An authority that is not a configured site cannot be told from the head of
    a path: `hodd://models/core/X` and `hodd://eitri/adapters/x` are the same
    shape. So a mistyped site resolves locally, and is then simply not found.

    What matters is the failure mode, and it is the safe one: a mistyped
    authority can never resolve to a *different* forge's artefact. It resolves
    to a local path that does not exist.
    """
    drivers: dict[str, Any] = {
        "sindri": PosixStoreDriver(root=tmp_path, local_site="sindri", sites=frozenset({"sindri"}))
    }

    chosen = driver_for("hodd://eitri/adapters/x", drivers, local_site="sindri")
    assert chosen is drivers["sindri"]

    resolved = parse("hodd://eitri/adapters/x", local_site="sindri", sites=("sindri",))
    assert resolved.site_id == "sindri"
    assert resolved.path == "eitri/adapters/x"
    # Not found, rather than somebody else's corpus.
    assert not chosen.stat("hodd://eitri/adapters/x").exists


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_a_sealed_artefact_is_not_overwritten(tmp_path: Path, artefact: Path) -> None:
    store = PosixStoreDriver(root=tmp_path / "vault", local_site="sindri")
    store.put(URI, artefact)
    store.seal(URI)

    with pytest.raises(ImmutableArtefactError, match="immutable"):
        store.put(URI, artefact)


def test_sealing_is_idempotent(tmp_path: Path, artefact: Path) -> None:
    store = PosixStoreDriver(root=tmp_path / "vault", local_site="sindri")
    store.put(URI, artefact)
    store.seal(URI)
    store.seal(URI)
    assert store.is_sealed(URI)


def test_an_absent_artefact_cannot_be_sealed(tmp_path: Path) -> None:
    store = PosixStoreDriver(root=tmp_path / "vault", local_site="sindri")
    with pytest.raises(StoreError, match="does not exist"):
        store.seal(URI)


def test_deletion_reports_what_it_freed(tmp_path: Path, artefact: Path) -> None:
    store = PosixStoreDriver(root=tmp_path / "vault", local_site="sindri")
    store.put(URI, artefact)
    store.seal(URI)

    freed = store.delete(URI)
    assert freed == len("the text")
    assert not store.stat(URI).exists
    # Deleting what is not there is not an error; a retention action may be
    # retried after a partial failure.
    assert store.delete(URI) == 0


def test_stat_reports_a_hash_for_a_file_and_a_size_for_a_tree(
    tmp_path: Path, artefact: Path
) -> None:
    store = PosixStoreDriver(root=tmp_path / "vault", local_site="sindri")
    store.put(URI, artefact)

    tree = store.stat(URI)
    assert tree.exists
    assert tree.size == len("the text")

    single = store.stat(f"{URI}/corpus.txt")
    assert single.sha256 is not None
    assert len(single.sha256) == 64


def test_getting_an_absent_artefact_is_refused(tmp_path: Path) -> None:
    store = PosixStoreDriver(root=tmp_path / "vault", local_site="sindri")
    with pytest.raises(StoreError, match="does not exist"):
        store.get(URI, tmp_path / "out")


def test_an_object_store_records_its_seals() -> None:
    # Weaker than the POSIX driver, and it says so: this stops HODD, not a
    # bucket policy. Object locking is the real mechanism.
    store = ObjectStoreDriver(bucket="draupnir", client=object(), local_site="sindri")
    assert not store.is_sealed(URI)
    store.seal(URI)
    assert store.is_sealed(URI)
    store.unseal(URI)
    assert not store.is_sealed(URI)


def test_an_object_store_is_not_bounded_by_the_vault_reserve() -> None:
    # Returning zero would refuse every run for a limit that does not apply.
    store = ObjectStoreDriver(bucket="draupnir", client=object(), local_site="sindri")
    assert store.free_bytes() > 1 << 40


@pytest.mark.parametrize(
    ("count", "expected"),
    [(512, "512 B"), (2048, "2.0 KiB"), (5 * 1024**3, "5.0 GiB")],
)
def test_sizes_render_for_an_operator(count: int, expected: str) -> None:
    assert readable_size(count) == expected
