"""Sender propagation through iterator/zip/dict/list constructions.

Each case is executed once to prove the notation really sends, then handed to
the guard: a construction whose selection is provable must keep the sender
visible, and an unprovable one must fail closed. No per-notation exemption.
"""
from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from tests.unit import test_owner_message_adoption_conformance as guard

# Bodies whose selection is provable value-by-value: the sender must stay visible.
_PROPAGATING: dict[str, str] = {
    "next-iter": "next(iter((client.send_owner_dm,)))('raw')",
    "list-index": "[client.send_owner_dm][0]('raw')",
    "dict-zip-distinct": "dict(zip((0, 1), (client.log, client.send_owner_dm)))[1]('raw')",
    "iter-unpack": "dispatch, = iter((client.send_owner_dm,))\n dispatch('raw')",
    "list-construction": "list((client.send_owner_dm,))[0]('raw')",
    "tuple-construction": "tuple([client.send_owner_dm])[0]('raw')",
    "consumed-iterator": "callbacks = iter((client.log, client.send_owner_dm))\n next(callbacks)\n next(callbacks)('raw')",
}
# True and 1.0 collide as dict keys, so [1] cannot be proven; refuse, never certify.
_UNPROVABLE: dict[str, str] = {
    "dict-zip-colliding": "dict(zip((True, 1.0), (client.log, client.send_owner_dm)))[1]('raw')",
    "aliased-unproven-key": "def log(body): pass\n callbacks = dict(zip((0, 0.0), (log, client.send_owner_dm)))\n callbacks[0]('raw')",
}


def _sends(body: str) -> bool:
    sent: list[str] = []
    logged: list[str] = []
    namespace: dict[str, object] = {}
    exec(compile("def notify(client):\n " + body, "<container-probe>", "exec"), namespace)  # noqa: S102
    notify = namespace["notify"]
    assert callable(notify)
    notify(SimpleNamespace(send_owner_dm=sent.append, log=logged.append))
    return sent == ["raw"] and logged == []


@pytest.mark.parametrize("body", [*_PROPAGATING.values(), *_UNPROVABLE.values()],
                         ids=[*_PROPAGATING, *_UNPROVABLE])
def test_container_notation_reaches_the_sender_at_runtime(body: str) -> None:
    # Given a container construction; when executed; then the owner sender receives the body.
    assert _sends(body)


@pytest.mark.parametrize("body", [*_PROPAGATING.values()], ids=[*_PROPAGATING])
@pytest.mark.parametrize("migrated", [False, True])
def test_provable_container_selection_keeps_the_sender_visible(body: str, migrated: bool) -> None:
    # Given a provable construction; when scanned; then the send site is discovered, not lost.
    source = "def notify(client):\n " + body
    if migrated:
        source = ("from automation.interop.owner_message import render\n"
                  + source.replace("'raw'", "render(envelope)"))
    path = "skills/synthetic/scripts/container.py"
    scan = guard._scan({path: ast.parse(source)})
    assert scan.sites[f"{path}::notify"] == [migrated]
    if migrated:
        guard.test_senders_use_envelopes_when_discovered(scan)
    else:
        with pytest.raises(AssertionError, match=f"{path}::notify"):
            guard.test_senders_use_envelopes_when_discovered(scan)


@pytest.mark.parametrize("body", [*_UNPROVABLE.values()], ids=[*_UNPROVABLE])
@pytest.mark.parametrize("migrated", [False, True])
def test_unprovable_container_selection_refuses_instead_of_certifying(body: str, migrated: bool) -> None:
    # Given an unprovable selection; when scanned; then the guard names the construct and refuses.
    source = "def notify(client):\n " + body
    if migrated:
        source = ("from automation.interop.owner_message import render\n"
                  + source.replace("'raw'", "render(envelope)"))
    path = "skills/synthetic/scripts/container.py"
    with pytest.raises(AssertionError) as caught:
        guard.test_senders_use_envelopes_when_discovered({path: ast.parse(source)})
    assert f"UNRESOLVED_OWNER_SENDER {path}::notify:" in str(caught.value)
    assert "Subscript.value" in str(caught.value)


@pytest.mark.parametrize("body", [*_PROPAGATING.values(), *_UNPROVABLE.values()],
                         ids=[*_PROPAGATING, *_UNPROVABLE])
def test_container_without_a_sender_stays_clean(body: str) -> None:
    # Given the same notations carrying no sender; when scanned; then nothing is reported.
    source = "def notify(client):\n " + body.replace("client.send_owner_dm", "client.log")
    path = "skills/synthetic/scripts/container.py"
    scan = guard._scan({path: ast.parse(source)})
    assert not scan.sites
    guard.test_senders_use_envelopes_when_discovered(scan)


@pytest.mark.parametrize("body", [
    "callbacks = [client.log, client.send_owner_dm]\n callbacks = [client.send_owner_dm, client.log]\n dispatch, unused = callbacks\n dispatch('raw')",
    "def log(body): pass\n flag = True\n callbacks = list([log] if not flag else [client.send_owner_dm])\n callbacks[0]('raw')",
])
def test_ambiguous_positions_refuse_instead_of_flattening_alternatives(body: str) -> None:
    # Given alternatives that really send; when scanned; then position guesses cannot certify them.
    assert _sends(body)
    path = "skills/synthetic/scripts/container.py"
    with pytest.raises(AssertionError, match="UNRESOLVED_OWNER_SENDER"):
        guard.test_senders_use_envelopes_when_discovered({path: ast.parse("def notify(client):\n " + body)})
