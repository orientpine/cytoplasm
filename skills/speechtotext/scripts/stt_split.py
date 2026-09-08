"""문장을 화자 경계와 15초 마크에서 자른다 — 화자 배정 **직전**의 순수 단계.

화자 배정은 문장 하나에 화자 하나를 준다. 그래서 문장이 화자보다 길면 분리 결과가
문서에 도달할 방법이 없다. 현장 근거(2026-09): 4명이 말한 57초 중국어 샘플에서
whisper 가 구두점을 하나도 내지 않아 전사가 158자짜리 문장 하나로 나왔고, 분리기가
화자 4명을 제대로 찾았는데도 전사본에는 화자1 만 남았다. 한국어 large-v3-turbo 에서도
735문장 중 1문장이 같은 이유로 구두점 없이 나왔다.

stt_polish·stt_blocks 의 문장 나누기는 **구두점**을 본다. 구두점이 없으면 그 규칙은
아무것도 못 한다. 여기서는 구두점 대신 **시각**을 본다 — 화자가 바뀐 순간과, 사람이 한
숨에 읽을 수 있는 길이(15초). 자를 근거(시각·띄어쓰기)가 없으면 아무것도 하지 않는다.
전사를 실패시키는 것보다 안 자른 문장 하나가 늘 낫기 때문에 절대 예외를 던지지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import Field, dataclass, replace
from typing import ClassVar, Final, Protocol, TypeVar

import stt_gap
from stt_sentence import SentenceWord, slice_words, word_bounds

#: 사람이 한 덩어리로 읽을 수 있는 상한. 넘어가면 구두점이 없는 한 끊는다.
DEFAULT_MAX_SPAN_MS: Final = 15_000
#: 이보다 짧게 걸치는 턴은 화자 교대가 아니라 분리기가 경계에서 떠는 것이다.
DEFAULT_MIN_SHARE_MS: Final = 1_000

# 문장을 끝맺는 부호(전각 포함). 이걸로 끝났다면 길이는 화자의 선택이지 누락이 아니다.
_TERMINAL: Final = ".!?\u2026\u3002\uff01\uff1f"
# 닫는 따옴표·괄호는 종결 부호 **뒤에** 남으므로 벗겨내고 마지막 글자를 본다.
_TRAILING: Final = "\"')]}\u201d\u2019\u300d\u300f\uff09"
_WORD: Final = re.compile(r"\S+")
#: 띄어쓰기가 없는 표기에서는 글자 경계가 유일한 자를 자리다.
_CHARACTER: Final = re.compile(r"\S")
_WHITESPACE: Final = re.compile(r"\s+")


class _Timed(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Field[object]]]

    @property
    def text(self) -> str: ...

    @property
    def start_ms(self) -> int | None: ...

    @property
    def end_ms(self) -> int | None: ...


class _Turn(Protocol):
    @property
    def start_ms(self) -> int: ...

    @property
    def end_ms(self) -> int: ...

    @property
    def speaker(self) -> int: ...


SentenceT = TypeVar("SentenceT", bound=_Timed)


@dataclass(frozen=True, slots=True)
class _Clock:
    """글자 위치를 시각으로 읽는 자.

    낱말 시각 눈금(`marks`)이 있으면 그것을 쓰고, 없으면 문장의 시작·끝 사이를 글자 수로
    선형 보간한다. 보간은 말의 빠르기가 문장 안에서 일정하다고 가정하지만, 어차피 우리는
    **가장 가까운 띄어쓰기**를 고를 뿐이라 몇 백 ms 의 오차는 결과를 바꾸지 않는다.
    """

    text_length: int
    start_ms: int
    end_ms: int
    marks: tuple[tuple[int, int], ...]

    def at(self, position: int) -> float:
        for where, when in reversed(self.marks):
            if where <= position:
                return float(when)
        if self.text_length <= 0:
            return float(self.start_ms)
        span = self.end_ms - self.start_ms
        return self.start_ms + span * position / self.text_length


def split_on_turns(
    sentences: Iterable[SentenceT],
    turns: Iterable[_Turn],
    *,
    max_span_ms: int = DEFAULT_MAX_SPAN_MS,
    min_share_ms: int = DEFAULT_MIN_SHARE_MS,
) -> tuple[SentenceT, ...]:
    """시간이 붙은 문장을 화자 경계와 15초 마크에서 자른 결과를 순서대로 돌려준다.

    한 문장을 **둘 이상의 턴이 각각 `min_share_ms` 이상** 나눠 가졌을 때만 화자 경계로
    자른다. 비율(20%) 대신 절대 시간을 쓰는 이유: 짧은 문장일수록 경계의 떨림이 비율로는
    커 보여 멀쩡한 문장이 잘게 부서진다. "1초 넘게 말했으면 그 사람 몫"이 읽기 쉽고
    분리기의 시간 해상도와도 맞는다. 단, 단어 시각 눈금이 있으면 짧은 응답도
    독립 근거이므로 이 최소 지분 게이트를 적용하지 않는다.
    """
    available = tuple(turns)
    pieces: list[SentenceT] = []
    for sentence in sentences:
        pieces.extend(_split(sentence, available, max_span_ms, min_share_ms))
    return tuple(pieces)


def _split(
    sentence: SentenceT,
    turns: tuple[_Turn, ...],
    max_span_ms: int,
    min_share_ms: int,
) -> tuple[SentenceT, ...]:
    start, end = sentence.start_ms, sentence.end_ms
    text = sentence.text
    # 전사 실패 표지는 발화가 아니라 문서가 자기 자신에 대해 하는 말이다. 창 하나만큼 긴
    # 구간을 가리키므로 화자 경계는 얼마든지 걸치지만, 잘리는 순간 소유자가 읽어야 할 그
    # 한 줄이 조각으로 흩어진다(2026-09-04: 11 조각, 14분에 걸쳐).
    if stt_gap.is_marker(text):
        return (sentence,)
    # 시각이 없으면(API 백엔드) 자를 근거가 없고, 띄어쓰기가 없으면 자를 자리가 없다.
    if start is None or end is None or end <= start:
        return (sentence,)
    words = tuple((match.group(), match.start(), match.end()) for match in _WORD.finditer(text))
    if len(words) < 2:
        # 중국어·일본어처럼 띄어쓰기가 없으면 낱말이 하나로 잡혀 자를 자리가 사라진다.
        # 그러면 화자가 넷이어도 문장이 하나라 전부 첫 화자에게 간다(실측: 57초 4인
        # 중국어 샘플이 158자 한 문장). 글자 경계는 그 표기에서 안전한 자를 자리다.
        words = tuple(
            (match.group(), match.start(), match.end()) for match in _CHARACTER.finditer(text)
        )
    if len(words) < 2:
        return (sentence,)
    clock = _Clock(len(text), start, end, _marks(sentence, text))
    wanted = _cut_times(text, start, end, turns, max_span_ms, 1 if clock.marks else min_share_ms)
    cuts = _word_cuts(words, wanted, clock)
    if not cuts:
        return (sentence,)
    return _pieces(sentence, words, cuts, clock)


def _cut_times(
    text: str,
    start: int,
    end: int,
    turns: tuple[_Turn, ...],
    max_span_ms: int,
    min_share_ms: int,
) -> tuple[int, ...]:
    """자르고 싶은 **시각**들. 화자 경계가 먼저, 그다음 남은 긴 구간을 15초로 채운다."""
    sharing = [turn for turn in turns if _overlap(start, end, turn) >= min_share_ms]
    edges: list[int] = []
    # **화자가 실제로 바뀌는 자리에서만** 자른다. 예전에는 걸치는 turn 의 모든 경계를 자를
    # 자리로 삼았는데, 같은 사람이 문장 도중에 숨을 쉬면 turn 이 둘로 갈리므로 화자가 바뀌지
    # 않았는데도 문장이 잘렸다. 2026-09-06 에 --min-duration-off 를 0.0 으로 내려 같은 화자의
    # 짧은 간격을 더 이상 이어 붙이지 않게 한 것이 그 상황을 흔하게 만들었다(턴 구조를 되찾은
    # 대가). 잘린 조각 중 침묵에 걸친 것은 근거가 없어 화자0 이 되고, 그래서 노드 실측에서
    # 낱말의 비-SPEAKER 12.0%·20.5% 가 블록의 화자0 47.6%·49.7% 로 두 녹음 모두 2.4배 증폭됐다.
    for turn in sharing:
        for edge in (turn.start_ms, turn.end_ms):
            if start < edge < end and edge not in edges and _changes_speaker(sharing, edge):
                edges.append(edge)
    edges.sort()
    if _terminated(text):
        return tuple(edges)
    # 구두점이 없으면 어떤 구간도 상한을 넘겨선 안 된다. 화자 경계로 이미 짧아진 구간은
    # 그대로 두고, 남은 긴 구간만 15초 간격으로 채운다 — 1초 차이로 두 번 자르지 않는다.
    filled: list[int] = []
    cursor = start
    for edge in (*edges, end):
        while edge - cursor > max_span_ms:
            cursor += max_span_ms
            filled.append(cursor)
        if edge != end:
            filled.append(edge)
        cursor = edge
    return tuple(filled)


def _changes_speaker(turns: tuple[_Turn, ...], when: int) -> bool:
    """그 순간을 사이에 두고 화자가 실제로 바뀌는가 — 침묵은 바뀜이 아니다."""
    before = _speaker_before(turns, when)
    after = _speaker_after(turns, when)
    return before is not None and after is not None and before != after


def _speaker_before(turns: tuple[_Turn, ...], when: int) -> int | None:
    """직전 순간의 화자. 덮는 turn 이 여럿이면 가장 짧은 것(안쪽 맞장구)이 이긴다."""
    inside = [turn for turn in turns if turn.start_ms < when <= turn.end_ms]
    if inside:
        return min(inside, key=lambda turn: turn.end_ms - turn.start_ms).speaker
    earlier = [turn for turn in turns if turn.end_ms <= when]
    return max(earlier, key=lambda turn: turn.end_ms).speaker if earlier else None


def _speaker_after(turns: tuple[_Turn, ...], when: int) -> int | None:
    """직후 순간의 화자. 덮는 turn 이 없으면 다음 turn 을 본다 — 침묵은 화자를 바꾸지 않는다."""
    inside = [turn for turn in turns if turn.start_ms <= when < turn.end_ms]
    if inside:
        return min(inside, key=lambda turn: turn.end_ms - turn.start_ms).speaker
    later = [turn for turn in turns if turn.start_ms >= when]
    return min(later, key=lambda turn: turn.start_ms).speaker if later else None


def _word_cuts(
    words: tuple[tuple[str, int, int], ...],
    wanted: tuple[int, ...],
    clock: _Clock,
) -> tuple[int, ...]:
    """자르고 싶은 시각마다 **가장 가까운 띄어쓰기**의 낱말 색인을 고른다.

    낱말 중간을 자르면 없는 말이 생긴다. 그래서 자를 자리는 언제나 낱말 경계이고,
    같은 자리가 두 번 골리면 한 번만 쓴다."""
    chosen: list[int] = []
    for when in wanted:
        best, gap = 0, -1.0
        for index in range(1, len(words)):
            distance = abs(clock.at(words[index][1]) - when)
            if gap < 0 or distance < gap:
                best, gap = index, distance
        if best and best not in chosen:
            chosen.append(best)
    return tuple(sorted(chosen))


def _pieces(
    sentence: SentenceT,
    words: tuple[tuple[str, int, int], ...],
    cuts: tuple[int, ...],
    clock: _Clock,
) -> tuple[SentenceT, ...]:
    """단어가 있으면 포함 단어의 시각과 재기준 좌표, 없으면 기존 보간을 사용한다."""
    made: list[SentenceT] = []
    edges = (0, *cuts, len(words))
    for begin, finish in zip(edges[:-1], edges[1:]):
        # 조각은 원문에서 잘라 낸다 — 낱말을 다시 이어 붙이면 원래 없던 띄어쓰기가
        # 생기고, 글자 단위로 자른 경우에는 글자마다 공백이 박힌다.
        left, right = words[begin][1], words[finish - 1][2]
        body = sentence.text[left:right].strip()
        carried: Sequence[object] = getattr(sentence, "words", ())
        located = tuple(ref for ref in carried if isinstance(ref, SentenceWord))
        if located:
            refs = slice_words(located, sentence.text, left, right)
            start_ms, end_ms = word_bounds(refs)
            made.append(replace(sentence, text=body, start_ms=start_ms, end_ms=end_ms, words=refs))
            continue
        first = begin == 0
        last = finish == len(words)
        starts = clock.start_ms if first else round(clock.at(words[begin][1]))
        ends = clock.end_ms if last else round(clock.at(words[finish - 1][2]))
        # 오리 타입 동결 데이터클래스(stt_blocks.TimedSentence 등)를 그대로 복제한다.
        made.append(replace(sentence, text=body, start_ms=starts, end_ms=max(ends, starts)))
    return tuple(made)


def _marks(sentence: _Timed, text: str) -> tuple[tuple[int, int], ...]:
    """문장이 낱말 시각을 들고 있으면 (글자 위치, 시각) 눈금으로 되돌린다.

    정규화된 문자 좌표가 있으면 직접 읽고 세그먼트 상속·절단·미상 시각은 제외한다.
    좌표 없는 옛 낱말은 순서대로 찾는다. 시각이 없어도 커서는 전진해야 반복 낱말을
    앞의 낱말에 잘못 붙이지 않는다. 문자열이 어긋나면 기존처럼 눈금 전체를 버린다."""
    carried = getattr(sentence, "words", None)
    if not isinstance(carried, Sequence) or isinstance(carried, str) or not carried:
        return ()
    found: list[tuple[int, int]] = []
    cursor = 0
    for word in carried:
        if isinstance(word, SentenceWord):
            if not word.clipped and word.word.timing_source != "segment" and word.word.start_ms >= 0:
                found.append((word.start_char, word.word.start_ms))
            continue
        piece = _WHITESPACE.sub(" ", str(getattr(word, "text", ""))).strip()
        if not piece:
            continue
        where = text.find(piece, cursor)
        if where < 0:
            return ()
        cursor = where + len(piece)
        when = getattr(word, "start_ms", None)
        if isinstance(when, int) and when >= 0:
            found.append((where, when))
    return tuple(found)


def _overlap(start: int, end: int, turn: _Turn) -> int:
    return min(end, turn.end_ms) - max(start, turn.start_ms)


def _terminated(text: str) -> bool:
    """종결 부호로 끝났는가 — 닫는 따옴표·괄호는 벗겨내고 본다."""
    stripped = text.rstrip().rstrip(_TRAILING)
    return bool(stripped) and stripped[-1] in _TERMINAL
