"""Finite patterns and the default-suspect boundary through the real guard."""
from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from tests.unit import test_owner_message_adoption_conformance as guard
from tests.unit.owner_message_conformance_ast import transport_calls
from tests.unit.owner_message_sender_shapes import FINITE, PATTERNS, RAW_SOURCES, UNMODELLED


@pytest.mark.parametrize("name", PATTERNS)
@pytest.mark.parametrize("migrated", [False, True])
def test_match_binding_and_migrated_control(name: str, migrated: bool) -> None:
    source = "def notify(client):\n " + PATTERNS[name]
    if migrated:
        source = "from automation.interop.owner_message import render\n" + source.replace("'raw'", "render(envelope)")
    path = f"skills/synthetic/scripts/{name}.py"
    tree = ast.parse(source)
    assert [scope for scope, _ in transport_calls(tree)] == ["notify"]
    scan = guard._scan({path: tree})
    assert scan.sites[f"{path}::notify"] == [migrated]
    if migrated:
        guard.test_senders_use_envelopes_when_discovered(scan)
    else:
        with pytest.raises(AssertionError, match=f"{path}::notify"):
            guard.test_senders_use_envelopes_when_discovered(scan)


@pytest.mark.parametrize("name", UNMODELLED)
@pytest.mark.parametrize("sender", [True, False])
def test_unmodelled_binding_is_suspect_only_with_sender(name: str, sender: bool) -> None:
    body, construct = UNMODELLED[name]
    if not sender:
        body = body.replace("client.send_owner_dm", "client.log")
    tree = ast.parse("def notify(client):\n " + body)
    path = f"skills/synthetic/scripts/{name}.py"
    if sender:
        with pytest.raises(AssertionError) as caught:
            guard.test_senders_use_envelopes_when_discovered({path: tree})
        assert f"UNRESOLVED_OWNER_SENDER {path}::notify:" in str(caught.value)
        assert construct in str(caught.value)
    else:
        assert list(transport_calls(tree)) == []
        guard.test_senders_use_envelopes_when_discovered({path: tree})


@pytest.mark.parametrize(("body", "construct"), [
    ("match (client.send_owner_dm,):\n  case tuple(__iter__=dispatch): dispatch('raw')", "Match.subject"),
    ("callbacks = (client.send_owner_dm,)\n match callbacks:\n  case Unknown(dispatch): dispatch('raw')", "Match.subject"),
    ("dispatch = [client.send_owner_dm] * 1\n dispatch[0]('raw')", "BinOp.left"),
    ("callbacks = {client.send_owner_dm}\n for dispatch in callbacks: dispatch('raw')", "Set.elts"),
    ("yield client.send_owner_dm", "Yield.value"),
    ("with client.send_owner_dm as dispatch: dispatch('raw')", "withitem.context_expr"),
    ("callbacks = {'send': client.send_owner_dm}\n callbacks[index]('raw')", "Subscript.value"),
    ("callbacks = [client.send_owner_dm]\n callbacks.pop()('raw')", "Attribute.value"),
    ("client.send_owner_dm.__call__('raw')", "Attribute.value"),
    ("callbacks = [client.send_owner_dm]\n [dispatch] = callbacks[index]", "Subscript.value"),
])
def test_unsupported_sender_consumption_names_its_construct(body: str, construct: str) -> None:
    path = "skills/synthetic/scripts/unresolved.py"
    with pytest.raises(AssertionError) as caught:
        guard.test_senders_use_envelopes_when_discovered({path: ast.parse("def notify(client):\n " + body)})
    assert f"UNRESOLVED_OWNER_SENDER {path}::notify:" in str(caught.value)
    assert construct in str(caught.value)


@pytest.mark.parametrize("source", [*FINITE.values(), *RAW_SOURCES.values()], ids=[*FINITE, *RAW_SOURCES])
def test_previously_resolved_forms_accept_actual_rendered_send(source: str) -> None:
    tree = ast.parse(source)
    calls = list(transport_calls(tree))
    assert calls
    # Migrate the discovered send itself, not an unrelated render in the scope.
    for _, call in calls:
        call.args[0] = ast.copy_location(ast.Call(func=ast.Name(id="render", ctx=ast.Load()),
                                                 args=[call.args[0]], keywords=[]), call.args[0])
    tree.body.insert(0, ast.ImportFrom(module="automation.interop.owner_message",
                                      names=[ast.alias(name="render")], level=0))
    ast.fix_missing_locations(tree)
    path = "skills/synthetic/scripts/migrated.py"
    scan = guard._scan({path: tree})
    assert sum(map(len, scan.sites.values())) == len(calls)
    assert all(all(adopted) for adopted in scan.sites.values())
    guard.test_senders_use_envelopes_when_discovered(scan)


@pytest.mark.parametrize("expression", [
    "(client or client).send_owner_dm", "getattr((client or client), 'send_owner_dm')",
    "[*[client.send_owner_dm]][0]",
])
@pytest.mark.parametrize("migrated", [False, True])
def test_seed_and_finite_pack_do_not_depend_on_receiver_resolution(expression: str, migrated: bool) -> None:
    source = f"def notify(client):\n dispatch = {expression}\n dispatch('raw')"
    if migrated:
        source = "from automation.interop.owner_message import render\n" + source.replace("'raw'", "render(envelope)")
    path = "skills/synthetic/scripts/receiver.py"
    scan = guard._scan({path: ast.parse(source)})
    assert scan.sites[f"{path}::notify"] == [migrated]
    if migrated:
        guard.test_senders_use_envelopes_when_discovered(scan)
    else:
        with pytest.raises(AssertionError, match=f"{path}::notify"):
            guard.test_senders_use_envelopes_when_discovered(scan)


@pytest.mark.parametrize("source", [
    "def choose(client): return client.send_owner_dm\ndef notify(client, external): external(choose)(client)('raw')",
    "class C:\n emit = client.send_owner_dm\ndef notify(external): external(C).emit('raw')",
])
@pytest.mark.parametrize("sender", [False, True])
def test_opaque_consumer_sees_factory_and_class_payloads(source: str, sender: bool) -> None:
    path = "skills/synthetic/scripts/payload.py"
    if not sender:
        source = source.replace("client.send_owner_dm", "client.log")
    if sender:
        with pytest.raises(AssertionError, match=f"UNRESOLVED_OWNER_SENDER {path}::notify:.*external"):
            guard.test_senders_use_envelopes_when_discovered({path: ast.parse(source)})
    else:
        guard.test_senders_use_envelopes_when_discovered({path: ast.parse(source)})


@pytest.mark.parametrize("body", [
    "callbacks = (client.log, client.send_owner_dm)\n dispatch = callbacks[True]",
    "callbacks = (client.log, client.send_owner_dm)\n dispatch = callbacks[len('x')]",
    "callbacks = (client.log, client.send_owner_dm)\n dispatch, = callbacks[1:]",
    "callbacks = {1: client.send_owner_dm}\n dispatch = callbacks[1.0]",
    "callbacks = (client.log, client.send_owner_dm)\n dispatch = callbacks[-1]",
])
@pytest.mark.parametrize("sender", [True, False])
def test_uncertified_projection_cannot_lose_a_known_sender(body: str, sender: bool) -> None:
    if not sender:
        body = body.replace("client.send_owner_dm", "client.log")
    source = "def notify(client):\n " + body + "\n dispatch('raw')"
    sent: list[str] = []
    logged: list[str] = []
    namespace: dict[str, object] = {}
    exec(compile(source, "<projection-probe>", "exec"), namespace)
    notify = namespace["notify"]
    assert callable(notify)
    notify(SimpleNamespace(send_owner_dm=sent.append, log=logged.append))
    assert sent == (["raw"] if sender else [])
    assert logged == ([] if sender else ["raw"])
    path = "skills/synthetic/scripts/projection.py"
    if sender:
        with pytest.raises(AssertionError, match=f"UNRESOLVED_OWNER_SENDER {path}::notify:.*Subscript.value"):
            guard.test_senders_use_envelopes_when_discovered({path: ast.parse(source)})
    else:
        scan = guard._scan({path: ast.parse(source)})
        assert not scan.sites
        guard.test_senders_use_envelopes_when_discovered(scan)


@pytest.mark.parametrize("source", [
    "def notify(client):\n callbacks = (lambda body: None, client.send_owner_dm)\n dispatch = callbacks[0]\n dispatch('raw')",
    "def notify(client):\n callbacks = {0: lambda body: None, '0': client.send_owner_dm}\n callbacks[0]('raw')",
    "def notify(client):\n callbacks = {'0': lambda body: None, 0: client.send_owner_dm}\n callbacks['0']('raw')",
])
def test_fully_resolved_projection_can_select_a_non_sender(source: str) -> None:
    path = "skills/synthetic/scripts/resolved-projection.py"
    scan = guard._scan({path: ast.parse(source)})
    assert not scan.sites
    guard.test_senders_use_envelopes_when_discovered(scan)


@pytest.mark.parametrize(("entries", "index"), [
    ("0: client.send_owner_dm, 'False': lambda body: None", "False"),
    ("False: lambda body: None, 0: client.send_owner_dm", "False"),
    ("1: lambda body: None, True: client.send_owner_dm", "1"),
    ("0: lambda body: None, False: client.send_owner_dm", "0"),
    ("1: lambda body: None, 1.0: client.send_owner_dm", "1"),
    ("0: lambda body: None, 0.0: client.send_owner_dm", "0"),
    ("0x1: lambda body: None, 1: client.send_owner_dm", "1"),
])
def test_literal_key_selection_uses_values_not_text(entries: str, index: str) -> None:
    source = f"def notify(client):\n callbacks = {{{entries}}}\n callbacks[{index}]('raw')"
    sent: list[str] = []
    namespace: dict[str, object] = {}
    exec(compile(source, "<key-selection-probe>", "exec"), namespace)
    notify = namespace["notify"]
    assert callable(notify)
    notify(SimpleNamespace(send_owner_dm=sent.append))
    assert sent == ["raw"]
    path = "skills/synthetic/scripts/key-selection.py"
    with pytest.raises(AssertionError, match=f"UNRESOLVED_OWNER_SENDER {path}::notify:"):
        guard.test_senders_use_envelopes_when_discovered({path: ast.parse(source)})


@pytest.mark.parametrize("index", [
    "0 if flag else unknown", "1 if flag else unknown", "unknown + 0", "1 if flag else 0",
])
def test_partial_key_domain_is_not_certified_by_a_known_alternative(index: str) -> None:
    path = "skills/synthetic/scripts/key.py"
    source = ("from automation.interop.owner_message import render\ndef notify(client, flag, unknown):\n"
              " callbacks = [client.log, client.send_owner_dm]\n index = 0\n"
              f" index = {index}\n callbacks[index](render(envelope))")
    if "unknown" in index:
        with pytest.raises(AssertionError, match=f"UNRESOLVED_OWNER_SENDER {path}::notify:.*Subscript.value"):
            guard.test_senders_use_envelopes_when_discovered({path: ast.parse(source)})
    else:
        scan = guard._scan({path: ast.parse(source)})
        assert scan.sites[path + "::notify"] == [True]
        guard.test_senders_use_envelopes_when_discovered(scan)


@pytest.mark.parametrize("alternative", ["external", "forward"])
def test_partial_callee_domain_cannot_hide_an_opaque_sender_escape(alternative: str) -> None:
    path = "skills/synthetic/scripts/callee.py"
    source = ("from automation.interop.owner_message import render\ndef forward(send): send(render(envelope))\n"
              f"def notify(client, flag, external):\n dispatch = forward if flag else {alternative}\n"
              " dispatch(client.send_owner_dm)")
    if alternative == "external":
        with pytest.raises(AssertionError, match=f"UNRESOLVED_OWNER_SENDER {path}::notify:"):
            guard.test_senders_use_envelopes_when_discovered({path: ast.parse(source)})
    else:
        scan = guard._scan({path: ast.parse(source)})
        assert scan.sites[path + "::forward"] == [True]
        guard.test_senders_use_envelopes_when_discovered(scan)
