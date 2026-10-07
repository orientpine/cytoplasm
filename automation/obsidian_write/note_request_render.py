"""Approval card for one Obsidian note request (owner envelope, append-only versions)."""

from __future__ import annotations

from typing import Final

from .note_request import NoteRequest

RENDER_VERSION: Final = "obsidian-note-v1"
MAX_MESSAGE_CHARS: Final = 1900
_MIN_PREVIEW: Final = 200


class RenderError(ValueError):
    pass


def render_note_approval(record: NoteRequest, body: str, *, render_version: str = RENDER_VERSION) -> str:
    if render_version != RENDER_VERSION:
        raise RenderError(f"unsupported note render version: {render_version}")
    preview_limit = 900
    while True:
        content = _render_v1(record, body, preview_limit)
        if len(content) <= MAX_MESSAGE_CHARS:
            return content
        if preview_limit <= _MIN_PREVIEW:
            raise RenderError("note approval exceeds the postable length")
        preview_limit = max(_MIN_PREVIEW, preview_limit - (len(content) - MAX_MESSAGE_CHARS) - 20)


def _links_line(record: NoteRequest) -> str:
    if not record.links_checked:
        return "링크 확인: 불가(읽기 사본을 읽지 못했다 — 링크가 볼트에 있는지 확인하지 않았다)"
    if not record.unresolved_links:
        return "링크 확인: 모든 [[링크]]가 볼트에 있다"
    names = ", ".join(f"[[{target}]]" for target in record.unresolved_links)
    return f"⚠ 볼트에 없는 링크 {len(record.unresolved_links)}개: {names}"


def _related_line(record: NoteRequest) -> str:
    if not record.related:
        return "관련 노트 후보: 없음"
    names = ", ".join(f"[[{stem}]]" for stem in record.related)
    return f"관련 노트 후보({record.related_source}): {names}"


def _render_v1(record: NoteRequest, body: str, preview_limit: int) -> str:
    try:
        from automation.interop import owner_message as om
    except ImportError as error:
        raise RenderError("owner envelope unavailable") from error
    preview = body if len(body) <= preview_limit else (
        body[:preview_limit].rstrip() + f"\n…(미리보기 {preview_limit}자 / 전체 {len(body)}자)"
    )
    operation = "기존 노트 덮어쓰기 ⚠" if record.overwrite else "새 노트 생성"
    here = om.Ref(scope="self")
    message = om.OwnerMessage(
        subject_key=record.request_id,
        subject="옵시디언 노트 승인 요청",
        fact=(
            f"작업: {operation}\n"
            f"전체 경로: {record.relpath}\n"
            f"제목: {record.title}\n"
            f"본문 sha256: {record.body_sha256}\n"
            f"{_links_line(record)}\n"
            f"{_related_line(record)}\n"
            f"본문:\n{preview}\n"
            f"판본: {RENDER_VERSION}"
        ),
        location=here,
        owner=om.Action("react", here, "✅ 저장·push / ⛔ 취소"),
        agent_next="✅ 뒤 이 경로·이 본문 그대로 볼트에 저장하고 push 한 뒤 원격에서 다시 읽어 해시를 확인한다",
        recovery="not_applicable",
        detail=om.Approval(None, "볼트를 바꾸지 않는다"),
        render_version="owner-ko-v2",
    )
    try:
        lines = om.render(message, destination=here).splitlines()
    except om.OwnerMessageError as error:
        raise RenderError("owner envelope cannot render") from error
    return "\n".join((*lines[:-1], f"- action_hash: `{record.action_hash}`", lines[-1]))
