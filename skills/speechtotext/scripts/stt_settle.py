"""화자 경계에서 잘린 문장 조각을, 근거가 허락하는 만큼 원래 문장으로 되돌린다.

단어 시각이 있으면 `stt_split` 은 문장에 닿은 모든 화자 경계에서 자른다(최소 지분 1ms).
그래서 분리기가 경계에서 한 번 떨기만 해도 문장 꼬리 한두 낱말이 떨어져 나가 근거 없는
`화자0` 블록이 됐다. 2026-09-22 실측: 13분 녹음의 71블록 중 32블록이 `화자0` 조각이었고
UNKNOWN 조각의 길이 중앙값은 4자, 줄 112개 중 36개가 5자 이하였다 — 같은 녹음을 Plaud 는
29문단으로 냈다.

이 모듈은 한 문장에서 나온 조각끼리만 다시 붙인다. 근거는 문장 밖으로 나가지 않으므로
금지된 '직전 화자 상속' 이 아니다. 판정은 세 가지다.

* **약한 조각**(확정 근거 없음 + 짧음)은 앞 조각에, 첫 조각이면 뒤 조각에 붙는다. 붙는 쪽이
  확정 화자면 그 라벨을 유지한다 — 근거 없는 낱말이 근거 있는 판정을 뒤집지 못한다.
* **확실한 짧은 끼어듦**(다른 화자, 직접 겹침 근거, 1초 미만)은 문장을 자르지 않고 문장
  안에 `[화자2: 네]` 로 남는다(stt_asides).
* 나머지(1초 넘는 다른 화자의 말, 길고 근거 없는 말)는 예전처럼 따로 둔다 — 보수적이다.

그 뒤에 구두점 없는 긴 문장의 15초 규칙을 따로 적용하므로 자를 자리는 사라지지 않는다.
모든 결과 텍스트는 원래 문장의 정확한 조각이다. 낱말은 바꾸지도 버리지도 않는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final, Protocol

import stt_split
from stt_asides import RESERVED, Aside, within
from stt_attribute import SpeakerTag, WordAttribution
from stt_blocks import SentenceWord, TimedSentence
from stt_sentence import slice_words, word_bounds

#: 근거 없는 조각을 붙여도 되는 길이. 측정된 꼬리는 대부분 한두 낱말(중앙값 4자)이었고,
#: 2초를 넘는 근거 없는 말은 다른 사람의 발화일 수 있어 따로 둔다.
WEAK_PIECE_MS: Final = 2_000
WEAK_PIECE_CHARS: Final = 12
#: 문장 안에 끼워 넣을 수 있는 다른 화자의 말. "1초 넘게 말했으면 그 사람 몫"(stt_split)과
#: 같은 자다 — 그보다 길면 끼어듦이 아니라 화자 교대다.
ASIDE_MS: Final = stt_split.DEFAULT_MIN_SHARE_MS
ASIDE_CHARS: Final = 8
_UNBOUNDED_MS: Final = 1 << 62

Attributions = Mapping[int, WordAttribution]


class _Turn(Protocol):
    @property
    def start_ms(self) -> int: ...

    @property
    def end_ms(self) -> int: ...

    @property
    def speaker(self) -> int: ...


@dataclass(frozen=True, slots=True)
class _Piece:
    """원래 문장의 [left, right) 조각. 끼어듦 좌표도 원래 문장 기준이다."""

    left: int
    right: int
    refs: tuple[SentenceWord, ...]
    tag: SpeakerTag
    asides: tuple[Aside, ...] = ()


def spoken_by(refs: Sequence[SentenceWord], attributions: Attributions) -> SpeakerTag:
    """조각 하나의 화자 — 그 조각 안의 낱말 판정만 센다. 동률이면 지어내지 않고 미상이다."""
    spoken: Counter[str] = Counter()
    overlaps = 0
    participants: list[str] = []
    for ref in refs:
        item = attributions.get(ref.source_index)
        if item is None:
            continue
        if item.tag.kind == "SPEAKER":
            spoken[item.tag.speakers[0]] += 1
        elif item.tag.kind == "OVERLAP":
            overlaps += 1
            participants.extend(name for name in item.tag.speakers if name not in participants)
    ranked = spoken.most_common()
    if ranked and ranked[0][1] > overlaps and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
        return SpeakerTag("SPEAKER", (ranked[0][0],))
    if overlaps and participants:
        return SpeakerTag("OVERLAP", tuple(participants))
    return SpeakerTag("UNKNOWN")


def settle(
    sentence: TimedSentence, turns: Sequence[_Turn], attributions: Attributions,
    *, max_span_ms: int = stt_split.DEFAULT_MAX_SPAN_MS,
) -> tuple[TimedSentence, ...]:
    """구두점 문장 하나를 화자 경계에서 자르고, 근거 없는 절단은 되돌린다."""
    pieces = _split(sentence, turns, attributions)
    if len(pieces) > 1:
        pieces = _absorb(sentence, pieces, attributions)
        host = spoken_by(sentence.words, attributions)
        if host.kind == "SPEAKER":
            pieces = _embed(sentence, pieces, host, attributions)
        pieces = _join_equal(sentence, pieces, attributions)
    made: list[TimedSentence] = []
    for piece in pieces:
        made.extend(_span_cut(_materialize(sentence, piece), max_span_ms))
    return tuple(made)


def _split(
    sentence: TimedSentence, turns: Sequence[_Turn], attributions: Attributions,
) -> tuple[_Piece, ...]:
    cut = stt_split.split_on_turns((sentence,), turns, max_span_ms=_UNBOUNDED_MS)
    made: list[_Piece] = []
    cursor = 0
    for part in cut:
        left = sentence.text.find(part.text, cursor)
        if left < 0:
            # 조각은 원문의 정확한 부분 문자열이다. 못 찾으면 자르지 않은 문장 하나가 낫다.
            return (_piece(sentence, 0, len(sentence.text), attributions),)
        cursor = left + len(part.text)
        made.append(_piece(sentence, left, cursor, attributions))
    return tuple(made)


def _piece(
    sentence: TimedSentence, left: int, right: int, attributions: Attributions,
    asides: tuple[Aside, ...] = (), tag: SpeakerTag | None = None,
) -> _Piece:
    refs = slice_words(sentence.words, sentence.text, left, right)
    return _Piece(left, right, refs, tag or spoken_by(refs, attributions), asides)


def _combine(
    sentence: TimedSentence, first: _Piece, last: _Piece, attributions: Attributions,
    tag: SpeakerTag | None = None, extra: tuple[Aside, ...] = (),
) -> _Piece:
    asides = tuple(sorted((*first.asides, *last.asides, *extra), key=lambda aside: aside.start_char))
    return _piece(sentence, first.left, last.right, attributions, asides, tag)


def _firm(piece: _Piece, attributions: Attributions) -> bool:
    """그 화자의 낱말 중 하나라도 turn 과 **직접** 겹쳤는가 — 허용 오차(tolerance)만으로는 아니다."""
    if piece.tag.kind != "SPEAKER":
        return False
    label = piece.tag.speakers
    return any(
        (item := attributions.get(ref.source_index)) is not None
        and item.reason == "direct" and item.tag.speakers == label
        for ref in piece.refs
    )


def _short(sentence: TimedSentence, piece: _Piece, limit_ms: int, limit_chars: int) -> bool:
    start, end = word_bounds(piece.refs)
    if start is not None and end is not None:
        return end - start < limit_ms
    return len("".join(sentence.text[piece.left:piece.right].split())) <= limit_chars


def _absorb(
    sentence: TimedSentence, pieces: tuple[_Piece, ...], attributions: Attributions,
) -> tuple[_Piece, ...]:
    merged = list(pieces)
    while len(merged) > 1:
        weak = next((index for index, piece in enumerate(merged)
                     if not _firm(piece, attributions)
                     and _short(sentence, piece, WEAK_PIECE_MS, WEAK_PIECE_CHARS)), None)
        if weak is None:
            break
        keeper = weak - 1 if weak > 0 else 1
        first, last = min(weak, keeper), max(weak, keeper)
        tag = merged[keeper].tag if _firm(merged[keeper], attributions) else None
        merged[first:last + 1] = [_combine(sentence, merged[first], merged[last], attributions, tag)]
    return tuple(merged)


def _embed(
    sentence: TimedSentence, pieces: tuple[_Piece, ...], host: SpeakerTag,
    attributions: Attributions,
) -> tuple[_Piece, ...]:
    merged = list(pieces)
    index = 0
    while index < len(merged):
        piece = merged[index]
        hosts = [near for near in (index - 1, index + 1)
                 if 0 <= near < len(merged) and merged[near].tag == host]
        if not hosts or not _aside(sentence, piece, host, attributions):
            index += 1
            continue
        first, last = min(*hosts, index), max(*hosts, index)
        extra = (Aside(piece.left, piece.right, piece.tag.speakers[0]),)
        combined = _combine(sentence, merged[first], merged[last], attributions, host, extra)
        inner = tuple(aside for middle in merged[first + 1:last] for aside in middle.asides)
        merged[first:last + 1] = [replace(combined, asides=tuple(sorted(
            {*combined.asides, *inner}, key=lambda aside: aside.start_char)))]
        index = first
    return tuple(merged)


def _aside(
    sentence: TimedSentence, piece: _Piece, host: SpeakerTag, attributions: Attributions,
) -> bool:
    return (piece.tag != host and not piece.asides and _firm(piece, attributions)
            and _short(sentence, piece, ASIDE_MS, ASIDE_CHARS)
            and not RESERVED & set(sentence.text[piece.left:piece.right]))


def _join_equal(
    sentence: TimedSentence, pieces: tuple[_Piece, ...], attributions: Attributions,
) -> tuple[_Piece, ...]:
    merged = [pieces[0]]
    for piece in pieces[1:]:
        if piece.tag == merged[-1].tag:
            merged[-1] = _combine(sentence, merged[-1], piece, attributions, piece.tag)
        else:
            merged.append(piece)
    return tuple(merged)


def _materialize(sentence: TimedSentence, piece: _Piece) -> TimedSentence:
    start_ms, end_ms = word_bounds(piece.refs)
    return replace(
        sentence, text=sentence.text[piece.left:piece.right], start_ms=start_ms, end_ms=end_ms,
        words=piece.refs, speaker=piece.tag.label, attribution=piece.tag,
        asides=within(piece.asides, piece.left, piece.right),
    )


def _span_cut(sentence: TimedSentence, max_span_ms: int) -> tuple[TimedSentence, ...]:
    """정리가 끝난 문장에 15초 규칙을 적용한다. 끼어듦은 걸친 조각마다 나눠 옮긴다."""
    parts = stt_split.split_on_turns((sentence,), (), max_span_ms=max_span_ms)
    if len(parts) == 1:
        return parts
    made: list[TimedSentence] = []
    cursor = 0
    for part in parts:
        at = sentence.text.find(part.text, cursor)
        cursor = at + len(part.text)
        made.append(replace(part, asides=within(sentence.asides, at, cursor)))
    return tuple(made)
