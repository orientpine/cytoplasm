"""Key-level lease + posting journal for owner-approval producers.

The lease is a NON-BLOCKING ``fcntl.flock`` on a per-key file that is NEVER
unlinked — the kernel releases it on crash, so a SIGKILL cannot wedge a key.
Producer and watcher take the SAME lease, so a 900s effect legitimately defers
the producer instead of racing it.
"""
from __future__ import annotations

import fcntl
import hashlib
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

    def enrich(
        self,
        key: str,
        action_hash: str,
        message_id: str,
        channel_id: str,
    ) -> None:
        reservation = self.outstanding(key)
        if reservation is None or (
            reservation.get("key"),
            reservation.get("action_hash"),
        ) != (key, action_hash):
            raise RuntimeError("posting journal reservation changed before enrichment")
        payload = {
            "action_hash": action_hash,
            "at": reservation.get("at", ""),
            "channel_id": channel_id,
            "key": key,
            "message_id": message_id,
        }
        path = self._path(key)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

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


class ReminderJournalError(RuntimeError):
    """A reminder ledger cannot be read or updated safely."""


@dataclass(frozen=True, slots=True)
class ReminderHistory:
    slot: int
    state: str
    due_at: str
    claimed_at: str
    sent_at: str | None = None


@dataclass(frozen=True, slots=True)
class ReminderJournal:
    """Durable reminder claims keyed by approval key and source message id.

    Callers hold the approval key lease while using this ledger. Including the source
    message id makes superseding an approval start a fresh schedule without confusing
    its claims with those of the replaced message.
    """

    root: Path

    def _path(self, key: str, message_id: str) -> Path:
        digest = hashlib.sha256(f"{key}\0{message_id}".encode()).hexdigest()
        return self.root / f"{digest}.reminders.json"

    @staticmethod
    def _empty(key: str, message_id: str) -> dict[str, object]:
        return {"version": 1, "key": key, "message_id": message_id,
                "next_due_at": None, "retired": None, "slots": {}}

    def _read(self, key: str, message_id: str) -> dict[str, object]:
        path = self._path(key, message_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty(key, message_id)
        except (OSError, json.JSONDecodeError) as error:
            raise ReminderJournalError(str(path)) from error
        if (not isinstance(data, dict) or data.get("version") != 1
                or data.get("key") != key or data.get("message_id") != message_id
                or not isinstance(data.get("slots"), dict)):
            raise ReminderJournalError(str(path))
        return data

    def _write(self, key: str, message_id: str, data: dict[str, object]) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        path = self._path(key, message_id)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
            temporary.chmod(0o600)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def is_retired(self, key: str, message_id: str) -> bool:
        return self._read(key, message_id).get("retired") is not None

    def retire(self, key: str, message_id: str, status: str, at: str) -> None:
        data = self._read(key, message_id)
        if data.get("retired") is None:
            data["retired"] = {"status": status, "at": at}
            data["next_due_at"] = None
            self._write(key, message_id, data)

    def claim(self, key: str, message_id: str, slot: int, due_at: str,
              next_due_at: str, at: str) -> bool:
        data = self._read(key, message_id)
        if data.get("retired") is not None:
            return False
        slots = data["slots"]
        assert isinstance(slots, dict)
        claim_key = str(slot)
        if claim_key in slots:
            return False
        slots[claim_key] = {"state": "claimed", "due_at": due_at,
                            "claimed_at": at, "sent_at": None}
        data["next_due_at"] = next_due_at
        self._write(key, message_id, data)
        return True

    def mark_sent(self, key: str, message_id: str, slot: int,
                  sent_at: str, next_due_at: str) -> None:
        data = self._read(key, message_id)
        slots = data["slots"]
        assert isinstance(slots, dict)
        record = slots.get(str(slot))
        if not isinstance(record, dict) or record.get("state") != "claimed":
            raise ReminderJournalError(f"unclaimed reminder slot: {slot}")
        record.update({"state": "sent", "sent_at": sent_at})
        data["next_due_at"] = next_due_at
        self._write(key, message_id, data)

    def history(self, key: str, message_id: str) -> tuple[ReminderHistory, ...]:
        slots = self._read(key, message_id)["slots"]
        assert isinstance(slots, dict)
        history: list[ReminderHistory] = []
        try:
            for slot, record in slots.items():
                if not isinstance(record, dict):
                    raise TypeError
                history.append(ReminderHistory(
                    slot=int(slot), state=str(record["state"]),
                    due_at=str(record["due_at"]), claimed_at=str(record["claimed_at"]),
                    sent_at=None if record.get("sent_at") is None else str(record["sent_at"]),
                ))
        except (KeyError, TypeError, ValueError) as error:
            raise ReminderJournalError("malformed reminder history") from error
        return tuple(sorted(history, key=lambda item: item.slot))


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
