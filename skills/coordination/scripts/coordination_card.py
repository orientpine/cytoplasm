"""Frozen coordination v1 wording; calendar effects remain in the owner lifecycle."""
from __future__ import annotations

import argparse
from typing import NotRequired, TypedDict
import coordinate_io as io


class OwnerCardDraft(TypedDict):
    id: str
    sha256: str
    created: str
    start: str
    render_version: NotRequired[str]


def legacy(draft: OwnerCardDraft, args: argparse.Namespace, correlation: str) -> str:
    """Replay the original card without following shared wording changes."""
    label = io.kst_label(str(draft["start"]), args.duration_min)
    return (
        f"📅 일정 조율 ({correlation}): 상대 에이전트({args.peer})가 "
        f"{label} 슬롯을 승인했습니다.\n제목: {args.summary}\n"
        "이 메시지에 ✅ 실행 / ⛔ 취소 — 또는 "
        f"`실행 {draft['id']}`/`취소 {draft['id']}` 텍스트도 가능\n"
        f"sha256:{draft['sha256']}"
    )
