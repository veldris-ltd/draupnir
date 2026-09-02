"""An ExportDriver for NVFP4, GGUF and MLX.

SAD 8.2 lists these as implementations of `draupnir.export`: TensorRT Model
Optimizer for NVFP4, llama.cpp for GGUF, and mlx-lm for MLX. One driver rather
than three, because the three share everything except the command they invoke,
and three packages would be three places to change when the collection step
changes.

MLX is the interesting one. It cannot be built on the forge -- ALVISS is the
only Apple silicon in the estate -- so a plan for MLX names the `export`
partition and the driver records which host it must run on. SKIDBLADNIR then
compares the MLX result against the NVFP4 result as a cross-platform check
(see `draupnir.skidbladnir.formats`), and that check is the reason the MLX
build is worth its allocation: a quantisation defect looks like a healthy model
until two builds of the same weights disagree.

`render` is pure, so nothing here reads the model it is about to quantise. The
driver reports what it will produce; whether the bytes turn out right is
established by re-gating the output, which AC-F9 requires in any case.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
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

NAME = "skidbladnir.quantise/v1"

#: The formats of SAD 6.2. `mlx4` is declared here and built elsewhere; a
#: capability says what the driver can produce, not where it runs.
CAPABILITIES = frozenset({"nvfp4", "gguf-q4km", "mlx4"})

#: How each format is built, and on what. The host matters: an MLX build
#: scheduled onto the forge would silently not happen, or would happen through
#: an emulation nobody validated.
BUILDS: dict[str, dict[str, str]] = {
    "nvfp4": {
        "tool": "modelopt",
        "host": "sindri",
        "command": "python -m modelopt.torch.quantization.export --format nvfp4",
        "output": "model-nvfp4.safetensors",
    },
    "gguf-q4km": {
        "tool": "llama.cpp",
        "host": "sindri",
        "command": "llama-quantize --allow-requantize model-f16.gguf out.gguf Q4_K_M",
        "output": "out.gguf",
    },
    "mlx4": {
        "tool": "mlx-lm",
        "host": "alviss",
        "command": "python -m mlx_lm.convert -q --q-bits 4",
        "output": "model-mlx4.safetensors",
    },
}

_PROGRESS = re.compile(r"(?P<step>\d+)\s*/\s*(?P<total>\d+)\s+(?:tensors|layers|shards)")
_WRITING = re.compile(r"(?:Writing|Saving|Wrote)\s+(?P<path>\S+\.(?:safetensors|gguf))")
_WARNING = re.compile(r"^\s*(?:WARNING|warn)[:\s]+(?P<message>.+)$")


class ExportError(Exception):
    """Raised when a format cannot be built."""


@dataclass
class QuantiseDriver:
    """Builds NVFP4, GGUF and MLX from a merged model."""

    name: str = NAME
    capabilities: frozenset[str] = CAPABILITIES

    # -- validate ----------------------------------------------------------

    def validate(self, spec: RunSpec) -> list[ValidationError]:
        """Return every reason these formats cannot be produced."""
        problems: list[ValidationError] = []

        unknown = sorted(set(spec.release.formats) - self.capabilities)
        if unknown:
            problems.append(
                ValidationError(
                    field="spec.release.formats",
                    message=(
                        f"this driver does not produce {', '.join(unknown)}. It builds "
                        f"{', '.join(sorted(self.capabilities))}."
                    ),
                    code="unsupported_format",
                )
            )

        if not spec.release.formats:
            problems.append(
                ValidationError(
                    field="spec.release.formats",
                    message="no format was named, so this export would produce nothing",
                    code="no_formats",
                )
            )

        # The cross-platform check needs both halves. Building MLX without
        # NVFP4 produces a build with nothing to compare it against, which is
        # the one situation where a quantisation defect goes unnoticed.
        formats = set(spec.release.formats)
        if "mlx4" in formats and "nvfp4" not in formats:
            problems.append(
                ValidationError(
                    field="spec.release.formats",
                    message=(
                        "mlx4 is checked against nvfp4 as a cross-platform "
                        "quantisation check, and nvfp4 was not requested. An MLX build "
                        "with nothing to compare against is the one case where a "
                        "quantisation defect passes unnoticed."
                    ),
                    code="missing_crosscheck_reference",
                )
            )

        return problems

    # -- render ------------------------------------------------------------

    def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
        """Produce the command, environment and resources. Pure (Decision S5)."""
        formats = tuple(sorted(spec.release.formats))
        if not formats:
            msg = "no format was named; validate this specification before rendering it"
            raise ExportError(msg)

        unknown = sorted(set(formats) - self.capabilities)
        if unknown:
            msg = (
                f"this driver does not produce {', '.join(unknown)}. There is no "
                "nearest-format fallback: a customer asking for NVFP4 and receiving "
                "GGUF has received a different model."
            )
            raise ExportError(msg)

        steps = " && ".join(f"{BUILDS[name]['command']} --out {name}" for name in formats)
        return JobPlan(
            command=("sh", "-c", steps),
            environment={"PYTHONHASHSEED": "0", "TOKENIZERS_PARALLELISM": "false"},
            workdir=str(workdir),
            resources=ResourceRequest(
                partition="export", nodes=1, gpus_per_node=1, time_limit_minutes=240
            ),
            expected_artefacts=tuple(BUILDS[name]["output"] for name in formats),
        )

    def hosts_for(self, spec: RunSpec) -> dict[str, str]:
        """Which host each requested format must be built on.

        MLX on ALVISS is the requirement; the rest on the forge. Returned as a
        mapping so a scheduler can split one export across two hosts without
        this driver knowing how a scheduler works.
        """
        return {
            name: BUILDS[name]["host"] for name in sorted(spec.release.formats) if name in BUILDS
        }

    # -- observe -----------------------------------------------------------

    def parse_progress(self, line: str) -> ProgressEvent | None:
        """Translate one line of quantiser output into a structured event."""
        writing = _WRITING.search(line)
        if writing:
            return ProgressEvent(kind=ProgressKind.CHECKPOINT, message=writing.group("path"))

        progress = _PROGRESS.search(line)
        if progress:
            return ProgressEvent(
                kind=ProgressKind.STEP,
                step=int(progress.group("step")),
                total=int(progress.group("total")),
            )

        warning = _WARNING.match(line)
        if warning:
            return ProgressEvent(
                kind=ProgressKind.WARNING, message=warning.group("message").strip()
            )
        return None

    # -- collect -----------------------------------------------------------

    def collect(self, workdir: Path) -> RunArtefacts:
        """Return each built format with its hash. Never mutates them."""
        root = Path(workdir)
        if not root.is_dir():
            return RunArtefacts()

        artefacts = tuple(
            ProducedArtefact(
                path=str(path.relative_to(root)).replace("\\", "/"),
                kind=_format_of(path) or "other",
                sha256=_digest(path),
                size=path.stat().st_size,
            )
            for path in sorted(root.rglob("*"))
            if path.is_file()
        )
        return RunArtefacts(artefacts=artefacts)

    def built_formats(self, workdir: Path) -> tuple[str, ...]:
        """Which formats actually exist on disk.

        What AC-F9 is checked against. Read from the filesystem rather than
        from the specification, because the question is which builds exist and
        need evaluating, not which were asked for.
        """
        root = Path(workdir)
        if not root.is_dir():
            return ()
        found = {
            name
            for path in root.rglob("*")
            if path.is_file()
            for name in (_format_of(path),)
            if name
        }
        return tuple(sorted(found))


def _format_of(path: Path) -> str | None:
    """Which format a produced file belongs to, from its name."""
    name = path.name.lower()
    for fmt, build in BUILDS.items():
        if name == build["output"].lower() or fmt.replace("-", "_") in name.replace("-", "_"):
            return fmt
    if name.endswith(".gguf"):
        return "gguf-q4km"
    return None


def _digest(path: Path) -> str:
    """SHA-256 of a file, read in chunks and never held whole."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


driver = QuantiseDriver()

__all__ = ["BUILDS", "CAPABILITIES", "NAME", "ExportError", "QuantiseDriver", "driver"]
