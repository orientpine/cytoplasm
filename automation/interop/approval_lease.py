"""Key-level lease + posting journal for owner-approval producers.

The lease is a NON-BLOCKING ``fcntl.flock`` on a per-key file that is NEVER
unlinked — the kernel releases it on crash, so a SIGKILL cannot wedge a key.
Producer and watcher take the SAME lease, so a 900s effect legitimately defers
the producer instead of racing it.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def slug(key: str) -> str:
    """Filesystem-safe, collision-free key encoding (':' and '/' are escaped, not dropped)."""
    return _UNSAFE.sub(lambda m: f"%{ord(m.group()):02x}", key)


class ApprovalLease(Protocol):
    """Mutual exclusion for one logical approval key."""

    @contextmanager
    def hold(self, key: str) -> Iterator[bool]:
        """Yield True iff this process owns the key; False means someone else does."""
        ...


@dataclass(frozen=True, slots=True)
class FileKeyLease:
    """Default lease — one never-unlinked lock file per key under ``root``."""

    root: Path

    @contextmanager
    def hold(self, key: str) -> Iterator[bool]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.root / f"{slug(key)}.lease"
        handle = path.open("a", encoding="utf-8")
        try:
            path.chmod(0o600)
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                yield False          # held elsewhere — caller must change NOTHING
                return
            try:
                yield True
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()           # 파일은 절대 unlink 하지 않는다 — 크래시 시 커널이 해제


@dataclass(frozen=True, slots=True)
class PostingJournal:
    """Durable 'I am about to post' reservation — makes a POST/commit crash LOUD, not silent."""

    root: Path

    def _path(self, key: str) -> Path:
        return self.root / f"{slug(key)}.posting.json"

    def reserve(self, key: str, action_hash: str, at: str) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._path(key)
        path.write_text(
            json.dumps({"action_hash": action_hash, "at": at, "key": key}, sort_keys=True),
            encoding="utf-8",
        )
        path.chmod(0o600)
        os.sync()                    # 저널이 POST보다 먼저 디스크에 닿아야 의미가 있다

    def outstanding(self, key: str) -> dict[str, str] | None:
        try:
            data = json.loads(self._path(key).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return {"action_hash": "", "at": "", "key": key}   # 못 읽음 = 미결로 간주(fail-closed)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else None

    def clear(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def abandon(key: str, journal: PostingJournal, audit_path: Path) -> None:
    audit_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = json.dumps(
        {"event": "posting-abandoned", "key": key, "reservation": journal.outstanding(key)},
        sort_keys=True,
    )
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    audit_path.chmod(0o600)
    journal.clear(key)
