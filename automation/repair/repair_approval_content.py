"""Read stored repair approval bytes without invoking a card renderer.

New records carry the posted digest. Pre-digest records use frozen v1/v2/v3
read grammar, not the optional envelope or the current posting formatter.
"""
from __future__ import annotations

from datetime import UTC, timedelta
from typing import Protocol

from automation.repair.repair_approval_render import ApprovalRecordView
from automation.repair.repair_patch_binding import PatchFileDelta
from automation.repair.repair_redaction import redact
from automation.stored_content import content_matches


class StoredApprovalView(ApprovalRecordView, Protocol):
    @property
    def content_sha256(self) -> str | None: ...


# Frozen logical lines, not splitlines of substituted content: a field may
# contain newlines or a complete record, but it never becomes wire structure.
_V1 = (
    "[repair] 승인 요청",
    "- ticket: `{ticket}`",
    "- sha256: `{hash}`",
    "- repair_nonce: `{nonce}`",
    "- sandbox: PASS (offline-subset bank + repro GREEN)",
    "- cha가 이 메시지에 ✅ 승인 또는 ⛔ 취소 리액션",
)
_V2 = (
    "[repair] 승인 요청",
    "- ticket: `{ticket}`",
    "- action_hash: `{hash}`",
    "- patch_sha256: `{patch}`",
    "- changed_files: {count} total, +{added}/-{removed}",
    "{files}",
    "- repair_nonce: `{nonce}`",
    "- sandbox: PASS (offline-subset bank + repro GREEN)",
    "- patch_body: 비노출 — ops 호스트의 `{path}` 에서 확인",
    "- cha가 이 메시지에 ✅ 승인 또는 ⛔ 취소 리액션",
)
_V3 = (
    "대상: 수리 승인 ({ticket})",
    "사실: action_hash: {hash}; patch_sha256: {patch}; "
    "{count} files +{added}/-{removed}; {files}; nonce: {nonce}; sandbox: PASS; "
    "패치 본문 비노출: `{path}` (승인 요청; 만료: {expiry})",
    "위치: 이 메시지",
    "인계: 소유자: 위 위치 · 반응 ✅ 승인 또는 ⛔ 취소; 다음: 승인된 패치만 반영",
    "되돌리기: 해당 없음; 취소 시: 패치 미반영, 티켓 재개",
)


def approval_content_matches(pending: StoredApprovalView, content: str) -> bool:
    """Check the digest and frozen wire binding, never current rendering behavior."""
    if pending.content_sha256 is not None and not content_matches(content, pending.content_sha256):
        return False
    if pending.content_binding_version is None:
        return pending.render_version is None and _bound_match(_V1, content, {
            "ticket": pending.ticket_id, "hash": pending.action_hash, "nonce": pending.nonce,
        })
    if pending.content_binding_version != 2 or len(content) > 1900:
        return False
    envelope = pending.render_version == 3
    changes = pending.changes
    if not changes or not pending.patch_sha256 or not pending.patch_source_path:
        return False
    files = _files(changes, envelope)
    if files is None:
        return False
    expected = {
        "ticket": _field(pending.ticket_id), "hash": pending.action_hash,
        "nonce": pending.nonce, "patch": pending.patch_sha256,
        "count": str(len(changes)), "added": str(sum(c.insertions for c in changes)),
        "removed": str(sum(c.deletions for c in changes)), "path": _field(pending.patch_source_path),
        "files": files,
    }
    if envelope:
        expected["expiry"] = (pending.created_at.astimezone(UTC) + timedelta(hours=24)).isoformat()
    return _bound_match(_V3 if envelope else _V2, content, expected, envelope)


def _bound_match(grammar: tuple[str, ...], content: str, fields: dict[str, str], envelope: bool = False) -> bool:
    # BASE substituted first, folded each assembled envelope line once, then
    # compared COMPLETE bytes. Never trim individual fields or received bytes.
    lines = (line.format_map(fields) for line in grammar)
    if envelope:
        lines = (" ".join(line.split()) for line in lines)
    return content == "\n".join(lines)


def _shorten(value: str, limit: int) -> str:
    """Frozen wire field clipping, independent of future posting display choices."""
    if len(value) <= limit:
        return value
    head = (limit - 1) // 2
    return f"{value[:head]}…{value[len(value) - (limit - 1 - head):]}"


def _field(value: str) -> str:
    return _shorten(redact(value), 96)


def _files(changes: tuple[PatchFileDelta, ...], envelope: bool) -> str | None:
    lines: list[str] = []
    for change in changes[:10]:
        old, new = change.old_path, change.new_path
        if old is not None and new is not None:
            name = redact(old) if old == new else f"{redact(old)} → {redact(new)}"
        elif new is not None:
            name = f"(신규) {redact(new)}"
        elif old is not None:
            name = f"{redact(old)} → (삭제)"
        else:
            return None
        counts = f" (+{change.insertions}/-{change.deletions})"
        name = _shorten(name, max(100 - len("  - ") - len(counts), 16))
        line = f"  - {name}{counts}"
        # Historical v3 removed list indentation before joining the fact.
        # Whitespace folding belongs to the complete fact line, not this name.
        lines.append(line.strip() if envelope else line)
    omitted = len(changes) - 10
    if omitted > 0:
        lines.append(
            f"외 {omitted}개 생략 (합계·해시는 전체)" if envelope else
            f"  … 외 {omitted}개 파일 생략 (합계와 action_hash는 전체를 포함)"
        )
    return ("; " if envelope else "\n").join(lines)
