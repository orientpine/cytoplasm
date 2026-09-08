"""lifelog·meeting 원본의 비공개 SHA 원장을 원자적으로 기록한다."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    audio_sha256: str
    drive_file_id: str
    duration_ms: int
    transcript_stem: str
    created_at: str
    domain: Literal["lifelog", "meeting"] = "lifelog"

    def row(self) -> dict[str, object]:
        if not re.fullmatch(r"[0-9a-f]{64}", self.audio_sha256):
            raise ValueError("audio sha256")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.drive_file_id):
            raise ValueError("drive file id")
        if type(self.duration_ms) is not int or self.duration_ms < 0:
            raise ValueError("duration_ms")
        if self.domain not in ("lifelog", "meeting") or not self.transcript_stem:
            raise ValueError("manifest fields")
        _ = datetime.fromisoformat(self.created_at)
        return {"recording_id": self.audio_sha256[:8], "domain": self.domain,
                "audio_sha256": self.audio_sha256, "drive_file_id": self.drive_file_id,
                "duration_ms": self.duration_ms, "transcript_stem": self.transcript_stem,
                "created_at": self.created_at}


def outside_checkout(path: Path) -> Path:
    """.git 파일인 worktree와 심링크 목적지도 거부한다."""
    resolved = path.expanduser().resolve()
    if any((parent / ".git").exists() for parent in (resolved, *resolved.parents)):
        raise ValueError("STT-EVAL-ROOT-REFUSED")
    return resolved


def manifest_path(env: Mapping[str, str]) -> Path:
    home = Path(env.get("HOME") or Path.home())
    raw = env.get("STT_EVAL_ROOT", str(home / ".hermes" / "stt-eval"))
    root = home / raw[2:] if raw.startswith("~/") else Path(raw)
    return outside_checkout(root / "manifest.jsonl")


def audio_digest(path: Path) -> str:
    if path.stat().st_size == 0:
        raise ValueError("empty audio")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@contextmanager
def manifest_lock(path: Path) -> Iterator[None]:
    lock = outside_checkout(path.with_suffix(".lock"))
    lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    result: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = cast(object, json.loads(line))
        if not isinstance(row, dict):
            raise ValueError("manifest object")
        result.append(cast(dict[str, object], row))
    return result


def contains(path: Path, digest: str) -> bool:
    return any(row.get("audio_sha256") == digest for row in rows(path))


def append_locked(path: Path, entry: ManifestEntry) -> bool:
    """호출자가 lock을 소유한다. 중단 시 부분 JSONL 대신 이전 원장을 남긴다."""
    row = entry.row()
    previous = rows(path)
    if any(item.get("audio_sha256") == entry.audio_sha256 for item in previous):
        return False
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".manifest-", delete=False) as handle:
            temporary = Path(handle.name)
            for item in (*previous, row):
                _ = handle.write((json.dumps(item, ensure_ascii=True) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        _ = temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def record_manifest(entry: ManifestEntry, env: Mapping[str, str]) -> bool:
    """기존 Drive ID·digest를 가진 meeting watcher도 업로드 없이 사용한다."""
    _ = entry.row()
    path = manifest_path(env)
    with manifest_lock(path):
        return append_locked(path, entry)
