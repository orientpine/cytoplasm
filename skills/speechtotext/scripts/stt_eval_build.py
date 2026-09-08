"""로컬 전사에서 보존한 원 단어·turn·격리 창을 평가 자료로 변환한다."""
from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import stt_diarize
import stt_eval_record
import stt_gap
import stt_window_run
import stt_window_store
from stt_local_config import LocalToolchain
from stt_attribute import AttributionPolicy, SpeakerTurn, attribute_words
from stt_attribute_intervals import valid_interval
from stt_client import Transcription
from stt_eval_record import EvalSnapshot, SnapshotGap, SnapshotTag, SnapshotTurn, SnapshotWord
from stt_sentence import TimedWord, spoken_sentences


_POLICY = AttributionPolicy()


def snapshot_for(
    audio: Path, toolchain: LocalToolchain, *, words: Sequence[TimedWord],
    turns: Sequence[SpeakerTurn], report: stt_window_run.WindowReport, duration_ms: int,
    env: Mapping[str, str], prompt: str, diarizer: stt_diarize.DiarizeToolchain | None,
    num_speakers: int | None,
) -> EvalSnapshot | None:
    """실행 당시 근거만 보존하며 부가 산출 실패는 전사 결과를 바꾸지 않는다."""
    try:
        effective = dict(env)
        effective["SPEECHTOTEXT_PROMPT"] = prompt
        if diarizer is None:
            effective["SPEECHTOTEXT_DIARIZE_BACKEND"] = "none"
        else:
            settings = {
                "BACKEND": diarizer.backend, "BIN": diarizer.binary,
                "SEGMENTATION": diarizer.segmentation, "EMBEDDING": diarizer.embedding,
                "THRESHOLD": diarizer.threshold, "MIN_SPEECH": diarizer.min_duration_on,
                "MIN_SILENCE": diarizer.min_duration_off, "MODE": diarizer.mode,
                "MAX_SPEAKERS": diarizer.max_speakers, "THREADS": diarizer.threads,
                "TIMEOUT": diarizer.timeout,
                "SPEAKERS": num_speakers if num_speakers is not None else diarizer.speakers,
            }
            effective.update({"SPEECHTOTEXT_DIARIZE_" + key: "" if value is None else str(value)
                              for key, value in settings.items()})
        gaps = tuple(SnapshotGap(
            result.window.start_ms,
            min(result.window.end_ms, report.results[index + 1].window.start_ms)
            if index + 1 < len(report.results) else result.window.end_ms,
            result.folded_reason or "quarantined",
        ) for index, result in enumerate(report.results) if result.window.index in report.quarantined)
        # 길이를 읽지 못한 옛 도구는 관측된 마지막 시각까지만 알려져 있다.
        duration = duration_ms or max((word.end_ms for word in words), default=0)
        duration = max(duration, 0, *(turn.end_ms for turn in turns))
        return build_record(
            audio_sha256=stt_window_store.digest(audio), duration_ms=duration,
            config_sha256=stt_eval_record.config_fingerprint(effective, toolchain),
            words=words, turns=turns, gaps=gaps,
        )
    except (OSError, ValueError, TypeError) as error:
        print(f"STT-EVAL-SNAPSHOT-FAIL reason={type(error).__name__}", file=sys.stderr)
        return None


@dataclass(frozen=True, slots=True)
class SnapshotTranscription(Transcription):
    # 부가 평가 설정은 기존 전사 결과의 동등성·해시를 바꾸지 않는다.
    eval_record: EvalSnapshot | None = field(default=None, compare=False)


def build_record(
    *, audio_sha256: str, duration_ms: int, config_sha256: str,
    words: Sequence[TimedWord], turns: Sequence[SpeakerTurn] = (),
    gaps: tuple[SnapshotGap, ...] = (), policy: AttributionPolicy = _POLICY,
) -> EvalSnapshot:
    """접은 발화와 실패 표지는 text에서 빼고 원 turn의 구간은 합치거나 자르지 않는다."""
    trusted = tuple(word for word in words if not word.folded and not stt_gap.is_marker(word.text))
    assignments = {item.source_index: item.tag for item in attribute_words(trusted, turns, policy=policy)}
    sentences = spoken_sentences(trusted)
    text = " ".join(sentence.text for sentence in sentences)
    serialized: list[SnapshotWord] = []
    offset = 0
    for sentence in sentences:
        for ref in sentence.words:
            word = ref.word
            timed = valid_interval(word.start_ms, word.end_ms)
            tag = assignments[ref.source_index]
            serialized.append(SnapshotWord(
                f"w{len(serialized)}", offset + ref.start_char, offset + ref.end_char,
                word.start_ms if timed else None, word.end_ms if timed else None,
                SnapshotTag(tag.kind, tag.speakers), word.timing_source if timed else "missing",
            ))
        offset += len(sentence.text) + 1
    first: dict[int, int] = {}
    for turn in turns:
        if valid_interval(turn.start_ms, turn.end_ms):
            first[turn.speaker] = min(first.get(turn.speaker, turn.start_ms), turn.start_ms)
    labels = {speaker: f"화자{index}" for index, speaker in enumerate(
        sorted(first, key=lambda speaker: (first[speaker], speaker)), 1,
    )}
    return EvalSnapshot(
        recording_id=audio_sha256[:8], audio_sha256=audio_sha256, duration_ms=duration_ms,
        text=text, config_sha256=config_sha256, words=tuple(serialized),
        turns=tuple(SnapshotTurn(turn.start_ms, turn.end_ms, labels[turn.speaker]) for turn in turns),
        gaps=gaps, status="failed" if not text else "partial" if gaps else "ok",
    )
