"""Personal conversation digest source (Hermes state.db, read-only).

LLM-free per plan v2.2: builds deterministic per-(session, KST day) digests
of user/assistant turns instead of an LLM summary. A day bucket only becomes
eligible once its newest message is older than the settle window, so
fingerprints stay stable and re-runs cause zero re-ingest churn.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..chunking import chunk_markdown
from ..documents import LogicalDocument, build_document
from ..metadata import build_metadata

_KST = timezone(timedelta(hours=9))
_SETTLE_HOURS = 6
_MAX_MESSAGES_PER_DAY = 60
_MAX_CHARS_PER_MESSAGE = 300


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def scan_conversations(
    db_path: Path,
    perspective: dict[str, str],
    max_chunk_chars: int,
    now: datetime | None = None,
) -> list[LogicalDocument]:
    if not db_path.exists():
        return []
    current = now or datetime.now(tz=timezone.utc)
    settle_cutoff = (current - timedelta(hours=_SETTLE_HOURS)).timestamp()
    documents: list[LogicalDocument] = []
    connection = _connect_readonly(db_path)
    try:
        sessions = connection.execute(
            "SELECT id, COALESCE(title, '') FROM sessions ORDER BY id"
        ).fetchall()
        for session_id, title in sessions:
            rows = connection.execute(
                "SELECT role, content, timestamp FROM messages "
                "WHERE session_id = ? AND role IN ('user', 'assistant') "
                "AND content IS NOT NULL AND content != '' "
                "ORDER BY timestamp, id",
                (session_id,),
            ).fetchall()
            buckets: dict[str, list[tuple[str, str]]] = {}
            newest: dict[str, float] = {}
            for role, content, timestamp in rows:
                moment = datetime.fromtimestamp(float(timestamp), tz=_KST)
                day = moment.strftime("%Y-%m-%d")
                buckets.setdefault(day, []).append((str(role), str(content)))
                newest[day] = max(newest.get(day, 0.0), float(timestamp))
            for day, turns in sorted(buckets.items()):
                if newest[day] > settle_cutoff:
                    continue
                documents.append(
                    _digest_document(
                        str(session_id), title, day, turns, perspective, max_chunk_chars
                    )
                )
    finally:
        connection.close()
    return documents


def _digest_document(
    session_id: str,
    title: str,
    day: str,
    turns: list[tuple[str, str]],
    perspective: dict[str, str],
    max_chunk_chars: int,
) -> LogicalDocument:
    header = f"# 대화 다이제스트 {day} (session {session_id})"
    if title:
        header += f"\n제목: {title}"
    lines = [header]
    for role, content in turns[:_MAX_MESSAGES_PER_DAY]:
        text = " ".join(content.split())[:_MAX_CHARS_PER_MESSAGE]
        lines.append(f"[{role}] {text}")
    body = "\n".join(lines)
    source_key = f"conversation:{session_id}:{day}"
    base_metadata = build_metadata(
        perspective,
        "conversation",
        {
            "session_id": session_id,
            "day": day,
            "title": title or f"session {session_id}",
            "turns": str(min(len(turns), _MAX_MESSAGES_PER_DAY)),
        },
    )
    return build_document(source_key, chunk_markdown(body, max_chunk_chars), base_metadata)
