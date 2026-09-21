"""Finite iterable elements and container constructions; never execute them.

iteration() answers what a loop sees. entries() answers which (key, value) slots
a construction addresses, so iter/list/tuple/zip/enumerate/dict(pairs) bind the
same slots a literal binds and one value-based subscript rule covers every
notation. selection() answers what next() can yield. Unproven shapes return
None and the caller fails closed; construction cycles are cut, never guessed.
"""
from __future__ import annotations

import ast
from typing import Final, Protocol, TypeAlias

# One expansion chain shared with Bindings.sequence; a repeat means a cycle.
Seen: TypeAlias = frozenset[tuple[str, str, ast.expr]]
EMPTY_SEEN: Final[Seen] = frozenset()
_ELEMENTWISE: Final = frozenset({"iter", "list", "tuple"})
_ROWWISE: Final = frozenset({"enumerate", "zip"})


class Bindings(Protocol):
    """Read-only graph operations needed to expand iterable syntax."""

    def expressions(self, scope: str, value: ast.expr) -> list[ast.expr]: ...
    def sequence(self, scope: str, value: ast.expr, seen: Seen = EMPTY_SEEN) -> list[ast.expr] | None: ...
    def carries(self, scope: str, value: ast.expr) -> bool: ...


def sequence(graph: Bindings, scope: str, value: ast.expr, seen: Seen = EMPTY_SEEN) -> list[ast.expr] | None:
    """Ordered elements require one origin; alternatives are not concatenated."""
    choices = graph.expressions(scope, value)
    if len(choices) != 1 or isinstance(choices[0], ast.IfExp):
        return None
    choice = choices[0]
    if (scope, "sequence", choice) in seen:
        return None
    nested = seen | {(scope, "sequence", choice)}
    match choice:
        case ast.Tuple(elts=items) | ast.List(elts=items):
            result: list[ast.expr] = []
            for item in items:
                match item:
                    case ast.Starred(value=source):
                        expanded = sequence(graph, scope, source, nested)
                        if expanded is None:
                            return None
                        result.extend(expanded)
                    case _:
                        result.append(item)
            return result
        case ast.Call(func=ast.Name(id=name)) if name in {"iter", "zip", "enumerate"} and isinstance(value, ast.Name):
            # A stored iterator may already be consumed: its positions are unknown.
            return None
        case _:
            return _elements(graph, scope, choice, nested)


def iteration(graph: Bindings, scope: str, value: ast.expr, seen: Seen = EMPTY_SEEN) -> list[ast.expr] | None:
    """Union literal choices; None means an iterator needs fail-closed review."""
    choices = graph.expressions(scope, value)
    if not choices:
        return None
    result: list[ast.expr] = []
    for choice in choices:
        if (scope, "iterate", choice) in seen:
            return None
        elements = _elements(graph, scope, choice, seen | {(scope, "iterate", choice)})
        if elements is None:
            return None
        result.extend(elements)
    return result


def entries(
    graph: Bindings, scope: str, value: ast.expr, seen: Seen = EMPTY_SEEN,
) -> list[tuple[ast.expr, ast.expr]] | None:
    """(key, value) slots a finite construction addresses; None when unproven."""
    pairs = _pairs(graph, scope, value, seen)
    if pairs is not None:
        return pairs
    elements = iteration(graph, scope, value, seen)
    if elements is None:
        return None
    return [(ast.copy_location(ast.Constant(value=index), value), element)
            for index, element in enumerate(elements)]


def selection(
    graph: Bindings, scope: str, value: ast.expr, seen: Seen = EMPTY_SEEN,
) -> list[ast.expr] | None:
    """Values next(iterable[, default]) can yield; None when unproven."""
    if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name) or value.func.id != "next":
        return None
    if value.keywords or not 1 <= len(value.args) <= 2 or any(isinstance(arg, ast.Starred) for arg in value.args):
        return None
    elements = iteration(graph, scope, value.args[0], seen)
    # This graph is flow-insensitive: prior consumption may expose any element.
    return None if elements is None else [*elements, *value.args[1:]]


def _pairs(
    graph: Bindings, scope: str, value: ast.expr, seen: Seen,
) -> list[tuple[ast.expr, ast.expr]] | None:
    """Mapping slots: dict literals and dict(pairs) over a finite pair sequence."""
    choices = graph.expressions(scope, value)
    if not choices or not all(_is_mapping(choice) for choice in choices):
        return None
    result: list[tuple[ast.expr, ast.expr]] = []
    for choice in choices:
        if (scope, "map", choice) in seen:
            return None
        pairs = _mapping_pairs(graph, scope, choice, seen | {(scope, "map", choice)})
        if pairs is None:
            return None
        result.extend(pairs)
    return result


def _mapping_pairs(
    graph: Bindings, scope: str, value: ast.expr, seen: Seen,
) -> list[tuple[ast.expr, ast.expr]] | None:
    """Slots of one mapping construction already guarded against re-entry."""
    if isinstance(value, ast.Dict):
        if None in value.keys:
            return None
        return [(key, item) for key, item in zip(value.keys, value.values, strict=True) if key is not None]
    if not isinstance(value, ast.Call):
        return None
    rows = graph.sequence(scope, value.args[0], seen) if value.args else []
    if rows is None:
        return None
    result: list[tuple[ast.expr, ast.expr]] = []
    for row in rows:
        pair = graph.sequence(scope, row, seen)
        if pair is None or len(pair) != 2:
            return None
        result.append((pair[0], pair[1]))
    return result


def _is_mapping(value: ast.expr) -> bool:
    return isinstance(value, ast.Dict) or (
        isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "dict"
        and not value.keywords and len(value.args) <= 1
        and not any(isinstance(arg, ast.Starred) for arg in value.args)
    )


def _elements(graph: Bindings, scope: str, value: ast.expr, seen: Seen) -> list[ast.expr] | None:
    if isinstance(value, (ast.Tuple, ast.List)):
        return graph.sequence(scope, value, seen)
    if isinstance(value, ast.IfExp):
        left = iteration(graph, scope, value.body, seen)
        right = iteration(graph, scope, value.orelse, seen)
        return None if left is None or right is None else [*left, *right]
    if _is_mapping(value):
        # Iterating a mapping yields its keys, whatever notation built it.
        pairs = _mapping_pairs(graph, scope, value, seen)
        return None if pairs is None else [key for key, _ in pairs]
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
    if not isinstance(value.func, ast.Name):
        return None
    if value.func.id in _ELEMENTWISE:
        # iter/list/tuple keep element order, so their slots stay addressable.
        if value.keywords or len(value.args) != 1 or isinstance(value.args[0], ast.Starred):
            return None
        return graph.sequence(scope, value.args[0], seen)
    if value.func.id not in _ROWWISE:
        return None
    sequences = [graph.sequence(scope, arg, seen) for arg in value.args]
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
