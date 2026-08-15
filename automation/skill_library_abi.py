"""Can a mounted skill snapshot still CALL the shared library it imports?

A live skill is a frozen snapshot that imports ``automation.interop.*`` from the
ops checkout at runtime. When the library moves under it — every ``deploy-skill.sh``
starts with an ops ``git pull`` — a call the snapshot froze against an older
signature raises ``TypeError`` the moment its binding helper runs (AS-3.2 removed
``DiscordChannelDirectory(approval_env_var=...)``; three live skills still passed
it). The break is inside a function, so importing the file never surfaces it.

This walks the snapshot's AST, resolves each ``_repo_module("x").Symbol(...)`` call
to ``automation.interop.x.Symbol`` in the CURRENT library, and binds the call's
keyword names to that signature. A checker that cries wolf gets switched off, so
anything it cannot judge statically is SKIPPED, never reported as a violation.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

_INTEROP_PACKAGE: Final = "automation.interop"
_REPO_MODULE_HELPERS: Final = frozenset({"_repo_module", "repo_module"})


class SkipReason(StrEnum):
    """Why a call site could not be judged — each is a deliberate non-verdict."""

    DYNAMIC_KWARGS = "dynamic-kwargs"  # **payload / *args: keyword names unknown
    UNKNOWN_SYMBOL = "unknown-symbol"  # symbol absent from the current library module
    LIBRARY_UNIMPORTABLE = "library-unimportable"  # module failed to import — never a break


@dataclass(frozen=True, slots=True)
class Violation:
    snapshot: Path
    qualname: str
    symbol: str
    detail: str


@dataclass(frozen=True, slots=True)
class Skip:
    snapshot: Path
    qualname: str
    symbol: str
    reason: SkipReason


@dataclass(frozen=True, slots=True)
class AbiReport:
    violations: tuple[Violation, ...]
    skipped: tuple[Skip, ...]


def check_snapshot(snapshot_root: Path, library_root: Path) -> AbiReport:
    """Judge every ``scripts/*.py`` in one snapshot against the current library."""
    del library_root  # signatures come from the imported library, not a path scan
    scripts = snapshot_root / "scripts"
    violations: list[Violation] = []
    skipped: list[Skip] = []
    if not scripts.is_dir():
        return AbiReport((), ())
    for path in sorted(scripts.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        _check_tree(tree, path, violations, skipped)
    return AbiReport(tuple(violations), tuple(skipped))


def _check_tree(
    tree: ast.Module, snapshot: Path, violations: list[Violation], skipped: list[Skip]
) -> None:
    bindings = _repo_module_bindings(tree)
    for parent, node in _walk_with_parents(tree):
        if not isinstance(node, ast.Call):
            continue
        resolved = _resolve_library_symbol(node.func, bindings)
        if resolved is None:
            continue
        module_name, symbol = resolved
        where = snapshot / _qualname_of(parent)
        signature = _library_signature(module_name, symbol)
        if signature is None:
            skipped.append(Skip(snapshot, _qualname_of(parent), symbol, SkipReason.LIBRARY_UNIMPORTABLE))
            continue
        if signature is _MISSING:
            skipped.append(Skip(snapshot, _qualname_of(parent), symbol, SkipReason.UNKNOWN_SYMBOL))
            continue
        _bind_call(node, signature, snapshot, _qualname_of(parent), symbol, violations, skipped)
        del where


def _bind_call(
    call: ast.Call,
    signature: inspect.Signature,
    snapshot: Path,
    qualname: str,
    symbol: str,
    violations: list[Violation],
    skipped: list[Skip],
) -> None:
    if any(isinstance(arg, ast.Starred) for arg in call.args) or any(kw.arg is None for kw in call.keywords):
        skipped.append(Skip(snapshot, qualname, symbol, SkipReason.DYNAMIC_KWARGS))
        return
    positional = [None] * len(call.args)
    keywords = {kw.arg: None for kw in call.keywords if kw.arg is not None}
    try:
        signature.bind_partial(*positional, **keywords)
    except TypeError as error:
        violations.append(Violation(snapshot, qualname, symbol, str(error)))


_MISSING: Final = object()


def _library_signature(module_name: str, symbol: str) -> inspect.Signature | None | object:
    """The current signature, ``_MISSING`` if the symbol is gone, ``None`` if unimportable."""
    try:
        module = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 — an unimportable library is never a skill's ABI break
        return None
    target = getattr(module, symbol, _MISSING)
    if target is _MISSING:
        return _MISSING
    try:
        return inspect.signature(target)
    except (TypeError, ValueError):
        return None


def _repo_module_bindings(tree: ast.Module) -> dict[str, str]:
    """Map local names bound to ``_repo_module("x")`` onto ``automation.interop.x``."""
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        interop = _interop_module_of(node.value)
        if isinstance(target, ast.Name) and interop is not None:
            bindings[target.id] = interop
    return bindings


def _interop_module_of(value: ast.expr) -> str | None:
    if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
        return None
    if value.func.id not in _REPO_MODULE_HELPERS or len(value.args) != 1:
        return None
    literal = value.args[0]
    if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
        return f"{_INTEROP_PACKAGE}.{literal.value}"
    return None


def _resolve_library_symbol(func: ast.expr, bindings: dict[str, str]) -> tuple[str, str] | None:
    """A call ``<name>.Symbol(...)`` where ``<name>`` is a _repo_module binding."""
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return None
    module_name = bindings.get(func.value.id)
    if module_name is None:
        return None
    return module_name, func.attr


def _walk_with_parents(tree: ast.Module) -> list[tuple[ast.AST, ast.AST]]:
    pairs: list[tuple[ast.AST, ast.AST]] = []
    stack: list[tuple[ast.AST, ast.AST]] = [(tree, tree)]
    while stack:
        parent, node = stack.pop()
        enclosing = node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)) else parent
        for child in ast.iter_child_nodes(node):
            pairs.append((enclosing, child))
            stack.append((enclosing, child))
    return pairs


def _qualname_of(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    return "<module>"


def scan_live_root(live_root: Path, library_root: Path) -> AbiReport:
    """Check every ``<live_root>/<skill>`` snapshot (a symlink dir or release dir)."""
    violations: list[Violation] = []
    skipped: list[Skip] = []
    if not live_root.is_dir():
        return AbiReport((), ())
    for entry in sorted(live_root.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        report = check_snapshot(entry, library_root)
        violations.extend(report.violations)
        skipped.extend(report.skipped)
    return AbiReport(tuple(violations), tuple(skipped))


def _main(argv: list[str]) -> int:
    """CLI for the deploy/land paths: scan a live-mount root against a library."""
    if len(argv) != 2:
        print("usage: skill_library_abi.py <live-root> <library-root>", file=sys.stderr)
        return 2
    library_root = Path(argv[1]).resolve()
    # The signatures come from importing the ops-checkout library, so its parent
    # (the checkout root holding the ``automation`` package) must be importable.
    checkout_root = str(library_root.parent)
    if checkout_root not in sys.path:
        sys.path.insert(0, checkout_root)
    report = scan_live_root(Path(argv[0]), library_root)
    for violation in report.violations:
        print(
            f"ABI-VIOLATION {violation.snapshot.name}::{violation.qualname} "
            f"{violation.symbol}: {violation.detail}",
            file=sys.stderr,
        )
    if report.violations:
        print(f"ABI-WARN: {len(report.violations)} violation(s), {len(report.skipped)} skipped")
        return 1
    print(f"ABI-OK: 0 violation(s), {len(report.skipped)} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
