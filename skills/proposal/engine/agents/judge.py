from __future__ import annotations

from dataclasses import dataclass
import json
from typing import cast

from ..contracts.models import FrozenModel
from ..contracts.protocols import LLMClient

# rule.md (lines 91-106) AI 평가 — 7개 항목 각 1~5점, 가중치(배점) 적용 후 100점 환산.
# Names + weights copied verbatim from resource/rule.md so the judge mirrors the
# official scoring rubric.
#
# NOTE: This LLM-judge is OPT-IN and intentionally NOT part of the CI gate
# (`make check`). It is non-deterministic (depends on the judging backend) and is
# meant for offline, replayable evaluation only — never wire it into the
# deterministic pipeline or a CI assertion. Use a recorded/cached backend
# (CachingLLMClient) for reproducible runs.

JUDGE_CRITERIA: tuple[str, ...] = (
    "구조 및 형식 완성도",
    "논리적 일관성",
    "구체성 및 명확성",
    "양식 일관성",
    "참고문헌 신뢰도",
    "간결성 및 전달력",
    "단어 적절성 및 문체 안정성",
)

# Per-criterion weight (배점) from rule.md; sums to 100.
JUDGE_WEIGHTS: tuple[int, ...] = (20, 20, 20, 10, 10, 10, 10)

_MAX_SCORE = 5
_DEFAULT_SCORE = 3  # graceful fallback when the LLM response cannot be parsed.


@dataclass(frozen=True)
class CriterionScore(FrozenModel):
    criterion: str
    score: int  # 1..5
    comment: str


@dataclass(frozen=True)
class JudgeResult(FrozenModel):
    scores: tuple[CriterionScore, ...]
    total_score: float  # weighted 100-point conversion (pre-cap)
    missing_sections: int  # count of missing mandatory sections
    capped_score: float  # total_score after missing-section ceiling
    threshold: int  # per-criterion pass threshold (default 3)
    below_threshold: tuple[str, ...]  # criteria scoring < threshold


def _build_prompt(text: str) -> str:
    criteria_lines = "\n".join(
        f"{idx + 1}. {name} (배점 {weight})"
        for idx, (name, weight) in enumerate(zip(JUDGE_CRITERIA, JUDGE_WEIGHTS, strict=True))
    )
    return (
        "다음 R&D 연구계획서를 아래 7개 항목으로 각 1~5점 채점하라.\n"
        "정수 점수와 한국어 근거 코멘트를 제시하라.\n\n"
        f"[평가 항목]\n{criteria_lines}\n\n"
        "[응답 형식] 다음 JSON 스키마만 출력하라(코드블록 금지):\n"
        '{"scores": [{"criterion": "<항목명>", "score": <1-5 정수>, '
        '"comment": "<근거>"}, ...]}\n\n'
        f"[연구계획서]\n{text}"
    )


def _coerce_score(value: object) -> int:
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly.
        return _DEFAULT_SCORE
    if isinstance(value, (int, float)):
        score = int(value)
    elif isinstance(value, str):
        try:
            score = int(value.strip())
        except ValueError:
            return _DEFAULT_SCORE
    else:
        return _DEFAULT_SCORE
    return max(1, min(_MAX_SCORE, score))


def _parse_scores(raw: str) -> dict[str, CriterionScore]:
    """Parse the judge JSON; graceful per-criterion fallback to default score."""
    parsed: dict[str, object] = {}
    try:
        loaded: object = cast("object", json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        loaded = None
    if isinstance(loaded, dict):
        parsed = cast("dict[str, object]", loaded)

    by_name: dict[str, CriterionScore] = {}
    entries = parsed.get("scores")
    if isinstance(entries, list):
        for entry in cast("list[object]", entries):
            if not isinstance(entry, dict):
                continue
            item = cast("dict[str, object]", entry)
            name = item.get("criterion")
            if not isinstance(name, str) or name not in JUDGE_CRITERIA:
                continue
            by_name[name] = CriterionScore(
                criterion=name,
                score=_coerce_score(item.get("score")),
                comment=str(item.get("comment", "")),
            )
    return by_name


def _missing_section_cap(missing_sections: int) -> float | None:
    """rule.md ceiling: 3+ missing → 40, exactly 2 missing → 60, else no cap."""
    if missing_sections >= 3:
        return 40.0
    if missing_sections == 2:
        return 60.0
    return None


def judge_proposal(
    text: str,
    llm: LLMClient,
    threshold: int = 3,
    missing_sections: int = 0,
) -> JudgeResult:
    """Judge a proposal `text` against the rule.md 7 criteria via an LLM.

    The LLM is asked (role="judge") to return JSON with a per-criterion 1-5 score
    plus a Korean rationale comment. Scores are weighted (rule.md 배점) into a
    100-point total, then a missing-section ceiling is applied (2 missing → max 60,
    3+ missing → max 40). Criteria scoring below `threshold` are flagged.

    Parsing is defensive: any missing/invalid criterion falls back to a neutral
    score of 3 so a malformed judge response never raises.

    This is an OPT-IN evaluation helper — do NOT add it to the CI gate.
    """
    raw = llm.complete("judge", _build_prompt(text))
    parsed = _parse_scores(raw)

    scores: list[CriterionScore] = []
    for name in JUDGE_CRITERIA:
        scores.append(
            parsed.get(
                name,
                CriterionScore(criterion=name, score=_DEFAULT_SCORE, comment=""),
            )
        )

    weighted = sum(
        cs.score / _MAX_SCORE * weight
        for cs, weight in zip(scores, JUDGE_WEIGHTS, strict=True)
    )
    total_score = round(weighted, 2)

    cap = _missing_section_cap(missing_sections)
    capped_score = total_score if cap is None else min(total_score, cap)

    below = tuple(cs.criterion for cs in scores if cs.score < threshold)

    return JudgeResult(
        scores=tuple(scores),
        total_score=total_score,
        missing_sections=missing_sections,
        capped_score=capped_score,
        threshold=threshold,
        below_threshold=below,
    )


__all__ = [
    "JUDGE_CRITERIA",
    "JUDGE_WEIGHTS",
    "CriterionScore",
    "JudgeResult",
    "judge_proposal",
]
