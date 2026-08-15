from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, TypeAlias

MemoryTarget: TypeAlias = Literal["wiki", "memory_md", "skill", "tasks", "none"]

_EXPLICIT_CUES: Final = ("기억해", "기억해줘", "기억해 줘", "앞으로도 이렇게")
_AMBIGUOUS_REQUESTS: Final = frozenset(
    {
        "기억해",
        "기억해줘",
        "기억해 줘",
        "앞으로도 이렇게",
        "앞으로도 이렇게 해줘",
    }
)
_REFERENTIAL_CUES: Final = ("이것", "그것", "저것", "이렇게", "그렇게", "저렇게")
_PROCEDURE_CUES: Final = (
    "절차",
    "템플릿",
    "체크리스트",
    "워크플로",
    "매뉴얼",
    "루틴",
    "단계",
    "순서로",
    "재사용",
)
_TEMPORARY_WINDOWS: Final = (
    "오늘",
    "내일",
    "모레",
    "이번 주",
    "이번주",
    "금요일까지",
    "주말까지",
    "7일",
    "일주일",
    "며칠",
)
_STATUS_CUES: Final = (
    "중이",
    "중입니다",
    "상태",
    "출장",
    "휴가",
    "부재",
    "바빠",
    "아파",
    "연락이 어려",
    "답장이 늦",
    "응답이 늦",
)
_STABLE_CUES: Final = (
    "항상",
    "선호",
    "좋아해",
    "싫어해",
    "내 이름은",
    "제 이름은",
    "앞으로 답변",
    "앞으로 응답",
    "앞으로 나를",
    "앞으로 저를",
)
_GLOBAL_CUES: Final = (
    "답변",
    "응답",
    "호칭",
    "불러",
    "말투",
    "한국어",
    "영어",
    "언어",
    "시간대",
    "타임존",
    "단위",
    "날짜 형식",
    "내 이름은",
    "제 이름은",
    "알레르기",
)
_MAX_STABLE_GLOBAL_LENGTH: Final = 120

@dataclass(frozen=True, slots=True)
class MemoryRoute:
    canonical: MemoryTarget
    co_write: tuple[MemoryTarget, ...]
    never_persist: bool
    needs_sensitive_approval: bool
    reason: str


def classify_memory_request(
    text: str,
    *,
    sensitivity: frozenset[str] = frozenset(),
) -> MemoryRoute:
    normalized = " ".join(text.casefold().split()).strip(" .,!?:;~。！？")
    needs_sensitive_approval = bool(sensitivity)
    is_explicit = any(cue in normalized for cue in _EXPLICIT_CUES)
    has_temporary_window = any(cue in normalized for cue in _TEMPORARY_WINDOWS)
    has_status = any(cue in normalized for cue in _STATUS_CUES)
    is_procedure = any(cue in normalized for cue in _PROCEDURE_CUES)
    is_short = len(normalized) <= _MAX_STABLE_GLOBAL_LENGTH
    is_stable = any(cue in normalized for cue in _STABLE_CUES)
    is_global = any(cue in normalized for cue in _GLOBAL_CUES)
    is_ambiguous = normalized in _AMBIGUOUS_REQUESTS or (
        len(normalized) <= 24
        and any(cue in normalized for cue in _REFERENTIAL_CUES)
    )

    if not is_explicit:
        canonical: MemoryTarget = "none"
        co_write: tuple[MemoryTarget, ...] = ()
        never_persist = True
        reason = "uncertain-conservative"
    elif has_temporary_window and has_status:
        canonical: MemoryTarget = "tasks"
        co_write: tuple[MemoryTarget, ...] = ()
        never_persist = True
        reason = "temporary-status"
    elif is_procedure:
        canonical = "skill"
        co_write = ()
        never_persist = False
        reason = "reusable-procedure"
    elif is_short and is_stable and is_global:
        canonical = "wiki"
        co_write = ("memory_md",)
        never_persist = False
        reason = "stable-global-preference"
    else:
        canonical = "wiki"
        co_write = ()
        never_persist = False
        reason = "uncertain-conservative" if is_ambiguous else "explicit-memory-wiki"

    if needs_sensitive_approval:
        reason = "sensitive-needs-approval"

    return MemoryRoute(
        canonical=canonical,
        co_write=co_write,
        never_persist=never_persist,
        needs_sensitive_approval=needs_sensitive_approval,
        reason=reason,
    )
