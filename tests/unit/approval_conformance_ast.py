"""AST machinery behind the approval-surface conformance guards (split out under
AS-1.11).

Helper module, not a test module: the name carries no ``test_`` prefix so pytest
does not collect it. It owns the ONE tree walk the approval tests share —
``_qualnames`` — plus the predicates that read the inventory in
``approval_conformance_inventory`` and turn it into a yes/no about one source
file. The dependency runs one way only: inventory holds data, this module reads
it, the test modules assert on it.

``_qualnames`` replaces what used to be three byte-identical walkers (the
conformance file's ``_module_scopes`` and a private ``_qualnames`` in each of the
characterization and inventory test files); ``_function_names`` is now derived
from it rather than re-walking the tree with a fourth visitor.
"""
from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from pathlib import Path

from approval_conformance_inventory import (
    APPROVAL_PRODUCERS,
    _ADAPTER_POSTERS,
    _BANNED_ENV_OVERRIDE,
    _BANNED_RESOLVER_LITERALS,
    _DM_OPEN_PATH,
    _LIFECYCLE_HOSTS,
    _PENDING_MIGRATION,
    _POST_NAMES,
    _POSTING_PRIMITIVE_IMPLEMENTATIONS,
    _RECORD_WRITERS,
    _REPO,
    _RULE,
    _SURFACE_LITERALS,
)


def _tree(relative: str) -> ast.Module:
    path = _REPO / relative
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _surface_parts(surface: str) -> tuple[str, str]:
    module, _, function = surface.partition("::")
    return module, function


def _qualnames(tree: ast.Module) -> Mapping[str, ast.AST]:
    """Every class/def keyed by dotted qualname — the AS-0.2 inventory's own walk."""
    found: dict[str, ast.AST] = {}

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                nested = (*scope, child.name)
                found[".".join(nested)] = child
                walk(child, nested)
            else:
                walk(child, scope)

    walk(tree, ())
    return found


def _function_names(tree: ast.Module) -> frozenset[str]:
    """Dotted qualnames of every def — classes are dropped, class scopes are kept."""
    return frozenset(
        qualname
        for qualname, node in _qualnames(tree).items()
        if not isinstance(node, ast.ClassDef)
    )


def _called_name(call: ast.Call) -> str | None:
    match call.func:
        case ast.Name(id=name):
            return name
        case ast.Attribute(attr=name):
            return name
        case _:
            return None


def _has_facade_call(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Call) and _called_name(node) == "request_owner_approval"
        for node in ast.walk(tree)
    )


def _references_adapter(tree: ast.Module, adapter: str, producer: str) -> bool:
    if adapter == producer:
        return True
    dotted = adapter.removesuffix(".py").replace("/", ".")
    name = Path(adapter).stem
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name in {dotted, name} for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (
            node.module == dotted or any(alias.name == name for alias in node.names)
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and _called_name(node) == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == name
        ):
            return True
    return False


def _module_is_approval_flow(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id in {"APPROVE_EMOJI", "CANCEL_EMOJI"}
        or isinstance(node, ast.Attribute)
        and node.attr in {"APPROVE_EMOJI", "CANCEL_EMOJI", "add_reaction"}
        for node in ast.walk(tree)
    )


def _literal_text(node: ast.expr) -> str | None:
    match node:
        case ast.Constant(value=str(value)):
            return value
        case ast.JoinedStr(values=values):
            return "".join(
                value.value for value in values if isinstance(value, ast.Constant) and isinstance(value.value, str)
            )
        case _:
            return None


def _is_message_post(call: ast.Call) -> bool:
    name = _called_name(call)
    if name in _POST_NAMES:
        return True
    if name not in {"_api", "api"} or len(call.args) < 2:
        return False
    method, path = _literal_text(call.args[0]), _literal_text(call.args[1])
    return method == "POST" and path is not None and "/messages" in path


def _posting_callers(tree: ast.Module) -> frozenset[str]:
    callers: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scopes: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.scopes.append(node.name)
            self.generic_visit(node)
            self.scopes.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.scopes.append(node.name)
            self.generic_visit(node)
            self.scopes.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.visit_FunctionDef(node)

        def visit_Call(self, node: ast.Call) -> None:
            if self.scopes and _is_message_post(node):
                callers.add(".".join(self.scopes))
            self.generic_visit(node)

    Visitor().visit(tree)
    return frozenset(callers)


def _deployed_sources() -> list[Path]:
    paths = sorted(_REPO.glob("skills/*/scripts/*.py"))
    paths += sorted(path for path in _REPO.glob("automation/**/*.py") if "__pycache__" not in path.parts)
    assert paths, "approval conformance glob found no deployed Python sources"
    return paths


def _violation(surface: str, detail: str) -> str:
    return f"{surface}: {detail} ({_RULE})"


def _string_constants(node: ast.AST) -> frozenset[str]:
    return frozenset(
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    )


def _scoped_hits(tree: ast.Module, *, names: frozenset[str], text: Callable[[str], bool]) -> frozenset[str]:
    """Qualnames of the innermost scopes defining/calling `names` or holding matching text."""
    hits: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scopes: list[str] = []

        def _here(self) -> str:
            return ".".join(self.scopes) if self.scopes else "<module>"

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.scopes.append(node.name)
            self.generic_visit(node)
            self.scopes.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.scopes.append(node.name)
            if node.name in names:
                hits.add(self._here())
            self.generic_visit(node)
            self.scopes.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.visit_FunctionDef(node)

        def visit_Call(self, node: ast.Call) -> None:
            if _called_name(node) in names:
                hits.add(self._here())
            self.generic_visit(node)

        def visit_Constant(self, node: ast.Constant) -> None:
            if isinstance(node.value, str) and text(node.value):
                hits.add(self._here())

    Visitor().visit(tree)
    return frozenset(hits)


def _resolves_own_channel(text: str) -> bool:
    return text in _BANNED_RESOLVER_LITERALS or _BANNED_ENV_OVERRIDE.match(text) is not None


def _reads_flow_env_override(text: str) -> bool:
    """A retired per-flow ``*_APPROVALS_CHANNEL_ID`` name (AS-3.2 removed the branch)."""
    return _BANNED_ENV_OVERRIDE.match(text) is not None


def _names_a_surface(text: str) -> bool:
    return any(literal in text for literal in _SURFACE_LITERALS)


def _awaiting_migration(module: str) -> bool:
    paths = (part for key in _PENDING_MIGRATION for part in key.split("+"))
    return any(module == path or module.startswith(f"{path}/") for path in paths)


def _inventory_modules() -> tuple[str, ...]:
    """Every producer, adapter, lifecycle host, posting primitive and record writer."""
    surfaces = (*APPROVAL_PRODUCERS, *_ADAPTER_POSTERS, *_POSTING_PRIMITIVE_IMPLEMENTATIONS, *_RECORD_WRITERS)
    modules = {_surface_parts(surface)[0] for surface in surfaces}
    modules |= {*APPROVAL_PRODUCERS.values(), *_LIFECYCLE_HOSTS.values(), *_RECORD_WRITERS.values()}
    return tuple(sorted(module for module in modules if (_REPO / module).is_file()))


def _dm_opener_sites() -> Mapping[str, ast.AST]:
    """Same scan as `test_approval_surface_inventory._scan`, keyed `path::qualname`."""
    sites: dict[str, ast.AST] = {}
    for path in _deployed_sources():
        relative = path.relative_to(_REPO)
        for qualname, node in _qualnames(_tree(str(relative))).items():
            if isinstance(node, ast.ClassDef):
                continue
            if any(_DM_OPEN_PATH in value for value in _string_constants(node)):
                sites[f"{relative}::{qualname}"] = node
    return sites


def _builds_an_approval(node: ast.AST) -> bool:
    names = {
        child.id if isinstance(child, ast.Name) else child.attr if isinstance(child, ast.Attribute) else child.arg
        for child in ast.walk(node)
        if isinstance(child, (ast.Name, ast.Attribute, ast.keyword))
    }
    return "ApprovalIntent" in names or "message_id" in names


def _record_fields(tree: ast.Module) -> frozenset[str]:
    """Names a record in this module can carry: annotations, dict keys, keyword args."""
    fields: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            fields.add(node.target.id)
        elif isinstance(node, ast.Dict):
            fields |= {
                key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
        elif isinstance(node, ast.keyword) and node.arg is not None:
            fields.add(node.arg)
    return frozenset(fields)
