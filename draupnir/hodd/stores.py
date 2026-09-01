"""`StoreDriver` implementations, and the resolution of `hodd://` URIs.

SAD 7.4: "Artefacts are addressed by a `hodd://` URI resolved by HODD to a
physical location. This keeps run specifications portable across a storage
change, which matters because gap G2 and the Phase 2 NVMe over Fabrics option
both alter physical placement."

That sentence is the whole design. A run specification records
`hodd://sindri/corpora/GBR/curated` and never a path. Moving the vault, or
replacing it with an object store, changes which driver resolves that URI and
changes nothing about the specification -- so a run recorded in 2026 still
resolves in 2030 against storage that did not exist when it was written.

Two drivers are provided, matching SAD 7.2: a POSIX one for the HODD vault
over NFS, and an S3 one for MinIO on ANDVARI. Both are `StoreDriver`
implementations registered by entry point like any other plug-in, so a third
is an installation rather than a change here.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from draupnir.hodd.manifest import hash_file
from draupnir.interfaces.types import ObjectInfo

SCHEME = "hodd"


class StoreError(Exception):
    """Raised when a store cannot satisfy a request."""


class ImmutableArtefactError(StoreError):
    """Raised when a write would overwrite a sealed artefact.

    SAD 5.2 gives HODD "immutability". An artefact that has been sealed is the
    input to a lineage chain, and rewriting it would make every attestation
    downstream a lie that still verifies.
    """

    def __init__(self, uri: str) -> None:
        """Name the artefact that was not overwritten."""
        self.uri = uri
        super().__init__(
            f"{uri} is sealed and will not be overwritten. An artefact is immutable "
            "once registered; produce a new one (SAD 5.2)."
        )


@dataclass(frozen=True, slots=True)
class Address:
    """A parsed `hodd://` URI: which site holds it, and where within the site."""

    site_id: str
    path: str

    def __str__(self) -> str:
        """The fully qualified form, which always names its site."""
        return f"{SCHEME}://{self.site_id}/{self.path}"


def parse(uri: str, *, local_site: str, sites: Iterable[str] = ()) -> Address:
    """Parse a `hodd://` URI, resolving an omitted authority to the local site.

    SAD 7.4 says the authority carries the site and that an omitted one
    resolves locally. SAD 6.2 writes `hodd://models/core/...`, which has an
    authority that is not a site. Both are accepted: an authority is taken as
    a site only when it is one, which is why `sites` exists.

    Passing only `local_site` would make `hodd://brokkr/adapters/x` resolve to
    a *local* path called `brokkr/adapters/x` -- silently, and wrongly. AC-F18
    requires a second site's artefacts to address correctly from the first, so
    every known forge has to be nameable here.
    """
    parts = urlsplit(uri)
    if parts.scheme != SCHEME:
        msg = f"{uri!r} is not a {SCHEME}:// address"
        raise StoreError(msg)

    known = {local_site, *sites}
    authority, path = parts.netloc, parts.path.lstrip("/")
    if authority and authority in known:
        resolved = path
        site = authority
    else:
        resolved = f"{authority}/{path}".strip("/") if authority else path
        site = local_site

    if not resolved:
        msg = f"{uri!r} names no artefact"
        raise StoreError(msg)
    if ".." in PurePosixPath(resolved).parts:
        # A relative segment in an artefact address is either a mistake or an
        # attempt to reach outside the vault. Neither should resolve.
        msg = f"{uri!r} contains a relative path segment"
        raise StoreError(msg)
    return Address(site_id=site, path=resolved)


# ---------------------------------------------------------------------------
# POSIX and NFS
# ---------------------------------------------------------------------------

#: Read-only for owner and group, and nothing for anyone else. Applied to
#: every file in a sealed artefact, which is what AC-F3 checks when it writes
#: to a raw corpus and expects to be refused.
READ_ONLY = stat.S_IRUSR | stat.S_IRGRP


@dataclass
class PosixStoreDriver:
    """The HODD vault over NFS. SAD 7.2.

    Sealing is a filesystem permission change rather than a database flag,
    because the thing that must fail is a write by a curation script that never
    consulted the database.
    """

    root: Path
    name: str = "hodd.posix/v1"
    capabilities: frozenset[str] = frozenset({"posix", "nfs", "seal"})
    local_site: str = "sindri"
    #: Every forge whose identifier may appear as an authority. Without the
    #: others, `hodd://brokkr/...` resolves to a local path of that name.
    sites: frozenset[str] = frozenset()

    def resolve(self, uri: str) -> str:
        """Return the physical location of a `hodd://` URI."""
        address = self._address(uri)
        return str(self._path(address))

    def _address(self, uri: str) -> Address:
        return parse(uri, local_site=self.local_site, sites=self.sites)

    def _path(self, address: Address) -> Path:
        base = (self.root / address.site_id).resolve()
        candidate = (base / address.path).resolve()
        if not candidate.is_relative_to(base):
            msg = f"{address} resolves outside the vault"
            raise StoreError(msg)
        return candidate

    def stat(self, uri: str) -> ObjectInfo:
        """Return what is known about the object without fetching it."""
        path = Path(self.resolve(uri))
        if not path.exists():
            return ObjectInfo(uri=uri, exists=False)
        if path.is_dir():
            files = [item for item in path.rglob("*") if item.is_file()]
            return ObjectInfo(uri=uri, exists=True, size=sum(item.stat().st_size for item in files))
        return ObjectInfo(uri=uri, exists=True, sha256=hash_file(path), size=path.stat().st_size)

    def get(self, uri: str, destination: Path) -> ObjectInfo:
        """Copy the object to `destination`."""
        source = Path(self.resolve(uri))
        if not source.exists():
            msg = f"{uri} does not exist"
            raise StoreError(msg)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
        return self.stat(uri)

    def put(self, uri: str, source: Path) -> ObjectInfo:
        """Store `source` at `uri`, refusing to overwrite a sealed artefact."""
        target = Path(self.resolve(uri))
        if self.is_sealed(uri):
            raise ImmutableArtefactError(uri)

        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
        return self.stat(uri)

    # -- immutability ------------------------------------------------------

    def seal(self, uri: str) -> None:
        """Make every file read only. Idempotent."""
        target = Path(self.resolve(uri))
        if not target.exists():
            msg = f"{uri} does not exist and cannot be sealed"
            raise StoreError(msg)
        for path in self._files(target):
            path.chmod(READ_ONLY)

    def is_sealed(self, uri: str) -> bool:
        """Whether every file of the artefact is read only."""
        target = Path(self.resolve(uri))
        files = list(self._files(target)) if target.exists() else []
        if not files:
            return False
        return all(not (path.stat().st_mode & stat.S_IWUSR) for path in files)

    def unseal(self, uri: str) -> None:
        """Make files writable again.

        Only a ledgered retention action reaches this (SAD 7.3). It exists so
        that deletion is possible at all; nothing in the ingest or curation
        path calls it.
        """
        target = Path(self.resolve(uri))
        for path in self._files(target):
            path.chmod(path.stat().st_mode | stat.S_IWUSR)

    def delete(self, uri: str) -> int:
        """Remove the artefact and return how many bytes went. Ledgered by the caller."""
        target = Path(self.resolve(uri))
        if not target.exists():
            return 0
        freed = sum(path.stat().st_size for path in self._files(target))
        self.unseal(uri)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return freed

    @staticmethod
    def _files(target: Path) -> list[Path]:
        if target.is_file():
            return [target]
        if target.is_dir():
            return [path for path in target.rglob("*") if path.is_file()]
        return []

    # -- capacity ----------------------------------------------------------

    def free_bytes(self) -> int:
        """Free space on the vault, for the pre-flight quota check of AC-S10."""
        self.root.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(self.root).free

    def total_bytes(self) -> int:
        """Total capacity of the vault."""
        self.root.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(self.root).total


# ---------------------------------------------------------------------------
# S3 and MinIO
# ---------------------------------------------------------------------------


@dataclass
class ObjectStoreDriver:
    """MinIO on ANDVARI, and any S3 interface. SAD 7.2.

    Sealing on an object store is not a permission bit. Object locking or a
    versioned bucket is the real mechanism, and until one is configured this
    driver records the seal and refuses the overwrite itself. That is weaker
    than the POSIX driver -- it stops HODD, not a bucket policy -- and it says
    so rather than implying otherwise.
    """

    bucket: str
    client: Any
    name: str = "hodd.s3/v1"
    capabilities: frozenset[str] = frozenset({"s3", "versioned"})
    local_site: str = "sindri"
    sites: frozenset[str] = frozenset()
    _sealed: set[str] = field(default_factory=set, repr=False)

    def resolve(self, uri: str) -> str:
        """Return the `s3://bucket/key` this URI addresses."""
        return f"s3://{self.bucket}/{self._key(self._address(uri))}"

    def _address(self, uri: str) -> Address:
        return parse(uri, local_site=self.local_site, sites=self.sites)

    @staticmethod
    def _key(address: Address) -> str:
        return f"{address.site_id}/{address.path}"

    def stat(self, uri: str) -> ObjectInfo:
        """Ask the store about the object without fetching it."""
        key = self._key(self._address(uri))
        try:
            info = self.client.stat_object(self.bucket, key)
        except Exception:
            return ObjectInfo(uri=uri, exists=False)
        return ObjectInfo(
            uri=uri,
            exists=True,
            sha256=(info.metadata or {}).get("x-amz-meta-sha256"),
            size=info.size,
        )

    def get(self, uri: str, destination: Path) -> ObjectInfo:
        """Fetch the object to `destination`."""
        key = self._key(self._address(uri))
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.fget_object(self.bucket, key, str(destination))
        return self.stat(uri)

    def put(self, uri: str, source: Path) -> ObjectInfo:
        """Store `source`, refusing to overwrite a sealed artefact."""
        if self.is_sealed(uri):
            raise ImmutableArtefactError(uri)
        key = self._key(self._address(uri))
        self.client.fput_object(
            self.bucket,
            key,
            str(source),
            metadata={"x-amz-meta-sha256": hash_file(source)},
        )
        return self.stat(uri)

    def seal(self, uri: str) -> None:
        """Record the artefact as sealed."""
        self._sealed.add(str(self._address(uri)))

    def is_sealed(self, uri: str) -> bool:
        """Whether this driver has sealed the artefact."""
        return str(self._address(uri)) in self._sealed

    def unseal(self, uri: str) -> None:
        """Release the seal. Only a ledgered retention action reaches this."""
        self._sealed.discard(str(self._address(uri)))

    def delete(self, uri: str) -> int:
        """Remove the object and return how many bytes went."""
        info = self.stat(uri)
        if not info.exists:
            return 0
        key = self._key(self._address(uri))
        self.unseal(uri)
        self.client.remove_object(self.bucket, key)
        return info.size or 0

    def free_bytes(self) -> int:
        """Object stores do not report free space; the vault is what is bounded.

        Returning the maximum rather than zero, so that a quota check against
        an object store passes rather than refusing every run for a limit that
        does not apply here.
        """
        return _UNBOUNDED

    def total_bytes(self) -> int:
        """Unbounded, for the same reason."""
        return _UNBOUNDED


#: What an unbounded store reports as capacity. Large enough that no plausible
#: run breaches it, finite so that arithmetic on it stays ordinary.
_UNBOUNDED = 1 << 62


def driver_for(uri: str, drivers: dict[str, Any], *, local_site: str) -> Any:
    """Return the driver that holds an artefact, by the site its URI names.

    A second forge's artefacts are addressed the same way and resolved by a
    different driver, which is what makes `hodd://brokkr/...` meaningful from
    Sindri without either forge sharing a filesystem.
    """
    address = parse(uri, local_site=local_site, sites=drivers.keys())
    driver = drivers.get(address.site_id)
    if driver is None:
        known = ", ".join(sorted(drivers)) or "none"
        msg = f"no store driver is configured for site {address.site_id!r}; configured: {known}"
        raise StoreError(msg)
    return driver


def readable_size(count: int) -> str:
    """Render a byte count for an operator-facing message."""
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TiB"


def is_writable(path: Path) -> bool:
    """Whether the current process could write to `path`. Used by AC-F3."""
    return os.access(path, os.W_OK)
