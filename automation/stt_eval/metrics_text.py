"""ko-cer-v1 기반 CER, 화자 순열 CER, 무음 삽입의 결정적 계산."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from itertools import permutations

from automation.stt_eval.model import EvalRecord, EvalRecordError, EvalWord, TimeRange
from automation.stt_eval.normalize import normalize_cer


class EvalLimitError(ValueError):
    """정확 순열 평가의 화자 수 한도를 넘었다."""


@dataclass(frozen=True, slots=True)
class EditCounts:
    """치환, 삭제, 삽입 횟수와 정규화된 참조 글자 수."""

    S: int
    D: int
    insertions: int
    ref_len: int

    def __getattr__(self, name: str) -> int:
        """린트가 금지하는 한 글자 저장 필드 대신 공개 I 읽기를 제공한다."""
        if name == "I":
            return self.insertions
        raise AttributeError(name)


def cer(reference: str, hypothesis: str) -> EditCounts:
    """단위 비용 DP를 exact > 치환 > 삭제 > 삽입 순으로 역추적한다."""
    ref, hyp = normalize_cer(reference), normalize_cer(hypothesis)
    rows, cols = len(ref), len(hyp)
    costs = [list(range(cols + 1))] + [[i] + [0] * cols for i in range(1, rows + 1)]
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            costs[i][j] = min(costs[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]),
                              costs[i - 1][j] + 1, costs[i][j - 1] + 1)
    i, j = rows, cols
    substitutions = deletions = insertions = 0
    while i or j:
        if i and j and ref[i - 1] == hyp[j - 1] and costs[i][j] == costs[i - 1][j - 1]:
            i, j = i - 1, j - 1
        elif i and j and costs[i][j] == costs[i - 1][j - 1] + 1:
            substitutions += 1
            i, j = i - 1, j - 1
        elif i and costs[i][j] == costs[i - 1][j] + 1:
            deletions += 1
            i -= 1
        else:
            insertions += 1
            j -= 1
    return EditCounts(substitutions, deletions, insertions, rows)


def cer_rate(counts: EditCounts) -> Fraction | None:
    """참조가 비면 판정 불가(None)이며 삽입 횟수는 counts에 남는다."""
    if counts.ref_len == 0:
        return None
    return Fraction(counts.S + counts.D + counts.I, counts.ref_len)


def _word_text(record: EvalRecord, word: EvalWord) -> str:
    return normalize_cer(record.text[word.char_start:word.char_end])


def speaker_streams(record: EvalRecord, *, reference: bool = False) -> dict[str, str]:
    """단일 화자별 정규화 스트림을 만든다. 기본은 가설이다.

    참조 OVERLAP은 speakers에 실제 화자 한 명을 명시해야 한다. 후보만 여럿이면
    실제 화자를 추측하거나 모든 스트림에 복제하지 않고 입력 오류로 거부한다.
    시각 없는 단어는 시각 있는 단어 뒤에서 문자 좌표순으로 정렬한다.
    """
    streams: dict[str, list[str]] = {}
    words = sorted(record.words, key=lambda word: (
        word.start_ms is None, word.start_ms if word.start_ms is not None else 0,
        word.char_start, word.char_end, word.id,
    ))
    for word in words:
        tag = word.tag
        if tag.state == "OVERLAP" and reference:
            if len(tag.speakers) != 1:
                raise EvalRecordError("words.tag.speakers: 참조 OVERLAP의 실제 화자 한 명이 필요합니다")
        elif tag.state != "SPEAKER" or len(tag.speakers) != 1:
            continue
        streams.setdefault(tag.speakers[0], []).append(_word_text(record, word))
    return {speaker: "".join(parts) for speaker, parts in streams.items()}


def cpcer(ref_streams: dict[str, str], hyp_streams: dict[str, str]) -> Fraction | None:
    """빈 스트림 패딩 후 모든 화자 순열의 최소 CER. 참조 길이 0이면 None."""
    size = max(len(ref_streams), len(hyp_streams))
    if size > 8:
        raise EvalLimitError(f"cpCER: K={size} exceeds limit=8")
    refs = [normalize_cer(text) for text in ref_streams.values()]
    hyps = [normalize_cer(text) for text in hyp_streams.values()]
    ref_len = sum(map(len, refs))
    if ref_len == 0:
        return None
    refs += [""] * (size - len(refs))
    hyps += [""] * (size - len(hyps))
    distances: list[list[int]] = []
    for ref in refs:
        row: list[int] = []
        for hyp in hyps:
            counts = cer(ref, hyp)
            row.append(counts.S + counts.D + counts.I)
        distances.append(row)
    errors = min(sum(distances[i][j] for i, j in enumerate(order))
                 for order in permutations(range(size)))
    return Fraction(errors, ref_len)


def cpcer_strict(ref: EvalRecord, hyp: EvalRecord) -> Fraction | None:
    """기본 cpCER 분자에 가설 UNKNOWN/OVERLAP 정규화 글자 수 U를 더한다."""
    refs = speaker_streams(ref, reference=True)
    base = cpcer(refs, speaker_streams(hyp))
    if base is None:
        return None
    uncertain = sum(len(_word_text(hyp, word)) for word in hyp.words
                    if word.tag.state in ("UNKNOWN", "OVERLAP"))
    return base + Fraction(uncertain, sum(len(normalize_cer(text)) for text in refs.values()))


def _union(intervals: tuple[TimeRange, ...]) -> list[TimeRange]:
    merged: list[TimeRange] = []
    for start, end in sorted(intervals):
        if start == end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def silence_insertion(hyp: EvalRecord, ref: EvalRecord) -> tuple[int, int]:
    """turn 합집합 밖 ∩ der_regions의 글자 수와 ms를 반환한다.

    반열린 비발화 구간에 단어의 양수 길이 구간 전체가 포함될 때만 센다.
    경계에 걸치거나 시각 없는 단어는 글자 시각을 보간하지 않으므로 제외한다.
    빈 der_regions는 평가 구간 없음이며 녹음 전체로 대체하지 않는다.
    """
    speech = _union(tuple((turn.start_ms, turn.end_ms) for turn in ref.turns))
    silence: list[TimeRange] = []
    for start, end in _union(ref.der_regions):
        cursor = start
        for speech_start, speech_end in speech:
            if speech_end <= cursor:
                continue
            if speech_start >= end:
                break
            if cursor < speech_start:
                silence.append((cursor, speech_start))
            cursor = max(cursor, speech_end)
        if cursor < end:
            silence.append((cursor, end))
    chars = sum(len(_word_text(hyp, word)) for word in hyp.words
                if word.start_ms is not None and word.end_ms is not None
                and word.start_ms < word.end_ms
                and any(start <= word.start_ms and word.end_ms <= end for start, end in silence))
    return chars, sum(end - start for start, end in silence)
