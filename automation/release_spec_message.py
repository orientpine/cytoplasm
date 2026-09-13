"""Stored release v4 envelope replay; staged beside the release spec."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.release_spec import ReleaseSpec


def render_v4(spec: ReleaseSpec, *, bundle_names: str) -> str:
    """Replay only stored fields, without consulting a clock or release plan."""
    from automation.release_spec import EnvelopeUnavailable

    try:
        from automation.interop.owner_message import Action, Approval, OwnerMessage, OwnerMessageError, Ref, render
    except ImportError as error:
        raise EnvelopeUnavailable("release owner envelope is unavailable") from error
    here = Ref(scope="self")
    note = f"; {spec.major_note}" if spec.major_note else ""
    # This approval has no render-time expiry: introducing a clock changes the
    # replay bytes and invalidates an already-posted owner's approval.
    try:
        return render(OwnerMessage(
            subject_key=f"release:{spec.version}", subject="릴리스 배포 승인",
            fact=(f"배포 기준 `{spec.head_sha}`;"
                  f" 배포 번들 ({len(spec.surface_digests)}): {bundle_names};"
                  f" 승인 바인딩 `{spec.action_hash()}`;"
                  f" 변경 상세 아래 {len(spec.detail_messages())}개 메시지"
                  f" (같은 릴리스 {spec.version} · 기준 {spec.head_sha[:12]}){note}"),
            location=here,
            owner=Action("react", here, "✅ 승인 또는 ⛔ 취소 (소유자 전용 — 봇/타인 리액션은 거부됨)"),
            agent_next="승인된 릴리스만 태그·배포", recovery="not_applicable",
            detail=Approval(None, "배포 미실행, 요청 폐기"),
        ), destination=here)
    except OwnerMessageError as error:
        raise EnvelopeUnavailable("release owner envelope cannot render") from error
