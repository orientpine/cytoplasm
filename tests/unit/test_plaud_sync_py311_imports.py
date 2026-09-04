"""Name-level py3.11 import guard for automation/plaud_sync.

The hermes no-agent cron runs its own uv-managed CPython 3.11, and the py311
SYNTAX guard (``ast.parse(feature_version=(3, 11))``) only checks grammar - a
``from typing import override`` parses fine everywhere yet ImportErrors at
module load on 3.11, which killed every plaud-sync-watch tick on 2026-09-02
(11 failures in a row before detection). Post-3.11 typing names must come from
``automation.typing_compat`` instead. New file on purpose: FS3-pinned test
files must not grow cases (tests/AGENTS.md).

The scan follows the IMPORTS, not the directory: the watcher's reaction transport
now lives in ``automation/interop``, and a guard that only read ``automation/plaud_sync``
would have waved that move through while the 3.11 tick still loads the code.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
#: Every package the plaud-sync tick imports at runtime under CPython 3.11.
_PACKAGES: Final = (
    _REPO / "automation" / "plaud_sync",
    _REPO / "automation" / "interop",
)
_POST_311_TYPING_NAMES: Final = frozenset(
    {
        "override",
        "TypeAliasType",
        "get_protocol_members",
        "is_protocol",
        "ReadOnly",
        "TypeIs",
        "NoDefault",
    }
)


def test_plaud_sync_when_scanned_then_imports_no_post_311_typing_names() -> None:
    offenders: list[str] = []
    scanned = 0
    for path in sorted(p for package in _PACKAGES for p in package.rglob("*.py")):
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != "typing":
                continue
            offenders.extend(
                f"{path.relative_to(_REPO)}:{node.lineno}: {alias.name}"
                for alias in node.names
                if alias.name in _POST_311_TYPING_NAMES
            )
    assert scanned, "the scanned packages hold no python files - repo layout changed?"
    assert not offenders, (
        "hermes cron runs CPython 3.11; import these via automation.typing_compat "
        "instead of typing:\n" + "\n".join(offenders)
    )
