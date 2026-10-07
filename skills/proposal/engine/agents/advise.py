from __future__ import annotations

from dataclasses import dataclass
import json
from typing import cast

from ..contracts.collab_models import AdviseRanking, EvidenceCandidate
from ..contracts.models import FrozenModel
from ..contracts.protocols import LLMClient

ADVISE_CRITERIA: tuple[str, ...] = (
    "목표/KPI 버킷 적합성",
    "출처 신뢰도",
    "최신성",
    "PUBLIC 안전성",
)

ADVISE_WEIGHTS: tuple[int, ...] = (40, 25, 20, 15)

_MAX_SCORE = 5
_DEFAULT_SCORE = 3


@dataclass(frozen=True)
class CandidateScore(FrozenModel):
    candidate_id: str
    scores: tuple[int, ...]
    total_score: float
    rationale: str


def _build_prompt(candidates: list[EvidenceCandidate]) -> str:
    criteria_lines = "\n".join(
        f"{idx + 1}. {name} (가중치 {weight})"
        for idx, (name, weight) in enumerate(zip(ADVISE_CRITERIA, ADVISE_WEIGHTS, strict=True))
    )
    candidate_lines = "\n".join(
        f"[{candidate.candidate_id[:8]}] source={candidate.source_url}\n"
        f"  verbatim={candidate.verbatim[:200]}"
        for candidate in candidates
    )
    return (
        "아래 연구 근거 후보들을 4개 기준으로 각 1~5점 채점하라.\n\n"
        f"[채점 기준]\n{criteria_lines}\n\n"
        "[응답 형식] JSON만 출력(코드블록 금지):\n"
        '{"scores": [{"candidate_id": "<id>", "scores": [<s1>,<s2>,<s3>,<s4>], '
        '"rationale": "<근거>"}, ...]}\n\n'
        f"[후보 목록]\n{candidate_lines}"
    )


def _coerce_score(value: object) -> int:
    if isinstance(value, bool):
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


def _weighted_score(scores: list[int]) -> float:
    total = 0.0
    for score, weight in zip(scores, ADVISE_WEIGHTS, strict=True):
        total += score * weight
    return total / _MAX_SCORE


def _parse_response(raw: str, candidates: list[EvidenceCandidate]) -> list[CandidateScore]:
    valid_ids = {candidate.candidate_id for candidate in candidates}
    by_id: dict[str, CandidateScore] = {}

    parsed: dict[str, object] = {}
    try:
        loaded = cast("object", json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        loaded = None
    if isinstance(loaded, dict):
        parsed = cast("dict[str, object]", loaded)

    entries = parsed.get("scores")
    if isinstance(entries, list):
        for entry in cast("list[object]", entries):
            if not isinstance(entry, dict):
                continue
            item = cast("dict[str, object]", entry)
            candidate_id = str(item.get("candidate_id", ""))
            if candidate_id not in valid_ids:
                continue
            raw_scores = item.get("scores")
            scores = _parse_score_list(raw_scores)
            by_id[candidate_id] = CandidateScore(
                candidate_id=candidate_id,
                scores=tuple(scores),
                total_score=_weighted_score(scores),
                rationale=str(item.get("rationale", "")),
            )

    for candidate in candidates:
        if candidate.candidate_id not in by_id:
            default_scores = [_DEFAULT_SCORE] * len(ADVISE_CRITERIA)
            by_id[candidate.candidate_id] = CandidateScore(
                candidate_id=candidate.candidate_id,
                scores=tuple(default_scores),
                total_score=_weighted_score(default_scores),
                rationale="(fallback: no LLM score)",
            )

    return list(by_id.values())


def _parse_score_list(raw_scores: object) -> list[int]:
    if not isinstance(raw_scores, list):
        return [_DEFAULT_SCORE] * len(ADVISE_CRITERIA)

    scores = [_coerce_score(score) for score in raw_scores[: len(ADVISE_CRITERIA)]]
    while len(scores) < len(ADVISE_CRITERIA):
        scores.append(_DEFAULT_SCORE)
    return scores


def score_candidates(
    candidates: list[EvidenceCandidate],
    llm: LLMClient,
) -> AdviseRanking:
    """채점·정렬 → AdviseRanking(ranked by total_score desc)."""
    if not candidates:
        return AdviseRanking(ranked=(), rationale={})

    raw = llm.complete("advise_scorer", _build_prompt(candidates))
    scored = _parse_response(raw, candidates)
    scored.sort(key=lambda score: score.total_score, reverse=True)

    return AdviseRanking(
        ranked=tuple(score.candidate_id for score in scored),
        rationale={score.candidate_id: score.rationale for score in scored},
    )


__all__ = [
    "ADVISE_CRITERIA",
    "ADVISE_WEIGHTS",
    "CandidateScore",
    "score_candidates",
]
