#!/usr/bin/env python3
"""Audio to a transcript, optionally chained into the governed meeting CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import stt_audio
import stt_blocks
import stt_chunked
import stt_client
import stt_diarize
import stt_local
import stt_media
import stt_correction_log
import stt_polish
import stt_runtime
import stt_speaker_flow
import stt_speakers
import stt_transcript
import speechtotext_governed

KST: Final = ZoneInfo("Asia/Seoul")
MEETING_CHAIN_EXIT: Final = 7
_DATE_PREFIX: Final = re.compile(r"^\d{4}-\d{2}-\d{2}_")
TRANSCRIPT_MISSING_NOTICE: Final = "전사본 파일을 찾을 수 없습니다. 경로를 확인해 주세요."
BACKEND_UNAVAILABLE_EXIT: Final = 4
LOCAL_UNAVAILABLE_NOTICE: Final = (
    "로컬 전사 도구를 찾지 못했습니다: whisper.cpp 바이너리와 ggml 모델을 설치하고 "
    "SPEECHTOTEXT_WHISPER_BIN/SPEECHTOTEXT_WHISPER_MODEL 을 설정하거나 "
    "SPEECHTOTEXT_BACKEND=api 로 전환해 주세요. 음성을 외부로 보내지 않고 중단합니다."
)
def _backend() -> str:
    return (os.environ.get("SPEECHTOTEXT_BACKEND") or "auto").strip().lower()


def _transcribe(
    args: argparse.Namespace, pairs: tuple[tuple[str, str], ...]
) -> stt_client.Transcription:
    backend = _backend()
    toolchain = (
        stt_local.resolve_toolchain(os.environ) if backend in {"auto", "local"} else None
    )
    ffmpeg = stt_media.resolve_ffmpeg(os.environ)
    ffprobe = stt_media.resolve_ffprobe(os.environ, ffmpeg=ffmpeg)
    can_window = ffmpeg is not None and ffprobe is not None
    unbounded = toolchain is not None or backend == "local" or can_window
    limit = stt_audio.limit_for("local" if unbounded else "api")
    checked = stt_audio.check_audio(Path(args.file).expanduser(), max_bytes=limit)
    if args.recorded:  # 테스트/오프라인 전용 — 전사 API 를 호출하지 않는다
        text = Path(args.recorded).read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            raise stt_audio.TranscriptionRefused(stt_audio.EMPTY_TRANSCRIPT_NOTICE, exit_code=5)
        return stt_client.Transcription(text=text, model="recorded", endpoint="recorded")

    if toolchain is not None:
        diarizer = None if args.no_diarize else stt_diarize.resolve_toolchain(os.environ)
        return stt_local.transcribe(
            checked,
            toolchain,
            prompt=_prompt(args, pairs),
            diarizer=diarizer,
            num_speakers=args.speaker_count,
        )
    if backend == "local":
        raise stt_audio.TranscriptionRefused(
            LOCAL_UNAVAILABLE_NOTICE, exit_code=BACKEND_UNAVAILABLE_EXIT
        )
    model = stt_runtime.setting("SPEECHTOTEXT_MODEL", stt_client.DEFAULT_MODEL)
    if checked.size_bytes > stt_audio.MAX_API_AUDIO_BYTES:
        if not can_window or ffmpeg is None or ffprobe is None:
            raise stt_audio.TranscriptionRefused(stt_audio.SIZE_EXCEEDED_NOTICE, exit_code=3)
        duration = stt_media.probe_duration_ms(checked.path, ffprobe=ffprobe)
        if not duration:
            raise stt_audio.TranscriptionRefused(stt_chunked.NO_DURATION_NOTICE, exit_code=8)
        return stt_chunked.transcribe_long(
            checked,
            duration_ms=duration,
            ffmpeg=ffmpeg,
            transcribe_window=lambda chunk, _index: _api_call(chunk, args, model, pairs).text,
            model=model,
        )
    return stt_client.transcribe(
        checked,
        api_key=stt_runtime.setting("OPENAI_API_KEY"),
        base_url=stt_runtime.setting("SPEECHTOTEXT_BASE_URL", stt_client.DEFAULT_BASE_URL),
        model=stt_runtime.setting("SPEECHTOTEXT_MODEL", stt_client.DEFAULT_MODEL),
        language=stt_runtime.setting("SPEECHTOTEXT_LANGUAGE", stt_client.DEFAULT_LANGUAGE),
        prompt=_prompt(args, pairs),
    )


def _api_call(
    path: Path, args: argparse.Namespace, model: str, pairs: tuple[tuple[str, str], ...]
) -> stt_client.Transcription:
    return stt_client.transcribe(
        stt_audio.check_audio(path, max_bytes=stt_audio.MAX_API_AUDIO_BYTES),
        api_key=stt_runtime.setting("OPENAI_API_KEY"),
        base_url=stt_runtime.setting("SPEECHTOTEXT_BASE_URL", stt_client.DEFAULT_BASE_URL),
        model=model,
        language=stt_runtime.setting("SPEECHTOTEXT_LANGUAGE", stt_client.DEFAULT_LANGUAGE),
        prompt=_prompt(args, pairs),
    )


def _prompt(args: argparse.Namespace, pairs: tuple[tuple[str, str], ...]) -> str:
    return stt_runtime.prompt_for(getattr(args, "prompt", ""), pairs)


def _project(args: argparse.Namespace, label: str) -> str:
    return (getattr(args, "project", "") or "").strip() or stt_runtime.project_of(label)


def cmd_run(args: argparse.Namespace, *, chain: bool) -> int:
    now = datetime.now(KST)
    source = Path(args.file).expanduser()
    label = args.label or source.stem
    project = _project(args, label)
    pairs = stt_runtime.merged_glossary(project)
    try:
        transcription = _transcribe(args, pairs)
    except stt_audio.TranscriptionRefused as refusal:
        print(refusal.notice)
        return refusal.exit_code
    except stt_client.SttError as failure:
        print(str(failure))
        return failure.exit_code

    tidied, speakers = stt_speaker_flow.tidy(transcription, pairs)
    stt_correction_log.record(
        tidied.corrections, label=label, project=project, stage="transcribe"
    )
    transcript = stt_transcript.write_transcript(
        stt_runtime.transcript_dir(), label=label, source_name=source.name,
        transcription=transcription, now=now, polish=tidied,
        extra_lines=stt_speaker_flow.legend_lines(speakers),
    )
    summary = stt_speaker_flow.run_summary(
        transcription, tidied, speakers, label=label, project=project,
        source=source.name, transcript=transcript,
        drive_link=stt_runtime.publish_transcript(transcript, label, now, project),
    )
    if not chain:
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    meeting_exit, llm = stt_speaker_flow.run_meeting(
        transcript,
        stt_speaker_flow.meeting_argv(
            args.label or transcript.stem, project, offline=args.offline,
            with_evidence=args.with_evidence, notify_channel=args.notify_channel,
            notify_message_id=args.notify_message_id, recorded=args.meeting_recorded,
        ),
    )
    speakers = stt_speaker_flow.absorb(transcript, llm, label, project, now)
    summary["meeting_exit"] = meeting_exit
    summary["speakers"] = stt_speaker_flow.speaker_summary(speakers)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if meeting_exit == 0 else MEETING_CHAIN_EXIT


def cmd_polish(args: argparse.Namespace) -> int:
    now = datetime.now(KST)
    path = Path(args.file).expanduser()
    try:
        document = path.read_text(encoding="utf-8")
    except OSError:
        print(TRANSCRIPT_MISSING_NOTICE)
        return 5
    label = args.label or _label_of(path)
    project = _project(args, label)
    header, body = stt_polish.split_document(document)
    existing = stt_speakers.parse_legend(header)
    override = stt_speakers.parse_override(args.speakers)
    speakers = stt_speakers.merge(override, existing)
    tidied = stt_polish.polish_sentences(
        stt_blocks.parse(body),
        glossary=stt_runtime.merged_glossary(project),
        names=stt_speakers.names(speakers),
    )
    if not tidied.body.strip():
        print(stt_audio.EMPTY_TRANSCRIPT_NOTICE)
        return 5
    stt_correction_log.record(tidied.corrections, label=label, project=project, stage="polish")
    path.write_text(
        stt_transcript.rewrite(
            header,
            tidied,
            label=label,
            extra_lines=stt_speaker_flow.legend_lines(speakers),
            managed_prefixes=(stt_transcript.TIDY_PREFIX, stt_speakers.SPEAKERS_PREFIX),
        ),
        encoding="utf-8",
    )
    path.chmod(stt_transcript.FILE_MODE)
    print(json.dumps({
        "label": label,
        "project": project,
        "transcript_path": str(path),
        "chars": len(tidied.body),
        "polish": stt_runtime.polish_summary(tidied),
        "drive_link": stt_runtime.publish_transcript(path, label, now, project),
        "speakers": stt_speaker_flow.speaker_summary(speakers),
    }, ensure_ascii=False))
    return 0


def _label_of(path: Path) -> str:
    return _DATE_PREFIX.sub("", path.stem, count=1) or path.stem


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", required=True, help="음성 파일 경로")
    parser.add_argument("--label", help="회의 라벨(기본: 파일명)")
    parser.add_argument("--project", help="과제명(기본: 파일명의 날짜가 아닌 첫 토큰)")
    parser.add_argument("--prompt", default="", help="고유명사 힌트")
    parser.add_argument("--recorded", help="녹취 텍스트 파일(테스트 전용 — 전사 API 미호출)")
    parser.add_argument("--speaker-count", type=int, help="예상 화자 수")
    parser.add_argument("--no-diarize", action="store_true", help="화자 분리 생략")


def main(argv: list[str] | None = None) -> int:
    stt_runtime.load_secrets_into_environment()
    parser = argparse.ArgumentParser(prog="speechtotext")
    subparsers = parser.add_subparsers(dest="command", required=True)
    transcribe = subparsers.add_parser("transcribe", help="음성 → 전사본(.md) 까지만")
    _add_common(transcribe)

    ingest = subparsers.add_parser("ingest", help="음성 → 전사본(.md) → 회의록")
    _add_common(ingest)
    ingest.add_argument("--offline", action="store_true", help="외부 부작용 없이 계획만 기록")
    ingest.add_argument("--with-evidence", action="store_true", help="선행 회의·노트 근거 수집")
    ingest.add_argument("--notify-channel", help="결과 통지 Discord 채널 ID")
    ingest.add_argument("--notify-message-id", help="지시 메시지 ID(스레드 앵커)")
    ingest.add_argument("--meeting-recorded", help="녹화된 회의록 LLM 응답(테스트 전용)")

    tidy = subparsers.add_parser("polish", help="이미 있는 전사본(.md)을 다시 다듬기")
    tidy.add_argument("--file", required=True, help="전사본 .md 경로")
    tidy.add_argument("--label", help="회의 라벨(기본: 파일명에서 날짜 프리픽스를 뺀 값)")
    tidy.add_argument("--project", help="과제명(기본: 라벨의 날짜가 아닌 첫 토큰)")
    tidy.add_argument("--speakers", default="", help="소유자 화자 이름(화자1=김민수,...)")

    args = parser.parse_args(argv)
    message = speechtotext_governed.refusal(Path(__file__))
    if message:
        print(message, file=sys.stderr)
        return 3
    if args.command == "polish":
        return cmd_polish(args)
    return cmd_run(args, chain=args.command == "ingest")


if __name__ == "__main__":
    raise SystemExit(main())
