"""Finite iterable elements for sender loop bindings; never execute iterators."""
from __future__ import annotations

import ast

from tests.unit.owner_message_sender_bindings import Bindings


def iteration(graph: Bindings, scope: str, value: ast.expr) -> list[ast.expr] | None:
    """Union literal choices; None means an iterator needs fail-closed review."""
    choices = graph.expressions(scope, value)
    if not choices:
        return None
    result: list[ast.expr] = []
    for choice in choices:
        elements = _elements(graph, scope, choice)
        if elements is None:
            return None
        result.extend(elements)
    return result


def _elements(graph: Bindings, scope: str, value: ast.expr) -> list[ast.expr] | None:
    if isinstance(value, (ast.Tuple, ast.List)):
        return graph.sequence(scope, value)
    if isinstance(value, ast.IfExp):
        left, right = iteration(graph, scope, value.body), iteration(graph, scope, value.orelse)
        return None if left is None or right is None else [*left, *right]
    if isinstance(value, ast.Dict):
        return None if None in value.keys else [key for key in value.keys if key is not None]
    if not isinstance(value, ast.Call):
        return None
    if isinstance(value.func, ast.Attribute) and value.func.attr in {"values", "items"} and not value.args and not value.keywords:
        choices = graph.expressions(scope, value.func.value)
        if not choices or any(not isinstance(choice, ast.Dict) for choice in choices):
            return None
        result: list[ast.expr] = []
        for choice in choices:
            if not isinstance(choice, ast.Dict) or None in choice.keys:
                return None
            if value.func.attr == "values":
                result.extend(choice.values)
            else:
                result.extend(ast.copy_location(ast.Tuple(elts=[key, item], ctx=ast.Load()), value)
                              for key, item in zip(choice.keys, choice.values, strict=True) if key is not None)
        return result
    if not isinstance(value.func, ast.Name) or value.func.id not in {"enumerate", "zip"}:
        return None
    sequences = [iteration(graph, scope, arg) for arg in value.args]
    if not sequences or any(sequence is None for sequence in sequences):
        return None
    finite = [sequence for sequence in sequences if sequence is not None]
    if value.func.id == "enumerate":
        # The index is irrelevant to sender binding; retain its pair position.
        if len(finite) != 1 or value.keywords:
            return None
        rows = [(ast.copy_location(ast.Constant(value=index), value), item)
                for index, item in enumerate(finite[0])]
    else:
        if any(keyword.arg != "strict" for keyword in value.keywords):
            return None
        rows = list(zip(*finite))
    return [ast.copy_location(ast.Tuple(elts=list(row), ctx=ast.Load()), value) for row in rows]


def carries_iteration(graph: Bindings, scope: str, value: ast.expr) -> bool:
    """An unresolved iterable with a syntactic sender seed cannot certify clean."""
    return any(graph.carries(scope, node) for choice in [value, *graph.expressions(scope, value)]
               for node in ast.walk(choice) if isinstance(node, ast.expr))
