"""Finite expression bindings for the owner sender graph (never execute source)."""
from __future__ import annotations

import ast
from typing import Final, TypeAlias

Key: TypeAlias = tuple[str, str]
SENDER: Final = "<owner-transport>"
UNKNOWN: Final = "<unresolved-value>"


def parent(scope: str) -> str:
    return scope.rpartition(".")[0] or ("<module>" if scope != "<module>" else "")


def symbol(node: ast.expr) -> str:
    return f"<{type(node).__name__}:{node.lineno}:{node.col_offset}:{node.end_lineno}:{node.end_col_offset}>"


def literal_value(path: str) -> object:
    return ast.literal_eval(path[1:])


class Bindings:
    def __init__(self) -> None:
        self.edges: dict[Key, set[Key]] = {}
        self.children: dict[Key, set[Key]] = {}
        self.resolved: dict[Key, set[str]] = {}
        self.suspect: dict[Key, bool] = {}
        self.functions: dict[Key, str] = {}
        self.namespaces: set[str] = set()
        self.redirects: dict[Key, str] = {}
        self.receivers: dict[Key, Key] = {}
        self.syntax: dict[Key, list[tuple[str, ast.expr]]] = {}
        self.changed: bool = False
        self.keys: dict[Key, dict[str, object]] = {}

    def key(self, scope: str, path: str, value: object) -> None:
        self.keys.setdefault((scope, path), {})[repr(value)] = value

    def link(self, target: Key, source: Key) -> None:
        scope, path = target
        head, dot, tail = path.partition(".")
        receiver = self.receivers.get((scope, head))
        if dot and receiver is not None:
            scope, head = receiver
            path = f"{head}.{tail}"
        scope = self.redirects.get((scope, path.split(".")[0]), scope)
        values = self.edges.setdefault((scope, path), set())
        if source not in values:
            if scope in self.namespaces:
                self.children.setdefault((scope, "<members>"), set()).add((scope, path))
            parts = path.split(".")
            for length in range(1, len(parts)):
                self.children.setdefault((scope, ".".join(parts[:length])), set()).add((scope, path))
            values.add(source)
            self.resolved.clear()
            self.suspect.clear()
            self.changed = True

    def resolve(self, scope: str, path: str, seen: frozenset[Key] = frozenset()) -> set[str]:
        if seen:
            return self._resolve(scope, path, seen)
        key = (scope, path)
        if key not in self.resolved:
            self.resolved[key] = self._resolve(scope, path, seen)
        return self.resolved[key]

    def _resolve(self, scope: str, path: str, seen: frozenset[Key]) -> set[str]:
        if path.endswith(".send_owner_dm"):
            return {SENDER}
        if path.startswith("#"):
            return {path}
        found: set[str] = set()
        parts = path.split(".")
        while scope:
            scope = self.redirects.get((scope, parts[0]), scope)
            key = (scope, path)
            if key in self.functions:
                found.add(self.functions[key])
            for length in range(1, len(parts) + 1):
                prefix = (scope, ".".join(parts[:length]))
                if prefix in seen:
                    continue
                suffix = ".".join(parts[length:])
                if suffix and prefix in self.functions:
                    found.update(self.resolve(self.functions[prefix], suffix, seen | {prefix}))
                for source_scope, source_path in self.edges.get(prefix, ()):
                    forwarded = f"{source_path}.{suffix}" if suffix else source_path
                    found.update(self.resolve(source_scope, forwarded, seen | {prefix}))
            scope = parent(scope)
        return found or {UNKNOWN}

    def values(self, scope: str, value: ast.expr) -> set[str]:
        # Preserve unknown alternatives; an empty set must not certify a finite union.
        return self._values(scope, value) or {symbol(value)}

    def _values(self, scope: str, value: ast.expr) -> set[str]:
        match value:
            case ast.Name(id=name):
                return {name}
            case ast.Attribute(value=receiver, attr=attribute):
                return {f"{path}.{attribute}" for path in self.values(scope, receiver)}
            case ast.Constant(value=text) if isinstance(text, (str, int)):
                return {f"#{text!r}"}
            case ast.IfExp(body=body, orelse=other):
                return self.values(scope, body) | self.values(scope, other)
            case ast.NamedExpr(target=target, value=source):
                _ = self.assign(scope, target, source)
                return self.values(scope, source)
            case ast.Starred(value=source):
                return self.values(scope, source)
            case ast.Call(func=ast.Name(id="getattr"), args=[receiver, attribute, *_]):
                names = {literal_value(name) for path in self.values(scope, attribute)
                         for name in self.resolve(scope, path) if name.startswith("#")}
                return {f"{path}.{name}" for path in self.values(scope, receiver) for name in names}
            case ast.Subscript(value=receiver, slice=index):
                indexes = {name[1:] for path in self.values(scope, index)
                           for name in self.resolve(scope, path) if name.startswith("#")}
                receivers = self.values(scope, receiver)
                if isinstance(value.ctx, ast.Store):
                    for path in receivers:
                        for index in indexes:
                            self.key(scope, path, literal_value(f"#{index}"))
                return {f"{path}.[{index}]" for path in receivers for index in indexes}
            case ast.List(elts=elements) | ast.Tuple(elts=elements):
                if any(isinstance(element, ast.Starred) for element in elements):
                    expanded = self.sequence(scope, value)
                    if expanded is None:
                        return set()
                    elements = expanded
                path = symbol(value)
                for index, element in enumerate(elements):
                    self.key(scope, path, index)
                    self.bind((scope, f"{path}.[{index}]"), scope, element)
                return {path}
            case ast.Dict(keys=keys, values=values):
                path = symbol(value)
                for key, element in zip(keys, values, strict=True):
                    if key is not None:
                        for name in self.values(scope, key):
                            for literal in self.resolve(scope, name):
                                if literal.startswith("#"):
                                    self.key(scope, path, literal_value(literal))
                                    self.bind((scope, f"{path}.[{literal[1:]}]"), scope, element)
                return {path}
            case ast.Call() | ast.Lambda():
                return {symbol(value)}
            case _:
                return set()

    def carries(self, scope: str, value: ast.expr) -> bool:
        return any(self._carries_path((scope, path)) for path in self.values(scope, value))

    def _carries_path(self, root: Key) -> bool:
        if root in self.suspect:
            return self.suspect[root]
        pending = [root]
        seen: set[Key] = set()
        while pending:
            scope, path = pending.pop()
            if (scope, path) in seen:
                continue
            seen.add((scope, path))
            targets = self.resolve(scope, path)
            if self.suspect.get((scope, path)) or SENDER in targets:
                self.suspect[root] = True
                return True
            # Passing a callback factory/class also escapes its known sender payload.
            pending.extend((target, "<members>" if target in self.namespaces else "<return>")
                           for target in targets if target != UNKNOWN and not target.startswith("#"))
            while scope:
                pending.extend(self.edges.get((scope, path), ()))
                pending.extend(self.children.get((scope, path), ()))
                scope = parent(scope)
        self.suspect.update(dict.fromkeys(seen, False))
        return False

    def bind(self, target: Key, scope: str, value: ast.expr) -> None:
        for path in self.values(scope, value):
            self.link(target, (scope, path))

    def assign(self, scope: str, target: ast.expr, value: ast.expr) -> bool:
        if isinstance(target, (ast.Tuple, ast.List)):
            elements = self.sequence(scope, value)
            if elements is None:
                return False
            complete = True
            starred = next((i for i, item in enumerate(target.elts) if isinstance(item, ast.Starred)), None)
            for index, item in enumerate(target.elts):
                if isinstance(item, ast.Starred):
                    end = len(elements) - (len(target.elts) - index - 1)
                    tail = ast.copy_location(ast.List(elts=elements[index:end], ctx=ast.Load()), target)
                    complete = self.assign(scope, item.value, tail) and complete
                else:
                    source_index = index if starred is None or index < starred else len(elements) - len(target.elts) + index
                    if source_index < len(elements):
                        complete = self.assign(scope, item, elements[source_index]) and complete
            return complete
        paths = self.values(scope, target)
        for path in paths:
            self.bind((scope, path), scope, value)
        return bool(paths)

    def expressions(self, scope: str, value: ast.expr, seen: frozenset[Key] = frozenset()) -> list[ast.expr]:
        """Follow stored syntax for finite argument packs, without evaluating it."""
        if not isinstance(value, ast.Name):
            return [value]
        while scope:
            key = (scope, value.id)
            if key not in seen and key in self.syntax:
                return [item for source_scope, source in self.syntax[key]
                        for item in self.expressions(source_scope, source, seen | {key})]
            scope = parent(scope)
        return []

    def sequence(self, scope: str, value: ast.expr) -> list[ast.expr] | None:
        choices = self.expressions(scope, value)
        if len(choices) == 1 and isinstance(choices[0], (ast.Tuple, ast.List)):
            result: list[ast.expr] = []
            for item in choices[0].elts:
                if isinstance(item, ast.Starred):
                    expanded = self.sequence(scope, item.value)
                    if expanded is None:
                        return None
                    result.extend(expanded)
                else:
                    result.append(item)
            return result
        return None

    def arguments(self, scope: str, call: ast.Call) -> tuple[list[ast.expr], dict[str, ast.expr], bool]:
        positional: list[ast.expr] = []
        keywords: dict[str, ast.expr] = {}
        finite = True
        for value in call.args:
            if isinstance(value, ast.Starred):
                expanded = self.sequence(scope, value.value)
                if expanded is None:
                    finite = False
                else:
                    positional.extend(expanded)
            else:
                positional.append(value)
        for keyword in call.keywords:
            if keyword.arg is not None:
                keywords[keyword.arg] = keyword.value
                continue
            choices = self.expressions(scope, keyword.value)
            if len(choices) != 1 or not isinstance(choices[0], ast.Dict):
                finite = False
                continue
            for key, value in zip(choices[0].keys, choices[0].values, strict=True):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keywords[key.value] = value
                else:
                    finite = False
        return positional, keywords, finite
