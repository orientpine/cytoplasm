"""토큰의 원래 시각과 출처를 정규화된 문장·조각의 문자 좌표에 연결한다."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Final, Literal

UNKNOWN_MS: Final = -1
_SENTENCE_END: Final = re.compile(r"(?<=[.!?\u2026])\s+")
_WHITESPACE: Final = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class TimedWord:
    """whisper 토큰 또는 토큰을 대신하는 세그먼트와 보간하지 않은 시각."""

    text: str
    start_ms: int
    end_ms: int
    folded: str = ""
    timing_source: Literal["token", "segment", "aligned"] = "token"


@dataclass(frozen=True, slots=True)
class SentenceWord:
    """입력 배열 색인과 정규화된 문장 안의 반열린 문자 범위."""

    source_index: int
    start_char: int
    end_char: int
    word: TimedWord
    clipped: bool = False


@dataclass(frozen=True, slots=True)
class TimedSentence:
    """문장과 시각·화자·원래 토큰. 단어 메타데이터는 문서에 렌더하지 않는다."""

    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    speaker: str = ""
    folded: str = ""
    words: tuple[SentenceWord, ...] = ()


def normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def split_sentences(text: str) -> tuple[str, ...]:
    """종결 부호 뒤 공백에서만 잘라 소수점과 날짜를 보존한다."""
    return tuple(part for part in _SENTENCE_END.split(normalize(text)) if part)


def _clusters(words: Sequence[TimedWord]) -> Iterator[tuple[str, set[int]]]:
    """결합 문자와 한글 자모 조합이 토큰 경계를 넘어도 같은 클러스터로 묶는다."""
    cluster = ""
    owners: set[int] = set()
    for index, word in enumerate(words):
        for char in word.text:
            if cluster and not unicodedata.combining(char):
                before = unicodedata.normalize("NFC", cluster)
                after = unicodedata.normalize("NFC", char)
                if unicodedata.normalize("NFC", cluster + char) == before + after:
                    yield before, owners
                    cluster, owners = "", set[int]()
            cluster += char
            owners.add(index)
    if cluster:
        yield unicodedata.normalize("NFC", cluster), owners


def _normalized(
    words: Sequence[TimedWord], source_offset: int,
) -> tuple[str, tuple[SentenceWord, ...]]:
    """NFC·공백 정리 뒤 좌표를 만든다. 합쳐진 클러스터는 출처끼리 범위를 공유한다."""
    chars: list[str] = []
    sources: list[set[int]] = []
    for cluster, owners in _clusters(words):
        for char in cluster:
            if char.isspace():
                if not chars:
                    continue
                if chars[-1] == " ":
                    sources[-1].update(owners)
                    continue
                char = " "
            chars.append(char)
            sources.append(set(owners))
    if chars and chars[-1] == " ":
        del chars[-1]
        del sources[-1]
    spans: dict[int, tuple[int, int]] = {}
    for position, owners in enumerate(sources):
        for index in owners:
            start, _ = spans.get(index, (position, position))
            spans[index] = (start, position + 1)
    return "".join(chars), tuple(
        SentenceWord(index + source_offset, *spans[index], word)
        for index, word in enumerate(words) if index in spans
    )


def slice_words(
    words: Sequence[SentenceWord], text: str, begin: int, finish: int,
) -> tuple[SentenceWord, ...]:
    """범위를 잘라 조각의 0점으로 옮기되 원래 토큰과 시각은 그대로 둔다."""
    made: list[SentenceWord] = []
    for ref in words:
        start, end = max(begin, ref.start_char), min(finish, ref.end_char)
        if start >= end:
            continue
        # 문장 앞뒤 공백 제거는 발화 절단이 아니다.
        clipped = bool(text[ref.start_char:start].strip() or text[end:ref.end_char].strip())
        made.append(replace(ref, start_char=start - begin, end_char=end - begin,
                            clipped=ref.clipped or clipped))
    return tuple(made)


def word_bounds(words: Sequence[SentenceWord]) -> tuple[int | None, int | None]:
    """포함된 단어의 알려진 양 끝만 취한다. 누락·역전 시각을 만들어 고치지 않는다."""
    starts = [ref.word.start_ms for ref in words if ref.word.start_ms != UNKNOWN_MS]
    ends = [ref.word.end_ms for ref in words if ref.word.end_ms != UNKNOWN_MS]
    return min(starts) if starts else None, max(ends) if ends else None


def spoken_sentences(
    words: Sequence[TimedWord], source_offset: int = 0,
) -> tuple[TimedSentence, ...]:
    """토큰 사이에 공백을 발명하지 않고 이어 붙인 뒤 문장과 원본 좌표를 함께 자른다."""
    text, refs = _normalized(words, source_offset)
    starts: list[int] = [0]
    ends: list[int] = []
    for match in _SENTENCE_END.finditer(text):
        ends.append(match.start())
        starts.append(match.end())
    ends.append(len(text))
    made: list[TimedSentence] = []
    for begin, finish in zip(starts, ends, strict=True):
        if begin == finish:
            continue
        carried = slice_words(refs, text, begin, finish)
        start_ms, end_ms = word_bounds(carried)
        made.append(TimedSentence(text[begin:finish], start_ms, end_ms, words=carried))
    return tuple(made)
