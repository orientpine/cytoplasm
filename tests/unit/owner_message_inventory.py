"""Classify one source mapping; keep the graph result explicit rather than cached."""
from __future__ import annotations

import ast
from collections.abc import Collection, Mapping
from typing import NamedTuple

from tests.unit.owner_message_conformance_ast import NOTICE_NAMES, notice_calls, renders, scoped_nodes, transport_calls
from tests.unit.owner_message_sender_repository import repository_calls


class Scan(NamedTuple):
    sites: dict[str, list[bool]]
    counts: dict[str, int]
    links: set[str]
    transports: set[str]


def scan_sources(
    sources: Mapping[str, ast.Module], not_owner_facing: Collection[str],
    card_roots: Mapping[str, str], link_definitions: Collection[str],
) -> Scan:
    sites: dict[str, list[bool]] = {}
    counts = {name: 0 for name in (*NOTICE_NAMES, "cards", "bypasses", "links")}
    links: set[str] = set()
    transports: set[str] = set()
    rendered: set[str] = set()
    owner_calls = repository_calls({path: list(scoped_nodes(tree)) for path, tree in sources.items()}, rendered)
    for path, tree in sources.items():
        notices: list[tuple[str, str, str | None, bool]] = []
        for scope, call, name in notice_calls(tree):
            keywords = {kw.arg: kw.value for kw in call.keywords}
            body = (call.args[0] if name in {"notify_owner", "notify_owner_dm"} and call.args
                    else keywords.get("content"))
            notices.append((scope, name, ast.dump(body) if body is not None else None, "message" in keywords))
        envelopes = {(scope, name, body) for scope, name, body, explicit in notices
                     if explicit and body is not None}
        for scope, name, body, explicit in notices:
            counts[name] += 1
            sites.setdefault(f"{path}::{scope}", []).append(explicit or (scope, name, body) in envelopes)
        for scope, _ in transport_calls(tree, owner_calls):
            key = f"{path}::{scope}"
            transports.add(key)
            if key not in not_owner_facing:
                sites.setdefault(key, []).append(key in rendered or renders(tree, scope))
            counts["bypasses"] += 1
        for scope, node in scoped_nodes(tree):
            key = f"{path}::{scope}"
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "discord.com/channels" in node.value:
                counts["links"] += 1
                if path not in link_definitions:
                    links.add(key)
    for producer, root in card_roots.items():
        path, scope = root.split("::")
        if path in sources:
            sites.setdefault(producer, []).append(renders(sources[path], scope))
            counts["cards"] += 1
    return Scan(sites, counts, links, transports)
