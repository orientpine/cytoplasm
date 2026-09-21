"""Controlled owner review notice; no submission or other outbound path.

ON-1..ON-3: 목적지 해석도 전송도 `automation.owner_notice` 파사드만 한다. 여기서는
본문만 만든다 — 대상 문자열은 "보낼지 말지"의 스위치로만 남는다(빈 값이면 전송 없음).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import ModuleType


class DeliveryError(RuntimeError):
    """The controlled owner review request could not be delivered."""


def resolve_target(explicit: str = "") -> str:
    """Resolve the owner-only Hermes DM target without committing an account id."""
    if explicit:
        return explicit
    configured = os.environ.get("DOCTYPE_DM_TARGET", "")
    if configured:
        return configured
    path = Path("~/.hermes/doctype/config.json").expanduser()
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise DeliveryError("doctype DM configuration is invalid") from error
        target = payload.get("dm_target") if isinstance(payload, dict) else None
        if isinstance(target, str) and target:
            return target
    if os.environ.get("DOCTYPE_DM_DISABLED") == "1":
        return ""
    raise DeliveryError("DOCTYPE_DM_TARGET is required for an owner review DM")


def _facade() -> ModuleType:
    """통지 파사드 — 배포 레이아웃에서도 automation 을 담은 코드 루트를 되짚어 import 한다."""
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    roots = (
        *((Path(override).expanduser(),) if override else ()),
        *Path(__file__).resolve().parents,
        Path("/srv/autophagy-agent-current"),
        Path("/srv/autophagy-agents"),
    )
    for root in roots:
        if (root / "automation" / "owner_notice.py").is_file():
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            break
    try:
        from automation import owner_notice
    except ImportError as error:
        raise DeliveryError(f"통지 파사드 사용 불가: {type(error).__name__}") from None
    return owner_notice


def send_review(target: str, message: str, file: Path | None = None) -> None:
    """문서 좌표를 렌더하되 옛 런타임과 좌표 없는 호출은 원문을 보낸다. 전송은 파사드가 한다."""
    if not target:
        return
    content = message
    if file is not None:
        try:
            from automation.interop.owner_message import Action, OwnerMessage, OwnerMessageError, Ref, Result, render
        except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
            content = message
        else:
            location = Ref(scope="resource", search=("문서 검색", file.name))
            try:
                content = render(OwnerMessage(
                    subject_key=str(file), subject="서류 초안", fact="검토 요청",
                    location=location, owner=Action("open", target=location, argument="검토·제출은 직접"),
                    agent_next=None, recovery="not_applicable", detail=Result("executed"),
                ), destination=Ref(scope="none"))
            except OwnerMessageError:
                content = message
    if not _facade().notify_owner(content):
        raise DeliveryError("소유자 통지 전송 실패 — owner-notice 마커를 확인하세요")
