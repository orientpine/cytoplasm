"""Small sender-shape corpus batched into the single deployed inventory scan."""
from __future__ import annotations

import ast
from collections.abc import Callable, Mapping

import pytest

from tests.unit.owner_message_inventory import Scan
from tests.unit.owner_message_sender_shapes import FINITE, HELPERS, LOOPS, PATTERNS, RAW_SOURCES

PREFIX = "skills/synthetic_owner_guard/"


def repository_probes() -> tuple[dict[str, ast.Module], dict[str, bool]]:
    sources: dict[str, ast.Module] = {}
    expected: dict[str, bool] = {}
    for family, shapes in (("aliases", RAW_SOURCES), ("finite", FINITE)):
        for name, source in shapes.items():
            path = f"{PREFIX}{family}/{name}.py"
            sources[path] = ast.parse(source)
            expected[path] = False
    for name, body in {**LOOPS, **PATTERNS}.items():
        for migrated in (False, True):
            path = f"{PREFIX}loops/{name}-{migrated}.py"
            source = "def notify(client, flag):\n " + body
            if migrated:
                source = "from automation.interop.owner_message import render\n" + source.replace("'raw'", "render(envelope)")
            sources[path] = ast.parse(source)
            expected[path] = migrated
    for name, (imports, callee) in HELPERS.items():
        for migrated in (False, True):
            root = f"{PREFIX}helpers/{name}-{migrated}"
            helper = "def forward(send, body): send(body)"
            if migrated:
                helper = "from automation.interop.owner_message import render\ndef forward(send, body): send(render(body))"
            path = f"{root}/helper.py"
            sources[path] = ast.parse(helper)
            sources[f"{root}/notify.py"] = ast.parse(imports + f"\ndef notify(client): {callee}(client.send_owner_dm, 'raw')")
            expected[path] = migrated
    return sources, expected


def batched_inventory(
    sources: Mapping[str, ast.Module], analyze: Callable[[Mapping[str, ast.Module]], Scan],
    assert_senders: Callable[[Scan], None],
) -> Scan:
    probes, expected = repository_probes()
    assert not sources.keys() & probes.keys()
    scan = analyze({**sources, **probes})
    found = {key: adopted for key, adopted in scan.sites.items() if key.split("::")[0] in probes}
    assert {key.split("::")[0] for key in found} == expected.keys()
    for key, adopted in found.items():
        assert adopted and all(value == expected[key.split("::")[0]] for value in adopted), key
    with pytest.raises(AssertionError) as caught:
        assert_senders(scan)
    for key, adopted in found.items():
        if not all(adopted):
            assert key in str(caught.value)
    # Reuse the same graph for all deployed assertions, excluding only our probes.
    # Probe modules contain only injected transports, no notices/cards/link literals.
    counts = dict(scan.counts)
    counts["bypasses"] -= sum(len(adopted) for adopted in found.values())
    return Scan(
        {key: adopted for key, adopted in scan.sites.items() if key not in found},
        counts, scan.links, {key for key in scan.transports if key.split("::")[0] not in probes},
    )
