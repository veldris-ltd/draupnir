"""HODD records licence facts. It must not interpret them.

SAD Decision S4, and an explicit requirement of this prompt: "A code review
that finds licence logic in `hodd/` fails this task." A requirement that
depends on somebody noticing during review is a requirement that holds until
the first busy week, so it is checked here instead.

Three things are checked, in increasing order of how easily they would be got
wrong:

  * `draupnir.hodd` does not import GLEIPNIR, or the vocabulary a judgement is
    expressed in. The module layering already forbids the first; this says so
    at the point somebody would try.
  * No module under `hodd/` contains an SPDX licence identifier outside a
    docstring. An allow list is the shape licence logic actually arrives in,
    and one constant is all it takes.
  * The reverse direction: GLEIPNIR does not import HODD either. It judges
    facts it is handed, and a policy that could read the register could
    accumulate state in it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

HODD = Path(__file__).resolve().parents[2] / "draupnir" / "hodd"
GLEIPNIR = Path(__file__).resolve().parents[2] / "draupnir" / "gleipnir"

#: Importing any of these into HODD means a judgement is being formed there.
FORBIDDEN_IN_HODD = {
    "draupnir.gleipnir",
    "Verdict",
    "PolicyDecision",
    "Policy",
    "Rule",
    "PolicyEngine",
    "Assessment",
}

#: An SPDX identifier in code -- as opposed to in prose -- is an allow list, a
#: deny list, or a comparison. All three are judgements.
SPDX_PREFIXES = (
    "CC-BY",
    "CC0-",
    "GPL-",
    "AGPL-",
    "LGPL-",
    "MPL-",
    "Apache-",
    "BSD-",
    "OGL-",
    "ODbL-",
    "LicenseRef-",
)


def modules(package: Path) -> list[Path]:
    return sorted(package.rglob("*.py"))


def docstring_nodes(tree: ast.AST) -> set[int]:
    """Every string node that is a docstring, by identity.

    Prose may name a licence: `register.py` explains why `CC-BY-SA-4.0` means
    different things under different policies, and that sentence is the
    opposite of licence logic.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("module", modules(HODD), ids=lambda path: path.name)
def test_hodd_imports_no_judgement(module: Path) -> None:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    offending = sorted(imported_names(tree) & FORBIDDEN_IN_HODD)
    assert not offending, (
        f"{module.name} imports {', '.join(offending)}. HODD records licence facts "
        "and must not interpret them; GLEIPNIR judges (SAD Decision S4)."
    )


@pytest.mark.parametrize("module", modules(HODD), ids=lambda path: path.name)
def test_hodd_contains_no_licence_identifier_in_code(module: Path) -> None:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    docstrings = docstring_nodes(tree)

    offending = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and node.value.startswith(SPDX_PREFIXES)
    ]
    assert not offending, (
        f"{module.name} names licences in code: {offending}. An allow list, a deny "
        "list or a comparison is a judgement, and it belongs in GLEIPNIR. HODD "
        "records the identifier a curator established and nothing more."
    )


@pytest.mark.parametrize("module", modules(GLEIPNIR), ids=lambda path: path.name)
def test_gleipnir_does_not_read_the_register(module: Path) -> None:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    offending = sorted(name for name in imported_names(tree) if name.startswith("draupnir.hodd"))
    assert not offending, (
        f"{module.name} imports {', '.join(offending)}. GLEIPNIR judges facts it is "
        "handed; a policy able to read the register could accumulate judgements "
        "there, which is what Decision S4 separates."
    )


def test_the_facts_interface_is_the_only_thing_that_crosses() -> None:
    """The one supported route from recording to judging, end to end.

    HODD renders records as mappings; GLEIPNIR consumes mappings. Neither
    imports the other, and this is what that costs: one function on each side.
    """
    from datetime import UTC, datetime
    from uuid import uuid4

    from draupnir.gleipnir.licence import CURRENT
    from draupnir.gleipnir.policy import PolicyEngine
    from draupnir.hodd.register import LicenceRegister, SourceRecord

    register = LicenceRegister()
    register.record(
        SourceRecord(
            id=uuid4(),
            jurisdiction="GBR",
            url="https://www.legislation.gov.uk/ukpga",
            licence_spdx="OGL-UK-3.0",
            attribution_required=True,
            retrieved_at=datetime(2026, 3, 2, tzinfo=UTC),
            sha256="a" * 64,
            personal_data=False,
        )
    )

    assessments = PolicyEngine(CURRENT).reassess(register.facts_for_policy())
    assert len(assessments) == 1
    assert assessments[0].permitted
    # The register learned nothing from the judgement.
    assert next(iter(register)).state.name == "DRAFT"
