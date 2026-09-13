"""동결된 위키 v1 확인 문자열. 신규 카드 판본에서 수정하지 않는다."""
from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module


def _owner_dm_surface() -> str | None:
    """공유 enum의 owner-DM 표면 값. 해석 불가하면 요약을 생략한다."""
    gate = import_module("wiki_gate")
    try:
        import wiki_binding
        surface = wiki_binding._repo_module("approval_surface").ApprovalSurface.OWNER_DM
    except (ImportError, AttributeError, gate.GateError):
        return None
    return str(surface)


def confirm_v1(draft: Mapping[str, str | int | None], *, surface: str | None = None) -> str:
    """레거시 한 줄과 명시적으로 요청한 DM 요약의 바이트를 재생한다."""
    gate = import_module("wiki_gate")
    legacy = f"저장 {draft['id']} sha256:{draft['sha256']}"
    summary = draft.get("summary")
    if not isinstance(summary, str) or not summary:
        return legacy
    effective_surface = surface if surface is not None else draft.get("surface")
    owner_dm = gate._owner_dm_surface()
    if owner_dm is None or effective_surface != owner_dm:
        return legacy
    budget = gate.CONFIRM_MAX_CHARS - len(legacy) - 1
    if budget <= 0:
        return legacy
    if len(summary) > budget:
        summary = summary[: budget - 1] + "…"
    return f"{legacy}\n{summary}"
