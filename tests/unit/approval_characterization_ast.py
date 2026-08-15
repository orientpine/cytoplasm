"""AST readers behind the approval-surface characterization locks (AS-0.2, split
out under AS-1.11).

Helper module, not a test module: the name carries no ``test_`` prefix so pytest
does not collect it.

Each reader pins ONE verbatim fact about a deployed source file — the expression
bound to ``channel_id=``, the annotated field order of a record, the exact set of
names a function calls — so a characterization test can assert on behaviour that
has no runtime seam. The tree walk itself is the shared one in
``approval_conformance_ast``; nothing here re-implements it.
"""
from __future__ import annotations

import ast

from approval_conformance_ast import _qualnames, _tree
from approval_conformance_inventory import _REPO


def _definition(relative: str, name: str) -> ast.AST:
    matches = [
        node
        for qualname, node in _qualnames(_tree(relative)).items()
        if qualname == name or qualname.endswith(f".{name}")
    ]
    assert len(matches) == 1, f"{relative}::{name} is not uniquely defined ({len(matches)} found)"
    return matches[0]


def _annotated_fields(relative: str, class_name: str) -> tuple[str, ...]:
    node = _definition(relative, class_name)
    assert isinstance(node, ast.ClassDef)
    return tuple(
        item.target.id
        for item in node.body
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
    )


def _channel_keyword_source(relative: str, name: str) -> str:
    """The verbatim expression bound to ``channel_id=`` inside one function."""
    for call in (n for n in ast.walk(_definition(relative, name)) if isinstance(n, ast.Call)):
        for keyword in call.keywords:
            if keyword.arg == "channel_id":
                return ast.unparse(keyword.value)
    raise AssertionError(f"{relative}::{name} binds no channel_id keyword")


def _returned_dict_keys(relative: str, name: str) -> frozenset[str]:
    for node in ast.walk(_definition(relative, name)):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            return frozenset(
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
    raise AssertionError(f"{relative}::{name} returns no literal dict")


def _dict_literal_keys(relative: str, name: str) -> frozenset[str]:
    """Every string key of every dict literal built inside one function."""
    return frozenset(
        key.value
        for node in ast.walk(_definition(relative, name))
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    )


def _called_names(node: ast.AST) -> frozenset[str]:
    names: set[str] = set()
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        match call.func:
            case ast.Name(id=name) | ast.Attribute(attr=name):
                names.add(name)
            case _:
                continue
    return frozenset(names)


def _keyword_bindings(relative: str, name: str) -> frozenset[tuple[str, str]]:
    """Every ``keyword=expression`` pair, verbatim, inside one function."""
    return frozenset(
        (keyword.arg, ast.unparse(keyword.value))
        for call in (n for n in ast.walk(_definition(relative, name)) if isinstance(n, ast.Call))
        for keyword in call.keywords
        if keyword.arg is not None
    )


def _return_sources(relative: str, name: str) -> frozenset[str]:
    """The verbatim expression of every ``return`` in one function."""
    return frozenset(
        ast.unparse(node.value)
        for node in ast.walk(_definition(relative, name))
        if isinstance(node, ast.Return) and node.value is not None
    )


def _package_string_constants(package: str) -> frozenset[str]:
    """Every string literal in one deployed package — for hunting channel sentinels."""
    return frozenset(
        node.value
        for path in sorted((_REPO / package).rglob("*.py"))
        if "__pycache__" not in path.parts
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
