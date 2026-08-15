"""Strict, lock-serialized JSONL queue for repair report requests."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final


REASON_CODES: Final = frozenset(
    {
        "applied",
        "sandbox_rejected",
        "bank_red",
        "bank_failed_reverted",
        "owner_cancelled",
        "approval_expired",
        "unspecified",
    }
)
_QUEUE_ENV: Final = "REPAIR_REPORT_QUEUE"
_DEFAULT_QUEUE: Final = "/srv/autophagy-repair-report-queue"
_DEFAULT_ACK: Final = "/srv/autophagy-repair-report-ack"
_LOCK_NAME: Final = "queue.lock"
_PENDING_NAME: Final = "pending.jsonl"
_RECORD_KEYS: Final = frozenset(
    {"request_id", "operation", "ticket_id", "reason_code", "occurrence", "mac", "created"}
)
_REQUEST_ID: Final = re.compile(r"^[0-9a-f]{32}$")
_TICKET_ID: Final = re.compile(r"^t_[A-Za-z0-9]{1,64}$")
_OCCURRENCE: Final = re.compile(r"^[0-9]{1,9}$")
_MAC: Final = re.compile(r"^[0-9a-f]{64}$")
_MAX_LINE_BYTES: Final = 512


class InvalidReportRequestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReportRequest:
    """One capability-bound request for a repair card lifecycle report."""

    request_id: str
    operation: str
    ticket_id: str
    reason_code: str
    occurrence: str
    mac: str
    created: str


def queue_dir() -> Path:
    return Path(os.environ.get(_QUEUE_ENV, _DEFAULT_QUEUE))


def lock_path() -> Path:
    return queue_dir() / _LOCK_NAME


def ack_dir() -> Path:
    return Path(os.environ.get("REPAIR_REPORT_ACK", _DEFAULT_ACK))


def parse_line(raw: bytes) -> ReportRequest | None:
    """Parse one exact queue record, returning ``None`` for every invalid input."""
    if len(raw) > _MAX_LINE_BYTES:
        return None
    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(payload, dict) or set(payload) != _RECORD_KEYS:
        return None
    if not all(isinstance(value, str) for value in payload.values()):
        return None

    request = ReportRequest(
        request_id=payload["request_id"],
        operation=payload["operation"],
        ticket_id=payload["ticket_id"],
        reason_code=payload["reason_code"],
        occurrence=payload["occurrence"],
        mac=payload["mac"],
        created=payload["created"],
    )
    if _REQUEST_ID.fullmatch(request.request_id) is None:
        return None
    if _TICKET_ID.fullmatch(request.ticket_id) is None:
        return None
    if request.operation not in {"complete", "reopen"}:
        return None
    if request.reason_code not in REASON_CODES:
        return None
    if (request.operation == "complete") != (request.reason_code == "applied"):
        return None
    if _OCCURRENCE.fullmatch(request.occurrence) is None:
        return None
    if _MAC.fullmatch(request.mac) is None:
        return None
    try:
        created = datetime.fromisoformat(request.created)
    except (ValueError, OverflowError):
        return None
    if created.tzinfo is None or created.utcoffset() is None:
        return None
    return request


def line_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def semantic_key(request: ReportRequest) -> str:
    payload = f"{request.ticket_id}|{request.occurrence}|{request.operation}|{request.reason_code}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _serialize(request: ReportRequest) -> bytes | None:
    payload = {
        "request_id": request.request_id,
        "operation": request.operation,
        "ticket_id": request.ticket_id,
        "reason_code": request.reason_code,
        "occurrence": request.occurrence,
        "mac": request.mac,
        "created": request.created,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return encoded if parse_line(encoded) == request else None


def _append_locked(request: ReportRequest) -> None:
    """Append one request while the caller holds the queue's exclusive lock."""
    encoded = _serialize(request)
    if encoded is None:
        raise InvalidReportRequestError
    descriptor = os.open(
        queue_dir() / _PENDING_NAME,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC,
        0o640,
    )
    try:
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "wb", buffering=0, closefd=False) as pending:
            written = pending.write(encoded + b"\n")
            if written != len(encoded) + 1:
                raise OSError("repair report queue append was incomplete")
            pending.flush()
            os.fsync(pending.fileno())
    finally:
        os.close(descriptor)


def enqueue(request: ReportRequest) -> None:
    """Append a request under the pre-created queue lock, never raising."""
    directory = queue_dir()
    if not directory.is_dir():
        print("repair report enqueue skipped: queue unavailable", file=sys.stderr)
        return
    try:
        with lock_path().open("rb") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            _append_locked(request)
    except (OSError, UnicodeError, ValueError):
        print("repair report enqueue skipped: queue unavailable", file=sys.stderr)


def enqueue_if_missing_semantic(request: ReportRequest) -> bool:
    """Append only when no queued request has the same semantic identity."""
    directory = queue_dir()
    if not directory.is_dir():
        print("repair report enqueue skipped: queue unavailable", file=sys.stderr)
        return False
    try:
        with lock_path().open("rb") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            pending_path = directory / _PENDING_NAME
            if pending_path.exists():
                with pending_path.open("rb") as pending:
                    target = semantic_key(request)
                    for raw in pending:
                        queued = parse_line(raw)
                        if queued is not None and semantic_key(queued) == target:
                            return False
            _append_locked(request)
            return True
    except (OSError, UnicodeError, ValueError):
        print("repair report enqueue skipped: queue unavailable", file=sys.stderr)
        return False


def read_receipt(name: str) -> dict[str, str | int | float | bool | None] | None:
    try:
        payload = json.loads((ack_dir() / name).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(payload, dict) or "terminal_at" not in payload:
        return None
    return payload


def _replace_pending_locked(lines: list[bytes]) -> None:
    directory = queue_dir()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".pending.", dir=directory)
    temporary = Path(temporary_name)
    replaced = False
    try:
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "wb") as pending:
            payload = b"".join(lines)
            if pending.write(payload) != len(payload):
                raise OSError("repair report queue compaction write was incomplete")
            pending.flush()
            os.fsync(pending.fileno())
        os.replace(temporary, directory / _PENDING_NAME)
        replaced = True
        directory_descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if not replaced:
            temporary.unlink(missing_ok=True)


def _terminal_state(raw: bytes, request: ReportRequest) -> tuple[bool, bool]:
    digest = line_digest(raw)
    key = semantic_key(request)
    conflict = read_receipt(f"conflict-{digest}.json") is not None
    receipt = read_receipt(f"{request.request_id}.json")
    identity_ack = receipt is not None and receipt.get("line_digest") == digest and receipt.get("semantic_key") == key
    semantic = read_receipt(f"sem-{key}.json") is not None
    return conflict or identity_ack, conflict or identity_ack or semantic


def compact() -> int:
    if not ack_dir().is_dir() or not queue_dir().is_dir():
        return 0
    pending_path = queue_dir() / _PENDING_NAME
    try:
        with lock_path().open("rb") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            if not pending_path.exists():
                return 0
            kept: list[bytes] = []
            removed = 0
            stale = False
            with pending_path.open("rb") as pending:
                for raw in pending:
                    request = parse_line(raw)
                    has_terminal = False
                    if request is None:
                        terminal = read_receipt(f"invalid-{line_digest(raw)}.json") is not None
                    else:
                        terminal, has_terminal = _terminal_state(raw, request)
                    if terminal:
                        removed += 1
                        continue
                    kept.append(raw)
                    if request is not None and not has_terminal:
                        created = datetime.fromisoformat(request.created)
                        stale = stale or datetime.now(tz=UTC) - created.astimezone(UTC) > timedelta(days=14)
            _replace_pending_locked(kept)
            if stale:
                print("repair report compact warning: stale unreceipted backlog", file=sys.stderr)
            if len(kept) > 5000:
                print("repair report compact warning: unprocessed backlog exceeds 5000 lines", file=sys.stderr)
            return removed
    except (OSError, UnicodeError, ValueError):
        print("repair report compact skipped: queue unavailable", file=sys.stderr)
        return 0


def _main(argv: list[str]) -> int:
    if "--compact" not in argv:
        return 2
    print(compact())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
