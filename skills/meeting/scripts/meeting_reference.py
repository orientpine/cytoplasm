"""소유자가 Drive 에 모아 둔 참고자료를 회의록의 근거 재료로 들여오는 어댑터.

발표자료와 같은 `Deck` 으로 들어오므로 민감도 게이트 합산(`gate_text`)과 프롬프트 주입이
이미 있는 계약을 그대로 탄다 — 참고자료만 게이트를 비켜 가면 특허 자료가 GLM 으로 샌다.
조회 자체는 `automation.drive_reference` 가 소유하고, 실패는 회의록을 멈추지 않는다.
"""

from __future__ import annotations

import sys
from collections import Counter
from typing import Any, Final

import meeting_knowledge
import meeting_slides

MAX_REFERENCES: Final = 3
MAX_QUERY_TERMS: Final = 6
MAX_QUERY_CHARS: Final = 4000
HEADING: Final = "소유자 참고자료"
# 모든 회의에서 되풀이되는 구어체 군더더기는 참고자료 검색어에서 뺀다.
STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "그리고",
        "그래서",
        "하지만",
        "그러면",
        "그런데",
        "이제",
        "우리",
        "저희",
        "지금",
        "부분",
        "경우",
        "정도",
        "생각",
        "말씀",
        "회의",
        "내용",
        "자료",
        "관련",
        "대한",
        "있습니다",
        "없습니다",
        "합니다",
        "됩니다",
        "것을",
        "것이",
        "하는",
        "있는",
        "같습니다",
    }
)


def _tokens(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    start = 0
    for end, character in enumerate(text):
        if character.isalnum():
            continue
        if end - start >= 2:
            tokens.append(text[start:end])
        start = end + 1
    if len(text) - start >= 2:
        tokens.append(text[start:])
    return tuple(tokens)


def query(label: str, project: str, text: str) -> str:
    base = " ".join(f"{label} {project}".split())
    excluded = STOPWORDS | frozenset(_tokens(base))
    counts = Counter(token for token in _tokens(text[:MAX_QUERY_CHARS]) if token not in excluded)
    ranked = sorted(counts, key=lambda token: (-counts[token], token))[:MAX_QUERY_TERMS]
    return " ".join((base, *ranked)) if base else " ".join(ranked)


def _scan(query: str, limit: int) -> Any:
    return meeting_knowledge.module("automation.drive_reference").collect(query, limit=limit)


def collect(query: str, *, limit: int = MAX_REFERENCES) -> tuple[meeting_slides.Deck, ...]:
    try:
        scan = _scan(query, limit)
    except Exception as failure:  # noqa: BLE001 - 참고자료가 없다고 회의록을 못 쓰면 안 된다
        print(f"REFERENCE-FETCH-FAIL {type(failure).__name__}", file=sys.stderr)
        return ()
    return tuple(
        meeting_slides.Deck(
            name=document.file.name,
            text=document.text,
            slide_count=document.sections,
            status=document.status,
        )
        for document in scan.documents
    )


def note_labels(references: tuple[meeting_slides.Deck, ...]) -> tuple[str, ...]:
    return tuple(meeting_slides.note_label(reference) for reference in references)


def prompt_block(references: tuple[meeting_slides.Deck, ...]) -> str:
    return meeting_slides.prompt_block(references, heading=HEADING)


def merged_prompt(slides: str, references: tuple[meeting_slides.Deck, ...]) -> str:
    return "\n\n".join(block for block in (slides, prompt_block(references)) if block)
