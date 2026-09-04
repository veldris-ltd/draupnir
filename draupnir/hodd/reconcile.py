"""Reconciliation: putting the vault back together after it went away.

SAD 11.2, row 4: an unavailable HODD vault means "new runs refuse to plan.
Running jobs writing to local scratch continue and stage on recovery", and the
recovery column reads "restore NFS, run reconciliation". Restoring the mount is
an operator's; this is the reconciliation.

**What it is for.** An outage leaves the vault behind in three ways at once. A
job that kept running wrote its output to local scratch and nothing staged it.
An ingest that was in flight when the mount dropped left a staged tree nothing
resolves to. And an ingest that got as far as the rename but not as far as the
register left a sealed artefact no source record names. Each is recoverable and
each is invisible until somebody looks, so this looks.

**The rule that matters.** An artefact is staged only when the bytes in scratch
hash to what the chain recorded for it. AC-S8: an artefact is registered by the
digest of the bytes that arrived, never by what the job said it wrote. Scratch
that hashes to something else is reported and left alone -- it is not the
artefact the chain describes, and staging it would put bytes in the vault under
an address that promises different ones.

**What it will not do.** It does not delete a sealed artefact, ever, including
one nothing points at: an artefact HODD sealed is precisely the sort of thing
that should not be removed by a process that found it surprising. It does not
overwrite. It does not decide that a run failed. The one thing it removes is an
abandoned staging tree, which is safe by construction because no `hodd://` URI
resolves into one.

**Look before touching.** `reconcile` reports by default and stages only when
asked. The moment after an outage is the moment to read what happened before
changing it, and a command that acted first would take that away.

**The marker.** A vault carries a `.hodd-vault` file naming the site. An NFS
mount that has dropped leaves the root absent; an operator who then creates the
mount point by hand to "unblock" planning leaves a root that is present and
empty, which is worse -- every artefact reads as missing and every write lands
on the control plane's local disk. The marker tells those two apart, so a vault
that is there but is not the vault refuses in its own words.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from draupnir.hodd.ingest import MANIFEST_NAME, IngestError, Ingestor
from draupnir.hodd.manifest import hash_file
from draupnir.hodd.stores import PosixStoreDriver, StoreError, VaultUnavailableError

#: Written at the root of a vault, naming the site it holds. Its presence is
#: what distinguishes a mounted vault from a directory of the same name.
VAULT_MARKER = ".hodd-vault"

#: The subject a reconciliation records itself against.
VAULT_SUBJECT = "vault"
#: The transition string a completed reconciliation carries.
RECONCILED = "vault.reconciled"
#: The transition string one staged artefact carries.
STAGED = "vault.staged"


class VaultNotInitialisedError(StoreError):
    """Raised when the vault root exists but is not a vault.

    The state an operator creates by running `mkdir` on the mount point to get
    planning working again. It is worse than the outage it appears to fix: the
    refusal is gone, so runs plan, and everything they write goes to the
    control plane's local disk under a path that will be shadowed the moment
    the real mount returns.
    """

    def __init__(self, root: Path) -> None:
        """Name the directory that is not the vault."""
        self.root = root
        super().__init__(
            f"{root} exists but holds no {VAULT_MARKER}, so it is a directory where the "
            "vault should be rather than the vault. Do not create the mount point by "
            "hand: mount the real one, or run `vault_admin.py initialise` if this is a "
            "new vault. Planning stays refused until one of those is true."
        )


class Outcome(StrEnum):
    """What reconciliation found, for one artefact the chain expects."""

    #: In the vault, and it hashes to what the chain recorded.
    PRESENT = "PRESENT"
    #: Was in scratch, hashed correctly, and is now in the vault.
    STAGED = "STAGED"
    #: Would be staged. Reported by a dry run, which is the default.
    STAGEABLE = "STAGEABLE"
    #: In the vault, and it does not hash to what the chain recorded.
    DIVERGED = "DIVERGED"
    #: In scratch, and it does not hash to what the chain recorded. Not staged.
    UNSTAGEABLE = "UNSTAGEABLE"
    #: Not in the vault and not in scratch. Only a rerun produces it.
    MISSING = "MISSING"


#: Outcomes an operator has to do something about.
NEEDS_ATTENTION: frozenset[Outcome] = frozenset(
    {Outcome.DIVERGED, Outcome.UNSTAGEABLE, Outcome.MISSING}
)


@dataclass(frozen=True, slots=True)
class Expected:
    """One artefact the chain says the vault should hold.

    Built by the caller from the ledger, not read from it here: HODD owns
    ingest, hashing and layout, and a store module that read the chain would
    be a second place the lifecycle is understood.
    """

    uri: str
    #: What the chain recorded as this artefact's digest.
    sha256: str
    #: One of the artefact kinds of SAD 7.1, for the manifest.
    kind: str
    #: The run whose work produced it.
    run_id: str
    #: Where a job left it on local scratch, if it is there.
    scratch: Path | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    """What reconciliation concluded about one expectation."""

    expected: Expected
    outcome: Outcome
    detail: str
    #: The digest of what was actually found, where anything was found.
    found_sha256: str = ""

    @property
    def attention(self) -> bool:
        """Whether a person has to do something about this one."""
        return self.outcome in NEEDS_ATTENTION

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, for the ledger and for the command's output."""
        return {
            "uri": self.expected.uri,
            "runId": self.expected.run_id,
            "kind": self.expected.kind,
            "outcome": str(self.outcome),
            "detail": self.detail,
            "expectedSha256": self.expected.sha256,
            "foundSha256": self.found_sha256 or None,
        }


@dataclass(frozen=True, slots=True)
class Report:
    """Everything one reconciliation found."""

    site_id: str
    at: datetime
    applied: bool
    findings: tuple[Finding, ...] = ()
    #: Staging trees removed, or that would be removed by a dry run.
    abandoned: tuple[str, ...] = ()
    #: Sealed artefacts no source record names. Reported, never removed.
    orphans: tuple[str, ...] = ()

    def of(self, *outcomes: Outcome) -> tuple[Finding, ...]:
        """Every finding with one of these outcomes."""
        wanted = set(outcomes)
        return tuple(item for item in self.findings if item.outcome in wanted)

    @property
    def attention(self) -> tuple[Finding, ...]:
        """Findings a person has to act on."""
        return tuple(item for item in self.findings if item.attention)

    @property
    def settled(self) -> bool:
        """Whether the vault is consistent with what the chain expects.

        Orphans do not unsettle it. An orphan is a sealed artefact nothing
        names, which is untidy rather than wrong, and reporting it is the whole
        of what is owed.
        """
        return not self.attention and not self.of(Outcome.STAGEABLE)

    def summary(self) -> str:
        """One line, for a log and for the top of the command's output."""
        counts = {outcome: len(self.of(outcome)) for outcome in Outcome}
        parts = [f"{count} {str(outcome).lower()}" for outcome, count in counts.items() if count]
        return ", ".join(parts) or "nothing expected"

    def as_payload(self) -> dict[str, Any]:
        """The ledger payload of a reconciliation."""
        return {
            "site": self.site_id,
            "at": self.at.isoformat(),
            "applied": self.applied,
            "summary": self.summary(),
            "settled": self.settled,
            "findings": [item.as_payload() for item in self.findings],
            "abandonedStaging": list(self.abandoned),
            "orphans": list(self.orphans),
        }


# ---------------------------------------------------------------------------
# Is this the vault?
# ---------------------------------------------------------------------------


def initialise(store: PosixStoreDriver, *, now: datetime | None = None) -> Path:
    """Write the marker that makes a directory the vault. Idempotent.

    Run once, on the real mount, by whoever commissioned it. Never by anything
    automatic: a process that wrote the marker when it found one missing would
    turn the state this exists to detect into the state it certifies.
    """
    if not store.root.is_dir():
        raise VaultUnavailableError(store.root)
    marker = store.root / VAULT_MARKER
    if not marker.is_file():
        marker.write_text(
            json.dumps(
                {
                    "site": store.local_site,
                    "initialisedAt": (now or _now()).isoformat(),
                    "driver": store.name,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return marker


def marker_of(store: PosixStoreDriver) -> dict[str, Any] | None:
    """What the vault's marker says, or None if there is not one."""
    marker = store.root / VAULT_MARKER
    if not store.root.is_dir() or not marker.is_file():
        return None
    try:
        loaded = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def mounted(store: PosixStoreDriver) -> bool:
    """Whether the vault is present and is the vault."""
    return marker_of(store) is not None


def require_vault(store: PosixStoreDriver) -> dict[str, Any]:
    """Return the marker, or raise saying which of the two failures this is.

    Two exceptions rather than one, because the operator's next action differs:
    an absent root is a mount to restore, and a present root with no marker is
    a directory somebody made that has to be removed before the real mount can
    go back over it.
    """
    if not store.root.is_dir():
        raise VaultUnavailableError(store.root)
    found = marker_of(store)
    if found is None:
        raise VaultNotInitialisedError(store.root)
    return found


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def reconcile(
    store: PosixStoreDriver,
    ingestor: Ingestor,
    expected: Iterable[Expected],
    *,
    known_uris: Iterable[str] = (),
    apply: bool = False,
    now: datetime | None = None,
) -> Report:
    """Compare the vault against what the chain expects, and optionally stage.

    `apply` is false by default. A dry run is the same walk and the same
    hashing with the writes withheld, so what it reports is what an applied run
    would do rather than a guess at it.
    """
    require_vault(store)
    at = now or _now()

    abandoned = tuple(str(path) for path in ingestor.abandoned_staging())
    if apply:
        ingestor.discard_abandoned()

    findings = tuple(_examine(store, ingestor, item, apply=apply) for item in expected)
    orphans = ingestor.orphans(known_uris)

    return Report(
        site_id=store.local_site,
        at=at,
        applied=apply,
        findings=findings,
        abandoned=abandoned,
        orphans=orphans,
    )


def _examine(
    store: PosixStoreDriver, ingestor: Ingestor, item: Expected, *, apply: bool
) -> Finding:
    """Decide what one expected artefact needs, and do it if asked."""
    held = _held_digest(store, item)
    if held is not None:
        if held == item.sha256:
            return Finding(item, Outcome.PRESENT, "in the vault and intact", held)
        return Finding(
            item,
            Outcome.DIVERGED,
            (
                f"the vault holds {item.uri} but it hashes to {held[:12]} and the chain "
                f"records {item.sha256[:12]}. Nothing has been overwritten: an artefact "
                "is immutable once registered, so this is a question about which of the "
                "two is authentic, not a staging job."
            ),
            held,
        )

    if item.scratch is None or not item.scratch.exists():
        return Finding(
            item,
            Outcome.MISSING,
            (
                f"not in the vault and not in scratch. Nothing was lost that the chain "
                f"does not still describe -- the digest {item.sha256[:12]} is recorded -- "
                "but only a rerun produces the bytes again."
            ),
        )

    if item.scratch.is_dir():
        # A tree's digest depends on how the tree was walked, and comparing one
        # computed here against one computed by whoever recorded it would be
        # comparing two rules. Row 4 is about what a running job wrote, and
        # what a job writes is a file.
        return Finding(
            item,
            Outcome.UNSTAGEABLE,
            (
                f"{item.scratch} is a directory. Reconciliation stages the files a "
                "running job wrote to scratch; a tree is ingested by the step that "
                "curated it, which records the digest rule it used."
            ),
        )

    arrived = hash_file(item.scratch)
    if arrived != item.sha256:
        return Finding(
            item,
            Outcome.UNSTAGEABLE,
            (
                f"{item.scratch} hashes to {arrived[:12]} and the chain records "
                f"{item.sha256[:12]}. It is not the artefact this address promises and "
                "it has not been staged (AC-S8). Left where it is, for an operator."
            ),
            arrived,
        )

    if not apply:
        return Finding(
            item,
            Outcome.STAGEABLE,
            f"in scratch at {item.scratch}, hashes correctly, would be staged",
            arrived,
        )

    try:
        _stage(ingestor, item)
    except (IngestError, StoreError) as refusal:
        return Finding(item, Outcome.UNSTAGEABLE, f"the ingest refused it: {refusal}", arrived)

    return Finding(item, Outcome.STAGED, f"staged from {item.scratch}", arrived)


def _stage(ingestor: Ingestor, item: Expected) -> None:
    """Ingest one scratch file at its address.

    Through the ingestor rather than by copying, so that a staged artefact is
    indistinguishable from one ingested normally: it gets a manifest, it is
    sealed, and the digest recorded is of the bytes that arrived. A
    reconciliation that put files in the vault by hand would produce artefacts
    that verification could not check.
    """
    assert item.scratch is not None  # noqa: S101 -- `_examine` established it
    tree = Path(tempfile.mkdtemp(prefix="hodd-reconcile-"))
    try:
        shutil.copy2(item.scratch, tree / item.scratch.name)
        ingestor.ingest(tree, item.uri, kind=item.kind, facts={"staged": "reconciliation"})
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def _held_digest(store: PosixStoreDriver, item: Expected) -> str | None:
    """The digest of what the vault holds at this address, or None if nothing.

    An ingested artefact is a directory holding one file and a manifest, so the
    digest compared is that file's -- the same number the chain recorded when
    the job wrote it. Reading it back off the disk rather than out of the
    manifest is the point: a manifest is a claim, and reconciliation is where
    the claim is checked against the bytes.
    """
    try:
        target = Path(store.resolve(item.uri))
    except StoreError:
        return None
    if not target.exists():
        return None
    if target.is_file():
        return hash_file(target)

    files = sorted(
        path for path in target.iterdir() if path.is_file() and path.name != MANIFEST_NAME
    )
    if len(files) != 1:
        return None
    return hash_file(files[0])


def describe(report: Report) -> tuple[str, ...]:
    """The reconciliation as lines an operator reads. AC-D3."""
    lines = [
        f"vault {report.site_id}  {report.at.isoformat()}  "
        f"{'applied' if report.applied else 'dry run'}",
        f"  {report.summary()}",
    ]
    for finding in report.findings:
        lines.append(f"  {finding.outcome:<12} {finding.expected.uri}")
        lines.append(f"               {finding.detail}")
    if report.abandoned:
        verb = "removed" if report.applied else "would remove"
        lines.append(f"  {verb} {len(report.abandoned)} abandoned staging tree(s)")
    for orphan in report.orphans:
        lines.append(f"  ORPHAN       {orphan} -- sealed, and no source record names it")
    return tuple(lines)


def known(expected: Iterable[Expected]) -> tuple[str, ...]:
    """The URIs an orphan check should treat as accounted for."""
    return tuple(item.uri for item in expected)


def _now() -> datetime:
    """The current instant, with an explicit offset. SAD 11E.2."""
    return datetime.now(UTC)


__all__ = [
    "NEEDS_ATTENTION",
    "RECONCILED",
    "STAGED",
    "VAULT_MARKER",
    "VAULT_SUBJECT",
    "Expected",
    "Finding",
    "Outcome",
    "Report",
    "VaultNotInitialisedError",
    "describe",
    "initialise",
    "known",
    "marker_of",
    "mounted",
    "reconcile",
    "require_vault",
]
