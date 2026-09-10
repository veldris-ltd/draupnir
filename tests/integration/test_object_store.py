"""MinIO, the artefact object store of SAD 7.2.

The container exists in the integration stage from the first build so that the
HODD repositories of Prompt 1 arrive into a working fixture rather than
bringing one with them.

The driver's own tests are here rather than in the unit suite because the
property RF-08 turns on is a property of the *bucket*: that a seal placed by
one process is seen by another, and that the second one's overwrite is refused.
A stub can be written to agree with itself. Only MinIO can be wrong about it.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def bucket(minio: dict[str, str]) -> str:
    """Create the artefact bucket and return its name."""
    from minio import Minio

    client = Minio(
        minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
        secure=False,
    )
    name = "draupnir"
    if not client.bucket_exists(name):
        client.make_bucket(name)
    return name


def test_an_artefact_round_trips(minio: dict[str, str], bucket: str) -> None:
    from minio import Minio

    client = Minio(
        minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
        secure=False,
    )

    payload = b"adapter weights would go here"
    client.put_object(bucket, "adapters/cim-gbr-v0.1/manifest", io.BytesIO(payload), len(payload))

    response = client.get_object(bucket, "adapters/cim-gbr-v0.1/manifest")
    try:
        assert response.read() == payload
    finally:
        response.close()
        response.release_conn()


def test_the_bucket_is_addressable_by_content_path(minio: dict[str, str], bucket: str) -> None:
    from minio import Minio

    client = Minio(
        minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
        secure=False,
    )
    payload = b"report"
    client.put_object(bucket, "reports/run-1/gates.json", io.BytesIO(payload), len(payload))

    names = {obj.object_name for obj in client.list_objects(bucket, "reports/", recursive=True)}
    assert "reports/run-1/gates.json" in names


# ---------------------------------------------------------------------------
# The driver, against the real thing. RF-08.
# ---------------------------------------------------------------------------


@pytest.fixture
def locked_bucket(minio: dict[str, str]) -> str:
    """A bucket with object locking, which is the only kind that can hold a seal.

    Object locking cannot be turned on after the fact -- it is settled when the
    bucket is made -- which is why `ObjectStoreDriver` refuses an unlockable
    bucket at construction rather than at the first seal. By the time a seal is
    attempted it is far too late to create the bucket differently.
    """
    from minio import Minio

    client = Minio(
        minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
        secure=False,
    )
    name = "draupnir-sealed"
    if not client.bucket_exists(name):
        client.make_bucket(name, object_lock=True)
    return name


def _driver(minio: dict[str, str], bucket: str) -> Any:
    """A fresh driver over that bucket.

    Fresh on purpose: two of these are two API processes, which is what SAD 5.1
    runs and what the in-process seal could not survive.
    """
    from minio import Minio

    from draupnir.hodd.stores import ObjectStoreDriver

    return ObjectStoreDriver(
        bucket=bucket,
        client=Minio(
            minio["endpoint"],
            access_key=minio["access_key"],
            secret_key=minio["secret_key"],
            secure=False,
        ),
        local_site="sindri",
    )


def test_an_artefact_put_by_the_driver_records_its_digest(
    minio: dict[str, str], locked_bucket: str, tmp_path: Path
) -> None:
    """Which is what makes staging idempotent.

    The worker re-stages after a failed transition and asks the store whether
    the bytes there are already its own. A store that could not answer would
    send every retry into the seal's overwrite refusal, and the run would defer
    for ever -- a livelock produced entirely by the fix (RF-08).
    """
    driver = _driver(minio, locked_bucket)
    source = tmp_path / "adapter.safetensors"
    source.write_bytes(b"adapter weights")
    uri = "hodd://sindri/adapters/digest-check"

    driver.put(uri, source)

    held = driver.stat(uri)
    assert held.exists
    assert held.sha256 == hashlib.sha256(b"adapter weights").hexdigest(), (
        "the store does not report the digest it was given, so a re-stage cannot "
        "recognise its own bytes"
    )


def test_two_drivers_over_one_bucket_agree_about_a_seal(
    minio: dict[str, str], locked_bucket: str, tmp_path: Path
) -> None:
    """RF-08's acceptance criterion, against MinIO rather than a stub.

    The seal used to be an in-process `set`. It did not survive a restart and
    was invisible to the second and third API processes SAD 5.1 specifies, so
    a seal placed by one was simply not there for another.
    """
    source = tmp_path / "merged.safetensors"
    source.write_bytes(b"merged weights")
    uri = "hodd://sindri/merged/two-drivers"

    first = _driver(minio, locked_bucket)
    first.put(uri, source)
    first.seal(uri)

    second = _driver(minio, locked_bucket)

    assert second.is_sealed(uri), (
        "a seal placed by one driver is invisible to another over the same bucket"
    )


def test_a_second_driver_cannot_overwrite_a_sealed_artefact(
    minio: dict[str, str], locked_bucket: str, tmp_path: Path
) -> None:
    """The refusal the seal exists for. T8, and AC-S8's re-hash at publication.

    A gate passes, an artefact is sealed, and something later replaces the
    bytes at that address. The publication re-hash would catch it -- after the
    fact. This is the control that stops it happening at all, and it has to
    hold across processes or it stops nothing.
    """
    from draupnir.hodd.stores import ImmutableArtefactError

    original = tmp_path / "merged.safetensors"
    original.write_bytes(b"the gated bytes")
    substitute = tmp_path / "substitute.safetensors"
    substitute.write_bytes(b"something else entirely")
    uri = "hodd://sindri/merged/no-overwrite"

    first = _driver(minio, locked_bucket)
    first.put(uri, original)
    first.seal(uri)

    with pytest.raises(ImmutableArtefactError):
        _driver(minio, locked_bucket).put(uri, substitute)

    assert (
        _driver(minio, locked_bucket).stat(uri).sha256
        == hashlib.sha256(b"the gated bytes").hexdigest()
    ), "the sealed artefact was replaced despite the refusal"


def test_a_bucket_with_no_object_lock_is_refused_at_construction(
    minio: dict[str, str], bucket: str
) -> None:
    """Rather than degrading to an in-memory set, which is what it did.

    `bucket` is the ordinary fixture above, made without object locking. A
    driver that accepted it would report every artefact sealed and protect none
    of them, and would report it convincingly.
    """
    from draupnir.hodd.stores import StoreError

    with pytest.raises(StoreError, match="object lock"):
        _driver(minio, bucket)
