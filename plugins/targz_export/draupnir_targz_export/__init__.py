"""An ExportDriver that packages an artefact directory as a gzipped tarball.

This exists to answer AC-N9: a new export format, working, in under two
hundred lines, with no file under `draupnir/core/` modified. It is deliberately
the least interesting format available -- the measurement is of what adding one
costs, not of what it produces.

`render` is pure, as SAD Decision S5 requires: it returns a command and nothing
else, and the conformance suite checks that by rendering twice, rendering with
the network removed, and comparing the working directory before and after.
"""

from __future__ import annotations

import hashlib
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from draupnir.interfaces.types import (
    JobPlan,
    ProducedArtefact,
    ProgressEvent,
    ProgressKind,
    ResourceRequest,
    RunArtefacts,
    RunSpec,
    ValidationError,
)

NAME = "skidbladnir.targz/v1"
FORMAT = "targz"
CAPABILITIES = frozenset({FORMAT})

#: What `collect` looks for. Named here rather than inline so that `render` and
#: `collect` cannot drift apart about it.
ARCHIVE = "export.tar.gz"


@dataclass
class TarGzExportDriver:
    """Packages the artefact named by a specification into one tarball."""

    name: str = NAME
    capabilities: frozenset[str] = CAPABILITIES
    _: dict[str, str] = field(default_factory=dict, repr=False)

    def validate(self, spec: RunSpec) -> list[ValidationError]:
        """Return every reason this driver cannot run the specification."""
        problems: list[ValidationError] = []

        unsupported = sorted(set(spec.release.formats) - self.capabilities)
        if unsupported:
            problems.append(
                ValidationError(
                    field="spec.release.formats",
                    message=(
                        f"this driver exports {FORMAT} only; it cannot produce "
                        f"{', '.join(unsupported)}"
                    ),
                    code="unsupported-format",
                )
            )
        if not spec.base.artefact:
            problems.append(
                ValidationError(
                    field="spec.base.artefact",
                    message="there is nothing to package",
                    code="missing-artefact",
                )
            )
        return problems

    def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
        """Return the command that produces the archive. Pure: writes nothing.

        `python -m tarfile` rather than the `tar` binary, because the driver
        must render the same plan on the aarch64 appliances and on an Apple
        silicon workstation (AC-N7), and the interpreter is the one tool both
        are guaranteed to have.
        """
        source = spec.base.artefact.rsplit("/", 1)[-1]
        return JobPlan(
            command=("python", "-m", "tarfile", "--create", ARCHIVE, source),
            environment={"PYTHONHASHSEED": "0"},
            workdir=str(workdir),
            resources=ResourceRequest(partition="export", nodes=1, gpus_per_node=0),
            expected_artefacts=(ARCHIVE,),
        )

    def parse_progress(self, line: str) -> ProgressEvent | None:
        """Translate one line of `python -m tarfile` output, or return None.

        Packaging has no steps, no loss and no checkpoints, so most lines carry
        no event in the vocabulary of `ProgressKind` and the honest answer is
        None. A line that looks like a failure is worth surfacing, and is the
        one thing here that is.
        """
        stripped = line.strip()
        if not stripped:
            return None
        if stripped.startswith("Traceback") or "Error" in stripped:
            return ProgressEvent(kind=ProgressKind.WARNING, message=stripped[:200])
        return None

    def collect(self, workdir: Path) -> RunArtefacts:
        """Return the archive with its hash. Reads only; mutates nothing."""
        archive = workdir / ARCHIVE
        if not archive.is_file():
            return RunArtefacts()

        digest = hashlib.sha256()
        with archive.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)

        return RunArtefacts(
            artefacts=(
                ProducedArtefact(
                    path=ARCHIVE,
                    kind="quantised",
                    sha256=digest.hexdigest(),
                    size=archive.stat().st_size,
                ),
            ),
            metrics={"members": float(_member_count(archive))},
        )


def _member_count(archive: Path) -> int:
    """How many members the archive holds. Opened read-only."""
    try:
        with tarfile.open(archive, "r:gz") as handle:
            return len(handle.getnames())
    except (tarfile.TarError, OSError):
        return 0


driver = TarGzExportDriver()

__all__ = ["ARCHIVE", "CAPABILITIES", "FORMAT", "NAME", "TarGzExportDriver", "driver"]
