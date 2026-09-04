"""Vault reconciliation: SAD 11.2 row 4's "restore NFS, run reconciliation".

The vault is a real directory in these tests and the unmount is a real removal,
because the two states that matter -- the root gone, and a root somebody
created where the vault should be -- are states of the filesystem and a fake
would let either of them pass.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from draupnir.hodd import reconcile
from draupnir.hodd.ingest import MANIFEST_NAME, Ingestor
from draupnir.hodd.reconcile import Expected, Outcome, VaultNotInitialisedError
from draupnir.hodd.register import LicenceRegister
from draupnir.hodd.stores import (
    PosixStoreDriver,
    VaultUnavailableError,
    artefact_uri,
)

SITE = "sindri"
RUN = "019cdd69-6d80-741a-95b7-fe36b74c22ef"


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def store(vault: Path) -> PosixStoreDriver:
    driver = PosixStoreDriver(root=vault, local_site=SITE)
    reconcile.initialise(driver)
    return driver


@pytest.fixture
def ingestor(store: PosixStoreDriver) -> Ingestor:
    return Ingestor(store, LicenceRegister())


def scratch_file(tmp_path: Path, data: bytes, name: str = "adapter.safetensors") -> Path:
    target = tmp_path / "scratch" / RUN / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def expectation(sha256: str, scratch: Path | None) -> Expected:
    return Expected(
        uri=artefact_uri(SITE, "adapter", RUN),
        sha256=sha256,
        kind="adapter",
        run_id=RUN,
        scratch=scratch,
    )


# ---------------------------------------------------------------------------
# Is this the vault?
# ---------------------------------------------------------------------------


def test_an_absent_root_and_an_empty_one_are_different_refusals(tmp_path: Path) -> None:
    """The second is the state an operator creates by `mkdir` on the mount point.

    It is the worse of the two -- the refusal is gone, so runs plan and write to
    local disk -- so it has to be told apart from the outage it looks like.
    """
    root = tmp_path / "vault"
    driver = PosixStoreDriver(root=root, local_site=SITE)

    with pytest.raises(VaultUnavailableError):
        reconcile.require_vault(driver)

    root.mkdir()
    with pytest.raises(VaultNotInitialisedError) as raised:
        reconcile.require_vault(driver)
    assert reconcile.VAULT_MARKER in str(raised.value)
    assert "Do not create the mount point by hand" in str(raised.value)

    reconcile.initialise(driver)
    assert reconcile.require_vault(driver)["site"] == SITE
    assert reconcile.mounted(driver)


def test_initialising_is_idempotent_and_refuses_an_absent_root(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    driver = PosixStoreDriver(root=root, local_site=SITE)
    with pytest.raises(VaultUnavailableError):
        reconcile.initialise(driver)

    root.mkdir()
    first = reconcile.initialise(driver)
    written = first.read_text(encoding="utf-8")
    assert reconcile.initialise(driver).read_text(encoding="utf-8") == written
    assert json.loads(written)["site"] == SITE


def test_an_unmounted_vault_refuses_to_report_its_capacity(tmp_path: Path) -> None:
    """`total_bytes` used to `mkdir` the root, which is the defect itself.

    Creating the vault in order to measure it recreates a dropped mount point
    on local disk and then reports that disk's capacity as the vault's, and a
    quota check against that number passes every run (AC-S10).
    """
    root = tmp_path / "vault"
    driver = PosixStoreDriver(root=root, local_site=SITE)
    with pytest.raises(VaultUnavailableError):
        driver.total_bytes()
    assert not root.exists(), "measuring the vault created it"


def test_reconciliation_refuses_a_vault_that_is_not_one(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    driver = PosixStoreDriver(root=root, local_site=SITE)
    with pytest.raises(VaultNotInitialisedError):
        reconcile.reconcile(driver, Ingestor(driver, LicenceRegister()), [])


# ---------------------------------------------------------------------------
# Staging what a running job wrote
# ---------------------------------------------------------------------------


def test_a_dry_run_says_what_it_would_stage_and_stages_nothing(
    store: PosixStoreDriver, ingestor: Ingestor, tmp_path: Path
) -> None:
    data = b"weights" * 100
    scratch = scratch_file(tmp_path, data)
    expected = expectation(digest_of(data), scratch)

    report = reconcile.reconcile(store, ingestor, [expected])

    assert [item.outcome for item in report.findings] == [Outcome.STAGEABLE]
    assert not report.settled, "a stageable artefact is not a settled vault"
    assert not Path(store.resolve(expected.uri)).exists()
    assert scratch.is_file(), "a dry run moved the scratch file"


def test_applying_stages_the_artefact_and_seals_it(
    store: PosixStoreDriver, ingestor: Ingestor, tmp_path: Path
) -> None:
    """Row 4: "running jobs writing to local scratch continue and stage on recovery"."""
    data = b"weights" * 100
    expected = expectation(digest_of(data), scratch_file(tmp_path, data))

    report = reconcile.reconcile(store, ingestor, [expected], apply=True)

    assert [item.outcome for item in report.findings] == [Outcome.STAGED]
    assert report.settled

    held = Path(store.resolve(expected.uri))
    assert (held / "adapter.safetensors").read_bytes() == data
    # Staged through the ingestor, so it is indistinguishable from a normal
    # ingest: it has a manifest and it verifies.
    assert (held / MANIFEST_NAME).is_file()
    assert ingestor.verify(expected.uri) == ()

    # And a second reconciliation finds it present rather than staging it twice.
    again = reconcile.reconcile(store, ingestor, [expected], apply=True)
    assert [item.outcome for item in again.findings] == [Outcome.PRESENT]


def test_scratch_that_hashes_to_something_else_is_never_staged(
    store: PosixStoreDriver, ingestor: Ingestor, tmp_path: Path
) -> None:
    """AC-S8. Registered by the digest of the bytes that arrived, never by claim."""
    scratch = scratch_file(tmp_path, b"not what the chain recorded")
    expected = expectation(digest_of(b"what the chain recorded"), scratch)

    report = reconcile.reconcile(store, ingestor, [expected], apply=True)

    finding = report.findings[0]
    assert finding.outcome is Outcome.UNSTAGEABLE
    assert finding.attention
    assert "AC-S8" in finding.detail
    assert not Path(store.resolve(expected.uri)).exists(), "the wrong bytes were staged"
    assert scratch.is_file(), "the scratch file was not left for an operator"


def test_an_artefact_in_neither_place_is_missing_rather_than_an_error(
    store: PosixStoreDriver, ingestor: Ingestor
) -> None:
    report = reconcile.reconcile(
        store, ingestor, [expectation(digest_of(b"gone"), None)], apply=True
    )
    finding = report.findings[0]
    assert finding.outcome is Outcome.MISSING
    assert "only a rerun produces the bytes again" in finding.detail


def test_a_vault_artefact_that_does_not_match_the_chain_is_reported_not_overwritten(
    store: PosixStoreDriver, ingestor: Ingestor, tmp_path: Path
) -> None:
    held = b"what the vault holds"
    expected = expectation(digest_of(held), scratch_file(tmp_path, held))
    reconcile.reconcile(store, ingestor, [expected], apply=True)

    # The chain now says something else about the same address.
    other = expectation(digest_of(b"what the chain says"), expected.scratch)
    report = reconcile.reconcile(store, ingestor, [other], apply=True)

    finding = report.findings[0]
    assert finding.outcome is Outcome.DIVERGED
    assert finding.found_sha256 == digest_of(held)
    assert "Nothing has been overwritten" in finding.detail
    assert (Path(store.resolve(other.uri)) / "adapter.safetensors").read_bytes() == held


def test_a_scratch_directory_is_refused_rather_than_guessed_at(
    store: PosixStoreDriver, ingestor: Ingestor, tmp_path: Path
) -> None:
    """Two digest rules compared against each other is worse than no comparison."""
    tree = tmp_path / "scratch" / RUN
    tree.mkdir(parents=True)
    (tree / "part").write_bytes(b"x")
    report = reconcile.reconcile(store, ingestor, [expectation("a" * 64, tree)], apply=True)
    assert report.findings[0].outcome is Outcome.UNSTAGEABLE
    assert "is a directory" in report.findings[0].detail


# ---------------------------------------------------------------------------
# What a crash during the outage left behind
# ---------------------------------------------------------------------------


def test_abandoned_staging_is_reported_by_a_dry_run_and_removed_by_an_applied_one(
    store: PosixStoreDriver, ingestor: Ingestor, vault: Path
) -> None:
    abandoned = vault / SITE / ".staging" / "deadbeef"
    abandoned.mkdir(parents=True)
    (abandoned / "half").write_bytes(b"partial")

    dry = reconcile.reconcile(store, ingestor, [])
    assert len(dry.abandoned) == 1
    assert abandoned.exists(), "a dry run removed something"

    applied = reconcile.reconcile(store, ingestor, [], apply=True)
    assert len(applied.abandoned) == 1
    assert not abandoned.exists()


def test_an_orphan_is_reported_and_never_removed(
    store: PosixStoreDriver, ingestor: Ingestor, tmp_path: Path
) -> None:
    """A sealed artefact nothing names is untidy, not wrong. Reporting is the duty."""
    data = b"orphaned"
    expected = expectation(digest_of(data), scratch_file(tmp_path, data))
    reconcile.reconcile(store, ingestor, [expected], apply=True)

    # Reconciled again while the chain accounts for nothing at all.
    report = reconcile.reconcile(store, ingestor, [], known_uris=[])

    assert expected.uri in report.orphans
    assert Path(store.resolve(expected.uri)).exists()
    # An orphan alone leaves the vault settled: nothing the chain expects is
    # unaccounted for.
    assert report.settled


def test_known_uris_account_for_what_the_chain_expects(
    store: PosixStoreDriver, ingestor: Ingestor, tmp_path: Path
) -> None:
    data = b"accounted for"
    expected = expectation(digest_of(data), scratch_file(tmp_path, data))
    reconcile.reconcile(store, ingestor, [expected], apply=True)

    report = reconcile.reconcile(
        store, ingestor, [expected], known_uris=reconcile.known([expected])
    )
    assert report.orphans == ()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_the_report_says_what_happened_in_lines_an_operator_reads(
    store: PosixStoreDriver, ingestor: Ingestor, tmp_path: Path
) -> None:
    data = b"weights"
    expected = expectation(digest_of(data), scratch_file(tmp_path, data))
    report = reconcile.reconcile(store, ingestor, [expected])

    lines = reconcile.describe(report)
    assert any("dry run" in line for line in lines)
    assert any(expected.uri in line for line in lines)
    payload = report.as_payload()
    assert payload["settled"] is False
    assert payload["findings"][0]["expectedSha256"] == expected.sha256


def test_the_address_of_a_run_artefact_is_derived_in_one_place() -> None:
    assert artefact_uri(SITE, "adapter", RUN) == f"hodd://{SITE}/adapters/{RUN}"
    assert artefact_uri(SITE, "quantised", RUN, "nvfp4") == (f"hodd://{SITE}/quantised/{RUN}/nvfp4")
    assert artefact_uri(SITE, "merged", RUN) == f"hodd://{SITE}/merged/{RUN}"
