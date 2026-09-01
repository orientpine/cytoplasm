"""Local transcription through a self-contained whisper.cpp binary.

Why this exists and why it is the default when available: the meeting skill's
sensitivity gate only ever sees **text**, so a cloud transcription would have
already shipped the raw audio of a patent-sensitive meeting to a shared
provider before any gate could look at it. Running the model on the node closes
that window — and it costs nothing per minute.

Integration follows the ``pdftotext`` precedent already in this repo: a plain
binary invoked through ``subprocess``, so the stdlib-only policy holds (no
torch, no transformers, no Python ASR dependency). whisper.cpp only accepts
16 kHz mono signed-16-bit PCM, so ffmpeg normalizes the input first.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import stt_audio
import stt_client
import stt_coverage
import stt_media

DEFAULT_LANGUAGE: Final = "ko"
DEFAULT_TIMEOUT: Final = 14400.0
_CONVERT_TIMEOUT: Final = 900.0
_MAX_THREADS: Final = 16
_WHITESPACE: Final = re.compile(r"\s+")

_MIN_REPEAT_WORDS: Final = 120
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
        timeout=float(raw_timeout) if raw_timeout.replace(".", "", 1).isdigit() else DEFAULT_TIMEOUT,
    )


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
    audio: stt_audio.CheckedAudio, toolchain: LocalToolchain, *, prompt: str = ""
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
        out = base / "transcript"
        _run(
            [
                str(toolchain.binary), "-m", str(toolchain.model), "-f", str(wav),
                "-l", toolchain.language, "-t", str(toolchain.threads),
                # -ojf keeps per-segment timings (the completeness evidence).
                # Deliberately absent: -nf (disables the temperature fallback that
                # rescues a failed window), --vad (trims quiet Korean speech) and
                # -mc (truncates carried context) — each one can drop real speech.
                "-ojf", "-of", str(out), "-np",
                *(("--prompt", hint) if (hint := (prompt or toolchain.prompt)) else ()),
                *(("-mc", toolchain.max_context) if toolchain.max_context else ()),
            ],
            "로컬 전사",
            toolchain.timeout,
        )
        payload = out.with_suffix(".json")
        try:
            data = json.loads(payload.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as failure:
            raise stt_client.SttError("로컬 전사 결과를 읽지 못했습니다.") from failure
    segments = data.get("transcription", []) if isinstance(data, dict) else []
    joined = " ".join(
        str(segment.get("text", "")).strip()
        for segment in segments
        if isinstance(segment, dict)
    )
    text = _WHITESPACE.sub(" ", joined).strip()
    if not text:
        raise stt_audio.TranscriptionRefused(stt_audio.EMPTY_TRANSCRIPT_NOTICE, exit_code=5)
    _assert_not_collapsed(text, toolchain)
    coverage = _coverage(audio, segments, toolchain)
    return stt_client.Transcription(
        text=text,
        model=f"local:{toolchain.model.stem}",
        endpoint="local",
        coverage=coverage,
    )


def _assert_not_collapsed(text: str, toolchain: LocalToolchain) -> None:
    """Refuse a transcript whose words collapsed into one repeated phrase."""
    if len(text.split()) < _MIN_REPEAT_WORDS:
        return
    ratio, phrase = stt_coverage.dominant_repeat(text)
    if ratio <= toolchain.repeat_limit:
        return
    if toolchain.allow_incomplete:
        print(f"REPETITION-ACCEPTED ratio={ratio:.2f}", file=sys.stderr)
        return
    raise stt_audio.TranscriptionRefused(
        REPETITION_NOTICE.format(ratio=ratio, phrase=phrase[:40]), exit_code=8
    )


def _spans(segments: object) -> tuple[tuple[int, int], ...]:
    """Segment timings from whisper.cpp full JSON, in milliseconds."""
    found: list[tuple[int, int]] = []
    for segment in segments if isinstance(segments, list) else []:
        offsets = segment.get("offsets") if isinstance(segment, dict) else None
        if not isinstance(offsets, dict):
            continue
        start, end = offsets.get("from"), offsets.get("to")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            found.append((int(start), int(end)))
    return tuple(found)


def _coverage(
    audio: stt_audio.CheckedAudio, segments: object, toolchain: LocalToolchain
) -> stt_coverage.Coverage | None:
    """Refuse a transcript that demonstrably stopped short of the recording."""
    if toolchain.ffprobe is None:
        print("COVERAGE-UNKNOWN reason=no-ffprobe", file=sys.stderr)
        return None
    duration = stt_media.probe_duration_ms(audio.path, ffprobe=toolchain.ffprobe)
    if not duration:
        print("COVERAGE-UNKNOWN reason=unprobeable", file=sys.stderr)
        return None
    verdict = stt_coverage.assess(_spans(segments), duration)
    if not verdict.complete and not toolchain.allow_incomplete:
        raise stt_audio.TranscriptionRefused(
            TRUNCATED_NOTICE.format(
                minutes=duration / 60_000,
                ratio=verdict.ratio,
                gaps=len(verdict.gaps),
                tail=verdict.trailing_gap_ms / 60_000,
            ),
            exit_code=8,
        )
    return verdict
