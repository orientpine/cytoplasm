#!/usr/bin/env python3
"""F4 감사: 소유자 승인 원장과 send-log 를 대조하고, 어긋난 행마다 사유를 이름 짓는다.

``automation/final/f4_scope.sh`` 가 이 파일을 노드의 ``python3 -`` 표준입력으로 흘려보낸다 —
그래서 stdlib 만 쓰고 repo 를 import 하지 않으며 부수효과가 없다. 인자는 인라인 판본과 같다:
``<approvals.jsonl> <agent-home>``.

왜 분리했나 — 인라인 판본은 어긋난 전송을 ``unmatched_sends`` 한 숫자로만 보고했다. 그래서
"검사기가 못 맞춘 것"과 "실제 감사 행이 없는 것"이 증적에서 똑같이 보였고, 판정하려면 노드에
직접 들어가야 했다. 이제 모든 unmatched 행이 세 사유 중 하나를 달고 나온다:

* ``approval-missing`` — 소유자 승인 레코드가 없다. 승인 게이트의 구멍이며 항상 실패다.
* ``send-log-row-missing`` — 승인은 있는데 approvals.jsonl 에 그 전송을 가리키는 행이 아예 없다.
* ``method-not-matched`` — 가리키는 행은 있으나 매칭 규칙이 받아주지 않았다(검사기 쪽 격차).

분류는 설명일 뿐 면제가 아니다 — 세 사유 모두 unmatched 로 세고 exit 1 을 만든다. 읽을 수 없는
입력도 통과가 아니라 오류다(fail-closed).

매칭 규칙에 ``compose_send`` 가 들어 있는 이유: 규칙이 쓰인 것은 2026-07-18(`35d104a7`)이고
``mail.compose_send`` 감사 행이 생긴 것은 그 다음 날 2026-07-19(`02c133fa`)다. 승인·전송·감사 행이
모두 정상인 compose 발송이 접미사 목록에 없다는 이유만으로 계속 어긋난 것으로 세어졌다.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias

JsonMap: TypeAlias = dict[str, object]

REASON_APPROVAL_MISSING: Final = "approval-missing"
REASON_SEND_LOG_ROW_MISSING: Final = "send-log-row-missing"
REASON_METHOD_NOT_MATCHED: Final = "method-not-matched"
REASONS: Final = (REASON_APPROVAL_MISSING, REASON_SEND_LOG_ROW_MISSING, REASON_METHOD_NOT_MATCHED)

OWNER_APPROVAL_ACTION: Final = "external_effect.approval"
SEND_AUDIT_ACTION_SUFFIXES: Final = ("reply_send", "compose_send", "request_mail")
INJECTED_METHOD: Final = "signed_injection_e2e"
INJECTED_REF_PREFIX: Final = "injected:"
INTEROP_CONFIG: Final = ".hermes/interop/config.json"


class AuditInputError(RuntimeError):
    """입력을 읽거나 해석할 수 없음 — 감사 결과가 아니라 오류로 표면화한다."""


@dataclass(frozen=True, slots=True)
class UnmatchedSend:
    """승인 원장과 대조되지 않은 전송 한 건과 그 사유."""

    ref: str
    method: str
    sha256: str
    reason: str

    def line(self) -> str:
        return (
            f"unmatched ref={self.ref!r} method={self.method!r} "
            f"sha256_prefix={self.sha256[:12]} reason={self.reason}"
        )


@dataclass(frozen=True, slots=True)
class AuditResult:
    """인라인 판본과 같은 집계 + 사유별 내역."""

    owner_approved_records: int
    send_logged_records: int
    sent_records: int
    injected_test_records: int
    unmatched: tuple[UnmatchedSend, ...]

    @property
    def exit_code(self) -> int:
        return 1 if self.unmatched else 0

    def reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {reason: 0 for reason in REASONS}
        for row in self.unmatched:
            counts[row.reason] += 1
        return counts

    def report_lines(self) -> tuple[str, ...]:
        lines = [
            f"owner_approved_records={self.owner_approved_records}",
            f"send_logged_records={self.send_logged_records}",
            f"sent_records={self.sent_records}",
            f"injected_test_records={self.injected_test_records}",
            f"unmatched_sends={len(self.unmatched)}",
        ]
        lines.extend(
            f"unmatched_{reason.replace('-', '_')}={count}"
            for reason, count in self.reason_counts().items()
        )
        lines.extend(row.line() for row in self.unmatched)
        return tuple(lines)


@dataclass(frozen=True, slots=True)
class _Ledger:
    """approvals.jsonl 에서 뽑은 세 가지 사실."""

    owner_approved: frozenset[tuple[str, str]]
    send_logged: frozenset[tuple[str, str]]
    mirrored_refs: frozenset[str]


def _text(mapping: JsonMap, key: str) -> str:
    value = mapping.get(key)
    return value if isinstance(value, str) else ""


def _mapping(mapping: JsonMap, key: str) -> JsonMap:
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def _read_rows(path: Path) -> tuple[JsonMap, ...]:
    """JSONL 을 통째로 읽는다 — 깨진 행은 건너뛰지 않고 오류로 올린다."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise AuditInputError(f"unreadable input: {path}") from error
    rows: list[JsonMap] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as error:
            raise AuditInputError(f"malformed row: {path}:{number}") from error
        if not isinstance(row, dict):
            raise AuditInputError(f"non-object row: {path}:{number}")
        rows.append(row)
    return tuple(rows)


def read_owner_id(home: Path) -> str:
    """승인 바인딩의 기준이 되는 소유자 id — 없으면 감사를 시작하지 않는다."""
    path = home / INTEROP_CONFIG
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuditInputError(f"unreadable interop config: {path}") from error
    owner_id = config.get("owner_id") if isinstance(config, dict) else None
    if not isinstance(owner_id, str) or not owner_id:
        raise AuditInputError("owner_id missing from interop config")
    return owner_id


def read_ledger(approvals_path: Path, owner_id: str) -> _Ledger:
    """소유자 승인 · 전송 감사 행 · 전송을 가리키는 모든 행의 ref 를 색인한다."""
    owner_approved: set[tuple[str, str]] = set()
    send_logged: set[tuple[str, str]] = set()
    mirrored_refs: set[str] = set()
    for row in _read_rows(approvals_path):
        action = _text(row, "action")
        approval = _mapping(row, "approval")
        method = _text(approval, "method")
        status = _text(_mapping(row, "result"), "status")
        if action == OWNER_APPROVAL_ACTION:
            if status == "approved" and _text(approval, "owner_id") == owner_id:
                owner_approved.add((_text(approval, "message_id"), method))
            continue
        # 감사 행은 ``ref`` 로 전송을 가리킨다. 구 스키마(gate-ledger-inventory 의
        # ``approval{message_id}``)도 같은 값을 다른 이름으로 들고 있어 함께 읽는다 —
        # 승인 요구 자체는 위의 owner_approved 가 따로 강제하므로 완화가 아니다.
        ref = _text(approval, "ref") or _text(approval, "message_id")
        if not ref:
            continue
        mirrored_refs.add(ref)
        if status == "sent" and action.endswith(SEND_AUDIT_ACTION_SUFFIXES):
            send_logged.add((ref, method))
    return _Ledger(frozenset(owner_approved), frozenset(send_logged), frozenset(mirrored_refs))


def classify(ref: str, method: str, ledger: _Ledger) -> str | None:
    """전송 한 건이 왜 대조되지 않았는지 — 맞으면 ``None``."""
    if (ref, method) not in ledger.owner_approved:
        return REASON_APPROVAL_MISSING
    if (ref, method) in ledger.send_logged:
        return None
    if ref in ledger.mirrored_refs:
        return REASON_METHOD_NOT_MATCHED
    return REASON_SEND_LOG_ROW_MISSING


def audit(approvals_path: Path, home: Path) -> AuditResult:
    """홈 아래 모든 send-log 의 ``sent`` 행을 승인 원장과 대조한다."""
    ledger = read_ledger(approvals_path, read_owner_id(home))
    sent = 0
    injected = 0
    unmatched: list[UnmatchedSend] = []
    for send_log in sorted(home.rglob("send-log.jsonl")):
        for row in _read_rows(send_log):
            if _text(row, "status") != "sent":
                continue
            ref = _text(row, "ref")
            method = _text(row, "method")
            if method == INJECTED_METHOD and ref.startswith(INJECTED_REF_PREFIX):
                injected += 1
                continue
            sent += 1
            reason = classify(ref, method, ledger)
            if reason is not None:
                unmatched.append(UnmatchedSend(ref, method, _text(row, "sha256"), reason))
    return AuditResult(
        owner_approved_records=len(ledger.owner_approved),
        send_logged_records=len(ledger.send_logged),
        sent_records=sent,
        injected_test_records=injected,
        unmatched=tuple(unmatched),
    )


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        print("usage: approvals_send_log_audit.py <approvals.jsonl> <agent-home>", file=sys.stderr)
        return 2
    try:
        result = audit(Path(argv[0]), Path(argv[1]))
    except AuditInputError as error:
        print(f"audit_input_error={error}", file=sys.stderr)
        return 2
    for line in result.report_lines():
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
