"""UUIDv7 identifiers, SAD 11E.2."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from draupnir.core.domain.identifiers import id_at, new_id


def test_new_id_is_version_7() -> None:
    assert new_id().version == 7


def test_new_ids_sort_by_creation_time() -> None:
    ids = [new_id() for _ in range(50)]
    assert ids == sorted(ids)


def test_id_at_is_deterministic(moment: datetime) -> None:
    entropy = bytes(range(10))
    assert id_at(moment, entropy) == id_at(moment, entropy)


def test_id_at_sorts_by_its_timestamp(moment: datetime) -> None:
    entropy = bytes(range(10))
    earlier = id_at(moment, entropy)
    later = id_at(moment + timedelta(seconds=1), entropy)
    assert earlier < later


def test_id_at_sets_version_and_variant(moment: datetime) -> None:
    identifier = id_at(moment, bytes(range(10)))
    assert identifier.version == 7
    assert identifier.variant == "specified in RFC 4122"


def test_id_at_refuses_a_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="explicit offset"):
        id_at(datetime(2026, 3, 2, 9, 0), bytes(range(10)))  # noqa: DTZ001


def test_id_at_refuses_thin_entropy(moment: datetime) -> None:
    with pytest.raises(ValueError, match="entropy"):
        id_at(moment, b"short")
