"""MinIO, the artefact object store of SAD 7.2.

The container exists in the integration stage from the first build so that the
HODD repositories of Prompt 1 arrive into a working fixture rather than
bringing one with them.
"""

from __future__ import annotations

import io

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
