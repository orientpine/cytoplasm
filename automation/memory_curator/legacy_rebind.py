"""Owner-driven reclamation of pre-v2 ``legacy_unbound`` promotions.

The v1->v2 migration parked the old proposed hashes as ``legacy_unbound``
records that reconcile refuses to delete: they were approved under the old
``저장`` confirm text, before the deletion-explaining marker existed, so their
old approval cannot authorize a native deletion.  This one-shot, owner-run tool
DROPS up to ``max_rebind`` legacy records whose entry is STILL present in native
memory, so the normal curation cycle re-proposes each one as a fresh
marker-bound promotion for cha's owner-DM ✅ — at which point reconcile can
verify the newly-saved note and reclaim the space.

Nothing is deleted here; this only re-opens the proposal.  Run it deliberately
(it is never invoked by ``run_cycle``)::

    python3 -m automation.memory_curator.legacy_rebind --max 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from .curator import curate, parse_memory_file
from .model import MemoryKind
from .promotion import content_hash
from .state import CuratorState, PromotionRecord
from .state_store import load_state, save_state

_FILENAMES: dict[MemoryKind, str] = {"memory": "MEMORY.md", "user": "USER.md"}
_DEFAULT_DIR = "~/.hermes/memories"
_DEFAULT_STATE = "~/.hermes/memory-curator/state.json"


def rebind_legacy(
    state: CuratorState,
    present_hashes: frozenset[str],
    *,
    max_rebind: int,
) -> tuple[CuratorState, tuple[str, ...]]:
    """Drop up to ``max_rebind`` present legacy records so the cycle re-proposes them."""
    dropped: list[str] = []
    kept: dict[str, PromotionRecord] = {}
    for key, record in state.promotions.items():
        eligible = (
            record.status == "legacy_unbound"
            and record.entry_sha256 in present_hashes
            and len(dropped) < max_rebind
        )
        if eligible:
            dropped.append(key)
            continue
        kept[key] = record
    return replace(state, promotions=kept), tuple(dropped)


def _present_hashes(memory_dir: Path) -> frozenset[str]:
    """Content hashes of durable candidates currently in native memory."""
    hashes: set[str] = set()
    for kind in ("memory", "user"):
        path = memory_dir / _FILENAMES[kind]
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        plan = curate(parse_memory_file(text, kind=kind))
        for entry in plan.promotion_candidates:
            hashes.add(content_hash(entry.text))
    return frozenset(hashes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memory_curator.legacy_rebind",
        description="Re-open pre-v2 legacy promotions so the cycle re-proposes them (deletes nothing).",
    )
    _ = parser.add_argument(
        "--memory-dir", default=os.environ.get("MEMORY_CURATOR_DIR", _DEFAULT_DIR)
    )
    _ = parser.add_argument(
        "--state-path", default=os.environ.get("MEMORY_CURATOR_STATE", _DEFAULT_STATE)
    )
    _ = parser.add_argument("--max", type=int, default=3, help="max legacy records to re-open")
    args = parser.parse_args(argv)

    memory_dir = Path(str(args.memory_dir)).expanduser()
    state_path = Path(str(args.state_path)).expanduser()
    if not memory_dir.is_dir():
        print(json.dumps({"error": "memory-dir-not-found", "path": str(memory_dir)}))
        return 2

    state = load_state(state_path)
    new_state, dropped = rebind_legacy(state, _present_hashes(memory_dir), max_rebind=int(args.max))
    save_state(state_path, new_state)
    print(json.dumps({"rebound": len(dropped)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
