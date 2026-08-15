"""Render the owner-facing repair approval request.

Two renderers live here on purpose. ``v1`` is frozen: records posted before
content binding existed are still compared to their Discord message by exact
equality, so a single changed character there would turn every outstanding
legacy request into a binding mismatch and stall the gate. ``v2`` is the
content-bound message. A future wording change becomes ``v3``; it never edits
``v2`` in place, for the same reason.

The patch body never reaches this module. Only counts, paths, and digests do —
the body stays under the ops-private root and the message points at it.
"""

from __future__ import annotations

from typing import Final, Protocol

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

    Deliberately a structural type: the renderer must never reach back into the
    patch file, because the watcher re-renders long after the patch is gone.
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
    def patch_sha256(self) -> str | None: ...

    @property
    def changes(self) -> tuple[PatchFileDelta, ...] | None: ...

    @property
    def patch_source_path(self) -> str | None: ...


def approval_request_content(pending: ApprovalRecordView) -> str:
    """Render the one message this record is bound to, by its binding version."""
    if pending.content_binding_version is None:
        return _render_v1(pending)
    if pending.content_binding_version != CONTENT_BINDING_VERSION:
        raise ApprovalRenderError("unknown repair approval binding version")
    content = _render_v2(pending)
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
