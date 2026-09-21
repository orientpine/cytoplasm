"""Local transcription through a self-contained whisper.cpp binary.

The sensitivity gate sees text too late to protect cloud-bound audio, so local is
preferred. ffmpeg normalizes input to whisper.cpp's 16 kHz mono PCM contract.

Since 2026-09-04 the recording is transcribed **one window at a time** (see
`stt_window`): a 2-hour file used to be a single process and a single JSON read, so
one undecodable byte at minute 118 threw away two hours of speech. Now a window that
fails is quarantined alone, its minutes are marked in the transcript, every other
window still reaches the document, and what succeeded is cached so a re-run resumes.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final

import stt_align
import stt_audio
import stt_blocks
import stt_client
import stt_coverage
import stt_diarize
import stt_eval_build
import stt_identify_run
import stt_local_attribution
import stt_media
import stt_speaker_count
import stt_window
import stt_window_run
import stt_window_store
from stt_local_config import DEFAULT_LANGUAGE as DEFAULT_LANGUAGE
from stt_local_config import DEFAULT_MAX_CONTEXT as DEFAULT_MAX_CONTEXT
from stt_local_config import DEFAULT_TIMEOUT as DEFAULT_TIMEOUT
from stt_local_config import MIN_CONFIGURED_WINDOW_MS as MIN_CONFIGURED_WINDOW_MS
from stt_local_config import LocalToolchain as LocalToolchain
from stt_local_config import resolve_toolchain as resolve_toolchain

_CONVERT_TIMEOUT: Final = 900.0

REPETITION_NOTICE: Final = (
    "전사 반복 붕괴: 같은 문장이 되풀이되며 전사본의 {ratio:.0%}를 차지합니다 «{phrase}…». "
    "모델이 그 구간의 실제 발화 대신 같은 말로 채운 것이라 회의록으로 넘기지 않습니다. "
    "문맥 이월은 이미 꺼져 있으니(-mc 0) 더 큰 모델로 다시 시도하거나, 내용을 확인한 뒤 "
    "SPEECHTOTEXT_ALLOW_INCOMPLETE=1 로 진행해 주세요."
)

TRUNCATED_NOTICE: Final = (
    "전사 누락 의심: 녹음 {minutes:.0f}분 가운데 전사 구간이 {ratio:.0%}뿐이고 "
    "미검출 구간이 {gaps}곳(마지막 {tail:.0f}분 포함)입니다. 잘린 전사본을 회의록으로 "
    "넘기지 않고 중단합니다 — 스레드·모델을 올려 다시 시도하거나, 확인 뒤 "
    "SPEECHTOTEXT_ALLOW_INCOMPLETE=1 로 진행해 주세요."
)

# A refusal must never leave the owner empty-handed: what was transcribed is written
# to a file first, and the refusal says where it is.
PARTIAL_NOTICE: Final = " 여기까지 전사된 부분 전사본은 {path} 에 남겨 두었습니다."
PARTIAL_UNSAVED: Final = " (부분 전사본을 저장하지 못했습니다.)"

ALL_QUARANTINED_NOTICE: Final = (
    "전사 전 구간 실패: {count}개 구간을 모두 전사하지 못했습니다. 격리된 원본은 "
    "{path} 에 있습니다. 표지만 남은 전사본을 회의록으로 넘기지 않고 중단합니다."
)

def _run(argv: list[str], stage: str, timeout: float) -> None:
    try:
        completed = subprocess.run(  # noqa: S603 - argv is built from resolved executables
            argv, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as failure:
        raise stt_client.SttError(f"{stage} 실패: {type(failure).__name__}") from None
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()[-200:]
        raise stt_client.SttError(f"{stage} 실패 rc={completed.returncode}: {detail}")


def asr_fingerprint(toolchain: LocalToolchain, *, prompt: str, env: Mapping[str, str]) -> str:
    material = {"language": toolchain.language, "prompt": prompt, "max_context": toolchain.max_context, "decode_flags": toolchain.decode_flags, "dtw": env.get("SPEECHTOTEXT_WHISPER_DTW", ""), "allow_incomplete": env.get("SPEECHTOTEXT_ALLOW_INCOMPLETE") == "1"}
    return hashlib.sha256(repr(sorted(material.items())).encode()).hexdigest()[:32]


def transcribe(
    audio: stt_audio.CheckedAudio,
    toolchain: LocalToolchain,
    *,
    prompt: str = "",
    diarizer: stt_diarize.DiarizeToolchain | None = None,
    num_speakers: int | None = None,
    count_speakers: Callable[[str], str] | None = None,
) -> stt_client.Transcription:
    """Transcribe ``audio`` on this machine; the bytes never leave it.

    ``prompt`` carries the meeting's own vocabulary — names, institutions, terms.
    Korean proper nouns are this model's weakest point, and without the hint the
    local backend had no way to be told them at all.
    """
    with tempfile.TemporaryDirectory(prefix="stt-local-") as workdir:
        base = Path(workdir)
        wav = base / "input16k.wav"
        _run(
            [
                str(toolchain.ffmpeg), "-nostdin", "-y", "-i", str(audio.path),
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav),
            ],
            "오디오 변환",
            _CONVERT_TIMEOUT,
        )
        windows = _plan(wav, toolchain)
        store = stt_window_store.resolve_store(
            os.environ,
            audio=audio.path,
            model=toolchain.model,
            windows=windows,
            tool=toolchain.binary,
            asr_fingerprint=asr_fingerprint(toolchain, prompt=prompt or toolchain.prompt, env=os.environ),
        )
        report = stt_window_run.run_windows(
            wav, windows, toolchain,
            workdir=base, prompt=prompt or toolchain.prompt, store=store,
        )
        segments = list(stt_window.merge(report.results))
        words = stt_blocks.words_from_whisper(segments)
        if os.environ.get("SPEECHTOTEXT_ALIGN_BACKEND", "none") != "none":
            words = stt_align.align_words(wav, words, env=os.environ)
        turns = None
        if diarizer is not None:
            try:
                turns = stt_diarize.diarize(wav, diarizer, num_speakers=num_speakers)
            except stt_diarize.DiarizeError as failure:
                print(f"DIARIZE-FAIL {failure}", file=sys.stderr)
        if turns and diarizer is not None and count_speakers is not None and num_speakers is None:
            turns = _recount(wav, words, turns, diarizer, count_speakers)
        attribution_mode: stt_client.AttributionMode | None = None
        if turns is None:
            sentences = stt_blocks.sentences_from_words(words)
        elif any(word.timing_source in {"token", "aligned"} for word in words):
            attribution_mode = "word"
            sentences = stt_local_attribution.sentences(words, turns)
        else:
            attribution_mode = "legacy"
            sentences = stt_diarize.assign(stt_blocks.sentences_from_words(words), turns)
        # 식별은 분리가 끝난 뒤, wav 가 아직 있는 이 자리에서만 할 수 있다. 어떤 실패도
        # 표식 한 줄로 끝나고 전사는 그대로 간다(fail-soft).
        identified = stt_identify_run.identify(wav, sentences, env=os.environ) if turns else ()
    text = stt_window.text_of(segments)
    if not text:
        raise stt_audio.TranscriptionRefused(stt_audio.EMPTY_TRANSCRIPT_NOTICE, exit_code=5)
    if len(report.quarantined) == len(windows):
        raise stt_audio.TranscriptionRefused(
            ALL_QUARANTINED_NOTICE.format(
                count=len(windows), path=store.root / "quarantine" / store.key
            ),
            exit_code=8,
        )
    _assert_not_collapsed(text, toolchain, store)
    coverage = _coverage(audio, segments, toolchain, store, text)
    # Only a run that lost nothing may forget its windows; anything else stays resumable.
    if not report.quarantined:
        store.clear()
    snapshot = stt_eval_build.snapshot_for(
        audio.path, toolchain, words=words, turns=turns or (), report=report,
        duration_ms=coverage.duration_ms if coverage is not None else windows[-1].end_ms,
        env=os.environ, prompt=prompt or toolchain.prompt,
        diarizer=diarizer, num_speakers=num_speakers,
    )
    return stt_eval_build.SnapshotTranscription(
        text=text,
        speakers=identified,
        model=f"local:{toolchain.model.stem}",
        endpoint="local",
        coverage=coverage,
        sentences=sentences,
        attribution_mode=attribution_mode,
        eval_record=snapshot,
    )


# 임계값은 화자 수를 정하지 못한다 — 노드 실측(2026-09-07)의 사다리는 0.8→18 · 1.0→6 ·
# 1.2→2 · 1.35→1 군집으로 절벽이라 어떤 고정값도 두 녹음을 동시에 맞히지 못했다. 그런데
# 화자 수를 **주면** 분리기는 그 수를 상한으로 다시 묶는다: 같은 4.5분 녹음에서 pyannote 는
# --num-speakers 3 으로 45.6/42.9/11.4% 세 사람을 냈고(자동 추정은 2명), 그 세 번째 목소리는
# 발화 시간 11.4% 라 잔여 군집이 아니다. 그러니 모자란 것은 분리기가 아니라 k 를 아는 일이고,
# 그 근거(자기소개·호칭·질문응답 짝)는 오디오가 아니라 전사본에 있다.
def _recount(
    wav: Path,
    words: Sequence[stt_blocks.TimedWord],
    turns: tuple[stt_diarize.Turn, ...],
    diarizer: stt_diarize.DiarizeToolchain,
    ask: Callable[[str], str],
) -> tuple[stt_diarize.Turn, ...]:
    """초안을 소유자가 볼 모양 그대로 보여 주고 화자 수를 물어 **한 번만** 재분리한다.

    질의 실패도 재분리 실패도 1차 결과를 그대로 남긴다 — 화자 수를 고치려다 전사본을
    잃는 일은 없다. 낱말은 어느 경로에서도 바뀌지 않고 바뀌는 것은 화자 라벨뿐이다.
    """
    observed = len({turn.speaker for turn in turns})
    draft = stt_blocks.render(stt_blocks.group(stt_local_attribution.sentences(words, turns)))
    try:
        answer = ask(stt_speaker_count.unlabelled(draft))
    except Exception as failure:  # noqa: BLE001 - 질의 실패가 전사를 멈추면 안 된다
        print(f"RECOUNT-FAIL {type(failure).__name__}", file=sys.stderr)
        return turns
    estimated = stt_speaker_count.parse_count(answer, limit=diarizer.max_speakers)
    wanted = stt_speaker_count.should_redo(estimated, observed)
    print(f"DIARIZE-RECOUNT observed={observed} asked={estimated} redo={wanted}", file=sys.stderr)
    if wanted is None:
        return turns
    try:
        return stt_diarize.diarize(wav, diarizer, num_speakers=wanted)
    except stt_diarize.DiarizeError as failure:
        print(f"RECOUNT-FAIL {failure}", file=sys.stderr)
        return turns


def _plan(wav: Path, toolchain: LocalToolchain) -> tuple[stt_window.Window, ...]:
    """Windows over the normalized wav — one unbounded pass when its length is unreadable.

    The wav is ours (ffmpeg just wrote it to whisper.cpp's contract), so its own header
    gives the duration without a second probe process. With no readable length there is
    no plan to make, and the run falls back to exactly the single pass it did before.
    """
    duration = stt_media.wav_duration_ms(wav)
    planned = stt_window.plan_windows(
        duration, window_ms=toolchain.window_ms, overlap_ms=toolchain.overlap_ms
    )
    return planned or (stt_window.Window(index=0, start_ms=0, length_ms=0),)


def _preserved(store: stt_window_store.WindowStore, text: str) -> str:
    """Write the partial transcript before refusing, and say where it landed."""
    kept = store.preserve(text)
    return PARTIAL_NOTICE.format(path=kept) if kept is not None else PARTIAL_UNSAVED


def _assert_not_collapsed(
    text: str, toolchain: LocalToolchain, store: stt_window_store.WindowStore
) -> None:
    """Refuse a transcript whose words collapsed into one repeated phrase."""
    ratio, phrase = stt_coverage.collapsed(text, limit=toolchain.repeat_limit)
    if not ratio:
        return
    if toolchain.allow_incomplete:
        print(f"REPETITION-ACCEPTED ratio={ratio:.2f}", file=sys.stderr)
        return
    raise stt_audio.TranscriptionRefused(
        REPETITION_NOTICE.format(ratio=ratio, phrase=phrase[:40]) + _preserved(store, text),
        exit_code=8,
    )


def _coverage(
    audio: stt_audio.CheckedAudio,
    segments: Sequence[stt_window.Segment],
    toolchain: LocalToolchain,
    store: stt_window_store.WindowStore,
    text: str,
) -> stt_coverage.Coverage | None:
    if toolchain.ffprobe is None:
        print("COVERAGE-UNKNOWN reason=no-ffprobe", file=sys.stderr)
        return None
    duration = stt_media.probe_duration_ms(audio.path, ffprobe=toolchain.ffprobe)
    if not duration:
        print("COVERAGE-UNKNOWN reason=unprobeable", file=sys.stderr)
        return None
    verdict = stt_coverage.assess(stt_window.spans(segments), duration)
    if not verdict.complete and not toolchain.allow_incomplete:
        raise stt_audio.TranscriptionRefused(
            TRUNCATED_NOTICE.format(
                minutes=duration / 60_000,
                ratio=verdict.ratio,
                gaps=len(verdict.gaps),
                tail=verdict.trailing_gap_ms / 60_000,
            )
            + _preserved(store, text),
            exit_code=8,
        )
    return verdict
