"""Finite ordinary bindings and loud rejection of opaque sender escapes."""
from __future__ import annotations

import ast

import pytest

from tests.unit.owner_message_sender_shapes import FINITE, OPAQUE, LOOPS, HELPERS

from tests.unit import test_owner_message_adoption_conformance as guard
from tests.unit.owner_message_conformance_ast import transport_calls


@pytest.mark.parametrize("name", FINITE)
def test_finite_sender_is_named(name: str) -> None:
    path = f"skills/synthetic/scripts/own-{name}.py"
    tree = ast.parse(FINITE[name])
    calls = list(transport_calls(tree))
    assert calls, f"{name}: missed finite sender"
    with pytest.raises(AssertionError, match=path):
        guard.test_senders_use_envelopes_when_discovered({path: tree})


@pytest.mark.parametrize("name", OPAQUE)
def test_opaque_sender_escape_fails_closed(name: str) -> None:
    path = f"skills/synthetic/scripts/own-{name}.py"
    with pytest.raises(AssertionError) as caught:
        guard.test_senders_use_envelopes_when_discovered({path: ast.parse(OPAQUE[name])})
    # Machine sentinel plus source location/construct, not a prose snapshot.
    assert "UNRESOLVED_OWNER_SENDER" in str(caught.value)
    assert path in str(caught.value)
    assert any(construct in str(caught.value) for construct in ("external", "factory"))


@pytest.mark.parametrize("name", HELPERS)
@pytest.mark.parametrize("migrated", [False, True])
def test_repository_helper_binding(name: str, migrated: bool) -> None:
    prefix, callee = HELPERS[name]
    helper = "def forward(send, body): send(body)"
    if migrated:
        helper = "from automation.interop.owner_message import render\ndef forward(send, body): send(render(body))"
    path = "skills/synthetic/scripts/helper.py"
    sources = {path: ast.parse(helper), "skills/synthetic/scripts/notify.py": ast.parse(
        prefix + f"\ndef notify(client): {callee}(client.send_owner_dm, 'raw')"),
        "automation/interop/owner_message.py": ast.parse("def render(message): return message")}
    if migrated:
        sites, _, _ = guard._inventory(sources)
        assert sites[f"{path}::forward"] == [True], "must resolve, not miss, the migrated helper"
        guard.test_senders_use_envelopes_when_discovered(sources)
    else:
        with pytest.raises(AssertionError, match=f"{path}::forward"):
            guard.test_senders_use_envelopes_when_discovered(sources)


@pytest.mark.parametrize("name", LOOPS)
@pytest.mark.parametrize("migrated", [False, True])
def test_loop_binding_and_migrated_control(name: str, migrated: bool) -> None:
    source = "def notify(client, flag):\n " + LOOPS[name]
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


@pytest.mark.parametrize("body", [
    "for dispatch in (client.send_owner_dm for _ in external): dispatch('raw')",
    "callbacks = [client.send_owner_dm]\n for dispatch in callbacks[index]: dispatch('raw')",
    "callbacks = (client.send_owner_dm for _ in external)\n for dispatch in callbacks: dispatch('raw')",
])
def test_unresolved_sender_iteration_fails_closed(body: str) -> None:
    path = "skills/synthetic/scripts/opaque-loop.py"
    with pytest.raises(AssertionError, match=f"UNRESOLVED_OWNER_SENDER {path}::notify"):
        guard.test_senders_use_envelopes_when_discovered({path: ast.parse("def notify(client):\n " + body)})


def test_finite_migrated_and_unrelated_controls() -> None:
    source = "from automation.interop.owner_message import render\ndef notify(client):\n send, = (client.send_owner_dm,)\n send(render(envelope))"
    path = "skills/synthetic/scripts/migrated.py"
    sources = {path: ast.parse(source)}
    sites, _, _ = guard._inventory(sources)
    assert sites[f"{path}::notify"] == [True]
    guard.test_senders_use_envelopes_when_discovered(sources)
    assert list(transport_calls(ast.parse("def notify(client, external): external(client.log)"))) == []
