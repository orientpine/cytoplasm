"""Shared recall grounding and model-aware sensitivity gate."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from automation.codex_llm import PROVIDER

DEFAULT_THRESHOLD = 0.45
DEFAULT_STRONG_THRESHOLD = 0.60
GROUNDING_RATIO = 0.5
SENSITIVE_MARKER = "[[PATENT-SENSITIVE-RECALL]]"

_STOPWORDS = {
    "무엇", "뭐야", "뭐지", "뭐였지", "뭔가", "언제", "어디", "누구", "누가", "어떻게",
    "어떤", "얼마", "왜", "알려줘", "알려주세요", "말해줘", "궁금해", "있어", "있나", "있지",
    "인가", "인가요", "대해", "대한", "관련", "관해", "그리고", "그런데", "하지만", "우리",
    "우리의", "당신", "제발", "혹시", "최근", "최근에", "요즘", "함께", "같이", "진행",
    "진행한", "진행했던", "업무", "협업", "회의", "과제", "내역", "내용", "기록", "사항",
    "무슨", "찾아줘", "찾아주세요", "보여줘", "보여주세요", "박사", "박사와", "교수", "교수와",
    "님과", "씨와",
}
_LATIN_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}")
_HANGUL_TOKEN = re.compile(r"[가-힣]{2,}")
_INTENT_MARKERS = ("최근", "요즘", "협업", "함께", "같이", "공동", "동료", "관계", "with")
_PERSON_TITLE = re.compile(r"(?P<name>[가-힣]{2,4})\s*(?:박사|교수|대표|선생님|연구원|님|씨)(?:[은는이가와과랑을를의도]|\b)")
_ORGANIZATION = re.compile(r"(?P<name>[가-힣A-Za-z0-9_-]{2,}(?:대학교|대학|연구소|연구원|회사|기관|센터|병원|재단|협회|그룹|랩))(?:[은는이가와과랑을를의도]|\b)")
_LATIN_NAME = re.compile(r"(?<![A-Za-z0-9_-])[A-Z][a-z]{2,}(?![A-Za-z0-9_-])")
_PARTICLE_NAME = re.compile(r"(?<![가-힣])(?P<name>[가-힣]{2,4})(?:와|과|랑|이랑)(?![가-힣])")
_KOREAN_SURNAMES = frozenset("김 이 박 최 정 강 조 윤 장 임 한 오 서 신 권 황 안 송 전 홍 유 고 문 양 손 배 백 허 남 심 노 하 곽 성 차 주 우 구 민 진 지 엄 채 원 천 방 공 현 함 변 염 여 추 도 소 석 선 설 마 길 연 위 표 명 기 반 왕 금 옥 육 인 맹 제 모 탁 국 어 은 편 용".split())
_ENTITY_BLOCKLIST = frozenset({"박사", "교수", "대표", "선생", "연구원", "동료", "업무", "회의", "과제"})


@dataclass(frozen=True, slots=True)
class EntityIntent:
    matches: bool
    entity_hints: tuple[str, ...]


def analyze_entity_intent(query: str) -> EntityIntent:
    people = (*_PERSON_TITLE.finditer(query), *_PARTICLE_NAME.finditer(query))
    hints = [match.group("name") for match in people if match.group("name")[0] in _KOREAN_SURNAMES and match.group("name") not in _ENTITY_BLOCKLIST]
    hints.extend(match.group("name") for match in _ORGANIZATION.finditer(query))
    hints.extend(match.group() for match in _LATIN_NAME.finditer(query))
    unique = tuple(dict.fromkeys(hints))
    return EntityIntent(any(marker in query.casefold() for marker in _INTENT_MARKERS) and bool(unique), unique)


def tokenize(query: str) -> list[str]:
    tokens = [match.group(0).lower() for match in _LATIN_TOKEN.finditer(query)]
    tokens.extend(match.group(0) for match in _HANGUL_TOKEN.finditer(query) if match.group(0) not in _STOPWORDS)
    return list(dict.fromkeys(tokens))


def _token_in(token: str, haystack: str) -> bool:
    return token in haystack or (len(token) >= 3 and token[:-1] in haystack)


def grounding_ratio(tokens: list[str], content: str) -> float:
    if not tokens:
        return 0.0
    haystack = content.lower()
    return sum(_token_in(token, haystack) for token in tokens) / len(tokens)


def grounded_rows(query: str, rows: list[dict[str, Any]], threshold: float = DEFAULT_THRESHOLD, strong_threshold: float = DEFAULT_STRONG_THRESHOLD) -> list[tuple[dict[str, Any], bool]]:
    tokens = tokenize(query)
    hits: list[tuple[dict[str, Any], bool]] = []
    for row in rows:
        score = float(row.get("score", 0.0))
        if score < threshold:
            continue
        grounded = grounding_ratio(tokens, str(row.get("content", ""))) >= GROUNDING_RATIO
        if score >= strong_threshold or grounded:
            hits.append((row, grounded))
    return hits


def visible_rows(rows: list[dict[str, Any]], sensitive_allowed: bool, marker: str = SENSITIVE_MARKER) -> tuple[list[dict[str, Any]], int, int]:
    visible: list[dict[str, Any]] = []
    excluded = released = 0
    for original in rows:
        metadata = original.get("metadata")
        sensitive = isinstance(metadata, dict) and metadata.get("sensitivity") == "patent-sensitive"
        if sensitive and not sensitive_allowed:
            excluded += 1
            continue
        row = original
        if sensitive:
            row = {**original, "content": f"{marker} {original.get('content', '')}"}
            released += 1
        visible.append(row)
    return visible, excluded, released


def parse_primary_model(text: str) -> tuple[str, str]:
    model = provider = ""
    in_model = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(line) == len(line.lstrip()):
            in_model = stripped == "model:"
            continue
        if in_model:
            key, _, value = stripped.partition(":")
            value = value.strip().strip("'\"")
            if key == "default":
                model = value
            elif key == "provider":
                provider = value
    return model, provider


def primary_route_is_codex_oauth(env: Mapping[str, str], default_path: str = "~/.hermes/config.yaml") -> bool:
    """True only when the primary route IS the Codex OAuth tier — unreadable or any other provider stays closed."""
    path = Path(env.get("RECALL_HERMES_CONFIG", env.get("KNOWLEDGE_HERMES_CONFIG", default_path))).expanduser()
    try:
        model, provider = parse_primary_model(path.read_text(encoding="utf-8"))
    except OSError:
        return False
    return bool(model.strip()) and provider.strip().casefold() == PROVIDER


#: Callers outside this module's ticket scope still import the old spelling.
primary_route_is_glm_free = primary_route_is_codex_oauth


def merge_entity_rows(primary: list[dict[str, Any]], auxiliary: list[dict[str, Any]], hints: tuple[str, ...]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in (*primary, *auxiliary):
        metadata = row.get("metadata")
        path = str(metadata.get("path", "")) if isinstance(metadata, dict) else ""
        identity = str(row.get("source", "")), str(row.get("document_id", "")) or path
        haystack = f"{row.get('content', '')}\n{json.dumps(metadata, ensure_ascii=False, sort_keys=True) if isinstance(metadata, dict) else ''}".casefold()
        if identity not in seen and any(hint.casefold() in haystack for hint in hints):
            seen.add(identity)
            merged.append(row)
    return sorted(merged, key=lambda row: float(row.get("score", 0.0)), reverse=True)
