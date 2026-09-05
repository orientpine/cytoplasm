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

import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import stt_audio
import stt_blocks
import stt_client
import stt_coverage
import stt_diarize
import stt_media
import stt_window
import stt_window_run
import stt_window_store

DEFAULT_LANGUAGE: Final = "ko"
DEFAULT_TIMEOUT: Final = 14400.0
_CONVERT_TIMEOUT: Final = 900.0
_MAX_THREADS: Final = 16
# Context carry-over is what lets a decode feed itself its own output until a window
# is consumed by one repeated sentence. Measured on a 94-minute Korean recording:
# carry-over on collapsed 28% of the transcript (one phrase, 910 times); the same
# span with `-mc 0` came back at 1.2% repetition — a clean sample's level.
DEFAULT_MAX_CONTEXT: Final = "0"

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

#: A window smaller than this is a typo, not a plan — the default is used instead.
MIN_CONFIGURED_WINDOW_MS: Final = 30_000


@dataclass(frozen=True, slots=True)
class LocalToolchain:
    """Everything needed to transcribe without touching the network."""

    binary: Path
    model: Path
    ffmpeg: Path
    ffprobe: Path | None
    threads: int
    language: str
    timeout: float
    allow_incomplete: bool
    prompt: str
    repeat_limit: float
    max_context: str
    window_ms: int
    overlap_ms: int


def _threads(env: Mapping[str, str]) -> int:
    raw = env.get("SPEECHTOTEXT_WHISPER_THREADS", "")
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return min(os.cpu_count() or 4, _MAX_THREADS)


def resolve_toolchain(env: Mapping[str, str]) -> LocalToolchain | None:
    """Return the local toolchain, or ``None`` when any piece is missing (fail closed)."""
    binary = stt_media.resolve_tool(env.get("SPEECHTOTEXT_WHISPER_BIN", ""), "whisper-cli")
    ffmpeg = stt_media.resolve_ffmpeg(env)
    raw_model = env.get("SPEECHTOTEXT_WHISPER_MODEL", "")
    if binary is None or ffmpeg is None or not raw_model:
        return None
    model = Path(raw_model).expanduser()
    if not model.is_file():
        return None
    raw_timeout = env.get("SPEECHTOTEXT_LOCAL_TIMEOUT", "")
    ffprobe = stt_media.resolve_ffprobe(env, ffmpeg=ffmpeg)
    return LocalToolchain(
        binary=binary,
        model=model,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        allow_incomplete=env.get("SPEECHTOTEXT_ALLOW_INCOMPLETE") == "1",
        threads=_threads(env),
        language=env.get("SPEECHTOTEXT_LANGUAGE") or DEFAULT_LANGUAGE,
        prompt=env.get("SPEECHTOTEXT_PROMPT", ""),
        repeat_limit=_ratio(env.get("SPEECHTOTEXT_MAX_REPEAT", "")),
        max_context=env.get("SPEECHTOTEXT_WHISPER_CONTEXT") or DEFAULT_MAX_CONTEXT,
        window_ms=_whole(
            env, "SPEECHTOTEXT_WINDOW_MS", stt_window.DEFAULT_WINDOW_MS,
            floor=MIN_CONFIGURED_WINDOW_MS,
        ),
        overlap_ms=_whole(env, "SPEECHTOTEXT_WINDOW_OVERLAP_MS", stt_window.DEFAULT_OVERLAP_MS),
        timeout=float(raw_timeout) if raw_timeout.replace(".", "", 1).isdigit() else DEFAULT_TIMEOUT,
    )


def _whole(env: Mapping[str, str], name: str, fallback: int, *, floor: int = 0) -> int:
    raw = env.get(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) >= floor else fallback


def _ratio(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        return stt_coverage.DEFAULT_REPEAT_LIMIT
    return value if 0.0 < value <= 1.0 else stt_coverage.DEFAULT_REPEAT_LIMIT


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


def transcribe(
    audio: stt_audio.CheckedAudio,
    toolchain: LocalToolchain,
    *,
    prompt: str = "",
    diarizer: stt_diarize.DiarizeToolchain | None = None,
    num_speakers: int | None = None,
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
            os.environ, audio=audio.path, model=toolchain.model, windows=windows
        )
        report = stt_window_run.run_windows(
            wav, windows, toolchain,
            workdir=base, prompt=prompt or toolchain.prompt, store=store,
        )
        segments = list(stt_window.merge(report.results))
        sentences = stt_blocks.sentences_from_words(stt_blocks.words_from_whisper(segments))
        if diarizer is not None:
            try:
                turns = stt_diarize.diarize(wav, diarizer, num_speakers=num_speakers)
            except stt_diarize.DiarizeError as failure:
                print(f"DIARIZE-FAIL {failure}", file=sys.stderr)
            else:
                sentences = stt_diarize.assign(sentences, turns)
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
    return stt_client.Transcription(
        text=text,
        model=f"local:{toolchain.model.stem}",
        endpoint="local",
        coverage=coverage,
        sentences=sentences,
    )


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
    segments: object,
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
