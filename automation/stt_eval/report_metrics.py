"""기존 지표의 분자·분모를 보존하는 보고용 어댑터."""
from __future__ import annotations

from collections.abc import Iterable
from fractions import Fraction
from typing import TypeAlias

from .entities import entity_metrics
from .metrics_der import der
from .metrics_text import cer, cpcer, cpcer_strict, silence_insertion, speaker_streams
from .metrics_timing import t200_counts
from .model import EvalRecord, EvalRecordError
from .normalize import normalize_cer

Count: TypeAlias = tuple[Fraction | int, int]
METRICS = ("cer", "cpcer", "cpcer_strict", "der", "t200", "entity_f1", "entity_miss_rate", "silence_insertion")


def ratio(count: Count) -> float | None:
    numerator, denominator = count
    return float(numerator / denominator) if denominator else None


def micro(counts: Iterable[Count]) -> float | None:
    numerator: Fraction | int = 0
    denominator = 0
    for errors, length in counts:
        numerator += errors
        denominator += length
    return ratio((numerator, denominator))


def measure(ref: EvalRecord, hyp: EvalRecord, metric: str) -> Count:
    """축약 Fraction의 분모가 아니라 원래 글자 수·화자 시간을 반환한다."""
    if metric == "t200":
        return t200_counts(ref, hyp)
    if metric == "cer":
        counts = cer(ref.text, hyp.text)
        return counts.S + counts.D + counts.I, counts.ref_len
    if metric in ("cpcer", "cpcer_strict"):
        refs = speaker_streams(ref, reference=True)
        length = sum(len(normalize_cer(text)) for text in refs.values())
        rate = cpcer_strict(ref, hyp) if metric == "cpcer_strict" else cpcer(refs, speaker_streams(hyp))
        # 빈 참조에도 가설 삽입은 마이크로 분자에 남긴다.
        if rate is None:
            chars = sum(len(normalize_cer(text)) for text in speaker_streams(hyp).values())
            if metric == "cpcer_strict":
                chars += sum(len(normalize_cer(hyp.text[word.char_start:word.char_end])) for word in hyp.words
                             if word.tag.state in ("UNKNOWN", "OVERLAP"))
            return chars, length
        return rate * length, length
    if metric == "der":
        counts_der = der(ref.turns, hyp.turns, regions=ref.der_regions)
        return counts_der.missed_ms + counts_der.false_alarm_ms + counts_der.confusion_ms, counts_der.ref_speech_ms
    if metric == "silence_insertion":
        chars, duration = silence_insertion(hyp, ref)
        return chars * 1000, duration
    if metric in ("entity_f1", "entity_miss_rate"):
        entities = entity_metrics(ref, hyp)
        tp = sum(row["tp"] for row in entities.values())
        fp = sum(row["fp"] for row in entities.values())
        fn = sum(row["fn"] for row in entities.values())
        return (2 * tp, 2 * tp + fp + fn) if metric == "entity_f1" else (fn, tp + fn)
    raise EvalRecordError("metric: 알 수 없는 지표입니다")
