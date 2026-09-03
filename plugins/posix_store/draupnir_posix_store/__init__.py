"""A StoreDriver over a POSIX filesystem: the reference `draupnir.store`.

AC-D2: "Every plug-in interface has a reference implementation and a worked
example." `draupnir.store` had neither, because HODD addresses its own vault
and never needed to go through the extension point to do it. That is exactly
why this exists: an extension point with no implementation is an extension
point nobody has tried to extend, and the first person to try finds out what
the Protocol left unsaid.

It is not HODD. `draupnir.hodd.stores.PosixStoreDriver` is the vault, with
sealing, quotas, manifests and a site registry, and an import contract forbids
a driver reaching into it. This is the smaller thing the Protocol actually
describes -- resolve, stat, get, put -- written against `draupnir.interfaces`
and nothing else, which is what a third party writing one has to work from.

Three things in here are worth copying.

**The mount is checked before every operation.** `put` creates the artefact's
parent directories, which is right inside a mounted filesystem and wrong
outside one: with the mount gone, creating parents recreates the mount point
on local disk and the artefact lands somewhere nobody backs up. That failure
was found by unmounting a vault during the degraded-mode tests rather than by
review, and it is why `_require_mounted` is the first line of four methods.

**A URI is resolved arithmetically and never by touching the disk.** So
`resolve` answers whether or not the store is reachable, and the caller can
print the path it was going to use in the error it is about to raise.

**A path that escapes the root is refused.** `hodd://sindri/../../etc/passwd`
resolves outside the root, and a store that returned that path would be a file
read primitive with a URI syntax.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from draupnir.interfaces.types import ObjectInfo

#: The scheme this driver answers for. SAD 7.4: a specification records a URI,
#: never a path, so that a change of physical placement leaves every
#: specification untouched.
SCHEME = "hodd://"

NAME = "hodd.posix_reference/v1"

#: `posix` is the scheme family. `seal` is absent on purpose: sealing is an
#: immutability control HODD owns, and a reference driver that claimed it would
#: be claiming to enforce something it does not.
CAPABILITIES = frozenset({"posix", "file"})

#: How much is read at a time when hashing. Large enough to be fast, small
#: enough that a 200 GB artefact does not load.
CHUNK = 1 << 20


class StoreError(Exception):
    """Raised when the store cannot satisfy a request."""


class NotMountedError(StoreError):
    """Raised when the root is not there.

    Distinct from "the artefact is missing", and the distinction is the whole
    point: a caller told the artefact is missing plans a run that writes it,
    and a caller told the store is gone stops.
    """

    def __init__(self, root: Path) -> None:
        """Name the root that is missing."""
        self.root = root
        super().__init__(
            f"the store root {root} is not there. This is not the same as an artefact "
            "being absent: nothing can be read or written until it returns."
        )


@dataclass
class PosixStoreDriver:
    """Resolves `hodd://` URIs to paths under one root."""

    root: Path = field(default_factory=lambda: Path("/srv/hodd"))
    name: str = NAME
    capabilities: frozenset[str] = CAPABILITIES
    #: The site an authority-less URI belongs to. `hodd://models/x` is this
    #: site's; `hodd://brokkr/models/x` is not, and is refused rather than
    #: silently resolved here (SAD 11A.2).
    local_site: str = "sindri"

    # -- addressing ---------------------------------------------------------

    def resolve(self, uri: str) -> str:
        """Return the physical location of a `hodd://` URI.

        Arithmetic on the path; touches no disk. So it answers when the store
        is unreachable, and an error message can name the path it meant.
        """
        site, relative = self._parse(uri)
        if site != self.local_site:
            msg = (
                f"{uri} names site {site!r} and this driver holds {self.local_site!r}. "
                "Another forge's artefacts resolve through that forge's driver."
            )
            raise StoreError(msg)

        base = self.root.resolve()
        candidate = (base / relative).resolve()
        if not candidate.is_relative_to(base):
            msg = f"{uri} resolves outside {base}"
            raise StoreError(msg)
        return str(candidate)

    def _parse(self, uri: str) -> tuple[str, str]:
        """Split a `hodd://` URI into (site, path), filling in the local site."""
        if not uri.startswith(SCHEME):
            msg = f"{uri!r} is not a {SCHEME} URI"
            raise StoreError(msg)
        rest = uri[len(SCHEME) :].strip("/")
        if not rest:
            msg = f"{uri!r} addresses nothing"
            raise StoreError(msg)

        head, _, tail = rest.partition("/")
        # An authority is a registered site identifier. Anything else is the
        # first path segment of a local address, which is what SAD 11A.2's
        # "an omitted authority resolves to the local site" means in practice.
        site, path = (head, tail) if head == self.local_site else (self.local_site, rest)
        if not path:
            msg = f"{uri!r} names a site and no artefact"
            raise StoreError(msg)
        if ".." in PurePosixPath(path).parts:
            msg = f"{uri!r} contains a relative path segment"
            raise StoreError(msg)
        return site, path

    def _require_mounted(self) -> None:
        """Refuse when the root is absent. See `NotMountedError`."""
        if not self.root.is_dir():
            raise NotMountedError(self.root)

    # -- the Protocol -------------------------------------------------------

    def stat(self, uri: str) -> ObjectInfo:
        """What is known about the object, without fetching it."""
        self._require_mounted()
        path = Path(self.resolve(uri))
        if not path.exists():
            return ObjectInfo(uri=uri, exists=False)
        if path.is_dir():
            files = [item for item in path.rglob("*") if item.is_file()]
            return ObjectInfo(uri=uri, exists=True, size=sum(item.stat().st_size for item in files))
        return ObjectInfo(uri=uri, exists=True, sha256=_digest(path), size=path.stat().st_size)

    def get(self, uri: str, destination: Path) -> ObjectInfo:
        """Copy the object to `destination`."""
        self._require_mounted()
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
        """Store `source` at `uri`, refusing to overwrite what is there.

        Refusing rather than replacing: an artefact is addressed by a URI and
        identified by its hash, so two different sets of bytes at one URI make
        every recorded hash ambiguous.
        """
        self._require_mounted()
        target = Path(self.resolve(uri))
        if target.exists():
            msg = (
                f"{uri} already holds an artefact. A store that overwrote it would make "
                "every hash recorded against this URI ambiguous."
            )
            raise StoreError(msg)

        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
        return self.stat(uri)


def _digest(path: Path) -> str:
    """SHA-256 of a file, read in blocks."""
    running = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            running.update(chunk)
    return running.hexdigest()


driver = PosixStoreDriver()

__all__ = [
    "CAPABILITIES",
    "NAME",
    "NotMountedError",
    "PosixStoreDriver",
    "StoreError",
    "driver",
]
