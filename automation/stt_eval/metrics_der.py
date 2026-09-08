"""정수 밀리초 구간 스윕으로 화자 분리 오류를 센다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import permutations
from typing import Literal, TypeAlias

from .model import EvalRecordError, EvalTurn, TimeRange

# 채널은 참조, 가설, 채점 영역, collar 제외 영역 순서다.
_Channel: TypeAlias = Literal[0, 1, 2, 3]
_Event: TypeAlias = tuple[_Channel, int, int]
_Events: TypeAlias = dict[int, list[_Event]]


class EvalLimitError(ValueError):
    """정확 순열 평가의 화자 수 상한을 넘었다."""


@dataclass(frozen=True, slots=True)
class DerCounts:
    missed_ms: int
    false_alarm_ms: int
    confusion_ms: int
    ref_speech_ms: int


def der(
    ref_turns: Sequence[EvalTurn],
    hyp_turns: Sequence[EvalTurn],
    *,
    regions: Sequence[TimeRange],
    collar_ms: int = 0,
) -> DerCounts:
    """반열린 regions 합집합에서 collar를 빼고 전역 매핑으로 센다.

    참조 겹침은 화자별로 세되 같은 화자의 중복 turn은 한 번만 센다.
    화자 수 K는 채점 영역 밖을 포함한 양쪽 전체 라벨 수의 최댓값이다.
    """
    _nonnegative_ms(collar_ms, "collar_ms")
    ref_labels = _labels(ref_turns, "ref_turns")
    hyp_labels = _labels(hyp_turns, "hyp_turns")
    for index, (start, end) in enumerate(regions):
        _validate_interval(start, end, f"regions[{index}]")
    size = max(len(ref_labels), len(hyp_labels))
    if size > 8:
        raise EvalLimitError(f"DER: K={size}, 최대 8화자까지 정확 평가합니다")

    events: _Events = {}
    _turn_events(events, ref_turns, ref_labels, 0)
    _turn_events(events, hyp_turns, hyp_labels, 1)
    for start, end in regions:
        _add_interval(events, start, end, 2, 0)
    if collar_ms:
        for turn in ref_turns:
            for boundary in (turn.start_ms, turn.end_ms):
                _add_interval(events, boundary - collar_ms, boundary + collar_ms, 3, 0)
    return _sweep(events, size)


def der_rate(counts: DerCounts) -> Fraction | None:
    """참조 화자 시간이 없으면 비율은 미정으로 남긴다."""
    if counts.ref_speech_ms == 0:
        return None
    return Fraction(
        counts.missed_ms + counts.false_alarm_ms + counts.confusion_ms,
        counts.ref_speech_ms,
    )


def _nonnegative_ms(value: object, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EvalRecordError(f"{field}: 음수가 아닌 정수 ms여야 합니다")


def _validate_interval(start: int, end: int, field: str) -> None:
    _nonnegative_ms(start, f"{field}.start_ms")
    _nonnegative_ms(end, f"{field}.end_ms")
    if end < start:
        raise EvalRecordError(f"{field}.end_ms: start_ms보다 작을 수 없습니다")


def _labels(turns: Sequence[EvalTurn], field: str) -> dict[str, int]:
    for index, turn in enumerate(turns):
        _validate_interval(turn.start_ms, turn.end_ms, f"{field}[{index}]")
    return {label: index for index, label in enumerate(sorted({turn.speaker for turn in turns}))}


def _add_interval(
    events: _Events, start: int, end: int, channel: _Channel, index: int,
) -> None:
    events.setdefault(start, []).append((channel, index, 1))
    events.setdefault(end, []).append((channel, index, -1))


def _turn_events(
    events: _Events, turns: Sequence[EvalTurn], labels: dict[str, int], channel: _Channel,
) -> None:
    for turn in turns:
        _add_interval(events, turn.start_ms, turn.end_ms, channel, labels[turn.speaker])


def _sweep(events: _Events, size: int) -> DerCounts:
    active = [[0] * size, [0] * size, [0], [0]]
    weights = [[0] * size for _ in range(size)]
    missed = false_alarm = paired = ref_speech = 0
    boundaries = sorted(events)
    for start, end in zip(boundaries, boundaries[1:]):
        # 같은 경계의 시작과 끝을 모두 적용한 뒤 다음 구간을 평가한다.
        for channel, index, delta in events[start]:
            active[channel][index] += delta
        if active[2][0] == 0 or active[3][0] > 0:
            continue
        reference = [index for index, count in enumerate(active[0]) if count > 0]
        hypothesis = [index for index, count in enumerate(active[1]) if count > 0]
        duration = end - start
        ref_count, hyp_count = len(reference), len(hypothesis)
        missed += duration * max(0, ref_count - hyp_count)
        false_alarm += duration * max(0, hyp_count - ref_count)
        paired += duration * min(ref_count, hyp_count)
        ref_speech += duration * ref_count
        for ref_index in reference:
            for hyp_index in hypothesis:
                weights[ref_index][hyp_index] += duration

    # 빈 행/열이 부족한 화자를 패딩하므로 일대일 매핑을 항상 유지한다.
    correct = max(
        sum(weights[index][target] for index, target in enumerate(mapping))
        for mapping in permutations(range(size))
    )
    return DerCounts(missed, false_alarm, paired - correct, ref_speech)
