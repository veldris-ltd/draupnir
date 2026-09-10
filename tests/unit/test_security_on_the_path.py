"""The cross-cutting security controls, on the paths they exist for. RF-09.

Five SVALINN modules had good tests and no callers outside them. The README's
claims — "the job environment carries lease references, so there is no point at
which the control plane could write a secret into a job environment file";
"every outbound call declares a destination, a purpose, a run and an approving
policy, and an undeclared destination fails" — were true of the libraries and
true of nothing that runs.

A security module nobody calls is worse than an absent one, because it reads as
coverage. These are the tests that would have failed then, and two of them are
structural: they enumerate the call sites rather than asserting about them, so
they keep being true of code written after them.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from draupnir.interfaces.types import JobHandle, JobPlan
from draupnir.motsognir import execution
from draupnir.svalinn import secrets

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Secrets: nothing leaves carrying one
# ---------------------------------------------------------------------------


def _broker() -> secrets.SecretsBroker:
    return secrets.SecretsBroker(store={"HF_TOKEN": "hf_a-real-looking-token-value"})


def test_a_plan_carrying_a_secret_value_is_refused_at_submission() -> None:
    """Threat T6, at the one place every plan passes through.

    The check is on the rendered plan rather than on its environment mapping,
    because a secret interpolated into a command string is the case a
    values-only check misses and the one that actually happens -- so this puts
    it in the command.
    """
    broker = _broker()
    plan = execution.stand_in_plan(
        Path("out.bin"),
        [Path("in.bin")],
        workdir=Path("work"),
        environment={"HF_TOKEN": "hf_a-real-looking-token-value"},
    )

    with pytest.raises(secrets.SecretMaterialisedError) as refusal:
        execution.submit(_NeverCalled(), plan, leak_check=broker)  # type: ignore[arg-type]

    assert "HF_TOKEN" in str(refusal.value), "the refusal does not name the variable"


def test_a_secret_hidden_in_a_command_string_is_caught_too() -> None:
    """The values-only check misses this one, and it is the one that happens."""
    broker = _broker()
    plan = execution.stand_in_plan(Path("out.bin"), [Path("in.bin")], workdir=Path("work"))
    poisoned = JobPlan(
        command=(*plan.command, "--token=hf_a-real-looking-token-value"),
        environment=plan.environment,
        workdir=plan.workdir,
        resources=plan.resources,
        expected_artefacts=plan.expected_artefacts,
        sandbox=plan.sandbox,
    )

    with pytest.raises(secrets.SecretMaterialisedError):
        execution.submit(_NeverCalled(), poisoned, leak_check=broker)  # type: ignore[arg-type]


def test_a_plan_carrying_lease_references_is_submitted() -> None:
    """The other half. The environment a job gets is references, never values.

    There is no code path by which a value could be written into one:
    `brokered_environment` is the only thing that builds a job environment, and
    it reads `lease.reference`, which is the handle. It never touches
    `reveal`.
    """
    broker = _broker()
    lease = broker.issue("HF_TOKEN", run_id="run-1", now=NOW)
    environment = secrets.brokered_environment([lease])

    assert environment == {"DRAUPNIR_LEASE_HF_TOKEN": lease.reference}
    assert "hf_a-real-looking-token-value" not in repr(environment)

    plan = execution.stand_in_plan(
        Path("out.bin"), [Path("in.bin")], workdir=Path("work"), environment=environment
    )
    handle = execution.submit(_Accepting(), plan, leak_check=broker)  # type: ignore[arg-type]

    assert handle.job_id == "1"
    assert plan.environment["PYTHONHASHSEED"] == "0", (
        "supplying leases dropped the determinism settings SAD 6.2 depends on"
    )


def test_the_worker_submits_through_its_broker() -> None:
    """Composed, not merely available.

    `Context.secrets` reaches `execution.submit` as its leak check. Without
    that the broker is a decision procedure nobody is obliged to consult, which
    is the shape RF-09 found five times over.
    """
    import inspect

    from draupnir.worker import stages

    source = inspect.getsource(stages)
    submissions = source.count("execution.submit(") + source.count("execution.dispatch(")
    checked = source.count("leak_check=context.secrets")

    assert submissions == checked, (
        f"{submissions} submission(s) in the worker and {checked} carry a leak check"
    )


class _NeverCalled:
    """A scheduler the refusal must reach first."""

    def submit(self, plan: Any) -> Any:
        raise AssertionError("a plan carrying a secret reached the scheduler")


class _Accepting:
    """A scheduler that takes anything."""

    def submit(self, plan: Any) -> Any:
        del plan
        return JobHandle(driver="local", job_id="1")


# ---------------------------------------------------------------------------
# Egress: every outbound call site is decided
# ---------------------------------------------------------------------------

#: How an HTTP client is constructed. Anything matching is a way out of this
#: process, and each one has to be decided by the broker before it is used.
_CLIENTS = {"httpx.Client", "httpx.AsyncClient", "urllib.request.urlopen", "requests.request"}

#: What counts as having been decided: the call is wrapped in the obliging
#: client, or the broker is consulted in the same function.
_DECIDERS = {"BrokeredClient", "EgressBroker"}


def _dotted(node: ast.AST) -> str:
    """The dotted name of a call target, as written."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _undecided(path: Path) -> list[str]:
    """Every HTTP client this file builds without consulting the broker."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []

    for scope in ast.walk(tree):
        if not isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        names = {_dotted(node.func) for node in ast.walk(scope) if isinstance(node, ast.Call)}
        # The last segment, so `egress.EgressBroker()` and a bare
        # `EgressBroker()` count the same. Whether the module was imported
        # whole or by name is not a security property.
        tails = {name.rsplit(".", 1)[-1] for name in names}
        built = names & _CLIENTS
        if built and not (tails & _DECIDERS):
            found.append(f"{path.as_posix()}:{scope.name} builds {', '.join(sorted(built))}")
    return found


def test_every_outbound_call_site_goes_through_the_allow_list() -> None:
    """Enumerated rather than asserted, which is what the criterion asks.

    An assertion that "outbound calls are brokered" is a statement about the
    author's intention at the time. This walks the source and finds the ones
    that are not, so a client added next year fails here rather than being
    discovered in a packet capture.

    Threat T11 is egress no allow list decided. The two that were undecided
    when this was written: the OIDC token exchange, which carries an
    authorisation code and sometimes a client secret, and -- had RF-07 not
    added `post` to the brokered client -- the anchor submission.
    """
    offenders: list[str] = []
    for path in sorted(Path("draupnir").rglob("*.py")):
        # The broker itself builds nothing; it is what everything else asks.
        if path.as_posix() == "draupnir/svalinn/egress.py":
            continue
        offenders.extend(_undecided(path))

    assert not offenders, (
        "outbound HTTP is built without the broker being consulted:\n"
        + "\n".join(offenders)
        + "\nEvery call declares a destination, a purpose, a run and an approving "
        "policy, and an undeclared destination fails (threat T11, AC-S11)."
    )


def test_the_teacher_destination_is_absent_and_stays_absent() -> None:
    """AC-S3, and the reason the list is evidence rather than configuration.

    Distillation from a hosted teacher is threat T3: the corpus leaves the
    estate one prompt at a time and no gate sees it. The control is that no
    such destination is on the list -- which is a claim about a file, and a
    claim about a file is a claim a test can hold.
    """
    from draupnir.svalinn.egress import allow_listed_hosts

    hosts = allow_listed_hosts()
    teachers = [
        item
        for item in hosts
        if any(
            word in item
            for word in ("openai", "anthropic", "generativelanguage", "cohere", "mistral.ai")
        )
    ]

    assert teachers == [], (
        f"{', '.join(teachers)} is on the egress allow list. A hosted teacher "
        "destination is threat T3: the corpus leaves one prompt at a time and no "
        "gate sees it (AC-S3)."
    )
