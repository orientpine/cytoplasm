"""Synthetic-file regressions for injected owner transports, independent of skills."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit.owner_message_sender_shapes import RAW_SOURCES

from tests.unit.owner_message_conformance_ast import renders, transport_calls


def synthetic_tree(tmp_path: Path, name: str, source: str) -> ast.Module:
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("name", RAW_SOURCES)
def test_raw_synthetic_sender_is_discovered(tmp_path: Path, name: str) -> None:
    tree = synthetic_tree(tmp_path, name, RAW_SOURCES[name])
    calls = list(transport_calls(tree))
    assert calls, f"{name}.py: raw owner sender escaped discovery"
    assert any(not renders(tree, scope) for scope, _ in calls)


@pytest.mark.parametrize("source", [
    "from automation.interop.owner_message import render\ndef notify(client):\n send = client.send_owner_dm\n send(render(envelope))",
    "from automation.interop.owner_message import render\ndef forward(send, envelope): send(render(envelope))\ndef notify(client): forward(client.send_owner_dm, envelope)",
])
def test_migrated_synthetic_sender_is_not_flagged(tmp_path: Path, source: str) -> None:
    tree = synthetic_tree(tmp_path, "migrated", source)
    calls = list(transport_calls(tree))
    assert calls, "negative must prove adoption, not missing discovery"
    assert all(renders(tree, scope) for scope, _ in calls)


def test_unrelated_synthetic_callback_is_not_discovered(tmp_path: Path) -> None:
    tree = synthetic_tree(tmp_path, "unrelated", "def forward(callback): callback(raw)\ndef notify(client): forward(client.log)")
    assert list(transport_calls(tree)) == []
