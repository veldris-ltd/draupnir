"""A MergeDriver for mergekit.

SAD 8.2 lists mergekit's TIES, DARE-TIES, SLERP and task arithmetic as
implementations of `draupnir.merge`. The driver turns a merge configuration
into a mergekit invocation and reads what mergekit prints. It does not choose
the weight, run the sweep, or decide whether the result is acceptable: the
sweep is BRISINGAMEN's and the verdict is RAUN's (SAD 5.2).

`render` is pure (Decision S5), so the mergekit YAML travels in the environment
and the job writes it before starting, the same shape the LLaMA-Factory driver
uses. Emitted as JSON, which mergekit's `yaml.safe_load` accepts, so the
serialisation is deterministic without depending on a YAML library's line
wrapping.

The one thing worth knowing about merging: mergekit is happy to merge models
with mismatched vocabularies and produces a model that generates plausible
nonsense. `validate` refuses a merge whose base and adapters do not declare the
same tokeniser hash, because the alternative is discovering it at evaluation.
"""

from __future__ import annotations

import hashlib
import json
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

NAME = "brisingamen.mergekit/v1"

#: The methods this driver implements, matching SAD 8.2's list plus `linear`,
#: which is the adapter-to-dense export BRISINGAMEN needs for route B.
CAPABILITIES = frozenset(
    {"ties", "dare-ties", "slerp", "task-arithmetic", "linear", "bf16", "multinode"}
)

CONFIG_VARIABLE = "DRAUPNIR_MERGE_CONFIG"
CONFIG_FILENAME = "mergekit.json"

#: mergekit prints `Executing graph: 42%|...| 210/500`.
_PROGRESS = re.compile(r"Executing graph:\s*\d+%\|[^|]*\|\s*(?P<step>\d+)/(?P<total>\d+)")
_WARNING = re.compile(r"^\s*WARNING[:\s]+(?P<message>.+)$")
_WRITING = re.compile(r"Writing (?P<path>\S+\.safetensors)")


@dataclass
class MergekitDriver:
    """Turns a merge configuration into a mergekit job."""

    name: str = NAME
    capabilities: frozenset[str] = CAPABILITIES
    executable: str = "mergekit-yaml"

    # -- validate ----------------------------------------------------------

    def validate(self, spec: RunSpec) -> list[ValidationError]:
        """Return every reason this merge cannot be run."""
        problems: list[ValidationError] = []
        params = dict(spec.train.params)

        method = str(params.get("merge_method", spec.train.method))
        if method not in {"ties", "dare-ties", "slerp", "task-arithmetic", "linear"}:
            problems.append(
                ValidationError(
                    field="spec.train.params.merge_method",
                    message=(
                        f"{method!r} is not a method this driver implements. It runs "
                        "ties, dare-ties, slerp, task-arithmetic and linear."
                    ),
                    code="unsupported_method",
                )
            )

        models = params.get("models") or []
        if not models:
            problems.append(
                ValidationError(
                    field="spec.train.params.models",
                    message="a merge names the models it combines; none were given",
                    code="no_models",
                )
            )

        # Mismatched vocabularies merge without complaint and produce a model
        # that generates plausible nonsense. Refused here rather than found at
        # evaluation, days later.
        tokenisers = {
            str(item.get("tokenizer_sha256", "")) for item in models if isinstance(item, dict)
        }
        tokenisers.discard("")
        if len(tokenisers) > 1:
            problems.append(
                ValidationError(
                    field="spec.train.params.models",
                    message=(
                        "the models declare different tokeniser hashes "
                        f"({', '.join(sorted(tokenisers))}). mergekit will merge them "
                        "without complaint and produce a model whose embeddings do "
                        "not correspond to its vocabulary."
                    ),
                    code="tokeniser_mismatch",
                )
            )

        if method in {"ties", "dare-ties", "slerp", "task-arithmetic"} and len(models) < 2:
            problems.append(
                ValidationError(
                    field="spec.train.params.models",
                    message=(
                        f"{method} combines several models and only {len(models)} was "
                        "given; use linear to apply one adapter to a base"
                    ),
                    code="insufficient_models",
                )
            )

        return problems

    # -- render ------------------------------------------------------------

    def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
        """Produce the command, environment and resources. Pure (Decision S5)."""
        configuration = self.configuration(spec)
        serialised = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
        script = (
            f'printf "%s" "${CONFIG_VARIABLE}" > {CONFIG_FILENAME} && '
            f"{self.executable} {CONFIG_FILENAME} merged --allow-crimes --out-shard-size 5B"
        )
        return JobPlan(
            command=("sh", "-c", script),
            environment={
                CONFIG_VARIABLE: serialised,
                "PYTHONHASHSEED": "0",
                "TOKENIZERS_PARALLELISM": "false",
            },
            workdir=str(workdir),
            resources=ResourceRequest(
                partition=spec.placement.partition,
                nodes=spec.placement.nodes,
                gpus_per_node=1,
            ),
            expected_artefacts=("merged/model.safetensors.index.json",),
        )

    def configuration(self, spec: RunSpec) -> dict[str, object]:
        """The mergekit configuration this specification renders to."""
        params = dict(spec.train.params)
        method = str(params.get("merge_method", spec.train.method))
        models = params.get("models") or []

        configuration: dict[str, object] = {
            "merge_method": method,
            "dtype": params.get("dtype", "bfloat16"),
            "models": [dict(sorted(item.items())) for item in models if isinstance(item, dict)],
        }
        if "base_model" in params:
            configuration["base_model"] = params["base_model"]
        if method in {"ties", "dare-ties"}:
            configuration["parameters"] = {
                "density": params.get("density", 0.5),
                "normalize": params.get("normalize", True),
            }
        return configuration

    def config_hash(self, spec: RunSpec) -> str:
        """The `merge_config_hash` of the MERGED to QUANTISED transition."""
        canonical = json.dumps(self.configuration(spec), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # -- observe -----------------------------------------------------------

    def parse_progress(self, line: str) -> ProgressEvent | None:
        """Translate one line of mergekit output into a structured event."""
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
        """Return the merged weights with their hashes. Never mutates them."""
        root = Path(workdir)
        if not root.is_dir():
            return RunArtefacts()

        artefacts = [
            ProducedArtefact(
                path=str(path.relative_to(root)).replace("\\", "/"),
                kind="weights" if path.suffix == ".safetensors" else "metadata",
                sha256=_digest(path),
                size=path.stat().st_size,
            )
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]
        return RunArtefacts(artefacts=tuple(artefacts))


def _digest(path: Path) -> str:
    """SHA-256 of a file, read in chunks and never held whole."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


driver = MergekitDriver()

__all__ = ["CAPABILITIES", "NAME", "MergekitDriver", "driver"]
