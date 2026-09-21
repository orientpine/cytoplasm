"""특허 승인 카드의 판본 재생. 문서 본문·제목·링크는 입력으로 받지 않는다."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from .patent_export_gate import ExportGateError


class CardFields(Protocol):
    @property
    def slug(self) -> str: ...
    @property
    def plaintext_sha256(self) -> str: ...
    @property
    def dest_folder_id(self) -> str: ...
    @property
    def mode(self) -> str: ...
    @property
    def expiry_ts(self) -> int: ...
    @property
    def render_version(self) -> int: ...


def render_approval(m: CardFields) -> str:
    """네 바인딩 줄은 원래 위치·순서 그대로이며 봉투 필드가 아니다."""
    version = m.render_version
    match version:
        case 1:
            return render_v1(m)
        case 2:
            return render_v2(m)
        case 3:
            return render_v3(m)
        case _:  # Persisted version boundary, not an open-ended fallback.
            raise ExportGateError("unknown patent approval render version", 3)


def render_v2(m: CardFields) -> str:
    """Frozen owner-ko-v1 envelope card."""
    try:
        from automation.interop.owner_message import Action, Approval, OwnerMessage, OwnerMessageError, Ref, render
    except ImportError as error:
        raise ExportGateError("patent owner envelope unavailable", 3) from error
    here = Ref(scope="self")
    message = OwnerMessage(
        subject_key=m.slug, subject=m.slug, fact="특허 반출",
        location=here, owner=Action("react", here, "✅ 실행 / ⛔ 취소"),
        agent_next="승인 시 반출", recovery="not_applicable",
        detail=Approval(datetime.fromtimestamp(m.expiry_ts, UTC), "반출하지 않음"),
    )
    try:
        lines = render(message, destination=here).splitlines()
    except OwnerMessageError as error:
        raise ExportGateError("patent owner envelope cannot render", 3) from error
    return "\n".join((*lines[:2], *binding_lines(m), *lines[2:]))


def render_v3(m: CardFields) -> str:
    try:
        from automation.interop.owner_message import Action, Approval, OwnerMessage, OwnerMessageError, Ref, render
    except ImportError as error:
        raise ExportGateError("patent owner envelope unavailable", 3) from error
    here = Ref(scope="self")
    message = OwnerMessage(
        subject_key=m.slug,
        subject="특허 반출",
        fact=f"반출 ID: {m.slug}\n모드: {m.mode}\n판본: patent-export-render-v3",
        location=here,
        owner=Action("react", here, "✅ 실행 / ⛔ 취소"),
        agent_next="승인 시 반출",
        recovery="not_applicable",
        detail=Approval(datetime.fromtimestamp(m.expiry_ts, UTC), "반출하지 않음"),
        render_version="owner-ko-v2",
    )
    try:
        lines = render(message, destination=here).splitlines()
    except OwnerMessageError as error:
        raise ExportGateError("patent owner envelope cannot render", 3) from error
    return "\n".join((*lines[:-1], *binding_lines(m), lines[-1]))


def binding_lines(m: CardFields) -> tuple[str, str, str, str]:
    """동결된 승인 바인딩 wire — 줄 앞뒤에 문자를 붙이지 않는다."""
    return (
        f"sha256: {m.plaintext_sha256}", f"dest_folder_id: {m.dest_folder_id}",
        f"expiry_ts: {m.expiry_ts}", f"mode={m.mode}",
    )


def render_v1(m: CardFields) -> str:
    """동결 2026-09-11: 모든 기존 표면의 중립 리액션 문구까지 고정한다."""
    return (
        f"PATENT EXPORT APPROVAL REQUEST\n"
        f"slug: {m.slug}\n"
        f"sha256: {m.plaintext_sha256}\n"
        f"dest_folder_id: {m.dest_folder_id}\n"
        f"expiry_ts: {m.expiry_ts}\n"
        f"mode={m.mode}\n"
        "이 메시지에 ✅ 실행 / ⛔ 취소\n"
    )
