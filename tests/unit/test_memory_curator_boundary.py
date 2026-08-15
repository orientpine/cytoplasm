"""Machine guard: the curator never reaches into the wiki approval internals.

The memory curator reuses ONLY the wiki gate's sanctioned proposer surface
(``create_draft`` + ``post_confirm_message``) and a read-only note read.  It
must NEVER call the approval-resolving / note-saving internals, nor import
``approval_lifecycle`` / ``wiki_approval`` / ``wiki_binding`` — those belong to
the wiki skill's own confirm watcher.  This locks that boundary (SI-6, one
gate) via an AST scan, so docstrings that merely *describe* the boundary are
ignored while a real call/import that crosses it fails here.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[2] / "automation" / "memory_curator"

#: Approval-resolving / note-saving functions the curator must never CALL.
_FORBIDDEN_CALLS: frozenset[str] = frozenset({
    "apply_draft",
    "resolve_reaction",
    "discard_draft",
    "confirm_via_injection",
    "confirm_via_owner_scan",
})

#: Approval-internal modules the curator must never IMPORT (static or dynamic).
_FORBIDDEN_MODULES: frozenset[str] = frozenset({
    "approval_lifecycle",
    "wiki_approval",
    "wiki_binding",
})

_DYNAMIC_IMPORTERS: frozenset[str] = frozenset({"import_module", "__import__"})


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _violations_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names if alias.name in _FORBIDDEN_MODULES]
        elif isinstance(node, ast.ImportFrom):
            if node.module in _FORBIDDEN_MODULES:
                found.append(node.module)
        elif isinstance(node, ast.Call):
            name = _called_name(node.func)
            if name in _FORBIDDEN_CALLS:
                found.append(name)
            elif name in _DYNAMIC_IMPORTERS and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and first.value in _FORBIDDEN_MODULES:
                    found.append(str(first.value))
    return found


def test_curator_source_never_names_wiki_approval_internals() -> None:
    # Given: every deployed curator source file (tests excluded).
    violations: list[str] = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        # When: an AST scan looks for a real call/import that crosses the boundary.
        for hit in _violations_in(path):
            violations.append(f"{path.relative_to(_PACKAGE.parent.parent)}: crosses via {hit!r}")

    # Then: the curator only ever used create_draft + post_confirm_message + a note read.
    assert not violations, "curator crossed the wiki approval boundary:\n" + "\n".join(violations)
