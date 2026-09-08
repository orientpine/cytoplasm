"""정수 밀리초 근거로만 단어에 화자를 배정한다."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Literal, Protocol, TypeAlias

from stt_attribute_intervals import (
    Intervals, coverage, intersect, lexical_words, measure, union, valid_interval,
)
from stt_sentence import SentenceWord, TimedWord

TagKind: TypeAlias = Literal["SPEAKER", "UNKNOWN", "OVERLAP"]
Reason: TypeAlias = Literal[
    "timing", "no_turns", "direct", "overlap", "boundary_tie", "low_coverage",
    "tolerance", "no_support",
]


class SpeakerTurn(Protocol):
    @property
    def start_ms(self) -> int: ...

    @property
    def end_ms(self) -> int: ...

    @property
    def speaker(self) -> int: ...


@dataclass(frozen=True, slots=True)
class SpeakerTag:
    """확정은 한 명, 미상은 빈 목록, 겹침은 참여자 전원을 안정 라벨 순으로 보존한다."""

    kind: TagKind
    speakers: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.speakers[0] if self.kind == "SPEAKER" else "화자0"


@dataclass(frozen=True, slots=True)
class AttributionPolicy:
    tolerance_ms: int = 200
    min_coverage: Fraction = Fraction(1, 2)
    min_margin: Fraction = Fraction(3, 20)
    nearest_tie_ms: int = 50


@dataclass(frozen=True, slots=True)
class WordAttribution:
    """BPE 구성 토큰마다 같은 낱말 판정을 반환한다. 수치는 낱말 단위로 중복 합산 금지.

    support_ms는 양의 직접 지지가 있는 라벨·길이, covered_ms는 전체 지지 합집합 길이,
    simultaneous_ms는 둘 이상 동시 지지 길이, nearest_ms는 선두 화자의 구간 거리다.
    """

    source_index: int
    tag: SpeakerTag
    support_ms: tuple[tuple[str, int], ...]
    covered_ms: int
    simultaneous_ms: int
    nearest_ms: int | None
    reason: Reason


def attribute_words(
    words: Iterable[TimedWord | SentenceWord], turns: Iterable[SpeakerTurn], *,
    policy: AttributionPolicy,
) -> tuple[WordAttribution, ...]:
    """직전 낱말을 참조하지 않고 전 turn의 안정 라벨과 각 낱말 자체의 근거만 채점한다."""
    available = _speaker_intervals(turns)
    assigned: list[WordAttribution] = []
    for word in lexical_words(words):
        result = _attribute(word.intervals, available, policy)
        assigned.extend(replace(result, source_index=index) for index in word.source_indices)
    return tuple(assigned)


def _speaker_intervals(turns: Iterable[SpeakerTurn]) -> tuple[tuple[str, Intervals], ...]:
    by_speaker: dict[int, list[tuple[int, int]]] = {}
    for turn in turns:
        if valid_interval(turn.start_ms, turn.end_ms):
            by_speaker.setdefault(turn.speaker, []).append((turn.start_ms, turn.end_ms))
    merged = {speaker: union(spans) for speaker, spans in by_speaker.items()}
    ordered = sorted(merged, key=lambda speaker: (merged[speaker][0][0], speaker))
    return tuple((f"화자{index}", merged[speaker])
                 for index, speaker in enumerate(ordered, 1))


def _attribute(
    intervals: Intervals, available: tuple[tuple[str, Intervals], ...],
    policy: AttributionPolicy,
) -> WordAttribution:
    empty = WordAttribution(0, SpeakerTag("UNKNOWN"), (), 0, 0, None, "timing")
    if not intervals:
        return empty
    if not available:
        return replace(empty, reason="no_turns")
    shared = tuple(intersect(intervals, spans) for _, spans in available)
    support = tuple((label, measure(spans))
                    for (label, _), spans in zip(available, shared, strict=True) if spans)
    covered, simultaneous = coverage(shared)
    if covered:
        result = replace(empty, support_ms=support, covered_ms=covered,
                         simultaneous_ms=simultaneous, nearest_ms=0)
        if simultaneous:
            return replace(result, tag=SpeakerTag("OVERLAP", tuple(label for label, _ in support)),
                           reason="overlap")
        ranked = sorted(support, key=lambda item: -item[1])
        leader, first = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0
        duration = measure(intervals)
        if Fraction(first, duration) < policy.min_coverage:
            return replace(result, reason="low_coverage")
        if Fraction(first - second, duration) < policy.min_margin or first == second:
            return replace(result, reason="boundary_tie")
        return replace(result, tag=SpeakerTag("SPEAKER", (leader,)), reason="direct")
    distances = sorted(
        (min(max(start - right, left - end, 0)
             for start, end in intervals for left, right in spans), label)
        for label, spans in available
    )
    nearest, leader = distances[0]
    result = replace(empty, nearest_ms=nearest, reason="no_support")
    if nearest > policy.tolerance_ms:
        return result
    if len(distances) > 1 and distances[1][0] - nearest <= policy.nearest_tie_ms:
        return replace(result, reason="boundary_tie")
    return replace(result, tag=SpeakerTag("SPEAKER", (leader,)), reason="tolerance")
