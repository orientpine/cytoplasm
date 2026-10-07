"""RCB todo 18: the repair-report runtime declaration equals the consumer's import closure.

The no-agent consumer imports only from its agent-owned runtime root, so every `automation.`
module the consumer reaches must be shipped there. The declaration in
`automation/repair/deploy-manifest.txt` names those files; this follows the imports with `ast`,
so an import nobody ships fails here instead of on the node. A new file because the repair
consumer tests are FS3-pinned.
"""
from __future__ import annotations

import ast
import shlex
import shutil
from pathlib import Path, PurePosixPath
from typing import Final

from automation.deploy_declarations import Declaration, all_declarations

_REPO: Final = Path(__file__).resolve().parents[2]
_DESTINATION: Final = ".hermes/repair-report-runtime/.current"
_DEPLOYER: Final = "automation/repair/deploy.sh"
_START: Final = "repair/repair_report_consumer.py"
_TABLE: Final = "configs/runtime-package-manifest.txt"


def _module_file(root: Path, dotted: str) -> str | None:
    """`automation.x.y` -> `x/y.py` or `x/y/__init__.py` relative to `root/automation`."""
    parts = dotted.split(".")[1:]
    if not parts:
        return "__init__.py"
    relative = "/".join(parts)
    for candidate in (f"{relative}.py", f"{relative}/__init__.py"):
        if (root / "automation" / candidate).is_file():
            return candidate
    return None


def _referenced(tree: ast.AST, root: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
            package = _module_file(root, node.module)
            if package is None or package.endswith("__init__.py"):
                # `from automation.pkg import name`: name may be a submodule of pkg.
                names += [f"{node.module}.{alias.name}" for alias in node.names]
        else:
            continue
        for name in names:
            if name != "automation" and not name.startswith("automation."):
                continue
            resolved = _module_file(root, name)
            if resolved is None and name.count(".") > 1:
                # An unresolvable submodule is still a file the runtime would need.
                resolved = name.split(".", 1)[1].replace(".", "/") + ".py"
            if resolved is not None:
                found.add(resolved)
    return found


def import_closure(root: Path, start: str = _START) -> frozenset[str]:
    """Files under `root/automation` the start module needs, package `__init__.py` included."""
    closure: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in closure:
            continue
        closure.add(current)
        for parent in PurePosixPath(current).parents:
            text = parent.as_posix()
            pending.append("__init__.py" if text == "." else f"{text}/__init__.py")
        path = root / "automation" / current
        if path.is_file():
            pending.extend(_referenced(ast.parse(path.read_text(encoding="utf-8")), root))
    return frozenset(closure)


def _declaration() -> Declaration:
    rows = [d for d in all_declarations(_REPO) if d.destination == _DESTINATION]
    assert len(rows) == 1, rows
    row = rows[0]
    assert (row.account, row.source, row.kind, row.policy) == (
        "agent", "automation", "tree", "required",
    )
    assert row.attr("prefix") == "automation"
    return row


def _declared_files() -> frozenset[str]:
    return frozenset(_declaration().attr("files").split(","))


def _swap_calls(text: str) -> list[list[str]]:
    """Argument lists of every `deploy_tree_swap` call (continuations joined, comments dropped)."""
    calls: list[list[str]] = []
    for line in text.replace("\\\n", " ").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped.startswith("deploy_tree_swap "):
            continue
        tokens: list[str] = []
        for token in shlex.split(stripped)[1:]:
            if token in ("||", "&&", ";", "|"):
                break
            tokens.append(token)
        calls.append(tokens)
    return calls


def _split_options(args: list[str]) -> tuple[list[str], list[str]]:
    position = 0
    while position < len(args) and args[position].startswith("--"):
        position += 1 if args[position] == "--link" else 2
    return args[:position], args[position:]


def test_declared_files_equal_the_import_closure() -> None:
    declared = _declared_files()
    assert import_closure(_REPO) == declared
    calls = _swap_calls((_REPO / _DEPLOYER).read_text(encoding="utf-8"))
    assert len(calls) == 1, calls
    options, positional = _split_options(calls[0])
    assert options == ["--link", "--prefix", "automation"]
    assert positional[1] == _DESTINATION
    assert frozenset(positional[2:]) == declared and len(positional[2:]) == len(declared)


def test_an_unshipped_import_breaks_the_build(tmp_path: Path) -> None:
    declared = _declared_files()
    for name in declared:
        target = tmp_path / "automation" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copyfile(_REPO / "automation" / name, target)
    assert import_closure(tmp_path) == declared
    consumer = tmp_path / "automation" / _START
    _ = consumer.write_text(
        consumer.read_text(encoding="utf-8")
        + "\nfrom automation.repair import repair_private_log\n",
        encoding="utf-8",
    )
    closure = import_closure(tmp_path)
    assert closure != declared
    assert closure - declared == {"repair/repair_private_log.py"}


def _watched_runtimes() -> list[str]:
    runtimes: list[str] = []
    for line in (_REPO / _TABLE).read_text(encoding="utf-8").splitlines():
        fields = line.split("|")
        if line.startswith("#") or len(fields) < 3 or not fields[2]:
            continue
        runtimes.append(fields[2])
    assert runtimes
    return runtimes


def test_link_destinations_are_not_watched_by_the_standing_probe() -> None:
    destinations: list[str] = []
    scripts = sorted(_REPO.glob("automation/*/deploy*.sh")) + sorted(_REPO.glob("skills/*/deploy*.sh"))
    for script in scripts:
        for args in _swap_calls(script.read_text(encoding="utf-8")):
            options, positional = _split_options(args)
            if "--link" in options:
                assert len(positional) >= 2 and "$" not in positional[1], (script, args)
                destinations.append(positional[1])
    assert _DESTINATION in destinations
    overlapping = [
        (destination, runtime)
        for destination in destinations
        for runtime in _watched_runtimes()
        if f"{runtime}/".startswith(f"{destination}/") or f"{destination}/".startswith(f"{runtime}/")
    ]
    assert not overlapping, overlapping
