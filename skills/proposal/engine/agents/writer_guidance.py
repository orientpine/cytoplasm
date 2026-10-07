"""Form-shaped instructions for live section writers.

The writer prompt names only the title, objectives and evidence. Replayed fixtures made
that enough, but a live model answered it with its own outline: section titles repeated
inside the body, pipe tables printed as text, "그림 N" captions typed into prose, and
the form's ``1-1.``–``4-3.`` sub-headings left empty beside its own (2026-09-28 node
render). The guidance is added on the way to the model, not to the writer prompt, so
replay caches and mock fixtures keyed by that prompt stay valid.
"""

from __future__ import annotations

import os
import re
from typing import Final

from ..contracts.protocols import LLMClient

_WRITER_ROLE: Final = re.compile(r"writer_sec(\d)")
_RULES: Final = (
    "아래 규칙으로 KIMM R&D 연구계획서의 한 절 본문만 한국어로 쓴다.",
    "- 모든 문장은 보고서체 '-다'로 끝낸다. '습니다'·'입니다' 같은 존댓말을 쓰지 않는다.",
    "- 절 제목(예: '1. 연구 배경 및 필요성')이나 '1.1' 같은 자체 번호 제목을 만들지 않는다.",
    "- 마크다운 표('|' 줄), 굵게(**), 코드 블록, 그림 캡션('그림 N'), [[FIG:...]] 표식을 쓰지 않는다."
    + " 표와 그림은 뒤 단계가 넣는다.",
    "- 근거에 없는 수치·기관명·실적을 만들지 않는다. KPI·일정 수치는 근거 문장의 값만 쓴다.",
    "- 성과지표의 현 수준·목표는 계획의 값을 그 표기 그대로 쓴다. 같은 지표에 다른 기준값"
    + "(예: 6%와 6.46%)을 함께 쓰지 않는다.",
    "- 같은 대상은 한 표기로만 쓴다. 외래어는 국립국어원 표기(예: 버킷, 데이터)를 따른다.",
)
# Where each fact may be stated. The owner's 2026-09-30 evaluation deducted for the same
# targets and schedule appearing in the summary, the goals, the methods and the tables,
# and for a measurement protocol without trial counts or formulas.
_SECTION_RULES: Final[dict[str, tuple[str, ...]]] = {
    "0": (
        "- 성과지표는 '지표 현 수준 → 목표'로 한 번씩만 쓰고, 일정은 단계별 월 구간을 한 번만 쓴다."
        + " 표지 칸(최종목표·연차목표·기대효과)과 같은 목록을 본문에 되풀이하지 않는다.",
    ),
    "1": ("- 성과지표 수치와 월 구간 일정을 적지 않는다. 필요하면 '2-3절 성과지표'를 가리킨다.",),
    "2": (
        "- 2-3 성과지표에는 지표마다 ① 반복 시험 횟수(예: 시나리오 3개 × 각 30회)"
        + " ② 산정식(예: 성공률 = 성공 사이클 수 ÷ 전체 시행 수 × 100)"
        + " ③ 측정 장비·기록 주기·판정 절차를 계획의 값으로 쓴다. 부수 지표(예: 에너지 절감)도"
        + " 측정·계산 절차를 적는다.",
    ),
    "3": (
        "- 성과지표 수치를 다시 적지 않고 '2-3절 성과지표'를 가리킨다. 월 구간 일정은 추진 일정표가"
        + " 실으므로 본문에는 단계 이름과 산출물만 쓴다.",
    ),
    "4": ("- 성과지표 수치와 월 구간 일정을 다시 적지 않는다. 필요하면 '2-3절 성과지표'를 가리킨다.",),
}


def _budget(section_id: str) -> int | None:
    from ..contracts.layout_profile import get_layout_profile

    try:
        profile = get_layout_profile(os.environ.get("KIMM_DOCBOT_PROFILE", "10-page"))
    except (KeyError, ValueError):
        return None
    budget = profile.prose_budgets.get(int(section_id))
    return int(budget * 0.85) if budget else None


def section_guidance(section_id: str) -> str:
    from ..hwpx.seed_fill import FORM_SUBHEADINGS

    headings = [title for _, section, title in FORM_SUBHEADINGS if section == section_id]
    lines = [*_RULES, *_SECTION_RULES.get(section_id, ())]
    if headings:
        lines.append(
            "- 소제목은 아래 목록만, 그 순서와 글자 그대로 '### ' 머리로 쓰고 각 소제목 아래에 문단 2~4개를 쓴다."
        )
        lines.extend(f"  ### {title}" for title in headings)
    else:
        lines.append("- 소제목 없이 문단 3~4개로 필요성·목표·내용·기대효과를 요약한다.")
    budget = _budget(section_id)
    if budget is not None:
        lines.append(f"- 분량은 공백 포함 {budget}자 이하로 쓴다.")
    return "\n".join(lines)


class SectionGuidedLLM:
    """Prefix writer prompts with the form's rules before they reach the backend."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner: LLMClient = inner

    def complete(self, role: str, prompt: str) -> str:
        match = _WRITER_ROLE.fullmatch(role)
        if match is not None:
            prompt = f"{section_guidance(match.group(1))}\n\n{prompt}"
        return self._inner.complete(role, prompt)


__all__ = ["SectionGuidedLLM", "section_guidance"]
