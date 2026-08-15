"""Private JSONL persistence for owner-confirmed coordination drafts."""
from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Iterable, TextIO

PENDING_CONFIRMS_ENV = "COORDINATION_PENDING_CONFIRMS"
DEFAULT_PENDING_CONFIRMS = Path("~/.hermes/coordination/pending-confirms.jsonl")


class PendingConfirmError(RuntimeError):
    """The private pending-confirm store cannot be trusted."""


@dataclass(frozen=True, slots=True)
class PendingConfirm:
    """Everything the no-agent watcher needs to bind a single confirmation."""

    draft_id: str
    sha256: str
    dm_channel_id: str
    dm_message_id: str
    slot: str
    summary: str
    correlation: str
    duration_min: int
    created: datetime
    key: str = field(default="", compare=False)
    kind: str | None = field(default=None, compare=False)
    surface: str | None = field(default=None, compare=False)
    channel_id: str = field(default="", compare=False)
    policy_version: int | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.key:
            object.__setattr__(self, "key", f"coord:{self.slot}")
        if not self.channel_id:
            object.__setattr__(self, "channel_id", self.dm_channel_id)
        elif self.channel_id != self.dm_channel_id:
            raise PendingConfirmError("pending confirmation channel binding is inconsistent")

    def as_json(self) -> str:
        """Render the stable JSONL schema with a UTC timestamp."""
        record: dict[str, str | int] = {
            "correlation": self.correlation,
            "created": self.created.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "dm_channel_id": self.dm_channel_id,
            "dm_message_id": self.dm_message_id,
            "draft_id": self.draft_id,
            "duration_min": self.duration_min,
            "key": self.key,
            "sha256": self.sha256,
            "slot": self.slot,
            "summary": self.summary,
        }
        if self.kind is not None and self.surface is not None and self.policy_version is not None:
            record.update(
                {
                    "channel_id": self.channel_id,
                    "kind": self.kind,
                    "policy_version": self.policy_version,
                    "surface": self.surface,
                }
            )
        return json.dumps(
            record,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class PendingConfirmStore:
    """Mode-600 JSONL store; a lock serializes append and watcher replacement."""

    def __init__(self, path: Path | None = None) -> None:
        configured = os.environ.get(PENDING_CONFIRMS_ENV, "")
        self.path = path or Path(configured or DEFAULT_PENDING_CONFIRMS).expanduser()
        self._lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")

    def append(self, entry: PendingConfirm) -> None:
        """Append one durable pending confirmation."""
        with self._lock():
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(entry.as_json() + "\n")
            self.path.chmod(0o600)

    def load(self) -> tuple[PendingConfirm, ...]:
        """Load and validate the complete store before an action is considered."""
        with self._lock():
            return self._read_unlocked()

    def replace(self, entries: Iterable[PendingConfirm]) -> None:
        """Atomically retain only entries that still await a decision."""
        with self._lock():
            self._write_unlocked(tuple(entries))

    def remove_completed(
        self,
        snapshot: tuple[PendingConfirm, ...],
        retained: tuple[PendingConfirm, ...],
    ) -> None:
        """Remove completed snapshot entries while retaining concurrent DM appends."""
        completed = {_entry_key(entry) for entry in snapshot} - {_entry_key(entry) for entry in retained}
        with self._lock():
            current = self._read_unlocked()
            self._write_unlocked(tuple(entry for entry in current if _entry_key(entry) not in completed))

    def drop(self, expected: PendingConfirm) -> None:
        """Compare-and-swap removal bound to the draft, message, and content hash."""
        binding = expected.draft_id, expected.dm_message_id, expected.sha256
        with self._lock():
            current = self._read_unlocked()
            self._write_unlocked(
                tuple(
                    entry
                    for entry in current
                    if (entry.draft_id, entry.dm_message_id, entry.sha256) != binding
                )
            )

    def _read_unlocked(self) -> tuple[PendingConfirm, ...]:
        if not self.path.exists():
            return ()
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise PendingConfirmError(f"pending store read failed: {self.path}") from error
        try:
            return tuple(_parse_entry(json.loads(line)) for line in lines if line)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise PendingConfirmError("pending store schema is invalid") from error

    def _write_unlocked(self, entries: tuple[PendingConfirm, ...]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        try:
            temporary.write_text("".join(f"{entry.as_json()}\n" for entry in entries), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(self.path)
            self.path.chmod(0o600)
        except OSError as error:
            raise PendingConfirmError(f"pending store write failed: {self.path}") from error

    def _lock(self) -> _StoreLock:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = self._lock_path.open("a", encoding="utf-8")
        self._lock_path.chmod(0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return _StoreLock(handle)


@dataclass(frozen=True, slots=True)
class _StoreLock:
    handle: TextIO

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def _parse_entry(raw: dict[str, str | int]) -> PendingConfirm:
    created = datetime.fromisoformat(str(raw["created"]).replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise ValueError("created is naive")
    duration = raw["duration_min"]
    if not isinstance(duration, int) or duration <= 0:
        raise ValueError("duration_min is invalid")
    values = (
        "draft_id", "sha256", "dm_channel_id", "dm_message_id", "slot", "summary", "correlation",
    )
    if not all(isinstance(raw[name], str) and raw[name] for name in values):
        raise ValueError("required string is missing")
    # correlation identifies a protocol run; slot alone is the stable approval key.
    key = raw.get("key") or f"coord:{raw['slot']}"
    kind, surface = raw.get("kind"), raw.get("surface")
    channel_id = raw.get("channel_id", raw["dm_channel_id"])
    policy_version = raw.get("policy_version")
    if (
        not isinstance(key, str)
        or not key
        or not isinstance(kind, str | None)
        or not isinstance(surface, str | None)
        or not isinstance(channel_id, str)
        or not isinstance(policy_version, int | None)
    ):
        raise ValueError("key is invalid")
    return PendingConfirm(
        draft_id=raw["draft_id"], sha256=raw["sha256"], dm_channel_id=raw["dm_channel_id"],
        dm_message_id=raw["dm_message_id"], slot=raw["slot"], summary=raw["summary"],
        correlation=raw["correlation"], duration_min=duration, created=created.astimezone(UTC), key=key,
        kind=kind, surface=surface, channel_id=channel_id, policy_version=policy_version,
    )


def _entry_key(entry: PendingConfirm) -> tuple[str, str]:
    return entry.draft_id, entry.dm_message_id
