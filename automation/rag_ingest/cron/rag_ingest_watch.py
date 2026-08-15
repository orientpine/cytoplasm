"""Hermes cron watcher (no_agent, LLM-free) for the W2-4 RAG ingest pipeline.

Deployed to ``~agent/.hermes/scripts/rag_ingest_watch.py`` and registered as

    hermes cron create "every 10m" --name rag-ingest-watch \
        --no-agent --script rag_ingest_watch.py --deliver local

The package itself is deployed to ``~agent/.hermes/rag_ingest_runtime/``.
Empty stdout = silent tick; a queued-backlog notice or fatal error is the
only output (classic watchdog pattern, see W1-7 daily-cost-report).

Single-instance flock guard: a long run (e.g. the first obsidian mirror
bootstrap, ~2240 files) can outlive the 10-minute cron interval, so an
overlapping tick exits 0 silently instead of racing a second pipeline run.
The kernel releases the flock when the holder exits — even on crash.
"""

from __future__ import annotations

import fcntl
import sys
from pathlib import Path
from typing import IO

RUNTIME_DIR = Path.home() / ".hermes" / "rag_ingest_runtime"
LOCK_PATH = Path.home() / ".hermes" / "rag-ingest" / "watch.lock"

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
