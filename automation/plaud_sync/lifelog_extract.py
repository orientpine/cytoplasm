"""라이프로그 필드 추출의 순수 절반 — 프롬프트 조립과 관대한 JSON 파싱 (I/O 없음)."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from automation.plaud_sync.lifelog_model import (
    LifelogDecision,
    LifelogExtraction,
    LifelogExtractError,
    LifelogRecording,
    LifelogTodo,
)

#: 템플릿이 반드시 갖는 두 자리. 한 번에 치환해 재료가 재료를 치환하지 못하게 한다.
_PLACEHOLDER_RE: Final = re.compile(r"\{\{(SUMMARY|TRANSCRIPT)\}\}")
#: 줄 단위 코드펜스 마커. 본문 문자열 안의 백틱은 건드리지 않는다.
_FENCE_RE: Final = re.compile(r"(?m)^\s*```[^\n]*$")
_AT_RE: Final = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_MAX_ITEMS: Final = 20
_MAX_TEXT: Final = 200
#: 이미 불릿인 줄은 다시 표시하지 않는다.
_BULLETS: Final = ("-", "*", "+", "•")
_DECODER: Final = json.JSONDecoder()

# 식별 근거 규칙표: 실명 사전이 아니라 근거 없는 표현의 어휘·형태만 판정한다.
#: 친족·호칭만으로는 누구인지 알 수 없다. 앞에 이름이 있으면 그 이름은 보존한다.
_KINSHIP: Final = frozenset("형 형님 누나 누님 언니 오빠 동생 아저씨 아주머니 아줌마 어머니 아버지 엄마 아빠".split())
#: 직함은 이름의 접미사일 수 있지만 직함 자체는 식별자가 아니다.
_ROLES: Final = frozenset("박사 박사님 교수 교수님 선생 선생님 연구원 연구원님 부장 부장님 팀장 팀장님 담당 담당자".split())
#: 일반 명사만 붙인 복합어에도 식별 근거는 없다(사람형님·사람 형님).
_COMMON: Final = frozenset("사람 분 남자 여자 친구 동료 직원 손님 회사 기관 조직 장소 사무실 회의실 카페 식당 집".split())
#: 지시어는 녹취 밖에서 대상을 복원할 수 없으므로 이름 대신 쓰지 않는다.
_DEICTIC: Final = frozenset("그분 이분 저분 누군가 누구 여기 거기 저기 이곳 그곳 저곳".split())
#: 빈칸·미확정·전사 라벨은 이름이 아니다. 규칙별로 분리해 오탐 원인을 읽을 수 있게 한다.
_REFERENCE_REJECTIONS: Final = (
    re.compile(r"^(?:미상|미정|불명|불명확|없음|알 수 없음|이름|장소명|unknown|none|null|n/a|tbd)$", re.I),
    re.compile(r"^(?:화자|speaker|사람|장소)\s*\d+$", re.I),  # 번호는 실명·장소의 근거가 아니다.
    re.compile(r"[<>{}\[\]?]|�"),  # 템플릿 빈칸·불확실 표식·깨진 글자는 추측해서 복원하지 않는다.
)
_GENERIC: Final = _KINSHIP | _ROLES | _COMMON | _DEICTIC
_GENERIC_RE: Final = re.compile("(?:" + "|".join(sorted(_GENERIC, key=len, reverse=True)) + ")+")
#: 조사는 모호한 호칭에 붙어도 이름을 만들지 않는다(형님과·그분과의).
_PARTICLES: Final = ("", "은", "는", "이", "가", "을", "를", "의", "과", "와", "과의", "와의", "에게", "께", "에서", "에", "하고", "이랑", "랑")


def _reference(value: object) -> str:
    """판정할 수 없는 개별 필드는 빈 값으로 접는다. 요약·전사 산문은 이 층에 넣지 않는다."""
    text = unicodedata.normalize("NFKC", _text(value))
    if any(unicodedata.category(char).startswith("C") for char in text):
        return ""  # 제어 문자·미배정 문자는 식별 근거로 판단할 수 없다.
    if any(rule.search(text) for rule in _REFERENCE_REJECTIONS):
        return ""
    words = [match.group() for match in re.finditer(r"[^\W_]+", text.casefold())]
    if len("".join(words)) < 2 or not any(char.isalpha() for char in text):
        return ""  # 한 글자·숫자·기호만으로는 식별할 수 없다.
    generic = tuple(_generic_word(word) for word in words)
    if all(generic):
        return ""
    for index, stem in enumerate(generic):
        if stem in _DEICTIC:
            return ""
        if stem and any(kin in stem for kin in _KINSHIP):
            if stem not in _KINSHIP or index == 0 or generic[index - 1]:
                return ""  # 제목의 나머지 산문이 모호한 호칭을 실명으로 위장하지 못하게 한다.
    return text


def _generic_word(word: str) -> str:
    for particle in _PARTICLES:
        stem = word.removesuffix(particle) if particle else word
        if _GENERIC_RE.fullmatch(stem):
            return stem
    return ""


def build_prompt(template: str, *, summary: str, transcript: str) -> str:
    """템플릿의 {{SUMMARY}}·{{TRANSCRIPT}} 자리에 녹취 재료를 채운다."""
    values = {"SUMMARY": summary, "TRANSCRIPT": transcript}
    return _PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], template)


def parse_extraction(raw: str) -> LifelogExtraction:
    """모델 응답에서 JSON 객체 하나를 건져 구조화 필드로 만든다 (형식 이탈에 관대)."""
    payload = _json_object(raw)
    return LifelogExtraction(
        people=_strings(payload.get("people")),
        places=_strings(payload.get("places")),
        decisions=_decisions(payload.get("decisions")),
        todos=_todos(payload.get("todos")),
        summary=_summary(payload.get("summary")),
        title=_reference(payload.get("title")),
    )


@dataclass(frozen=True, slots=True)
class ReferenceDrop:
    """거부된 문자열 한 건: 원래 필드 위치와 공백 정리·200자 제한을 거친 값."""

    field: str
    value: str


def reference_drops(raw: str) -> tuple[ReferenceDrop, ...]:
    """같은 응답의 식별값 거부를 데이터로 돌려준다. 판정은 오직 _reference 가 한다.

    제목 → 사람 → 장소 → 할 일 담당자 순, 중복·목록 상한 적용 전의 거부 횟수다.
    빈칸·비문자열·잘못된 목록·본문 없는 할 일은 식별값 후보가 아니므로 세지 않는다.
    JSON 실패는 parse_extraction 과 같은 LifelogExtractError 이며 개별 값은 던지지 않는다.
    알려진 한계: 실제 상호 '형님식당'도 거부된다. 빈도 근거 없이 예외를 만들지 않는다.
    """
    payload = _json_object(raw)
    candidates: list[tuple[str, object]] = [("title", payload.get("title"))]
    for field in ("people", "places"):
        candidates.extend(
            (f"{field}[{index}]", value)
            for index, value in enumerate(_items(payload.get(field)))
        )
    for index, item in enumerate(_items(payload.get("todos"))):
        fields = _fields(item)
        if _text(fields.get("text")):
            candidates.append((f"todos[{index}].owner", fields.get("owner")))
    return tuple(
        ReferenceDrop(field, text)
        for field, value in candidates
        if (text := _text(value)) and not _reference(value)
    )


def extract(
    recording: LifelogRecording, *, template: str, complete: Callable[[str], str]
) -> LifelogExtraction:
    """녹취 하나를 모델에 보내 구조화 필드를 얻는다. 모든 전송 실패는 이번 폴의 실패다."""
    prompt = build_prompt(
        template, summary=recording.summary_markdown, transcript=recording.transcript_text
    )
    try:
        raw = complete(prompt)
    except LifelogExtractError:
        raise
    except Exception as error:
        # 어떤 예외든 다음 폴에서 재시도할 일시 실패로 접는다 (노트를 열화 동결하지 않는다).
        raise LifelogExtractError(f"LLM 호출 실패: {type(error).__name__}") from None
    return parse_extraction(raw)


def summarize(
    recording: LifelogRecording, *, template: str, complete: Callable[[str], str]
) -> str:
    """요약만 다시 묻는 두 번째 호출 — 첫 추출이 요약을 못 줬을 때만 쓰인다.

    첫 호출은 다섯 가지를 한 번에 시킨다. 전사가 거칠면 모델이 구조화 필드는 채우면서
    요약만 빈 채로 답하는 일이 실제로 일어났다(2026-09-04 노트). 요약 하나만 묻는
    좁은 요청은 그 실패 모양을 다시 만들지 않는다.
    """
    prompt = build_prompt(
        template, summary=recording.summary_markdown, transcript=recording.transcript_text
    )
    try:
        raw = complete(prompt)
    except LifelogExtractError:
        raise
    except Exception as error:
        raise LifelogExtractError(f"요약 재시도 실패: {type(error).__name__}") from None
    try:
        payload = _json_object(raw)
    except LifelogExtractError:
        # JSON 을 못 냈어도 불릿 목록이면 요약이 맞다. 그 외의 산문은 받지 않는다 —
        # 이 호출에 도달했다는 것은 요약이 하나도 없다는 뜻이고, 거기서 "요약할 수
        # 없습니다" 를 요약으로 실으면 빈 요약보다 나쁘다(노트가 내용이 있는 척한다).
        return _bullets(raw)
    return _summary(payload.get("summary"))


def _json_object(raw: str) -> dict[str, object]:
    """펜스·산문을 걷어내고 첫 JSON 객체를 찾는다. 없으면 이번 폴은 실패다."""
    text = _FENCE_RE.sub("", raw)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        decoded = _loads(text[start : end + 1])
        if decoded is not None:
            return decoded
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            candidate, _ = _DECODER.raw_decode(text, index)
        except ValueError:
            continue
        if isinstance(candidate, dict):
            return candidate
    raise LifelogExtractError("LLM 응답에서 JSON 객체를 찾지 못했다")


def _loads(candidate: str) -> dict[str, object] | None:
    try:
        decoded = json.loads(candidate)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _text(value: object) -> str:
    """문자열만 받아 공백을 정규화하고 상한에서 자른다. 그 외 타입은 빈 문자열."""
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.split())
    if len(normalized) <= _MAX_TEXT:
        return normalized
    return normalized[: _MAX_TEXT - 1] + "…"


def _summary(value: object) -> str:
    """요약은 줄바꿈이 뜻을 갖는 마크다운이라 _text 의 공백 정규화를 쓸 수 없다.

    프롬프트가 '불릿 3–6개'를 요구하므로 모델은 문자열 대신 **배열**로 답하기도 한다.
    문자열만 받아들이면 그 응답이 통째로 사라지고 노트의 '## 요약'이 빈다 — 실측
    2026-09-04 노트가 정확히 그 모양이었다(사람·장소·결정·할 일은 채워졌다).
    """
    if isinstance(value, str):
        return value.strip()
    lines: list[str] = []
    for entry in _items(value):
        # 다른 목록 필드와 같은 자(_items·_fields·_text)로 읽고 재고 자른다 — 배열 요약만
        # 상한이 없으면 모델 한 번의 폭주가 노트 본문으로 그대로 들어간다.
        fields: dict[str, object] = _fields(entry)
        text = _text(fields.get("text"))
        if not text:
            continue
        lines.append(text if text.startswith(_BULLETS) else f"- {text}")
        if len(lines) == _MAX_ITEMS:
            break
    return "\n".join(lines)


def _bullets(raw: str) -> str:
    """첫 줄이 불릿일 때만 마크다운 목록을 요약으로 받는다 — 산문은 요약이 아니다."""
    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    if not lines or not lines[0].startswith(_BULLETS):
        return ""
    return "\n".join(lines[:_MAX_ITEMS])


def _at(value: object) -> str:
    """MM:SS(또는 HH:MM:SS)만 남기고 나머지 표기는 근거 없음으로 본다."""
    stamp = _text(value)
    return stamp if _AT_RE.match(stamp) else ""


def _items(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _fields(item: object) -> dict[str, object]:
    if isinstance(item, dict):
        return item
    if isinstance(item, str):
        return {"text": item}
    return {}


def _strings(value: object) -> tuple[str, ...]:
    """순서를 지키며 중복을 제거한 문자열 목록."""
    unique: list[str] = []
    for item in _items(value):
        text = _reference(item)
        if text and text not in unique:
            unique.append(text)
    return tuple(unique[:_MAX_ITEMS])


def _decisions(value: object) -> tuple[LifelogDecision, ...]:
    decisions: list[LifelogDecision] = []
    for item in _items(value):
        fields = _fields(item)
        text = _text(fields.get("text"))
        if text:
            decisions.append(LifelogDecision(text=text, at=_at(fields.get("at"))))
    return tuple(decisions[:_MAX_ITEMS])


def _todos(value: object) -> tuple[LifelogTodo, ...]:
    todos: list[LifelogTodo] = []
    for item in _items(value):
        fields = _fields(item)
        text = _text(fields.get("text"))
        if text:
            todos.append(
                LifelogTodo(
                    text=text,
                    owner=_reference(fields.get("owner")),
                    due=_text(fields.get("due")),
                    at=_at(fields.get("at")),
                )
            )
    return tuple(todos[:_MAX_ITEMS])
