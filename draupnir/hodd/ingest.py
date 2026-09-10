"""Ingest: hash, write the manifest, seal, then register.

The order is the requirement. A crash at any point must not leave a half
registered corpus, and the way to get that is to make registration the last
step and everything before it invisible.

    1. stage      copy into a staging directory nobody resolves
    2. hash       build the manifest over what was actually staged
    3. manifest   write it beside the artefact
    4. publish    move the staged directory into place -- one rename
    5. seal       make every file read only
    6. register   record the source, and only now is the corpus visible

Steps 1 to 3 happen under a name no `hodd://` URI resolves to, so a crash
leaves rubbish in a staging directory and nothing else. Step 4 is a directory
rename, which is atomic on POSIX and on NTFS for a same-volume move. A crash
after step 4 and before step 6 leaves a sealed artefact that no source record
names -- recoverable, and detectable, which is why `orphans` exists below.

Sealing before registering rather than after matters: registration is what
makes a corpus eligible for curation, and a corpus that is eligible before it
is read only is a corpus a curation script can rewrite.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from draupnir.hodd.manifest import Manifest, ManifestError, build, describe, verify
from draupnir.hodd.register import LicenceRegister, SourceRecord
from draupnir.hodd.stores import ImmutableArtefactError, PosixStoreDriver, parse

#: Where a partial ingest lives. Nothing resolves a `hodd://` URI into it, so
#: a crash mid-ingest leaves files that no lineage references.
STAGING = ".staging"

#: The manifest is written beside the artefact under this name, so that the
#: record survives in the same place as the thing it describes.
MANIFEST_NAME = "manifest.json"


class IngestError(Exception):
    """Raised when an ingest cannot be completed."""


@dataclass(frozen=True, slots=True)
class Ingested:
    """What an ingest produced."""

    uri: str
    manifest: Manifest
    source: SourceRecord | None
    bytes_written: int

    @property
    def digest(self) -> str:
        """The manifest digest: the `sha256_manifest` of SAD 7.1."""
        return self.manifest.digest()


class Ingestor:
    """Brings a corpus or an artefact under HODD's control, atomically."""

    def __init__(
        self,
        store: PosixStoreDriver,
        register: LicenceRegister | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        scan: Callable[[Path], Any] | None = None,
    ) -> None:
        """Bind to a store and, when sources are being recorded, a register.

        `scan` is the secret scanner an ingest runs before the point of no
        return -- `svalinn.scanning.scan_before_registration` in the running
        system (RF-09, AC-S6). Injected rather than imported: HODD and SVALINN
        are siblings in the layering and neither may import the other, so the
        composition root is the one place that puts them together.

        `None` scans nothing, which is what a unit test of the ingest mechanics
        wants and what nothing in the running system should be given.
        """
        self._store = store
        self._register = register
        self._clock = clock or _now
        self._scan = scan

    # -- the ingest --------------------------------------------------------

    def ingest(
        self,
        source_tree: Path,
        uri: str,
        *,
        kind: str,
        source: SourceRecord | None = None,
        facts: dict[str, Any] | None = None,
    ) -> Ingested:
        """Ingest `source_tree` as `uri`, atomically.

        `source` is recorded only if everything before it succeeded, so a
        failure leaves the register exactly as it was.
        """
        if not source_tree.is_dir():
            msg = f"{source_tree} is not a directory"
            raise IngestError(msg)

        target = Path(self._store.resolve(uri))
        if target.exists():
            raise ImmutableArtefactError(uri)

        staging = self._staging_for(uri)
        try:
            # 1. stage
            shutil.copytree(source_tree, staging, dirs_exist_ok=False)

            # 2. hash, over what was staged rather than over the origin: the
            #    manifest must describe the bytes HODD now holds. Before the
            #    scan, so that a refusal names bytes whose digest is known.
            manifest = build(staging, uri=uri, kind=kind, ingested_at=self._clock(), facts=facts)

            # 3. scan, over the staged tree and before the rename. AC-S6 and
            #    RF-09: the scanner existed and nothing called it, so a corpus
            #    carrying a credential was ingested, sealed and lineaged, and
            #    the only way back out is a ledgered retention action. Here
            #    rather than after publication because everything below this
            #    line is irreversible, and a scanner that runs after the point
            #    of no return reports rather than refuses.
            if self._scan is not None:
                self._scan(staging)

            # 4. manifest, written inside the staged tree so it moves with it
            (staging / MANIFEST_NAME).write_text(manifest.to_json(), encoding="utf-8")

            # 5. publish -- one rename, and the point of no return
            target.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(target)
        except Exception:
            # Everything before the rename is disposable, and disposing of it
            # is what makes a failed ingest leave nothing behind.
            shutil.rmtree(staging, ignore_errors=True)
            raise

        # 6. seal. From here the artefact is read only, and a curation script
        #    that never consulted the database is refused by the filesystem.
        self._store.seal(uri)

        # 7. register, last
        recorded = None
        if source is not None:
            if self._register is None:
                msg = "a source was given to ingest but no licence register is configured"
                raise IngestError(msg)
            recorded = self._register.record(source)

        return Ingested(
            uri=uri,
            manifest=manifest,
            source=recorded,
            bytes_written=manifest.size,
        )

    def _staging_for(self, uri: str) -> Path:
        address = parse(uri, local_site=self._store.local_site)
        staging = self._store.root / address.site_id / STAGING / uuid.uuid4().hex
        staging.parent.mkdir(parents=True, exist_ok=True)
        return staging

    # -- what a crash leaves behind ---------------------------------------

    def abandoned_staging(self) -> tuple[Path, ...]:
        """Staged trees no ingest completed.

        A crash between steps 1 and 4 leaves one of these. Nothing resolves to
        it and no lineage references it, so it is safe to remove -- but it
        should be found and removed rather than accumulating silently.
        """
        found: list[Path] = []
        for site in sorted(self._store.root.glob("*")):
            staging = site / STAGING
            if staging.is_dir():
                found.extend(sorted(path for path in staging.iterdir() if path.is_dir()))
        return tuple(found)

    def discard_abandoned(self) -> int:
        """Remove abandoned staging directories and return how many went."""
        abandoned = self.abandoned_staging()
        for path in abandoned:
            shutil.rmtree(path, ignore_errors=True)
        return len(abandoned)

    def orphans(self, known_uris: Iterable[str]) -> tuple[str, ...]:
        """Sealed artefacts that no source record names.

        A crash between step 4 and step 6 produces one: the bytes are on disk
        and sealed, and nothing points at them. They are reported rather than
        deleted, because an artefact that HODD sealed is exactly the sort of
        thing that should not be removed by a process that found it surprising.
        """
        known = {str(parse(uri, local_site=self._store.local_site)) for uri in known_uris}
        found: list[str] = []
        for site in sorted(self._store.root.glob("*")):
            if not site.is_dir():
                continue
            for manifest_path in sorted(site.rglob(MANIFEST_NAME)):
                artefact = manifest_path.parent
                relative = artefact.relative_to(site).as_posix()
                uri = f"hodd://{site.name}/{relative}"
                if uri not in known:
                    found.append(uri)
        return tuple(found)

    # -- verification ------------------------------------------------------

    def verify(self, uri: str) -> tuple[str, ...]:
        """Return every way the stored artefact differs from its manifest.

        AC-S1 calls this before a run starts; AC-S8 calls it before
        publication. Both are the same question asked at a different moment.
        """
        target = Path(self._store.resolve(uri))
        manifest_path = target / MANIFEST_NAME
        if not manifest_path.is_file():
            msg = f"{uri} has no manifest; it was never ingested by HODD"
            raise IngestError(msg)

        import json

        stored = json.loads(manifest_path.read_text(encoding="utf-8"))
        stored.pop("digest", None)
        try:
            manifest = Manifest.from_mapping(stored)
        except ManifestError as error:
            raise IngestError(str(error)) from error

        # The manifest describes the artefact and does not describe itself.
        divergences = tuple(item for item in verify(manifest, target) if item.path != MANIFEST_NAME)
        return tuple(str(item) for item in divergences)

    def describe_divergence(self, uri: str) -> str:
        """A one-line summary for a ledger payload."""
        divergences = self.verify(uri)
        return "intact" if not divergences else "; ".join(divergences)


def _now() -> datetime:
    from datetime import UTC

    return datetime.now(tz=UTC)


__all__ = [
    "MANIFEST_NAME",
    "STAGING",
    "IngestError",
    "Ingested",
    "Ingestor",
    "describe",
]
