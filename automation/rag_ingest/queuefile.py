"""Durable delivery queue (JSONL) for the RAG-down -> retry failure path.

Every changed document becomes one queued job carrying its full upsert
payloads, stale-point deletes, and post-delivery state/cursor updates. Jobs
are drained oldest-first; on MCP unreachability the remaining jobs stay
queued and the next cron tick retries them (0 loss). Job payloads can contain
sensitive local content, so the queue file lives inside the agent home
(mode 600) and is never copied into repo/QA artifacts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def load_jobs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    jobs: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            jobs.append(loaded)
    return jobs


def save_jobs(path: Path, jobs: list[dict[str, Any]]) -> None:
    """Atomic rewrite (tmp + rename) with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    lines = "".join(json.dumps(job, ensure_ascii=False) + "\n" for job in jobs)
    temp_path.write_text(lines, encoding="utf-8")
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def pending_source_keys(jobs: list[dict[str, Any]]) -> set[str]:
    return {str(job.get("source_key", "")) for job in jobs}


def make_job(
    source_key: str,
    fingerprint: str,
    upserts: list[dict[str, Any]],
    deletes: list[str],
    point_ids: list[str],
    cursor_updates: dict[str, str],
    created_at: str,
) -> dict[str, Any]:
    return {
        "source_key": source_key,
        "fingerprint": fingerprint,
        "upserts": upserts,
        "deletes": deletes,
        "point_ids": point_ids,
        "cursor_updates": cursor_updates,
        "created_at": created_at,
    }
