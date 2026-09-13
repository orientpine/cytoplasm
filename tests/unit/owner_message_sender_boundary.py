"""Positive consumption audit: an unmodelled use of a known sender is suspect.

Audit immediate AST expression edges, not only resolved outer expressions. Thus
an unsupported operator/binder cannot erase a seed before this check sees it.
Only explicitly modelled transfers and non-binding uses may consume that seed.
"""
from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence

from tests.unit.owner_message_sender_bindings import SENDER, UNKNOWN, Bindings, Key, literal_value, parent
from tests.unit.owner_message_sender_iteration import iteration
from tests.unit.owner_message_sender_patterns import bind_pattern


class UnresolvedOwnerSender(AssertionError):
    """An unmodelled construct consumed a statically known owner sender."""


def audit_consumption(
    graph: Bindings, nodes: Sequence[tuple[str, ast.AST]], originals: Mapping[ast.AST, str],
) -> None:
    call_keywords = {keyword for _, node in nodes if isinstance(node, ast.Call) for keyword in node.keywords}
    for scope, node in nodes:
        for field, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            for child in children:
                if not isinstance(child, ast.expr) or isinstance(getattr(child, "ctx", None), (ast.Store, ast.Del)):
                    continue
                if graph.carries(scope, child) and not _modelled(graph, scope, node, field, child, call_keywords):
                    raise UnresolvedOwnerSender(
                        f"UNRESOLVED_OWNER_SENDER {originals[node]}:{child.lineno} "
                        f"{type(node).__name__}.{field}: {ast.unparse(child)}; "
                        "sender binding is unresolved; use an explicit finite alias or extend the binding model"
                    )


def _modelled(
    graph: Bindings, scope: str, node: ast.AST, field: str, child: ast.expr,
    call_keywords: set[ast.keyword],
) -> bool:
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and field == "value":
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return all([_assign(graph, scope, target, child) for target in targets])
    if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) and field == "iter":
        elements = iteration(graph, scope, child)
        return elements is not None and all([_assign(graph, scope, node.target, item) for item in elements])
    if isinstance(node, ast.Match) and field == "subject":
        return all([bind_pattern(graph, scope, case.pattern, child) for case in node.cases])
    if isinstance(node, ast.Attribute) and field == "value":
        targets = {target for path in graph.values(scope, node) for target in graph.resolve(scope, path)}
        receiver = {target for path in graph.values(scope, child) for target in graph.resolve(scope, path)}
        # A sender's __call__/descriptor protocol is not a finite attribute alias.
        if SENDER in receiver:
            return False
        if UNKNOWN not in targets or graph.carries(scope, node):
            return True
        # These container views are explicitly modelled by the iteration graph.
        call = ast.copy_location(ast.Call(func=node, args=[], keywords=[]), node)
        return iteration(graph, scope, call) is not None
    if isinstance(node, ast.Subscript) and field == "value":
        targets = {target for path in graph.values(scope, node) for target in graph.resolve(scope, path)}
        # A retained sender stays visible to adoption checks; losing it requires
        # resolved targets. Unknown non-sender alternatives do not erase it.
        return (_finite_key(graph, scope, node.slice) and _certified_projection(graph, scope, node)
                and (graph.carries(scope, node) or UNKNOWN not in targets))
    if isinstance(node, (ast.List, ast.Tuple)) and field == "elts":
        return graph.sequence(scope, node) is not None
    if isinstance(node, ast.Dict) and field == "values":
        if not all(key is not None and _finite_key(graph, scope, key) for key in node.keys):
            return False
        keys = [literal_value(literal) for key in node.keys if key is not None
                for path in graph.values(scope, key) for literal in graph.resolve(scope, path)]
        return _distinct_keys(keys)
    if isinstance(node, ast.Call):
        # Arguments/opaque callees are checked by sender_calls after this audit.
        if field == "func":
            targets = {target for path in graph.values(scope, child) for target in graph.resolve(scope, path)}
            return SENDER in targets or UNKNOWN not in targets
        return field == "args"
    if isinstance(node, ast.keyword):
        return node in call_keywords
    if isinstance(node, ast.Starred):
        return field == "value"  # Its containing call/container must also pass.
    if isinstance(node, ast.IfExp):
        return field in {"test", "body", "orelse"}
    if isinstance(node, ast.Lambda):
        return field == "body"
    if isinstance(node, ast.arguments):
        return field in {"defaults", "kw_defaults"}
    # Returns flow through <return>; other uses only discard or inspect truth.
    if isinstance(node, (ast.Return, ast.Expr)):
        return field == "value"
    if isinstance(node, (ast.If, ast.While, ast.Assert)):
        return field == "test"
    if isinstance(node, ast.Compare):
        return all(isinstance(op, (ast.Is, ast.IsNot, ast.In, ast.NotIn)) for op in node.ops)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return field == "operand"
    if isinstance(node, ast.match_case):
        return field == "guard"
    return False


def _distinct_keys(keys: list[object]) -> bool:
    return all(key != earlier for index, key in enumerate(keys) for earlier in keys[:index])


def _certified_projection(graph: Bindings, scope: str, node: ast.Subscript) -> bool:
    origins = [keys for path in graph.values(scope, node.value)
               for keys in _key_origins(graph, (scope, path), frozenset())]
    if not origins or any(not _distinct_keys(list(keys.values())) for keys in origins):
        return False
    for path in graph.values(scope, node.slice):
        for literal in graph.resolve(scope, path):
            index = literal_value(literal)
            matched = False
            for keys in origins:
                # Labels only address graph slots. Python values certify selection.
                selected = [label for label, key in keys.items() if key == index]
                if selected and selected != [literal[1:]]:
                    return False
                matched = matched or bool(selected)
            if not matched:
                return False
    return True


def _key_origins(graph: Bindings, key: Key, seen: frozenset[Key]) -> list[dict[str, object]]:
    scope, path = key
    parts = path.split(".")
    found: list[dict[str, object]] = []
    while scope:
        scope = graph.redirects.get((scope, parts[0]), scope)
        keys = graph.keys.get((scope, path))
        if keys:
            found.append(keys)
        for length in range(1, len(parts) + 1):
            prefix = (scope, ".".join(parts[:length]))
            if prefix in seen:
                continue
            suffix = ".".join(parts[length:])
            if suffix and prefix in graph.functions:
                found.extend(_key_origins(graph, (graph.functions[prefix], suffix), seen | {prefix}))
            for source_scope, source_path in graph.edges.get(prefix, ()):
                forwarded = f"{source_path}.{suffix}" if suffix else source_path
                found.extend(_key_origins(graph, (source_scope, forwarded), seen | {prefix}))
        scope = parent(scope)
    return found


def _assign(graph: Bindings, scope: str, target: ast.expr, value: ast.expr) -> bool:
    complete = graph.assign(scope, target, value)
    return complete and all(_finite_key(graph, scope, node.slice) for node in ast.walk(target)
                            if isinstance(node, ast.Subscript))


def _finite_key(graph: Bindings, scope: str, value: ast.expr) -> bool:
    return all(_literal_path(graph, (scope, path), frozenset()) for path in graph.values(scope, value))


def _literal_path(graph: Bindings, key: Key, seen: frozenset[Key]) -> bool:
    scope, path = key
    if path.startswith("#"):
        try:
            literal_value(path)
        except (ValueError, SyntaxError):
            return False
        return True
    if key in seen:
        return False
    sources: set[Key] = set()
    while scope:
        sources.update(graph.edges.get((scope, path), ()))
        scope = parent(scope)
    return bool(sources) and all(_literal_path(graph, source, seen | {key}) for source in sources)
