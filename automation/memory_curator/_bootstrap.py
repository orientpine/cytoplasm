"""Make the repo root (with the ``automation`` package) importable.

The deployed runtime is the top-level ``memory_curator`` package under
``~/.hermes/memory_curator_runtime``.  The classifier reaches ``automation.*``
(deterministic sensitivity rules, the shared Codex OAuth client), which lives at
the repo root, not inside the deployed package.  Import this module BEFORE any
``automation.*`` import so ``python3 -m memory_curator.shadow_cli`` works
standalone on a node — the no-agent cron wrapper adds the same path for its own
run, but the owner-run shadow CLI has no such wrapper.

Resolution order: ``AUTOPHAGY_REPO_ROOT`` env, the dev checkout (``parents[2]``),
the release runtime ``/srv/autophagy-agent-current``, then the node ops checkout
``/srv/autophagy-agents`` — the first whose
``automation/`` subdirectory exists wins.  Pure best-effort: if none resolves,
nothing is inserted and the caller's import fails loudly on its own.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _resolve_repo_root() -> Path | None:
    candidates = (
        os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip(),
        str(Path(__file__).resolve().parents[2]),
        "/srv/autophagy-agent-current",
        "/srv/autophagy-agents",
    )
    for candidate in candidates:
        if candidate and (Path(candidate) / "automation").is_dir():
            return Path(candidate)
    return None


REPO_ROOT: Path | None = _resolve_repo_root()
if REPO_ROOT is not None and str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
