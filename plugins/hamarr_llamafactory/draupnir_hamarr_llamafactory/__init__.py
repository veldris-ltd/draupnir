"""A TrainDriver for LLaMA-Factory.

SAD 8.2 lists LLaMA-Factory as an implementation of `draupnir.train`. The
driver knows how to turn a run specification into a LLaMA-Factory invocation
and how to read what LLaMA-Factory prints; it does not decide what to train,
which base to use, or whether the result is good enough, because those belong
to HAMARR, to the tier table and to RAUN respectively (SAD 5.2).

`render` is pure (Decision S5), which shapes the whole thing. A pure render
cannot write a configuration file, so the configuration travels in the
environment and the command writes it at the start of the job. Two renders of
one specification produce identical bytes -- including the configuration --
and that is what the conformance suite checks.

The configuration is emitted as JSON. LLaMA-Factory parses its configuration
with `yaml.safe_load`, and JSON is valid YAML, so this costs nothing and buys
a deterministic serialisation with no dependency on a YAML library's
formatting choices.

The chat template is resolved from a versioned map and there is no default.
See `templates`, which explains why that refusal is the most important line in
this package.
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
from draupnir_hamarr_llamafactory.templates import (
    CURRENT_VERSION,
    UnknownBaseModelError,
    by_version,
)

#: The versioned entry point name. Must match the key in pyproject.toml.
NAME = "hamarr.llamafactory/v1"

#: The methods and precisions this driver can actually run. The core refuses a
#: specification that asks for anything not in here, before an allocation is
#: consumed (SAD 10.3 rule 4).
CAPABILITIES = frozenset({"lora", "qlora", "full", "moe", "multinode", "bf16", "fp16"})

#: Where the rendered configuration is handed to the job. Named rather than
#: written, because `render` is pure and may not touch the filesystem.
CONFIG_VARIABLE = "DRAUPNIR_TRAIN_CONFIG"

#: The configuration file the job writes and then reads.
CONFIG_FILENAME = "llamafactory.json"

# -- progress patterns -------------------------------------------------------
#
# These are the only regular expressions in the system that look at executor
# output, and they live here on purpose: an upgrade that changes LLaMA-Factory's
# log format is a version bump of this plug-in, not a change to the control
# plane. Everything downstream sees `ProgressEvent`s.

_STEP = re.compile(r"\{'loss':\s*(?P<loss>[0-9.eE+-]+).*?'epoch':\s*(?P<epoch>[0-9.]+)\}")
_PROGRESS = re.compile(r"(?P<step>\d+)/(?P<total>\d+)\s*\[")
_CHECKPOINT = re.compile(r"Saving model checkpoint to (?P<path>\S+)")
_METRIC = re.compile(r"'(?P<name>eval_[a-z_]+)':\s*(?P<value>[0-9.eE+-]+)")
_WARNING = re.compile(r"^\s*(?:WARNING|\[WARNING\]|warnings\.warn)[:\s]+(?P<message>.+)$")


@dataclass
class LlamaFactoryDriver:
    """Turns a run specification into a LLaMA-Factory training job."""

    name: str = NAME
    capabilities: frozenset[str] = CAPABILITIES
    #: Which template map to resolve against. Pinned per run so a replay
    #: resolves the template the original run used (SAD 10.1).
    template_version: str = CURRENT_VERSION
    #: The entry point of the trainer itself.
    executable: str = "llamafactory-cli"

    # -- validate ----------------------------------------------------------

    def validate(self, spec: RunSpec) -> list[ValidationError]:
        """Return every reason this specification cannot be run, not the first."""
        problems: list[ValidationError] = []

        if spec.train.method not in {"lora", "qlora", "full", "moe"}:
            problems.append(
                ValidationError(
                    field="spec.train.method",
                    message=(
                        f"{spec.train.method!r} is not a method this driver implements. "
                        "It runs lora, qlora, full and moe."
                    ),
                    code="unsupported_method",
                )
            )

        if spec.train.precision not in {"bf16", "fp16"}:
            problems.append(
                ValidationError(
                    field="spec.train.precision",
                    message=f"{spec.train.precision!r} is not supported; use bf16 or fp16.",
                    code="unsupported_precision",
                )
            )

        base = base_model_of(spec)
        try:
            by_version(self.template_version).resolve(base)
        except UnknownBaseModelError as unknown:
            problems.append(
                ValidationError(
                    field="spec.base.artefact",
                    message=str(unknown),
                    code="unknown_base_model",
                )
            )

        if "save_steps" not in spec.train.params:
            problems.append(
                ValidationError(
                    field="spec.train.params.save_steps",
                    message=(
                        "the checkpoint interval is missing. It is derived by HAMARR "
                        "from observed step time so that no more than thirty minutes "
                        "of work is ever unwritten, and a driver that chose its own "
                        "would put that budget in a plug-in. Call "
                        "draupnir.hamarr.config.prepare before rendering."
                    ),
                    code="missing_checkpoint_interval",
                )
            )

        if spec.placement.nodes > 1 and spec.train.method == "qlora":
            problems.append(
                ValidationError(
                    field="spec.train.method",
                    message=(
                        "qlora across more than one node is not supported by "
                        "LLaMA-Factory; the quantised base cannot be sharded."
                    ),
                    code="unsupported_combination",
                )
            )

        return problems

    # -- render ------------------------------------------------------------

    def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
        """Produce the command, environment and resources. Pure (Decision S5).

        Reads nothing, writes nothing, and consults no clock. The chat template
        resolution can raise here, and should: rendering a job against an
        unknown base is the failure this driver exists to make loud.
        """
        configuration = self.configuration(spec)
        serialised = json.dumps(configuration, sort_keys=True, separators=(",", ":"))

        # A pure render cannot write the configuration file, so the job writes
        # it. `printf %s` rather than `echo`, which mangles backslashes.
        script = (
            f'printf "%s" "${CONFIG_VARIABLE}" > {CONFIG_FILENAME} && '
            f"{self.executable} train {CONFIG_FILENAME}"
        )

        return JobPlan(
            command=("sh", "-c", script),
            environment={
                CONFIG_VARIABLE: serialised,
                "DRAUPNIR_TEMPLATE_MAP": self.template_version,
                # Determinism is not decoration for a run whose specification
                # is its identity: a rerun that cannot reproduce the loss curve
                # cannot be said to have reproduced the run (SAD 6.2).
                "PYTHONHASHSEED": "0",
                "TOKENIZERS_PARALLELISM": "false",
            },
            workdir=str(workdir),
            resources=ResourceRequest(
                partition=spec.placement.partition,
                nodes=spec.placement.nodes,
                gpus_per_node=1,
            ),
            expected_artefacts=("adapter_model.safetensors", "trainer_state.json"),
        )

    def configuration(self, spec: RunSpec) -> dict[str, object]:
        """The LLaMA-Factory configuration this specification renders to.

        Separate from `render` so that an operator can be shown exactly what
        will run before it runs, which the UX specification's submission
        preview needs, and so that a test can assert on the configuration
        without unpicking a shell command.
        """
        base = base_model_of(spec)
        template = by_version(self.template_version).resolve(base)
        params = dict(spec.train.params)

        configuration: dict[str, object] = {
            "stage": "sft",
            "do_train": True,
            "model_name_or_path": base,
            "template": template,
            "dataset": spec.dataset.artefact,
            "finetuning_type": "full" if spec.train.method == "full" else "lora",
            "output_dir": f"output/{spec.metadata.name}",
            "overwrite_output_dir": False,
            "save_steps": params["save_steps"],
            "save_total_limit": params.get("save_total_limit", 3),
            "logging_steps": params.get("logging_steps", 10),
            "seed": params.get("seed", 42),
            "bf16": spec.train.precision == "bf16",
            "fp16": spec.train.precision == "fp16",
            "ddp_timeout": 180000000,
        }

        if spec.train.method == "qlora":
            configuration["quantization_bit"] = params.get("quantization_bit", 4)
        if spec.train.method in {"lora", "qlora"}:
            configuration["lora_rank"] = params.get("lora_rank", 16)
            configuration["lora_alpha"] = params.get("lora_alpha", 32)
            configuration["lora_target"] = params.get("lora_target", "all")
        if spec.train.method == "moe":
            # A mixture of experts base trains its router in fp32 whatever the
            # rest is in; letting it follow `precision` produces a router that
            # collapses onto one expert.
            configuration["moe_aux_loss_coef"] = params.get("moe_aux_loss_coef", 0.001)
            configuration["router_dtype"] = "float32"

        for key in sorted(params):
            configuration.setdefault(key, params[key])

        return configuration

    # -- observe -----------------------------------------------------------

    def parse_progress(self, line: str) -> ProgressEvent | None:
        """Translate one line of LLaMA-Factory output into a structured event.

        Pure, and carries no timestamp: replaying a captured log yields the
        events it yielded live. Returns `None` for a line that says nothing,
        which is most of them.
        """
        checkpoint = _CHECKPOINT.search(line)
        if checkpoint:
            step = _PROGRESS.search(line)
            return ProgressEvent(
                kind=ProgressKind.CHECKPOINT,
                step=int(step.group("step")) if step else None,
                message=checkpoint.group("path"),
            )

        metric = _METRIC.search(line)
        if metric:
            return ProgressEvent(
                kind=ProgressKind.METRIC,
                value=float(metric.group("value")),
                message=metric.group("name"),
            )

        loss = _STEP.search(line)
        if loss:
            progress = _PROGRESS.search(line)
            return ProgressEvent(
                kind=ProgressKind.LOSS,
                step=int(progress.group("step")) if progress else None,
                total=int(progress.group("total")) if progress else None,
                value=float(loss.group("loss")),
            )

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
        """Return what the run produced, with hashes. Never mutates anything.

        Opened read-only and hashed in chunks; a 54 GB checkpoint does not fit
        in memory, and a `read_bytes` here would take the control plane down
        rather than the job.
        """
        artefacts: list[ProducedArtefact] = []
        metrics: dict[str, float] = {}

        root = Path(workdir)
        if not root.is_dir():
            return RunArtefacts()

        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            artefacts.append(
                ProducedArtefact(
                    path=str(path.relative_to(root)).replace("\\", "/"),
                    kind=_kind_of(path),
                    sha256=_digest(path),
                    size=path.stat().st_size,
                )
            )

        state = root / "trainer_state.json"
        if state.is_file():
            metrics = _metrics_from_state(state)

        return RunArtefacts(artefacts=tuple(artefacts), metrics=metrics)


def base_model_of(spec: RunSpec) -> str:
    """The base model name a specification names.

    A specification addresses its base by `hodd://` URI (SAD 7.4), and the
    template map is keyed by model name, which is the last segment.
    """
    artefact = spec.base.artefact
    return artefact.rstrip("/").rsplit("/", 1)[-1] if "/" in artefact else artefact


def _kind_of(path: Path) -> str:
    """What sort of artefact a produced file is, by its name."""
    name = path.name
    if name.endswith(".safetensors"):
        return "adapter" if "adapter" in name else "weights"
    if name.endswith(".json"):
        return "metadata"
    if name.endswith((".log", ".out")):
        return "log"
    return "other"


def _digest(path: Path) -> str:
    """SHA-256 of a file, read in chunks and never held whole."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metrics_from_state(path: Path) -> dict[str, float]:
    """Read the final metrics out of LLaMA-Factory's trainer state."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    history = state.get("log_history") or []
    metrics: dict[str, float] = {}
    for entry in history:
        if not isinstance(entry, dict):
            continue
        for key, value in entry.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                metrics[key] = float(value)
    return metrics


#: The object the entry point resolves to.
driver = LlamaFactoryDriver()

__all__ = [
    "CAPABILITIES",
    "CONFIG_FILENAME",
    "CONFIG_VARIABLE",
    "NAME",
    "LlamaFactoryDriver",
    "base_model_of",
    "driver",
]
