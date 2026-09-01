"""발표자료(슬라이드) 텍스트 추출 — 대명사·모호 지시어 교정 재료.

계약 둘이 이 모듈의 존재 이유다.

- **fail-soft**: 발표자료는 보조 재료다. 없거나·스캔본이거나·형식이 낯설어도 회의록 생성을
  멈추지 않는다. 실패는 예외가 아니라 `Deck.status` 로 돌아오고 노트에 그대로 적힌다.
- **fail-closed 는 게이트 쪽**: 추출한 텍스트는 `gate_text()` 로 회의 본문과 함께 민감도
  게이트에 **반드시** 합산된다. 합산하지 않으면 특허 슬라이드가 GLM 으로 새는 경로가 생긴다.
  그래서 `prompt_block()` 과 `gate_text()` 는 **같은 조건**으로 자료를 고른다 — 프롬프트로
  나가는 것은 예외 없이 게이트를 지난 것이다.

본문을 읽는 일 자체는 여기서 하지 않는다. `automation/document_text.py` 가 단일 정의이고
이 모듈은 그 단위들을 슬라이드 번호가 붙은 `Deck` 으로 옮길 뿐이다 — 추출기가 둘이면 한쪽만
새 형식을 배우고 갈라진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import meeting_knowledge

from meeting_extract import MAX_INPUT_BYTES

MAX_DECK_BYTES: Final = MAX_INPUT_BYTES
MAX_PROMPT_CHARS: Final = 12000
_UNIT: Final = "슬라이드"
_TRUNCATED: Final = "…(이하 생략)"
_RUNTIME_MISSING: Final = "본문 추출 런타임을 찾지 못했습니다"


@dataclass(frozen=True, slots=True)
class Deck:
    """One presentation file. ``status != "ok"`` means the text is empty on purpose."""

    name: str
    text: str
    slide_count: int
    status: str


def _refused(path: Path, reason: str) -> Deck:
    return Deck(name=path.name, text="", slide_count=0, status=reason)


def _extract(path: Path) -> Any:
    return meeting_knowledge.module("automation.document_text").extract_document(
        path, max_bytes=MAX_DECK_BYTES
    )


def _slide_text(number: int, body: str) -> str:
    return f"[{_UNIT} {number}] {body}".rstrip()


def extract_deck(path: Path) -> Deck:
    """Never raises — a deck that cannot be read comes back with an explaining status."""
    try:
        extracted = _extract(path)
    except Exception:  # noqa: BLE001 - 런타임 부재도 회의록을 멈추지 않는다
        return _refused(path, f"읽지 못함: {_RUNTIME_MISSING}")
    if extracted.status != "ok":
        return _refused(path, extracted.status)
    slides = [_slide_text(number, unit) for number, unit in enumerate(extracted.units, start=1)]
    return Deck(
        name=path.name,
        text="\n\n".join(slides),
        slide_count=len(slides),
        status=extracted.status,
    )


def note_label(deck: Deck) -> str:
    return f"{deck.name} ({deck.slide_count}쪽)" if deck.status == "ok" else f"{deck.name} — {deck.status}"


def gate_text(decks: tuple[Deck, ...]) -> str:
    return "\n".join(deck.text for deck in decks if deck.status == "ok")


def prompt_block(decks: tuple[Deck, ...], *, heading: str = "함께 제공된 발표자료") -> str:
    usable = [deck for deck in decks if deck.status == "ok"]
    if not usable:
        return ""
    body = "\n\n".join(f"— {deck.name} —\n{deck.text}" for deck in usable)
    if len(body) > MAX_PROMPT_CHARS:
        body = body[:MAX_PROMPT_CHARS] + _TRUNCATED
    return f"{heading}:\n\n{body}"
