"""Wire explicit repository imports into the finite owner sender graph."""
from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from tests.unit.owner_message_sender_ast import UnresolvedOwnerSender, sender_calls
from tests.unit.owner_message_sender_bindings import Key, symbol


def repository_calls(
    sources: Mapping[str, Sequence[tuple[str, ast.AST]]], rendered: set[str] | None = None,
) -> set[ast.Call]:
    modules = {path: f"<repo{index}>" for index, path in enumerate(sources)}
    nodes: list[tuple[str, ast.AST]] = []
    imports: dict[Key, set[Key]] = {}
    for path, scoped in sources.items():
        namespace = modules[path]
        for scope, node in scoped:
            scope = namespace if scope == "<module>" else f"{namespace}.{scope}"
            nodes.append((scope, node))
            if isinstance(node, ast.ImportFrom):
                module = (node.module or "").replace(".", "/")
                root = PurePosixPath(path).parent
                for _ in range(max(0, node.level - 1)):
                    root = root.parent
                candidates = [str(root / module)] if node.level else [str(root / module), module]
                destination = _destination(candidates, modules)
                for alias in node.names:
                    child = _destination([f"{candidate}/{alias.name}" for candidate in candidates], modules)
                    if child is not None:
                        imports.setdefault((scope, alias.asname or alias.name), set()).add(("<module>", child))
                    elif destination is not None:
                        imports.setdefault((scope, alias.asname or alias.name), set()).add((destination, alias.name))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.replace(".", "/")
                    destination = _destination([str(PurePosixPath(path).parent / module), module], modules)
                    if destination is not None:
                        imports.setdefault((scope, alias.asname or alias.name), set()).add(("<module>", destination))
            elif (isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
                  and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr) == "import_module"
                  and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                module = node.args[0].value.replace(".", "/")
                destination = _destination([str(PurePosixPath(path).parent / module), module], modules)
                if destination is not None:
                    imports.setdefault((scope, symbol(node)), set()).add(("<module>", destination))
    renderer = modules.get("automation/interop/owner_message.py")
    roots: set[str] = set()
    try:
        calls = {call for _, call in sender_calls(
            nodes, imports, renderer=f"{renderer}.render" if renderer else None, rendered=roots,
        )}
        if rendered is not None:
            for path, namespace in modules.items():
                rendered.update(f"{path}::{scope.removeprefix(namespace + '.')}" for scope in roots
                                if scope.startswith(namespace + "."))
        return calls
    except UnresolvedOwnerSender as error:
        detail = str(error)
        for path, namespace in modules.items():
            detail = detail.replace(namespace + ".", path + "::").replace(namespace + ":", path + "::<module>:")
        raise UnresolvedOwnerSender(detail) from error


def _destination(candidates: Sequence[str], modules: Mapping[str, str]) -> str | None:
    for candidate in candidates:
        for path in (f"{candidate}.py", f"{candidate}/__init__.py"):
            if path in modules:
                return modules[path]
    return None
