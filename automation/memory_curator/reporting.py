"""Privacy-bounded owner-DM reporting for one curator cycle."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias

from .apply import CurationResult
from .binding import PromotionReceipt
from .model import MemoryFile, MemoryKind
from .promotion import PromotionProposal
from .state import PendingOwnerEvent

_KINDS: Final[tuple[MemoryKind, ...]] = ("memory", "user")
_FILENAMES: Final[dict[MemoryKind, str]] = {
    "memory": "MEMORY.md",
    "user": "USER.md",
}
_TOKENISH: Final = re.compile(r"[A-Za-z0-9_./+=-]{16,}")
_PREVIEW_LIMIT: Final = 28

PromotedItem: TypeAlias = tuple[PromotionProposal, PromotionReceipt]


@dataclass(frozen=True, slots=True)
class DeletedItem:
    promotion_key: str
    source_kind: MemoryKind
    entry_text: str
    freed_chars: int
    backup_path: Path | None
    applied: bool


@dataclass(frozen=True, slots=True)
class OwnerReport:
    compacted: Mapping[MemoryKind, CurationResult]
    events: tuple[PendingOwnerEvent, ...]
    final_files: Mapping[MemoryKind, MemoryFile]
    near_cap_kinds: tuple[MemoryKind, ...]


def source_filename(kind: MemoryKind) -> str:
    """소유자가 실제로 아는 이름 — `USER.md` / `MEMORY.md`.

    두 파일은 성격이 다르다(신원·스타일 vs 운영 사실). 어느 쪽에서 빠지는지는 승인
    판단의 일부이므로 `source_kind` 를 그대로 노출하지 않고 파일명으로 옮긴다.
    """
    return _FILENAMES[kind]


def redacted(text: str) -> str:
    """공백을 접고 토큰 모양 실행만 가린다 — 자르지는 않는다.

    나열용 미리보기(`preview`)와 달리, 한 건짜리 승인 메시지는 원문 전체를 보여줘야
    소유자가 무엇을 인가하는지 알 수 있다. 마스킹은 두 경우 모두 필요하다.
    """
    return _TOKENISH.sub("[REDACTED]", " ".join(text.split()))


def preview(text: str) -> str:
    """여러 건을 한 줄씩 나열할 때 쓰는 짧은 미리보기."""
    masked = redacted(text)
    if len(masked) <= _PREVIEW_LIMIT:
        return masked
    return masked[:_PREVIEW_LIMIT] + "…"


def build_report(report: OwnerReport) -> str | None:
    """Build the single change-detected owner summary without full memory bodies."""
    lines: list[str] = []
    freed = sum(report.compacted[kind].plan.freed_chars for kind in _KINDS)
    if freed > 0:
        lines.append(f"정리: 중복 제거로 {freed}자 확보")

    deleted: list[PendingOwnerEvent] = []
    posted: list[PendingOwnerEvent] = []
    for event in sorted(report.events, key=lambda item: item.key):
        if event.phase == "deleted":
            deleted.append(event)
        else:
            posted.append(event)

    if deleted:
        deleted_chars = sum(
            event.freed_chars if event.freed_chars is not None else 0 for event in deleted
        )
        lines.append(
            f"삭제 완료(트윈 저장 검증 후): {len(deleted)}건, {deleted_chars}자 확보"
        )
        lines.extend(f"- '{event.preview}'" for event in deleted)

    if posted:
        lines.append(
            f"트윈 승격 제안 {len(posted)}건 — DM ✅ 시 자체 메모리에서 삭제됩니다:"
        )
        lines.extend(
            f"- 저장 {event.draft_id}: '{event.preview}' → {event.twin_kind}"
            for event in posted
        )

    for kind in report.near_cap_kinds:
        memory_file = report.final_files[kind]
        percentage = round(memory_file.fill_ratio * 100)
        lines.append(f"⚠️ 자체 메모리 근접: {_FILENAMES[kind]} {percentage}%")

    if not lines:
        return None
    return "🧠 메모리 큐레이터\n" + "\n".join(lines)
