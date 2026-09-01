"""Artefact manifests: what was ingested, and the hashes that identify it.

A manifest is the durable half of an artefact. SAD 7.3 requires that when a raw
corpus is deleted under retention, "HODD therefore retains the curated
manifests, per source hashes and the licence register entries indefinitely, so
that lineage and the Article 53 training content summary survive the deletion
of the underlying text."

So a manifest is written to be readable without the thing it describes. It
carries every file's path, size and SHA-256, a digest over the whole set, and
nothing that needs the bytes to interpret.

This module is pure. It hashes files and builds records; where those records
are stored is the store driver's business, and whether the licences they name
are acceptable is GLEIPNIR's.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

#: Read size for hashing. Large enough that a multi-gigabyte checkpoint is not
#: a syscall storm, small enough that memory stays flat.
CHUNK = 1024 * 1024

SCHEMA = "draupnir/manifest/v1"


class ManifestError(Exception):
    """Raised when a manifest cannot be built or does not verify."""


@dataclass(frozen=True, slots=True, order=True)
class ManifestEntry:
    """One file, as the manifest records it."""

    path: str
    size: int
    sha256: str

    def as_mapping(self) -> dict[str, Any]:
        """The wire shape, ordered for a canonical digest."""
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


def hash_file(path: Path, *, chunk: int = CHUNK) -> str:
    """Return the SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def walk(root: Path) -> Iterator[Path]:
    """Yield every file under `root`, in a stable order.

    Sorted by POSIX-style relative path rather than by whatever the filesystem
    returns, so that the same tree ingested on Linux and on macOS produces the
    same manifest digest (AC-N7).
    """
    files = [path for path in root.rglob("*") if path.is_file()]
    yield from sorted(files, key=lambda path: path.relative_to(root).as_posix())


@dataclass(frozen=True, slots=True)
class Manifest:
    """Everything HODD knows about one ingested artefact.

    Survives the artefact. A retention action may delete the bytes; this
    record is kept indefinitely so that lineage still resolves (SAD 7.3).
    """

    uri: str
    kind: str
    entries: tuple[ManifestEntry, ...]
    ingested_at: datetime
    #: Free-form provenance the ingest recorded. HODD records; it does not
    #: interpret. A licence identifier here is a fact, not a judgement.
    facts: dict[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA

    @property
    def size(self) -> int:
        """Total bytes across every file."""
        return sum(entry.size for entry in self.entries)

    @property
    def file_count(self) -> int:
        """How many files the artefact holds."""
        return len(self.entries)

    def as_mapping(self) -> dict[str, Any]:
        """The wire shape. Sorted entries, so the digest is a function of content."""
        return {
            "schema": self.schema,
            "uri": self.uri,
            "kind": self.kind,
            "ingestedAt": self.ingested_at.isoformat(),
            "size": self.size,
            "fileCount": self.file_count,
            "entries": [entry.as_mapping() for entry in sorted(self.entries)],
            "facts": self.facts,
        }

    def canonical(self) -> bytes:
        """The bytes the manifest digest covers."""
        return json.dumps(
            self.as_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def digest(self) -> str:
        """The `sha256_manifest` of SAD 7.1: one hash naming the whole artefact.

        Over the manifest rather than over a concatenation of the files,
        because the manifest is what survives the files. Two artefacts with
        identical bytes but different layouts are different artefacts, and
        this says so.
        """
        return hashlib.sha256(self.canonical()).hexdigest()

    def entry(self, path: str) -> ManifestEntry | None:
        """Return the entry for a relative path, if the manifest holds one."""
        return next((item for item in self.entries if item.path == path), None)

    def to_json(self) -> str:
        """A readable rendering, for writing beside the artefact."""
        return json.dumps({**self.as_mapping(), "digest": self.digest()}, indent=2, sort_keys=True)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Manifest:
        """Rebuild a manifest from its stored form."""
        if data.get("schema") != SCHEMA:
            msg = f"unknown manifest schema {data.get('schema')!r}, expected {SCHEMA}"
            raise ManifestError(msg)
        return cls(
            uri=str(data["uri"]),
            kind=str(data["kind"]),
            entries=tuple(
                ManifestEntry(
                    path=str(item["path"]), size=int(item["size"]), sha256=str(item["sha256"])
                )
                for item in data["entries"]
            ),
            ingested_at=datetime.fromisoformat(str(data["ingestedAt"])),
            facts=dict(data.get("facts", {})),
        )


def build(
    root: Path,
    *,
    uri: str,
    kind: str,
    ingested_at: datetime,
    facts: dict[str, Any] | None = None,
) -> Manifest:
    """Hash every file under `root` and return the manifest describing it."""
    if not root.is_dir():
        msg = f"{root} is not a directory"
        raise ManifestError(msg)
    if ingested_at.tzinfo is None:
        msg = "manifest timestamps carry an explicit offset (SAD 11E.2)"
        raise ManifestError(msg)

    entries = tuple(
        ManifestEntry(
            path=path.relative_to(root).as_posix(),
            size=path.stat().st_size,
            sha256=hash_file(path),
        )
        for path in walk(root)
    )
    if not entries:
        msg = f"{root} holds no files; there is nothing to ingest"
        raise ManifestError(msg)

    return Manifest(
        uri=uri, kind=kind, entries=entries, ingested_at=ingested_at, facts=dict(facts or {})
    )


@dataclass(frozen=True, slots=True)
class Divergence:
    """One way in which a tree no longer matches its manifest."""

    path: str
    reason: str

    def __str__(self) -> str:
        """Render for a report."""
        return f"{self.path}: {self.reason}"


def verify(manifest: Manifest, root: Path) -> tuple[Divergence, ...]:
    """Return every way `root` differs from what `manifest` recorded.

    AC-S1 and AC-S8 both rest on this: altering one byte of a weight file must
    be caught before the run starts, and modifying an artefact after its gates
    pass must fail publication. Both are this function, called at a different
    moment.
    """
    divergences: list[Divergence] = []
    recorded = {entry.path: entry for entry in manifest.entries}

    present = {path.relative_to(root).as_posix() for path in walk(root)} if root.is_dir() else set()

    for path in sorted(set(recorded) - present):
        divergences.append(Divergence(path, "recorded in the manifest but missing"))
    for path in sorted(present - set(recorded)):
        divergences.append(Divergence(path, "present but not in the manifest"))

    for path in sorted(set(recorded) & present):
        entry = recorded[path]
        actual = root / path
        if actual.stat().st_size != entry.size:
            divergences.append(
                Divergence(path, f"size {actual.stat().st_size}, manifest says {entry.size}")
            )
            continue
        if hash_file(actual) != entry.sha256:
            divergences.append(Divergence(path, "content does not match its recorded SHA-256"))

    return tuple(divergences)


def describe(divergences: Iterable[Divergence]) -> str:
    """Render divergences for a ledger payload or an error."""
    listed = list(divergences)
    if not listed:
        return "intact"
    return "; ".join(str(item) for item in listed)
