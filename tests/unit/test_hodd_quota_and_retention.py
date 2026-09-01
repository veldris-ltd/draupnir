"""AC-S10, AC-F19 and AC-F20: capacity refused at planning, deletion approved.

AC-S10: "A run whose projected output exceeds the vault free space is refused
at planning rather than failing partway."

AC-F19: "A retention action deletes a raw corpus after 24 months, retains the
curated manifests and licence entries, and the lineage for every derived
release remains complete afterwards."

AC-F20: "A retention action that would break a lineage chain is refused with
the affected release named."
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from draupnir.hodd import quota, retention
from draupnir.hodd.quota import Estimate, QuotaExceededError
from draupnir.hodd.retention import (
    RETENTION_MONTHS,
    LineageBreakError,
    LineageEdge,
    LineageIndex,
    NotDueError,
    RetentionPolicy,
    UnapprovedError,
)
from draupnir.hodd.stores import PosixStoreDriver
from draupnir.interfaces.testing import sample_spec

GIB = 1024**3
RELEASED = datetime(2026, 4, 11, tzinfo=UTC)


@dataclass
class Vault:
    """A store of a known size, so the arithmetic is the thing under test."""

    free: int
    total: int

    def free_bytes(self) -> int:
        return self.free

    def total_bytes(self) -> int:
        return self.total


# ---------------------------------------------------------------------------
# AC-S10
# ---------------------------------------------------------------------------


def test_a_run_that_fits_is_admitted() -> None:
    projection = quota.check(sample_spec(), Vault(free=200 * GIB, total=1000 * GIB))
    assert projection.fits
    assert projection.shortfall == 0


def test_a_run_that_would_fill_the_vault_is_refused_at_planning() -> None:
    """AC-S10."""
    with pytest.raises(QuotaExceededError) as raised:
        quota.check(sample_spec(), Vault(free=4 * GIB, total=1000 * GIB))

    estimate = raised.value.estimate
    assert not estimate.fits
    assert estimate.shortfall > 0
    # The refusal carries the arithmetic. One an operator cannot check is one
    # they will work around.
    assert "projected" in str(raised.value)
    assert "reserved" in str(raised.value)
    assert "short" in str(raised.value)


def test_the_reserve_is_kept_free_even_when_the_run_would_technically_fit() -> None:
    # 30 GiB free of a 100 GiB vault, and a run projected at 25 GiB. It fits
    # the free space and not the reserve, and a vault at 100 per cent fails
    # writes already in flight.
    spec = sample_spec()
    projected, _ = quota.project(spec)

    vault = Vault(free=projected + (5 * GIB), total=100 * GIB)
    assert quota.estimate(spec, vault, reserve_fraction=0.0).fits
    with pytest.raises(QuotaExceededError):
        quota.check(spec, vault, reserve_fraction=0.10)


def test_a_dense_run_projects_far_more_than_an_adapter_run() -> None:
    adapter, _ = quota.project(sample_spec())
    dense, _ = quota.project(
        sample_spec(train={"driver": "hamarr.llamafactory/v1", "method": "full"})
    )
    assert dense > adapter * 10


def test_concurrency_multiplies_the_projection() -> None:
    one, _ = quota.project(
        sample_spec(
            placement={"driver": "motsognir.slurm/v1", "partition": "adapters", "maxConcurrent": 1}
        )
    )
    three, _ = quota.project(
        sample_spec(
            placement={"driver": "motsognir.slurm/v1", "partition": "adapters", "maxConcurrent": 3}
        )
    )
    # SAD 6.2 runs three adapter elements at once, one per appliance.
    assert three == one * 3


def test_the_estimate_shows_its_working() -> None:
    explained = quota.estimate(sample_spec(), Vault(free=200 * GIB, total=1000 * GIB)).explain()
    for line in ("method", "checkpoint", "retained", "concurrent", "overhead", "verdict"):
        assert line in explained


def test_an_impossible_reserve_fraction_is_refused() -> None:
    for fraction in (-0.1, 1.0, 2.0):
        with pytest.raises(ValueError, match="proportion"):
            quota.estimate(sample_spec(), Vault(free=GIB, total=GIB), reserve_fraction=fraction)


def test_usable_space_never_goes_negative() -> None:
    estimate = Estimate(
        projected_bytes=GIB, free_bytes=1, total_bytes=1000 * GIB, reserve_fraction=0.10
    )
    assert estimate.usable_bytes == 0
    assert not estimate.fits


def test_a_real_vault_reports_its_capacity(tmp_path: Path) -> None:
    store = PosixStoreDriver(root=tmp_path / "vault", local_site="sindri")
    assert store.total_bytes() > 0
    assert store.free_bytes() > 0
    assert store.free_bytes() <= store.total_bytes()


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------

CORPUS = "hodd://sindri/corpora/GBR/raw"
CURATED = "hodd://sindri/corpora/GBR/curated"
ADAPTER = "hodd://sindri/adapters/cim-gbr-v0.1"
QUANTISED = "hodd://sindri/models/cim-gbr-v0.1/nvfp4"


@pytest.fixture
def lineage() -> LineageIndex:
    """Raw -> curated -> adapter -> quantised, with the last one released."""
    return LineageIndex(
        edges=(
            LineageEdge(CURATED, (CORPUS,)),
            LineageEdge(ADAPTER, (CURATED,)),
            LineageEdge(QUANTISED, (ADAPTER,)),
        ),
        releases={QUANTISED: "release-cim-gbr-v0.1"},
    )


def test_lineage_resolves_transitively(lineage: LineageIndex) -> None:
    assert lineage.ancestors(QUANTISED) == {ADAPTER, CURATED, CORPUS}
    assert lineage.ancestors(CORPUS) == frozenset()


def test_every_artefact_in_the_chain_is_load_bearing(lineage: LineageIndex) -> None:
    for uri in (CORPUS, CURATED, ADAPTER, QUANTISED):
        assert lineage.is_load_bearing(uri)
        assert lineage.releases_depending_on(uri) == ("release-cim-gbr-v0.1",)


def test_an_unrelated_artefact_is_not_load_bearing(lineage: LineageIndex) -> None:
    assert not lineage.is_load_bearing("hodd://sindri/adapters/experiment")


# ---------------------------------------------------------------------------
# AC-F19 and AC-F20
# ---------------------------------------------------------------------------


class FakeStore:
    """Records what was deleted."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, uri: str) -> int:
        self.deleted.append(uri)
        return 1024


def plan_for(
    uri: str, *, approved_by: str | None = "approver@veldris.internal"
) -> retention.RetentionAction:
    return retention.plan(
        action_id=uuid4(),
        subject_id=uuid4(),
        artefact_uri=uri,
        last_release_at=RELEASED,
        approved_by=approved_by,
    )


def test_retention_falls_due_24_months_after_the_last_release() -> None:
    action = plan_for(CORPUS)
    assert action.due_at == retention.due_at(RELEASED)
    assert (action.due_at - RELEASED).days == RETENTION_MONTHS * 30


def test_a_raw_corpus_is_deleted_after_24_months_and_the_manifests_survive(
    lineage: LineageIndex,
) -> None:
    """AC-F19."""
    store = FakeStore()
    action = plan_for(CORPUS)
    after = action.due_at + timedelta(days=1)

    executed = retention.execute(action, lineage, store, now=after)

    assert store.deleted == [CORPUS]
    assert executed.executed_at == after
    assert executed.manifests_retained

    # What survives, named rather than implied. SAD 7.3 lists all three.
    assert retention.survivors(executed) == (
        "curated manifests",
        "per-source SHA-256 hashes",
        "licence register entries",
    )
    # And the ledger payload says so.
    assert executed.as_payload()["manifestsRetained"] is True
    assert executed.as_payload()["approvedBy"] == "approver@veldris.internal"


def test_deletion_is_refused_before_the_retention_period_elapses(
    lineage: LineageIndex,
) -> None:
    action = plan_for(CORPUS)
    with pytest.raises(NotDueError) as raised:
        retention.execute(action, lineage, FakeStore(), now=RELEASED + timedelta(days=30))
    assert "not due" in str(raised.value)
    assert str(RETENTION_MONTHS) in str(raised.value)


def test_deletion_without_an_approver_is_refused(lineage: LineageIndex) -> None:
    """Deletion is an approved, ledgered action, never a cron job (SAD 7.3)."""
    action = plan_for(CORPUS, approved_by=None)
    with pytest.raises(UnapprovedError) as raised:
        retention.execute(action, lineage, FakeStore(), now=action.due_at + timedelta(days=1))
    assert "cron job" in str(raised.value)


def test_a_retention_action_that_would_break_lineage_names_the_release(
    lineage: LineageIndex,
) -> None:
    """AC-F20."""
    store = FakeStore()
    action = plan_for(CORPUS)

    with pytest.raises(LineageBreakError) as raised:
        retention.execute(
            action,
            lineage,
            store,
            now=action.due_at + timedelta(days=1),
            manifest_survives=False,
        )

    assert raised.value.releases == ("release-cim-gbr-v0.1",)
    assert "release-cim-gbr-v0.1" in str(raised.value)
    assert store.deleted == [], "nothing was deleted"


def test_deleting_an_intermediate_that_a_release_depends_on_is_refused(
    lineage: LineageIndex,
) -> None:
    """AC-F20 for a checkpoint, where no 24 month rule makes it permissible."""
    action = replace(plan_for(ADAPTER), policy=RetentionPolicy.INTERMEDIATE)
    store = FakeStore()

    with pytest.raises(LineageBreakError) as raised:
        retention.execute(action, lineage, store, now=action.due_at + timedelta(days=1))

    assert raised.value.releases == ("release-cim-gbr-v0.1",)
    assert store.deleted == []


def test_deleting_an_intermediate_nothing_depends_on_is_permitted() -> None:
    unreferenced = "hodd://sindri/adapters/experiment"
    action = replace(plan_for(unreferenced), policy=RetentionPolicy.INTERMEDIATE)
    store = FakeStore()

    retention.execute(action, LineageIndex(), store, now=action.due_at + timedelta(days=1))
    assert store.deleted == [unreferenced]


def test_lineage_remains_complete_after_the_corpus_is_deleted(
    lineage: LineageIndex,
) -> None:
    """AC-F19, the part that matters at due diligence.

    The bytes go; the chain does not. A released model can afterwards be
    verified but not re-derived, which is exactly what SAD 7.3 says.
    """
    action = plan_for(CORPUS)
    retention.execute(action, lineage, FakeStore(), now=action.due_at + timedelta(days=1))

    # The index is unchanged: deletion removes data, not derivation records.
    assert lineage.ancestors(QUANTISED) == {ADAPTER, CURATED, CORPUS}
    assert lineage.releases_depending_on(CORPUS) == ("release-cim-gbr-v0.1",)


def test_a_naive_timestamp_is_refused() -> None:
    with pytest.raises(retention.RetentionError, match="explicit offset"):
        retention.due_at(datetime(2026, 4, 11))  # noqa: DTZ001 -- that is the point
