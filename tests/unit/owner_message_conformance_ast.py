"""Static owner-message syntax analysis; no deployed imports or execution.

Notice aliases resolve imports, assignments and callback defaults within lexical
scopes. Conditional bindings are conservative: a possible notice alias counts.
Subprocess run/call/check_call/check_output/Popen resolve module attributes and
module/imported-name aliases in module or lexical function scopes. The first
argument or args= follows nearest preceding simple single-Name assignments in
its own function, including textually preceding nested blocks (flow-insensitive).
An RHS use sees the previous binding, never its enclosing assignment or a later
one. Lists/tuples expand starred Names to depth three; Name aliases are bounded
likewise. Hermes then send must occur in order, not necessarily adjacent.
HTTP POST paths accept literal strings/f-strings or same-function names.
Injected send_owner_dm discovery includes lexical bound-method/attribute aliases,
finite destructuring, lambdas, packs and repository helper forwarding.
owner_message_sender_ast documents that graph's acknowledged dynamic blind spots;
transport/reminder internals require reasoned audience-ledger entries.
KNOWN LIMITS for argv/HTTP analysis: AugAssign, BinOp concatenation, append/extend/insert, IfExp,
comprehensions, subscripts/slices, walrus, argv from parameters, return values,
attributes or call results (including shlex.split), dynamic attribute access,
eval and exec are outside the guaranteed closure. Legacy append/extend, +=,
concatenation and get/getenv-default recognition is retained, not generalized.
No deployed source is executed; this is bounded syntax analysis.
"""
from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping, Sequence
from typing import Final

from tests.unit.approval_conformance_ast import _called_name, _literal_text, _qualnames
from tests.unit.owner_message_sender_ast import sender_calls

NOTICE_NAMES: Final = frozenset({"deliver", "notify_owner", "notify_owner_dm"})
_SUBPROCESS_METHODS: Final = frozenset({"run", "call", "check_call", "check_output", "Popen"})
_SUBPROCESS_CALLS: Final = frozenset(f"subprocess.{name}" for name in _SUBPROCESS_METHODS)


def scoped_nodes(tree: ast.AST, scope: str = "<module>") -> Iterator[tuple[str, ast.AST]]:
    """Each node belongs to exactly one innermost scope, including nested defs."""
    for child in ast.iter_child_nodes(tree):
        nested = scope
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nested = child.name if scope == "<module>" else f"{scope}.{child.name}"
        yield nested, child
        yield from scoped_nodes(child, nested)


def notice_calls(tree: ast.Module) -> Iterator[tuple[str, ast.Call, str]]:
    nodes = list(scoped_nodes(tree))
    aliases: dict[tuple[str, str], str] = {("<module>", name): name for name in NOTICE_NAMES}
    bindings: list[tuple[str, str, ast.expr]] = []

    def resolve(scope: str, value: ast.expr) -> str | None:
        if isinstance(value, ast.Attribute) and value.attr in NOTICE_NAMES:
            return value.attr
        if isinstance(value, ast.Name):
            while scope:
                if (scope, value.id) in aliases:
                    return aliases[scope, value.id]
                scope = scope.rpartition(".")[0] or ("<module>" if scope != "<module>" else "")
        return None

    for scope, node in nodes:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in NOTICE_NAMES:
                    aliases[scope, alias.asname or alias.name] = alias.name
        if isinstance(node, ast.Assign):
            bindings.extend((scope, target.id, node.value) for target in node.targets if isinstance(target, ast.Name))
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            bindings.append((scope, node.target.id, node.value))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [*node.args.posonlyargs, *node.args.args]
            defaults = zip(args[len(args) - len(node.args.defaults):], node.args.defaults, strict=True)
            bindings.extend((scope, arg.arg, value) for arg, value in defaults)
            bindings.extend((scope, arg.arg, value) for arg, value in zip(
                node.args.kwonlyargs, node.args.kw_defaults, strict=True) if value is not None)
    changed = True
    while changed:
        changed = False
        for scope, name, value in bindings:
            resolved = resolve(scope, value)
            if resolved is not None and (scope, name) not in aliases:
                aliases[scope, name] = resolved
                changed = True
    for scope, node in nodes:
        if isinstance(node, ast.Call):
            name = resolve(scope, node.func)
            if name is not None:
                yield scope, node, name


def renders(tree: ast.Module, root: str) -> bool:
    scopes = {"<module>": tree, **_qualnames(tree)}
    assert root in scopes, f"render scope disappeared: {root}"
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    render_names = {alias.asname or alias.name for node in imports
                    if node.module == "automation.interop.owner_message"
                    for alias in node.names if alias.name == "render"}
    module_names = {alias.asname or alias.name for node in imports
                    if node.module == "automation.interop"
                    for alias in node.names if alias.name == "owner_message"}
    module_names |= {alias.asname or alias.name for node in ast.walk(tree)
                     if isinstance(node, ast.Import) for alias in node.names
                     if alias.name == "automation.interop.owner_message"}
    pending = [root]
    seen: set[str] = set()
    while pending:
        scope = pending.pop()
        if scope in seen:
            continue
        seen.add(scope)
        nodes = [node for name, node in scoped_nodes(scopes[scope], scope) if name == scope]
        for node in nodes:
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id in render_names
                or isinstance(node.func, ast.Attribute) and node.func.attr == "render"
                and ast.unparse(node.func.value) in module_names
            ):
                return True
            if isinstance(node, (ast.Name, ast.Attribute)):
                reference = node.id if isinstance(node, ast.Name) else node.attr
                parent = scope.rpartition(".")[0]
                candidate = f"{parent}.{reference}" if parent else reference
                pending.extend(name for name in (reference, candidate) if name in scopes and name not in seen)
    return False


def transport_calls(tree: ast.Module, owner_calls: set[ast.Call] | None = None) -> Iterator[tuple[str, ast.Call]]:
    scoped = list(scoped_nodes(tree))
    if owner_calls is None:
        owner_calls = {call for _, call in sender_calls(scoped)}
    groups: dict[str, list[ast.AST]] = {}
    aliases: dict[str, set[str]] = {}
    for scope, node in scoped:
        groups.setdefault(scope, []).append(node)
        names = aliases.setdefault(scope, set())
        match node:
            case ast.Import(names=imports):
                names.update(f"{alias.asname or alias.name}.{method}" for alias in imports
                             if alias.name == "subprocess" for method in _SUBPROCESS_METHODS)
            case ast.ImportFrom(module="subprocess", names=imports):
                names.update(alias.asname or alias.name for alias in imports if alias.name in _SUBPROCESS_METHODS)
            case _:
                continue
    for scope, node in scoped:
        if not isinstance(node, ast.Call):
            continue
        names = set(_SUBPROCESS_CALLS)
        parent = scope
        while parent:
            names.update(aliases.get(parent, ()))
            parent = parent.rpartition(".")[0] or ("<module>" if parent != "<module>" else "")
        if node in owner_calls or bypasses(node, groups[scope], frozenset(names)):
            yield scope, node


def _argv(value: ast.expr, bindings: Mapping[str, Sequence[ast.expr]], depth: int = 3) -> tuple[str | None, ...]:
    match value:
        case ast.Name(id=name) if name in bindings and depth >= 0:
            for previous in reversed(bindings[name]):
                # Parsed expression ends exclude the assignment containing this use.
                assert previous.end_lineno is not None and previous.end_col_offset is not None
                if (previous.end_lineno, previous.end_col_offset) <= (value.lineno, value.col_offset):
                    return _argv(previous, bindings, depth - isinstance(previous, ast.Name))
            return (None,)
        case ast.Starred(value=target) if depth > 0:
            return _argv(target, bindings, depth - 1)
        case ast.List(elts=elements) | ast.Tuple(elts=elements):
            return tuple(word for element in elements for word in _argv(element, bindings, depth))
        case ast.BinOp(left=left, op=ast.Add(), right=right):
            return (*_argv(left, bindings, depth), *_argv(right, bindings, depth))
        case ast.Call(args=[_, default]) if _called_name(value) in {"get", "getenv"}:
            return _argv(default, bindings, depth)
        case _:
            return (_literal_text(value),)


def bypasses(
    call: ast.Call, nodes: Sequence[ast.AST], subprocess_names: frozenset[str] = _SUBPROCESS_CALLS,
) -> bool:
    """Transport shape only: never infer audience from a function's name."""
    name = _called_name(call)
    if isinstance(call.func, ast.Attribute) and call.func.attr == "send_owner_dm":
        return True
    keywords = {kw.arg: kw.value for kw in call.keywords}
    paths = [*call.args, *keywords.values()]
    post = name == "post" or any(_literal_text(value) == "POST" for value in paths)
    subprocess_call = ast.unparse(call.func) in subprocess_names
    if not post and not subprocess_call:
        return False
    bindings: dict[str, list[ast.expr]] = {}
    for node in nodes:
        if node is call:
            break
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            bindings.setdefault(node.targets[0].id, []).append(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            bindings.setdefault(node.target.id, []).append(node.value)
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and isinstance(node.op, ast.Add):
            if node.target.id in bindings:
                history = bindings[node.target.id]
                history.append(ast.copy_location(ast.BinOp(left=history[-1], op=ast.Add(), right=node.value), node))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
            receiver = node.func.value
            if isinstance(receiver, ast.Name) and receiver.id in bindings and node.func.attr in {"append", "extend"}:
                addition = ast.List(elts=node.args, ctx=ast.Load()) if node.func.attr == "append" else node.args[0]
                history = bindings[receiver.id]
                history.append(ast.copy_location(ast.BinOp(left=history[-1], op=ast.Add(), right=addition), node))
    if post and any(text is not None and "/channels/" in text and text.endswith("/messages")
                    for value in paths for text in _argv(value, bindings)):
        return True
    argv = call.args[0] if call.args else keywords.get("args")
    if not subprocess_call or argv is None:
        return False
    words = _argv(argv, bindings)
    return any(word is not None and word.rsplit("/", 1)[-1] == "hermes" and "send" in words[index + 1:]
               for index, word in enumerate(words))
