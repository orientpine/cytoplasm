"""Finite match captures; unsupported sender-bearing patterns need review."""
from __future__ import annotations

import ast

from tests.unit.owner_message_sender_bindings import Bindings


def bind_pattern(graph: Bindings, scope: str, pattern: ast.pattern, value: ast.expr) -> bool:
    """Bind every possible capture, returning False when projection is unknown."""
    if isinstance(pattern, ast.MatchAs):
        if pattern.name is not None:
            graph.bind((scope, pattern.name), scope, value)
        return pattern.pattern is None or bind_pattern(graph, scope, pattern.pattern, value)
    if isinstance(pattern, ast.MatchOr):
        return all([bind_pattern(graph, scope, option, value) for option in pattern.patterns])
    if isinstance(pattern, (ast.MatchValue, ast.MatchSingleton)):
        return True  # Equality tests cannot capture the subject.
    if isinstance(pattern, ast.MatchSequence):
        choices = graph.expressions(scope, value)
        if not choices:
            return False
        return all([_sequence(graph, scope, pattern, choice) for choice in choices])
    if isinstance(pattern, ast.MatchMapping):
        choices = graph.expressions(scope, value)
        if not choices or any(not isinstance(choice, ast.Dict) or None in choice.keys for choice in choices):
            return False
        complete = True
        for choice in choices:
            if not isinstance(choice, ast.Dict):
                continue
            for key, child in zip(pattern.keys, pattern.patterns, strict=True):
                if not isinstance(key, ast.Constant):
                    complete = False
                    continue
                for source_key, source in zip(choice.keys, choice.values, strict=True):
                    if isinstance(source_key, ast.Constant) and source_key.value == key.value:
                        complete = bind_pattern(graph, scope, child, source) and complete
            if pattern.rest is not None:
                keys = {key.value for key in pattern.keys if isinstance(key, ast.Constant)}
                remaining = [(key, source) for key, source in zip(choice.keys, choice.values, strict=True)
                             if not isinstance(key, ast.Constant) or key.value not in keys]
                rest = ast.copy_location(ast.Dict(keys=[key for key, _ in remaining],
                                                  values=[source for _, source in remaining]), value)
                graph.bind((scope, pattern.rest), scope, rest)
        return complete
    return False


def _sequence(graph: Bindings, scope: str, pattern: ast.MatchSequence, value: ast.expr) -> bool:
    elements = graph.sequence(scope, value)
    if elements is None:
        return False
    star = next((i for i, child in enumerate(pattern.patterns) if isinstance(child, ast.MatchStar)), None)
    if (star is None and len(elements) != len(pattern.patterns)
            or star is not None and len(elements) < len(pattern.patterns) - 1):
        return True  # This finite choice cannot match.
    complete = True
    for index, child in enumerate(pattern.patterns):
        if isinstance(child, ast.MatchStar):
            if child.name is not None:
                end = len(elements) - (len(pattern.patterns) - index - 1)
                rest = ast.copy_location(ast.List(elts=elements[index:end], ctx=ast.Load()), value)
                graph.bind((scope, child.name), scope, rest)
        else:
            offset = index if star is None or index < star else len(elements) - len(pattern.patterns) + index
            complete = bind_pattern(graph, scope, child, elements[offset]) and complete
    return complete
