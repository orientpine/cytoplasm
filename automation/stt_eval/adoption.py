"""쌍대 비교의 기본값 후보 판정과 추적 규칙 경계."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias, cast

from .compare_store import JSON

VerdictStatus: TypeAlias = Literal["adopt", "reject", "insufficient", "inconclusive"]
Direction: TypeAlias = Literal["lower", "higher"]
DEFAULT_RULE = Path(__file__).resolve().parents[2] / "configs/stt-eval/adoption-rule.json"


class AdoptionRuleError(ValueError):
    """채택 규칙이 고정 계약을 만족하지 않는다."""


@dataclass(frozen=True, slots=True)
class AdoptionRule:
    min_references: int = 3
    metric: Literal["cpcer_strict"] = "cpcer_strict"
    confidence: float = 0.95
    guards: tuple[tuple[str, Direction], ...] = (("cer", "lower"), ("entity_f1", "higher"), ("der", "lower"))


@dataclass(frozen=True, slots=True)
class Verdict:
    status: VerdictStatus
    reasons: tuple[str, ...]


def load_rule(path: Path = DEFAULT_RULE) -> AdoptionRule:
    """필드 누락·추가·계약 완화를 모두 거부한다. 오류에 입력 원문을 넣지 않는다."""
    try:
        raw = cast(JSON, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        raise AdoptionRuleError("채택 규칙 JSON을 읽을 수 없습니다") from error
    expected: dict[str, JSON] = {"schema": "stt-eval-adoption/v1", "min_references": 3,
                                 "metric": "cpcer_strict", "confidence": 0.95,
                                 "guards": {"cer": "lower", "entity_f1": "higher", "der": "lower"}}
    if not isinstance(raw, dict) or set(raw) != set(expected):
        raise AdoptionRuleError("채택 규칙 필드가 다릅니다")
    minimum = raw["min_references"]
    if type(minimum) is not int or minimum < 3:
        raise AdoptionRuleError("정답 최소 표본은 3 이상의 정수여야 합니다")
    expected["min_references"] = minimum
    if raw != expected:
        raise AdoptionRuleError("채택 규칙의 지표·가드·95% 구간 계약이 다릅니다")
    return AdoptionRule(min_references=minimum)


def _interval(summary: JSON, minimum: int) -> tuple[float, float] | None:
    if not isinstance(summary, dict):
        return None
    scored = summary.get("scored_n")
    interval = summary.get("ci95")
    if type(scored) is not int or scored < minimum or not isinstance(interval, list) or len(interval) != 2:
        return None
    low, high = interval
    if (type(low) not in (int, float) or type(high) not in (int, float)
            or not isinstance(low, (int, float)) or not isinstance(high, (int, float))):
        return None
    if not math.isfinite(low) or not math.isfinite(high) or low > high:
        return None
    return float(low), float(high)


def decide(compare_result: dict[str, JSON], rule: AdoptionRule) -> Verdict:
    """delta=B-A다. adopt는 사람의 기본값 후보일 뿐 어떤 설정도 바꾸지 않는다."""
    if compare_result.get("self_test") is True:
        return Verdict("insufficient", ("self-test: 합성 참조는 정답 표본이 아닙니다",))
    n = compare_result.get("n")
    if type(n) is not int or n < rule.min_references:
        return Verdict("insufficient", (f"정답 완전 쌍 n<{rule.min_references}: 기본값 불변",))
    if compare_result.get("metric") != rule.metric:
        return Verdict("inconclusive", ("cpcer_strict 쌍대 비교가 필요합니다",))
    guards = compare_result.get("guards")
    unavailable: list[str] = []
    regressions: list[str] = []
    for metric, direction in rule.guards:
        interval = _interval(guards.get(metric) if isinstance(guards, dict) else None, rule.min_references)
        if interval is None:
            unavailable.append(f"{metric}: 가드 지표 또는 유효 쌍대 구간 없음")
        elif direction == "lower" and interval[0] > 0 or direction == "higher" and interval[1] < 0:
            regressions.append(f"{metric}: 가드 악화의 95% 구간이 0을 배제합니다")
    primary = _interval(compare_result, rule.min_references)
    if primary is not None and primary[0] > 0:
        regressions.append("cpcer_strict: B 악화의 95% 구간이 0을 배제합니다")
    if regressions:
        return Verdict("reject", tuple(regressions + unavailable))
    if primary is None:
        unavailable.append("cpcer_strict: 유효 쌍대 구간 없음")
    elif primary[1] >= 0:
        unavailable.append("cpcer_strict: B 우세의 95% 구간이 0을 배제하지 않습니다")
    if unavailable:
        return Verdict("inconclusive", tuple(unavailable))
    return Verdict("adopt", ("cpcer_strict B 우세·가드 비악화: 기본값 후보, 사람의 명시 커밋 필요",))
