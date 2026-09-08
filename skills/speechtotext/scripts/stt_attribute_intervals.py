"""배정용 반열린 구간 합집합과 원본 색인을 보존하는 BPE 묶음."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeAlias

from stt_sentence import SentenceWord, TimedWord

Interval: TypeAlias = tuple[int, int]
Intervals: TypeAlias = tuple[Interval, ...]


def valid_interval(start: int | None, end: int | None) -> bool:
    """입력 경계에서 누락·역전·소수 시각을 배제하며 보간하지 않는다."""
    return type(start) is int and type(end) is int and 0 <= start < end


def union(intervals: Iterable[Interval]) -> Intervals:
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(merged[-1][1], end)
        else:
            merged.append((start, end))
    return tuple(merged)


@dataclass(frozen=True, slots=True)
class LexicalWord:
    source_indices: tuple[int, ...]
    intervals: Intervals


def lexical_words(words: Iterable[TimedWord | SentenceWord]) -> tuple[LexicalWord, ...]:
    """공백 없는 BPE를 묶고 구두점 시각은 버린다. 원본 텍스트는 수정하지 않는다.

    결과는 원본 색인 순이다. SentenceWord의 색인 공백은 생략된 토큰 경계이며,
    문장별 호출에서도 토큰을 건너뛰어 낱말을 합치지 않는다. 구두점은 해당 묶음의
    판정을 공유하되 증거를 더하지 않는다. 부분 누락 BPE는 유효 조각만 채점한다.
    """
    indexed = sorted(
        ((word.source_index, word.word) if isinstance(word, SentenceWord) else (index, word)
         for index, word in enumerate(words)),
        key=lambda item: item[0],
    )
    groups: list[LexicalWord] = []
    indices: list[int] = []
    intervals: list[Interval] = []
    separated = False
    for index, word in indexed:
        spoken = any(not char.isspace() and not unicodedata.category(char).startswith("P")
                     for char in word.text)
        boundary = separated or bool(word.text[:1].isspace())
        if indices and ((spoken and boundary) or index != indices[-1] + 1):
            groups.append(LexicalWord(tuple(indices), union(intervals)))
            indices, intervals = [], []
        indices.append(index)
        if spoken and valid_interval(word.start_ms, word.end_ms):
            intervals.append((word.start_ms, word.end_ms))
        separated = bool(word.text[-1:].isspace()) or (separated and not spoken)
    if indices:
        groups.append(LexicalWord(tuple(indices), union(intervals)))
    return tuple(groups)


def intersect(left: Intervals, right: Intervals) -> Intervals:
    """정렬된 합집합끼리 교차하여 BPE의 빈 시간은 제외한다."""
    found: list[Interval] = []
    i = j = 0
    while i < len(left) and j < len(right):
        start, end = max(left[i][0], right[j][0]), min(left[i][1], right[j][1])
        if start < end:
            found.append((start, end))
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return tuple(found)


def measure(intervals: Intervals) -> int:
    return sum(end - start for start, end in intervals)


def coverage(shared: tuple[Intervals, ...]) -> tuple[int, int]:
    """화자별 합집합의 사건을 동시 처리하여 반열린 경계·삼중 중첩을 한 번만 센다."""
    events: dict[int, int] = {}
    for intervals in shared:
        for start, end in intervals:
            events[start] = events.get(start, 0) + 1
            events[end] = events.get(end, 0) - 1
    covered = simultaneous = active = previous = 0
    for when, delta in sorted(events.items()):
        if active:
            covered += when - previous
        if active > 1:
            simultaneous += when - previous
        active += delta
        previous = when
    return covered, simultaneous
