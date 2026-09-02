"""An EvalDriver for lm-evaluation-harness.

SAD 8.2 lists lm-evaluation-harness as an implementation of `draupnir.eval`.
The driver runs the tasks a suite names and reads the numbers back; it does not
know what a gate is worth. `collect_gates` maps task results onto gate
identifiers and reports the value it found, with `passed` left False -- whether
a value passes is GLEIPNIR's, reached through the judge seam, and a driver that
filled that field in would be a driver that could pass its own evaluation.

The task-to-gate mapping is the one piece of judgement this driver does hold,
and it is data rather than code for the same reason the gate thresholds are: a
jurisdiction suite measuring E4 with its own task set is a configuration
change, not a core change (SAD 10.2).

Decontamination is not optional. E6 measures evaluation-set contamination, and
a corpus containing its own evaluation measures nothing. The driver refuses to
run a suite whose specification does not name a decontamination report, because
a contamination figure nobody produced is not a contamination figure of zero.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from draupnir.interfaces.types import (
    GateOutcome,
    JobPlan,
    ProducedArtefact,
    ProgressEvent,
    ProgressKind,
    ResourceRequest,
    RunArtefacts,
    RunSpec,
    ValidationError,
)

NAME = "raun.lmeval/v1"

#: The suites this driver can run. A specification naming a suite that is not
#: here is refused before an allocation is consumed (SAD 10.3 rule 4).
CAPABILITIES = frozenset({"general-core", "cim-gbr", "cim-jurisdiction", "decontamination", "bf16"})

CONFIG_VARIABLE = "DRAUPNIR_EVAL_CONFIG"
CONFIG_FILENAME = "lmeval.json"
RESULTS_FILENAME = "results.json"

#: Which harness metric feeds which gate. Data, so a jurisdiction suite can
#: bring its own without a core change.
GATE_TASKS: dict[str, tuple[str, str]] = {
    "E1": ("mmlu", "acc_norm"),
    "E2": ("cim_jurisdiction", "acc_norm"),
    "E3": ("ifeval", "prompt_level_strict_acc"),
    "E4": ("cim_factual", "exact_match"),
    "E5": ("toxigen", "acc"),
    "E6": ("decontamination", "overlap_rate"),
}

_PROGRESS = re.compile(r"Running.*?(?P<step>\d+)/(?P<total>\d+)")
_TASK_DONE = re.compile(r"^\s*\|\s*(?P<task>[a-z0-9_]+)\s*\|.*?\|\s*(?P<value>[0-9.]+)\s*\|")
_WARNING = re.compile(r"^\s*WARNING[:\s]+(?P<message>.+)$")


@dataclass
class LmEvalDriver:
    """Runs an lm-evaluation-harness suite and reads the numbers back."""

    name: str = NAME
    capabilities: frozenset[str] = CAPABILITIES
    executable: str = "lm_eval"
    #: Overridable so a jurisdiction suite can supply its own task mapping.
    gate_tasks: dict[str, tuple[str, str]] = field(default_factory=lambda: dict(GATE_TASKS))

    # -- validate ----------------------------------------------------------

    def validate(self, spec: RunSpec) -> list[ValidationError]:
        """Return every reason this evaluation cannot be run."""
        problems: list[ValidationError] = []

        unknown = sorted(set(spec.evaluate.suites) - self.capabilities)
        if unknown:
            problems.append(
                ValidationError(
                    field="spec.evaluate.suites",
                    message=(
                        f"this driver does not implement {', '.join(unknown)}. It runs "
                        f"{', '.join(sorted(self.capabilities))}."
                    ),
                    code="unsupported_suite",
                )
            )

        unmapped = sorted(set(spec.evaluate.gates) - set(self.gate_tasks))
        if unmapped:
            problems.append(
                ValidationError(
                    field="spec.evaluate.gates",
                    message=(
                        f"no task is mapped to gate(s) {', '.join(unmapped)}, so running "
                        "this suite would produce no measurement for them. A gate "
                        "nobody measured is a gate nobody passed."
                    ),
                    code="unmapped_gate",
                )
            )

        if "E6" in spec.evaluate.gates and not spec.dataset.artefact:
            problems.append(
                ValidationError(
                    field="spec.dataset.artefact",
                    message=(
                        "E6 measures evaluation-set contamination and needs the corpus "
                        "to measure it against. A contamination figure nobody produced "
                        "is not a contamination figure of zero."
                    ),
                    code="missing_decontamination_input",
                )
            )

        if not spec.evaluate.baseline and any(
            gate in spec.evaluate.gates for gate in ("E1", "E2", "E3", "E5")
        ):
            problems.append(
                ValidationError(
                    field="spec.evaluate.baseline",
                    message=(
                        "gates E1, E2, E3 and E5 are relative and this specification "
                        "names no baseline. A relative gate with no baseline is not a "
                        "pass, it is an unknown."
                    ),
                    code="missing_baseline",
                )
            )

        return problems

    # -- render ------------------------------------------------------------

    def render(self, spec: RunSpec, workdir: Path) -> JobPlan:
        """Produce the command, environment and resources. Pure (Decision S5)."""
        configuration = self.configuration(spec)
        serialised = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
        tasks = ",".join(configuration["tasks"])  # type: ignore[arg-type]
        script = (
            f'printf "%s" "${CONFIG_VARIABLE}" > {CONFIG_FILENAME} && '
            f"{self.executable} --model hf --tasks {tasks} "
            f"--output_path {RESULTS_FILENAME} --seed 42"
        )
        return JobPlan(
            command=("sh", "-c", script),
            environment={
                CONFIG_VARIABLE: serialised,
                "PYTHONHASHSEED": "0",
                "TOKENIZERS_PARALLELISM": "false",
                # An evaluation that is not reproducible cannot support a gate:
                # a rerun that scores differently makes the margin meaningless.
                "HF_DATASETS_OFFLINE": "1",
            },
            workdir=str(workdir),
            resources=ResourceRequest(partition=spec.placement.partition, nodes=1, gpus_per_node=1),
            expected_artefacts=(RESULTS_FILENAME,),
        )

    def configuration(self, spec: RunSpec) -> dict[str, object]:
        """The harness configuration this specification renders to."""
        tasks = sorted(
            {self.gate_tasks[gate][0] for gate in spec.evaluate.gates if gate in self.gate_tasks}
        )
        return {
            "model": spec.base.artefact,
            "suites": list(spec.evaluate.suites),
            "gates": list(spec.evaluate.gates),
            "tasks": tasks,
            "baseline": spec.evaluate.baseline,
            "batch_size": spec.train.params.get("eval_batch_size", 8),
            "seed": 42,
        }

    # -- observe -----------------------------------------------------------

    def parse_progress(self, line: str) -> ProgressEvent | None:
        """Translate one line of harness output into a structured event."""
        done = _TASK_DONE.match(line)
        if done:
            return ProgressEvent(
                kind=ProgressKind.METRIC,
                value=float(done.group("value")),
                message=done.group("task"),
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
        """Return the evaluation report with its hash. Never mutates it."""
        root = Path(workdir)
        if not root.is_dir():
            return RunArtefacts()

        artefacts = tuple(
            ProducedArtefact(
                path=str(path.relative_to(root)).replace("\\", "/"),
                kind="report",
                sha256=_digest(path),
                size=path.stat().st_size,
            )
            for path in sorted(root.rglob("*"))
            if path.is_file()
        )
        return RunArtefacts(artefacts=artefacts, metrics=self.measurements(root))

    def measurements(self, workdir: Path) -> dict[str, float]:
        """The per-gate measurements, read from the harness results."""
        results = Path(workdir) / RESULTS_FILENAME
        if not results.is_file():
            return {}
        try:
            data = json.loads(results.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        per_task = data.get("results", {})
        found: dict[str, float] = {}
        for gate, (task, metric) in self.gate_tasks.items():
            entry = per_task.get(task)
            if not isinstance(entry, dict):
                continue
            value = entry.get(metric)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                found[gate] = float(value)
        return found

    def collect_gates(self, workdir: Path, suite_version: str) -> tuple[GateOutcome, ...]:
        """Return one outcome per measured gate, with `passed` left unset.

        The driver reports what it measured. Whether a measurement passes needs
        the threshold, the baseline and the comparison, all of which are
        GLEIPNIR's; a driver that filled `passed` in would be a driver that
        could pass its own evaluation.
        """
        return tuple(
            GateOutcome(
                gate=gate,
                suite_version=suite_version,
                value=value,
                baseline_value=None,
                margin=None,
                passed=False,
            )
            for gate, value in sorted(self.measurements(workdir).items())
        )


def _digest(path: Path) -> str:
    """SHA-256 of a file, read in chunks and never held whole."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


driver = LmEvalDriver()

__all__ = ["CAPABILITIES", "GATE_TASKS", "NAME", "LmEvalDriver", "driver"]
