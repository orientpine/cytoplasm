"""Watcher HMAC authorization artifacts for the calendar confirm gate (W3-1).

`calendar_confirm` 의 세 확인 경로 중 워처 경로만 이 모듈을 쓴다. 워처 부모가 정확한
메시지·드래프트 해시·소유자 전용 반응·취소 우선순위를 스스로 검증한 뒤 짧은 수명의
서명된 1회용 인가를 만들고, 자식 `calendar_cli confirm` 이 Discord 를 두 번 묻지 않고
그 인가만으로 실행 여부를 판정한다.

경계가 분리된 이유: 인가 파일의 생성·검증·소비는 서명 키·파일 권한·잠금·nonce 소진까지
자기 완결적이며, 확인 전송(DM 스캔·반응 조회·결과 통지)과 함께 살 이유가 없다. 소유자
판정(`owner_id`)과 pending 조회는 여전히 `calendar_confirm` 이 소유하므로 호출자가
`WatcherAuthorizationBindings` 로 주입한다 — 이 모듈은 그 둘의 두 번째 사본을 만들지 않는다.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any, TypeAlias

_gate = import_module("calendar_gate")
GateError = _gate.GateError
_pending = import_module("calendar_pending")
PendingConfirm = _pending.PendingConfirm

DraftRecord: TypeAlias = dict[str, str | list[str]]

WATCH_AUTH_TTL = timedelta(minutes=5)
WATCH_AUTH_MAX_BYTES = 16_384
WATCH_AUTH_VERSION = 1


@dataclass(frozen=True, slots=True)
class WatcherAuthorizationBindings:
    """소유자·pending 판정의 유일한 원천을 호출 시점에 주입받는다.

    `calendar_confirm` 이 두 판정을 소유한다. 여기서 다시 구현하면 한쪽만 바뀌어도
    인가 검증이 조용히 낡는다 — 그래서 값이 아니라 호출자의 함수를 그대로 받는다.
    """

    owner_id: Callable[[], str]
    pending_entry: Callable[[str], PendingConfirm | None]


def create_watcher_authorization(
    entry: PendingConfirm, owner: str, *, now: datetime | None = None
) -> Path:
    """Create a short-lived, signed, one-use authorization for the watcher child.

    Only the watcher calls this after independently validating the exact message,
    draft hash, owner-only reaction, and cancel precedence.  The child validates
    every binding against its current draft and pending store without querying
    Discord a second time.
    """
    observed = (now or datetime.now(UTC)).astimezone(UTC)
    if not owner:
        raise GateError("watcher 승인 소유자가 비어 있습니다", 3)
    nonce = secrets.token_hex(16)
    payload: dict[str, str | int] = {
        "action": "approve",
        "dm_channel_id": entry.dm_channel_id,
        "dm_message_id": entry.dm_message_id,
        "draft_id": entry.draft_id,
        "expires": (observed + WATCH_AUTH_TTL).isoformat(),
        "nonce": nonce,
        "observed": observed.isoformat(),
        "owner_id": owner,
        "pending_created": entry.created.astimezone(UTC).replace(microsecond=0).isoformat(),
        "sha256": entry.sha256,
        "version": WATCH_AUTH_VERSION,
    }
    payload["signature"] = _watch_auth_signature(payload)
    path = _watch_auth_dir() / f"{nonce}.json"
    _write_private_exclusive(path, _canonical_json(payload).encode("utf-8"))
    return path


def consume_watcher_authorization(
    draft: DraftRecord, authorization_path: Path, bindings: WatcherAuthorizationBindings
) -> str:
    """Atomically consume one watcher authorization bound to this exact draft/DM."""
    auth_dir = _watch_auth_dir()
    path = authorization_path.expanduser()
    if path.parent != auth_dir or path.suffix != ".json" or path.is_symlink():
        raise GateError("watcher 승인 파일 경로가 신뢰 경계 밖입니다", 1)
    lock_path = auth_dir / ".lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        raw = _read_private_file(path)
        try:
            record = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise GateError("watcher 승인 파일 형식이 올바르지 않습니다", 1) from error
        if type(record) is not dict:
            raise GateError("watcher 승인 파일 형식이 올바르지 않습니다", 1)
        signature = record.pop("signature", None)
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, _watch_auth_signature(record)
        ):
            raise GateError("watcher 승인 서명이 일치하지 않습니다", 1)
        _validate_watcher_authorization(draft, record, bindings)
        nonce = str(record["nonce"])
        spent_path = auth_dir / f"{nonce}.spent"
        try:
            _write_private_exclusive(spent_path, b"spent\n")
        except FileExistsError as error:
            raise GateError("watcher 승인이 이미 사용되었습니다", 1) from error
        try:
            path.unlink()
        except OSError as error:
            spent_path.unlink(missing_ok=True)
            raise GateError("watcher 승인 파일 소비에 실패했습니다", 3) from error
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    return f"reaction:{record['dm_message_id']}"


def _validate_watcher_authorization(
    draft: DraftRecord, record: dict[str, Any], bindings: WatcherAuthorizationBindings
) -> None:
    required_types = {
        "action": str,
        "dm_channel_id": str,
        "dm_message_id": str,
        "draft_id": str,
        "expires": str,
        "nonce": str,
        "observed": str,
        "owner_id": str,
        "pending_created": str,
        "sha256": str,
        "version": int,
    }
    if set(record) != set(required_types) or any(
        type(record.get(name)) is not expected for name, expected in required_types.items()
    ):
        raise GateError("watcher 승인 필드가 올바르지 않습니다", 1)
    if record["version"] != WATCH_AUTH_VERSION or record["action"] != "approve":
        raise GateError("watcher 승인 동작이 올바르지 않습니다", 1)
    if record["draft_id"] != draft.get("id") or record["sha256"] != draft.get("sha256"):
        raise GateError("watcher 승인 드래프트 바인딩이 일치하지 않습니다", 1)
    if record["owner_id"] != bindings.owner_id():
        raise GateError("watcher 승인 소유자가 일치하지 않습니다", 1)
    try:
        observed = _parse_ts(record["observed"])
        expires = _parse_ts(record["expires"])
        pending_created = _parse_ts(record["pending_created"])
    except (TypeError, ValueError) as error:
        raise GateError("watcher 승인 시각이 올바르지 않습니다", 1) from error
    now = datetime.now(UTC)
    if observed > now + timedelta(seconds=30) or expires <= now or expires - observed != WATCH_AUTH_TTL:
        raise GateError("watcher 승인이 만료되었거나 시각이 올바르지 않습니다", 1)
    entry = bindings.pending_entry(str(draft["id"]))
    if entry is None:
        raise GateError("watcher 승인용 pending confirm이 없습니다", 1)
    expected = (
        entry.dm_channel_id,
        entry.dm_message_id,
        entry.created.astimezone(UTC),
        entry.sha256,
    )
    actual = (
        record["dm_channel_id"],
        record["dm_message_id"],
        pending_created.astimezone(UTC),
        record["sha256"],
    )
    if actual != expected:
        raise GateError("watcher 승인 메시지 바인딩이 일치하지 않습니다", 1)


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _watch_auth_dir() -> Path:
    gate_dir = Path(os.environ.get("CALENDAR_GATE_DIR", "~/.hermes/calendar-gate")).expanduser()
    path = gate_dir / "watch-authorizations"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise GateError("watcher 승인 디렉터리 권한이 안전하지 않습니다", 3)
    return path


def _watch_auth_key() -> bytes:
    path = _watch_auth_dir() / ".hmac-key"
    try:
        _write_private_exclusive(path, secrets.token_bytes(32))
    except FileExistsError:
        pass
    value = _read_private_file(path)
    if len(value) != 32:
        raise GateError("watcher 승인 서명 키가 올바르지 않습니다", 3)
    return value


def _watch_auth_signature(record: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in record.items() if key != "signature"}
    return hmac.new(_watch_auth_key(), _canonical_json(unsigned).encode("utf-8"), hashlib.sha256).hexdigest()


def _canonical_json(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _write_private_exclusive(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("private file write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_private_file(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > WATCH_AUTH_MAX_BYTES
        ):
            raise GateError("watcher 승인 파일 권한 또는 크기가 올바르지 않습니다", 1)
        content = bytearray()
        while len(content) < metadata.st_size:
            chunk = os.read(descriptor, metadata.st_size - len(content))
            if not chunk:
                break
            content.extend(chunk)
        return bytes(content)
    finally:
        os.close(descriptor)
