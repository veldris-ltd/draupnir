"""Which modules a running deployment can reach, from its entry points. RF-30.

The reconciliation's IMPLEMENTED means "built, and exercised by something that
runs in the pipeline", and a unit test satisfies that. So a module can be
IMPLEMENTED and still sit on no path a request, a worker tick or a deployed
command takes -- which is what the remedial register's section 2 found for the
platform's security, federation, publication and array controls. This answers
the question that mark cannot.

Derived rather than judged, and static. A module is reachable when it is
imported, directly or through other modules, from something a deployment runs:

- the API application, and `draupnir.api.serve`, which serves it over mTLS and
  is the API image's command (`docker/api.Dockerfile`, RF-37);
- the approver's signing agent, `python -m draupnir.gleipnir.signing_agent`,
  which runs on the approver's own machine rather than on the forge (RF-40);
- the worker, run as `python -m draupnir.worker`;
- `draupnirctl`, the console script `pyproject.toml` declares;
- every installed plug-in, through the entry point its `pyproject.toml`
  declares -- `draupnir.core.plugins` loads them by group, so they are roots
  rather than imports anyone could find;
- the migrations, which `install.sh` runs.

It over-approximates in one direction and says so. An import inside a function
counts, whether or not anything calls the function, so a module reached only
through a function nobody calls is still reported reachable. It never
under-reports: an import under `if TYPE_CHECKING:` is not executed and is not
counted, and nothing else is excluded. So NOT REACHABLE is a finding, and
REACHABLE is a necessary condition rather than proof of use.

    python -m scripts.reachability            # every module nothing reaches
    python -m scripts.reachability MODULE ... # how each named module is reached
"""

from __future__ import annotations

import argparse
import ast
import tomllib
from collections import deque
from collections.abc import Iterable, Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: What a deployment runs, other than plug-ins and migrations.
ENTRY_POINTS = (
    "draupnir.api.app",
    "draupnir.api.serve",
    "draupnir.gleipnir.signing_agent",
    "draupnir.worker.__main__",
    "draupnirctl.__main__",
)

#: Where a root was found, for the explanation of how a module is reached.
MIGRATIONS = "migrations"
PLUGIN = "an installed plug-in"
ENTRY = "an entry point"


def _module_name(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _plugin_packages() -> dict[str, Path]:
    """Each plug-in's importable package, from the entry point it declares."""
    packages: dict[str, Path] = {}
    for project in sorted((ROOT / "plugins").glob("*/pyproject.toml")):
        declared = tomllib.loads(project.read_text(encoding="utf-8"))
        groups = declared.get("project", {}).get("entry-points", {})
        for group in groups.values():
            for target in group.values():
                package = target.split(":", 1)[0].split(".", 1)[0]
                packages[package] = project.parent
    return packages


def modules() -> dict[str, Path]:
    """Every module a deployment could import: the platform, the CLI and plug-ins."""
    found: dict[str, Path] = {}
    for top in ("draupnir", "draupnirctl"):
        for path in (ROOT / top).rglob("*.py"):
            if "__pycache__" not in path.parts:
                found[_module_name(path.relative_to(ROOT))] = path
    for package, project in _plugin_packages().items():
        for path in (project / package).rglob("*.py"):
            if "__pycache__" not in path.parts:
                found[_module_name(path.relative_to(project))] = path
    return found


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _executed(tree: ast.AST) -> Iterator[ast.AST]:
    """Every node, except the bodies of `if TYPE_CHECKING:`, which never run."""
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            stack.extend(node.orelse)
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def imported_names(path: Path, module: str) -> set[str]:
    """What `path` imports, as dotted names, with relative imports resolved.

    `from a.b import c` yields both `a.b` and `a.b.c`, because `c` may be a
    submodule rather than an attribute; resolution against the module table
    decides which.
    """
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    names: set[str] = set()
    for node in _executed(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                anchor = ".".join(parts[: len(parts) - (node.level - 1)])
                base = f"{anchor}.{base}" if base else anchor
            names.add(base)
            names |= {f"{base}.{alias.name}" for alias in node.names}
    return names


def _with_parents(name: str, table: dict[str, Path]) -> Iterator[str]:
    """`name` and every package above it: importing `a.b.c` runs `a` and `a.b`."""
    parts = name.split(".")
    for end in range(1, len(parts) + 1):
        candidate = ".".join(parts[:end])
        if candidate in table:
            yield candidate


def _roots(table: dict[str, Path]) -> Iterator[tuple[str, str]]:
    for entry in ENTRY_POINTS:
        yield entry, ENTRY
    for package in _plugin_packages():
        yield package, PLUGIN
    for path in sorted((ROOT / MIGRATIONS).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for name in imported_names(path, _module_name(path.relative_to(ROOT))):
            if name in table:
                yield name, MIGRATIONS


def reachable() -> dict[str, str]:
    """Every reachable module, mapped to what it was first reached from."""
    table = modules()
    via: dict[str, str] = {}
    queue: deque[str] = deque()

    def reach(names: Iterable[str], source: str) -> None:
        for name in names:
            for module in _with_parents(name, table):
                if module not in via:
                    via[module] = source
                    queue.append(module)

    for root, source in _roots(table):
        reach([root], source)
    while queue:
        module = queue.popleft()
        reach(imported_names(table[module], module), module)
    return via


def route(module: str, via: dict[str, str]) -> list[str]:
    """How `module` is reached, from its root down to it."""
    steps = [module]
    while steps[-1] in via and via[steps[-1]] in via:
        steps.append(via[steps[-1]])
    steps.append(via.get(steps[-1], "nothing"))
    return list(reversed(steps))


def unreachable() -> list[str]:
    """Every platform module nothing a deployment runs reaches."""
    via = reachable()
    return sorted(name for name in modules() if name.startswith("draupnir.") and name not in via)


def main(argv: list[str] | None = None) -> int:
    """List what nothing reaches, or explain how each named module is reached."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("module", nargs="*", help="explain how these modules are reached")
    arguments = parser.parse_args(argv)

    if not arguments.module:
        for name in unreachable():
            print(name)
        return 0

    via = reachable()
    for name in arguments.module:
        if name in via:
            print(f"{name}: REACHABLE, {' -> '.join(route(name, via))}")
        else:
            print(f"{name}: NOT REACHABLE")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
