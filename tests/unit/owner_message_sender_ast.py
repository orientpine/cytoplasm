"""Resolve ordinary owner sender bindings without executing deployed code.

Finite assignments, destructuring, lambdas, literal/local getattr names, packs,
containers, finite tuple/list loops (including enumerate/zip and dict views),
comprehension and finite match captures, returned callbacks, lexical global/nonlocal declarations and known
class receivers form a flow-insensitive union graph. Repository imports are wired
by owner_message_sender_repository. Every possible sender counts; render, not
message=, is the transport adoption contract. After convergence a positive AST
consumption audit rejects every unmodelled expression/binding edge carrying a
known sender, even when the outer expression resolves to nothing. Unknown forms
are SUSPECT, not CLEAN: UNRESOLVED_OWNER_SENDER names scope, line, construct and
requests an explicit finite alias or a binding-model extension. Finite sequence,
mapping, as/or and starred match captures resolve; class/unknown projections fail
closed when their subject carries a sender. Opaque calls also fail closed.
Unknown graph alternatives remain explicit, so one known literal/callee cannot
certify a partially unresolved sender projection or callback dispatch.

ACKNOWLEDGED BLIND SPOTS: runtime-computed getattr names, eval/exec, descriptors
and monkeypatching can introduce senders without a syntactic sender seed; these
require human review, not a claim of AST cleanliness. Arbitrary runtime object
identity is not inferred. Known class instances share conservative attribute
bindings. Recursive receiver paths are cut at repeated bindings for termination;
opaque sender-bearing forwarding, iteration, operators and patterns fail closed.
Without a syntactic seed the dynamic mechanisms above remain genuinely undecidable
and declared, not fail-closed detections. This is syntax adoption, not proof of
runtime message dataflow, descriptor behavior or arbitrary receiver identity.
"""
from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping, Sequence
from typing import TypeAlias

from tests.unit.owner_message_sender_bindings import SENDER, UNKNOWN, Bindings, Key, parent, symbol
from tests.unit.owner_message_sender_boundary import UnresolvedOwnerSender, audit_consumption
from tests.unit.owner_message_sender_iteration import carries_iteration, iteration
from tests.unit.owner_message_sender_patterns import bind_pattern

_Function: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda


def sender_calls(
    nodes: Sequence[tuple[str, ast.AST]], imports: Mapping[Key, set[Key]] | None = None,
    *, renderer: str | None = None, rendered: set[str] | None = None,
) -> Iterator[tuple[str, ast.Call]]:
    """Discover the finite closure; reject sender escapes into unknown callables."""
    graph = Bindings()
    for target, sources in (imports or {}).items():
        for source in sources:
            graph.link(target, source)
    for scope, _ in nodes:
        root = scope.split(".")[0]
        if root.startswith("<repo"):
            graph.functions["<module>", root] = root
    definitions: dict[str, _Function] = {}
    classes: set[str] = set()
    methods: set[str] = set()
    calls: list[tuple[str, ast.Call]] = []
    loops: list[tuple[str, ast.For | ast.AsyncFor | ast.comprehension]] = []
    originals = {node: scope for scope, node in nodes}
    lambda_scopes: dict[ast.AST, str] = {}
    for scope, node in nodes:
        if isinstance(node, ast.Lambda):
            scope = lambda_scopes.get(node, scope)
            nested = f"{scope}.{symbol(node)}"
            for child in ast.walk(node.body):
                lambda_scopes[child] = nested
    scoped = [(lambda_scopes.get(node, scope), node) for scope, node in nodes]
    for scope, node in scoped:
        if isinstance(node, ast.ClassDef):
            classes.add(scope)
            graph.namespaces.add(scope)
            graph.functions[parent(scope), node.name] = scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            enclosing = scope if isinstance(node, ast.Lambda) else parent(scope)
            name = symbol(node) if isinstance(node, ast.Lambda) else node.name
            target = f"{scope}.{name}" if isinstance(node, ast.Lambda) else scope
            definitions[target] = node
            graph.functions[enclosing, name] = target
            if enclosing in classes and node.args.args:
                methods.add(target)
                receiver = (parent(enclosing), enclosing.rsplit(".", 1)[-1])
                graph.link((target, node.args.args[0].arg), receiver)
                graph.receivers[target, node.args.args[0].arg] = receiver
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            enclosing = scope
            for name in node.names:
                destination = parent(scope)
                if isinstance(node, ast.Global):
                    while parent(destination) not in ("", "<module>"):
                        destination = parent(destination)
                graph.redirects[enclosing, name] = destination
        if isinstance(node, ast.Call):
            calls.append((scope, node))
        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            loops.append((scope, node))
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    graph.syntax.setdefault((scope, target.id), []).append((scope, node.value))
    graph.changed = True
    while graph.changed:
        graph.changed = False
        for scope, node in scoped:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    _ = graph.assign(scope, target, node.value)
            if isinstance(node, ast.NamedExpr):
                _ = graph.assign(scope, node.target, node.value)
            if isinstance(node, ast.Match):
                for case in node.cases:
                    _ = bind_pattern(graph, scope, case.pattern, node.subject)
            if isinstance(node, ast.Return) and node.value is not None:
                graph.bind((scope, "<return>"), scope, node.value)
        for scope, loop in loops:
            elements = iteration(graph, scope, loop.iter)
            if elements is not None:
                for element in elements:
                    _ = graph.assign(scope, loop.target, element)
        for target, definition in definitions.items():
            enclosing = parent(target)
            args = [*definition.args.posonlyargs, *definition.args.args]
            for arg, value in zip(args[len(args) - len(definition.args.defaults):], definition.args.defaults, strict=True):
                graph.bind((target, arg.arg), enclosing, value)
            for arg, value in zip(definition.args.kwonlyargs, definition.args.kw_defaults, strict=True):
                if value is not None:
                    graph.bind((target, arg.arg), enclosing, value)
            if isinstance(definition, ast.Lambda):
                graph.bind((target, "<return>"), target, definition.body)
        for scope, call in calls:
            targets = {target for path in graph.values(scope, call.func) for target in graph.resolve(scope, path)}
            positional, keywords, _ = graph.arguments(scope, call)
            for target in targets - {SENDER}:
                if target in classes:
                    graph.link((scope, symbol(call)), (parent(target), target.rsplit(".", 1)[-1]))
                    target = f"{target}.__init__"
                elif target in definitions:
                    graph.link((scope, symbol(call)), (target, "<return>"))
                if target not in definitions:
                    continue
                definition = definitions[target]
                parameters = [*definition.args.posonlyargs, *definition.args.args]
                if target in methods:
                    parameters = parameters[1:]
                for arg, value in zip(parameters, positional):
                    graph.bind((target, arg.arg), scope, value)
                for arg in (*parameters, *definition.args.kwonlyargs):
                    if arg.arg in keywords:
                        graph.bind((target, arg.arg), scope, keywords[arg.arg])
                if definition.args.vararg is not None:
                    for index, value in enumerate(positional[len(parameters):]):
                        graph.key(target, definition.args.vararg.arg, index)
                        graph.bind((target, f"{definition.args.vararg.arg}.[{index}]"), scope, value)
                if definition.args.kwarg is not None:
                    names = {arg.arg for arg in (*parameters, *definition.args.kwonlyargs)}
                    for name, value in keywords.items():
                        if name not in names:
                            graph.key(target, definition.args.kwarg.arg, name)
                            graph.bind((target, f"{definition.args.kwarg.arg}.[{name!r}]"), scope, value)
    audit_consumption(graph, scoped, originals)
    for scope, loop in loops:
        if iteration(graph, scope, loop.iter) is None and carries_iteration(graph, scope, loop.iter):
            raise UnresolvedOwnerSender(
                f"UNRESOLVED_OWNER_SENDER {originals[loop]}:{loop.iter.lineno} {ast.unparse(loop.iter)}"
            )
    targets_by_call = {call: {target for path in graph.values(scope, call.func)
                             for target in graph.resolve(scope, path)} for scope, call in calls}
    if rendered is not None and renderer is not None:
        rendered.add(renderer)
        changed = True
        while changed:
            changed = False
            for scope, call in calls:
                if scope not in rendered and targets_by_call[call] & rendered:
                    rendered.add(scope)
                    changed = True
    for scope, call in calls:
        targets = targets_by_call[call]
        if SENDER in targets:
            yield originals[call], call
            continue
        positional, keywords, finite = graph.arguments(scope, call)
        values = [*positional, *keywords.values(), *call.args, *(kw.value for kw in call.keywords)]
        carries_sender = any(graph.carries(scope, value) for value in values)
        if carries_sender and iteration(graph, scope, call) is not None:
            continue
        if carries_sender and (not finite or UNKNOWN in targets or not targets & (definitions.keys() | classes)):
            raise UnresolvedOwnerSender(
                f"UNRESOLVED_OWNER_SENDER {originals[call]}:{call.lineno} {ast.unparse(call)}"
            )
