"""Render the owner-facing repair approval request.

Three frozen posting versions live here on purpose. ``v1`` predates content
binding; ``v2`` is the content-bound message; ``v3`` uses the owner envelope.
Stored requests are read by ``repair_approval_content`` without rendering.
The stored render version is independent of the content-binding hash schema.

The patch body never reaches this module. Only counts, paths, and digests do —
the body stays under the ops-private root and the message points at it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final, Literal, Protocol, assert_never

from automation.interop.approval_surface import ApprovalKind, ApprovalSurface, reaction_instruction, required_surface
from automation.repair.repair_patch_binding import PatchFileDelta
from automation.repair.repair_redaction import redact

CONTENT_BINDING_VERSION: Final = 2
MAX_VISIBLE_FILES: Final = 10
MAX_VISIBLE_LINE_CHARS: Final = 100
MAX_FIELD_CHARS: Final = 96
_ELLIPSIS: Final = "…"
_ADDED: Final = "(신규) "
_REMOVED: Final = " → (삭제)"

# Discord rejects a message over 2000 characters; the margin absorbs any future
# line. Module-level so a test can shrink it and prove the guard refuses rather
# than silently slicing a finished message.
MAX_APPROVAL_CONTENT_CHARS = 1900


class ApprovalRenderError(RuntimeError):
    """The record cannot be rendered into a message the owner could consent to."""


class ApprovalRecordView(Protocol):
    """The persisted approval facts the message is reproduced from.

    Deliberately a structural type: posting uses the captured patch summary,
    while the render-independent reader can verify it after the patch is gone.
    """

    @property
    def ticket_id(self) -> str: ...

    @property
    def action_hash(self) -> str: ...

    @property
    def nonce(self) -> str: ...

    @property
    def kind(self) -> ApprovalKind | None: ...

    @property
    def surface(self) -> ApprovalSurface | None: ...

    @property
    def content_binding_version(self) -> int | None: ...

    @property
    def render_version(self) -> Literal[2, 3] | None: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def patch_sha256(self) -> str | None: ...

    @property
    def changes(self) -> tuple[PatchFileDelta, ...] | None: ...

    @property
    def patch_source_path(self) -> str | None: ...


def approval_request_content(pending: ApprovalRecordView) -> str:
    """Replay the stored wording version; an absent version selects frozen v1/v2."""
    if pending.content_binding_version is None and pending.render_version is None:
        return _render_v1(pending)
    if pending.content_binding_version != CONTENT_BINDING_VERSION:
        raise ApprovalRenderError("unknown repair approval binding version")
    match pending.render_version:
        case None | 2:
            content = _render_v2(pending)
        case 3:
            content = _render_v3(pending)
        case unreachable:
            assert_never(unreachable)
    if len(content) > MAX_APPROVAL_CONTENT_CHARS:
        raise ApprovalRenderError("repair approval request exceeds the postable length")
    return content


def _instruction(pending: ApprovalRecordView) -> str:
    kind = pending.kind or ApprovalKind.REPAIR
    surface = pending.surface or required_surface(kind)
    return reaction_instruction(kind, surface).replace("✅ 실행 / ⛔ 취소", "✅ 승인 또는 ⛔ 취소")


def _render_v1(pending: ApprovalRecordView) -> str:
    """FROZEN 2026-07-29. Do not edit — legacy messages are matched byte for byte."""
    return (
        "[repair] 승인 요청\n"
        f"- ticket: `{pending.ticket_id}`\n"
        f"- sha256: `{pending.action_hash}`\n"
        f"- repair_nonce: `{pending.nonce}`\n"
        "- sandbox: PASS (offline-subset bank + repro GREEN)\n"
        f"- cha가 {_instruction(pending)} 리액션"
    )


def _render_v2(pending: ApprovalRecordView) -> str:
    changes = pending.changes
    if not changes or not pending.patch_sha256 or not pending.patch_source_path:
        raise ApprovalRenderError("content-bound repair approval is missing its patch summary")
    insertions = sum(change.insertions for change in changes)
    deletions = sum(change.deletions for change in changes)
    lines = [
        "[repair] 승인 요청",
        f"- ticket: `{_field(pending.ticket_id)}`",
        f"- action_hash: `{pending.action_hash}`",
        f"- patch_sha256: `{pending.patch_sha256}`",
        f"- changed_files: {len(changes)} total, +{insertions}/-{deletions}",
        *(_file_line(change) for change in changes[:MAX_VISIBLE_FILES]),
    ]
    omitted = len(changes) - MAX_VISIBLE_FILES
    if omitted > 0:
        lines.append(f"  {_ELLIPSIS} 외 {omitted}개 파일 생략 (합계와 action_hash는 전체를 포함)")
    lines += [
        f"- repair_nonce: `{pending.nonce}`",
        "- sandbox: PASS (offline-subset bank + repro GREEN)",
        f"- patch_body: 비노출 — ops 호스트의 `{_field(pending.patch_source_path)}` 에서 확인",
        f"- cha가 {_instruction(pending)} 리액션",
    ]
    return "\n".join(lines)


def _render_v3(pending: ApprovalRecordView) -> str:
    """신규 카드만 봉투로 렌더한다. 재생 실패는 옛 문구로 대체하지 않는다."""
    try:
        from automation.interop.owner_message import Action, Approval, OwnerMessage, OwnerMessageError, Ref, render
    except ImportError as error:
        raise ApprovalRenderError("repair owner envelope is unavailable") from error
    changes = pending.changes
    if not changes or not pending.patch_sha256 or not pending.patch_source_path:
        raise ApprovalRenderError("content-bound repair approval is missing its patch summary")
    totals = f"{len(changes)} files +{sum(c.insertions for c in changes)}/-{sum(c.deletions for c in changes)}"
    files = "; ".join(_file_line(change).strip() for change in changes[:MAX_VISIBLE_FILES])
    omitted = max(0, len(changes) - MAX_VISIBLE_FILES)
    if omitted:
        files += f"; 외 {omitted}개 생략 (합계·해시는 전체)"
    here = Ref(scope="self")
    message = OwnerMessage(
        subject_key=_field(pending.ticket_id), subject="수리 승인",
        fact=(f"action_hash: {pending.action_hash}; patch_sha256: {pending.patch_sha256}; "
              f"{totals}; {files}; nonce: {pending.nonce}; sandbox: PASS; "
              f"패치 본문 비노출: `{_field(pending.patch_source_path)}`"),
        location=here, owner=Action("react", here, "✅ 승인 또는 ⛔ 취소"),
        agent_next="승인된 패치만 반영", recovery="not_applicable",
        detail=Approval(pending.created_at.astimezone(UTC) + timedelta(hours=24), "패치 미반영, 티켓 재개"),
    )
    try:
        return render(message, destination=here)
    except OwnerMessageError as error:
        raise ApprovalRenderError("repair owner envelope cannot render") from error


def _file_line(change: PatchFileDelta) -> str:
    suffix = f" (+{change.insertions}/-{change.deletions})"
    budget = MAX_VISIBLE_LINE_CHARS - len("  - ") - len(suffix)
    return f"  - {_shorten(_describe(change), max(budget, 16))}{suffix}"


def _describe(change: PatchFileDelta) -> str:
    old, new = change.old_path, change.new_path
    if old is not None and new is not None:
        return redact(old) if old == new else f"{redact(old)} → {redact(new)}"
    if new is not None:
        return f"{_ADDED}{redact(new)}"
    if old is not None:
        return f"{redact(old)}{_REMOVED}"
    raise ApprovalRenderError("repair patch summary entry names no file")


def _field(value: str) -> str:
    return _shorten(redact(value), MAX_FIELD_CHARS)


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = (limit - 1) // 2
    return f"{text[:head]}{_ELLIPSIS}{text[len(text) - (limit - 1 - head) :]}"
