"""Hermes cron watcher (no_agent, LLM-free) for the W2-4 RAG ingest pipeline.

Deployed to ``~agent/.hermes/scripts/rag_ingest_watch.py`` and registered as

    hermes cron create "every 10m" --name rag-ingest-watch \
        --no-agent --script rag_ingest_watch.py --deliver local

The package itself is deployed to ``~agent/.hermes/rag_ingest_runtime/``.
The pipeline config loader reads MCP and Discord credentials directly from its
configured secrets file (normally ``~/.env.secrets``); no environment export is needed.
Empty stdout = silent tick; a queued-backlog notice or fatal error is the
only output (classic watchdog pattern, see W1-7 daily-cost-report).

Single-instance flock guard: a long run (e.g. the first obsidian mirror
bootstrap, ~2240 files) can outlive the 10-minute cron interval, so an
overlapping tick exits 0 silently instead of racing a second pipeline run.
The kernel releases the flock when the holder exits — even on crash.
"""

from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path
from typing import IO

RUNTIME_DIR = Path.home() / ".hermes" / "rag_ingest_runtime"
LOCK_PATH = Path.home() / ".hermes" / "rag-ingest" / "watch.lock"


# Runtime root order (DG-4): AUTOPHAGY_REPO_ROOT override, else the release
# `current` symlink, else the resident mirror. Inlined by value because this
# wrapper sets sys.path BEFORE it can import automation.runtime_root.
def _runtime_root() -> Path:
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    current = Path("/srv/autophagy-agent-current")
    return current if current.exists() else Path("/srv/autophagy-agents")


# The deployed runtime dir carries ONLY ``rag_ingest/``, but the package imports
# ``automation.*`` (roster-bound sender identity, b16cdf79). The release root must
# therefore be importable too: without it EVERY tick dies with
# ``ModuleNotFoundError: No module named 'automation'`` — measured in production on
# 2026-08-22, minutes after the runtime was first deployed by the new deployer.
# RUNTIME_DIR is inserted last so it stays ahead of the release copy: the package
# that runs is the deployed one, and only its cross-package imports fall through.
_REPO_ROOT = _runtime_root()
if (_REPO_ROOT / "automation").is_dir() and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(RUNTIME_DIR))


def acquire_single_instance_lock(lock_path: Path = LOCK_PATH) -> IO[str] | None:
    """Non-blocking flock; ``None`` = another tick is still running (skip)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


if __name__ == "__main__":
    _lock = acquire_single_instance_lock()
    if _lock is None:
        sys.exit(0)
    from rag_ingest.cli import main

    sys.exit(main(["run"]))
