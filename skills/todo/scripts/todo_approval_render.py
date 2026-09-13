"""할 일 승인 카드 판본. 실행 argv·표면 정책·TTL 시계는 소유하지 않는다."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from todo_approval_model import TodoApprovalIntent

RENDER_VERSION: Final = "todo-render-v2"
MAX_MESSAGE_CHARS: Final = 1900


class TodoRenderError(RuntimeError):
    """No complete approval card can be posted."""


def prepare_approval_card(intent: TodoApprovalIntent, expires_at: datetime | None = None) -> tuple[str, str]:
    """Capability selection before thread creation; no clock or timestamp invention."""
    try:
        return RENDER_VERSION, render_todo_approval(intent, expires_at)
    except TodoRenderError:
        version = "todo-render-v1"
        return version, render_todo_approval(intent, expires_at, render_version=version)


def render_todo_approval(
    intent: TodoApprovalIntent, expires_at: datetime | None = None, *, render_version: str = RENDER_VERSION,
) -> str:
    match render_version:
        case "todo-render-v1":
            content = _render_v1(intent)
        case "todo-render-v2":
            content = _render_v2(intent, expires_at)
        case _:
            raise TodoRenderError(f"unsupported todo render version: {render_version}")
    if len(content) > MAX_MESSAGE_CHARS:
        raise TodoRenderError("todo approval exceeds the postable length")
    return content


def _render_v1(intent: TodoApprovalIntent) -> str:
    """Frozen previous card on todo's existing agent-chat-thread surface."""
    return (
        "Google Tasks 등록 승인\n"
        f"제목: {intent.title}\n"
        f"기한: {intent.due or '-'}\n"
        f"argv hash: {intent.action_hash}\n"
        "이 메시지에 ✅ 실행 / ⛔ 취소"
    )


def _render_v2(intent: TodoApprovalIntent, expires_at: datetime | None) -> str:
    try:
        from automation.interop import owner_message as om
    except ImportError as error:
        raise TodoRenderError("owner envelope unavailable") from error
    if not callable(getattr(om, "render", None)):
        raise TodoRenderError("owner envelope renderer unavailable")
    here = om.Ref(scope="self")
    message = om.OwnerMessage(
        subject_key=intent.title, subject="Google Tasks 등록",
        fact=f"기한: {intent.due or '-'}; todo-render-v2",
        location=here, owner=om.Action("react", here, "✅ 승인 / ⛔ 취소"),
        agent_next="Google Tasks 등록", recovery="not_applicable",
        detail=om.Approval(expires_at, "등록 취소"),
    )
    try:
        lines = om.render(message, destination=here).splitlines()
        return "\n".join((*lines[:3], f"argv hash: {intent.action_hash}", *lines[3:]))
    except om.OwnerMessageError as error:
        raise TodoRenderError("owner envelope cannot render") from error
