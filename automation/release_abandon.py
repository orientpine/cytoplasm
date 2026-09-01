"""The audited abandon for a decided release approval — release kind's escape hatch.

승인 lifecycle 은 소유자가 결정한(취소 포함) 요청을 절대 파괴하지 않는다(L3). 그래서
소유자가 ⛔ 한 뒤 남은 ``pending/release.json`` 은 다음 ``release.sh`` 의 요청 게시를
``owner-decided`` 로 DEFER 시킨다. 결정은 이미 소비됐는데 파일만 남아 다음 릴리스를
막는 가용성 결함이다. 스킬 게이트에는 ``skill_gate_retire.abandon`` 이 있었고, 이 모듈이
release kind 의 같은 탈출구다 — 인가 경계는 하나도 넓히지 않는다.

A1  운영자가 이름 댄 version·head_sha·message_id 세 필드가 저장된 레코드와 **정확히**
    일치할 때만 움직인다. 맹목적 삭제는 없다(``skill_gate_retire`` R1).
A2  요청 경로가 잡는 것과 같은 키 리스 아래에서만 변경한다(R2).
A3  Discord 를 호출하지 않는다 — 소유자의 ⛔ 는 그대로 남아 보인다(R3).
A4  감사 줄을 fsync 한 뒤에야 레코드가 pending 을 떠난다. 크래시는 감사 없는 삭제가 아니라
    복구 가능한 레코드를 남긴다(R5).
A5  레코드는 지워지지 않고 release-history 규약대로 0600 archive 로 바이트 그대로 옮겨진다 —
    ⛔ 된 승인의 증적은 릴리스가 지나가도 남는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Final

from automation import skill_gate, skill_gate_retire
from automation.release_retire import archive_bytes
from automation.release_spec import ReleaseSpecError
from automation.skill_gate_request import lease
from automation.skill_gate_specs import mask

ABANDON_ARCHIVE_DIRNAME: Final = "release-abandoned"
ABANDON_REFUSAL_EXIT: Final = 1
#: ``ReleaseSpec.key()`` / ``record_name()`` — 요청 경로가 잡는 리스와 같은 키, 같은 파일.
RELEASE_KEY: Final = "release"


class ReleaseAbandon(StrEnum):
    """Every terminal state of an abandon attempt, as its machine-readable token."""

    ABANDONED = "abandoned"
    RECORD_ABSENT = "record-absent"
    IDENTITY_MISMATCH = "identity-mismatch"
    STORE_UNREADABLE = "store-unreadable"
    LEASE_HELD = "lease-held"
    AUDIT_FAILED = "audit-failed"
    ARCHIVE_FAILED = "archive-failed"


@dataclass(frozen=True, slots=True)
class ReleaseAbandonOrder:
    """The operator override: which decided release to retire, why, and on whose authority."""

    version: str
    head_sha: str
    message_id: str
    reason: str
    actor: str


@dataclass(frozen=True, slots=True)
class ReleaseAbandoned:
    """One abandon outcome mapped onto the stdout/stderr and exit-code contract."""

    outcome: ReleaseAbandon
    exit_code: int
    message: str
    archived: Path | None = None


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _refused(outcome: ReleaseAbandon) -> ReleaseAbandoned:
    return ReleaseAbandoned(
        outcome, ABANDON_REFUSAL_EXIT, f"RELEASE-ABANDON-REFUSED reason={outcome.value}"
    )


def _matched(encoded: bytes, order: ReleaseAbandonOrder) -> dict[str, str] | ReleaseAbandon:
    """The stored record iff it STILL binds this exact (version, HEAD, message id)."""
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError:
        return ReleaseAbandon.STORE_UNREADABLE
    if not isinstance(decoded, dict):
        return ReleaseAbandon.STORE_UNREADABLE
    record = {str(name): str(value) for name, value in decoded.items()}
    named = (order.version, order.head_sha, order.message_id)
    if not all(named):
        return ReleaseAbandon.IDENTITY_MISMATCH
    stored = (
        record.get("version", ""),
        record.get("head_sha", ""),
        record.get("message_id", ""),
    )
    return record if stored == named else ReleaseAbandon.IDENTITY_MISMATCH


def _append_audit(path: Path, line: dict[str, str]) -> None:
    """Durable: the override reaches the disk before the record it retires moves."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        _ = handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _archive_name(head_sha: str, encoded: bytes) -> str:
    """release-history 규약(``{head}.json``)에 바이트 지문을 더한 이름.

    같은 HEAD 가 ⛔ 뒤 다시 요청되면 nonce 가 달라 레코드 바이트도 달라진다. 이름이
    HEAD 뿐이면 두 번째 abandon 이 충돌로 막혀 탈출구 자체가 막힌다 — 지문이 그 wedge 를
    없애고, 같은 바이트의 재실행은 여전히 같은 파일로 수렴한다(멱등).
    """
    return f"{head_sha}.{sha256(encoded).hexdigest()[:12]}.json"


def abandon(
    gate_dir: Path, order: ReleaseAbandonOrder, audit_path: Path
) -> ReleaseAbandoned:
    """Operator override: refuses on ANY field mismatch, audits first, never deletes the message."""
    record_path = gate_dir / "pending" / f"{RELEASE_KEY}.json"
    with lease(gate_dir).hold(RELEASE_KEY) as owned:
        if not owned:
            return _refused(ReleaseAbandon.LEASE_HELD)
        try:
            encoded = record_path.read_bytes()
        except FileNotFoundError:
            return _refused(ReleaseAbandon.RECORD_ABSENT)
        except OSError:
            return _refused(ReleaseAbandon.STORE_UNREADABLE)
        found = _matched(encoded, order)
        if isinstance(found, ReleaseAbandon):
            return _refused(found)
        name = _archive_name(order.head_sha, encoded)
        line = {
            "action_hash": found.get("action_hash", ""),
            "actor": order.actor,
            "archive": name,
            "event": "release-abandon",
            "head_sha": found["head_sha"],
            "key": RELEASE_KEY,
            "message_id": found["message_id"],
            "reason": order.reason,
            "timestamp": _utc_now(),
            "version": found["version"],
        }
        try:
            _append_audit(audit_path, line)
        except OSError:
            return _refused(ReleaseAbandon.AUDIT_FAILED)
        try:
            archived = archive_bytes(gate_dir / ABANDON_ARCHIVE_DIRNAME, name, encoded)
            record_path.unlink()
        except (ReleaseSpecError, OSError):
            return _refused(ReleaseAbandon.ARCHIVE_FAILED)
        return ReleaseAbandoned(
            ReleaseAbandon.ABANDONED,
            0,
            f"RELEASE-ABANDONED version={order.version} head={order.head_sha[:12]}"
            f" message_id={mask(order.message_id)} archive={archived.name}",
            archived,
        )


def emit(result: ReleaseAbandoned) -> int:
    """Both halves are machine-readable: the override on stdout, the refusal on stderr."""
    print(result.message, file=sys.stdout if result.exit_code == 0 else sys.stderr)
    return result.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-abandon",
        description="⛔ 된 release 승인 레코드를 감사와 함께 archive 로 놓아준다(메시지는 그대로).",
    )
    parser.add_argument("--version", required=True, help="레코드에 저장된 릴리스 버전")
    parser.add_argument("--head", required=True, help="레코드에 저장된 40자 HEAD sha")
    parser.add_argument("--message-id", required=True, help="소유자가 결정한 승인 메시지 id")
    parser.add_argument("--reason", required=True, help="감사에 남길 폐기 사유")
    args = parser.parse_args(argv)
    order = ReleaseAbandonOrder(
        version=str(args.version),
        head_sha=str(args.head),
        message_id=str(args.message_id),
        reason=str(args.reason),
        actor=skill_gate_retire.actor(),
    )
    audit_path = skill_gate_retire.abandon_log(skill_gate.APPROVAL_LOG)
    return emit(abandon(skill_gate.GATE_DIR, order, audit_path))


if __name__ == "__main__":
    raise SystemExit(main())
