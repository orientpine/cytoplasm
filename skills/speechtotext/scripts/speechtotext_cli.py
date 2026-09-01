#!/usr/bin/env python3
"""speechtotext — 음성 파일을 전사(.md)하고 기존 meeting 스킬로 회의록까지 잇는 CLI.

두 동사가 있다. ``transcribe`` 는 전사본까지만 만들고, ``ingest`` 는 그 전사본을
**meeting CLI 의 자식 프로세스**로 넘겨 회의록·칸반·통지까지 잇는다. 회의록 도메인의
민감도 게이트·승인·발행은 meeting 이 이미 소유하므로 여기서 다시 구현하지 않는다
(사본 금지). 자식에게는 워처-cron 설계규약 (b-2) 대로 자격증명을 명시 전파한다.

거부 코드: 3=크기 초과 · 5=미지원/빈 전사 · 6=전사 API 실패 · 7=회의록 체인 실패.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import stt_audio
import stt_chunked
import stt_client
import stt_local
import stt_media
import stt_polish
import stt_runtime
import stt_transcript

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
    # The backend decides the size ceiling: 25MiB is the API upload cap, while a
    # local run reads a 2-3 hour recording straight off disk.
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

    # Local first: a cloud call would ship raw audio before the meeting skill's
    # sensitivity gate ever sees the text. An explicit `local` request therefore
    # refuses rather than quietly failing over to a shared provider.
    if toolchain is not None:
        return stt_local.transcribe(
            checked, toolchain, prompt=_prompt(args, pairs)
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
    """One upload — used directly for a short file and per window for a long one."""
    return stt_client.transcribe(
        stt_audio.check_audio(path, max_bytes=stt_audio.MAX_API_AUDIO_BYTES),
        api_key=stt_runtime.setting("OPENAI_API_KEY"),
        base_url=stt_runtime.setting("SPEECHTOTEXT_BASE_URL", stt_client.DEFAULT_BASE_URL),
        model=model,
        language=stt_runtime.setting("SPEECHTOTEXT_LANGUAGE", stt_client.DEFAULT_LANGUAGE),
        prompt=_prompt(args, pairs),
    )


def _run_meeting(transcript: Path, args: argparse.Namespace, project: str) -> int:
    argv = [sys.executable, str(stt_runtime.meeting_cli_path()), "ingest", "--file", str(transcript),
            "--label", args.label or transcript.stem]
    if project:
        argv += ["--project", project]
    if args.offline:
        argv.append("--offline")
    if args.with_evidence:
        argv.append("--with-evidence")
    if args.notify_channel:
        argv += ["--notify-channel", args.notify_channel]
    if args.notify_message_id:
        argv += ["--notify-message-id", args.notify_message_id]
    if args.meeting_recorded:
        argv += ["--recorded-response", args.meeting_recorded]
    completed = subprocess.run(argv, env=stt_runtime.child_env(), check=False, timeout=1800)  # noqa: S603
    return completed.returncode


def _prompt(args: argparse.Namespace, pairs: tuple[tuple[str, str], ...]) -> str:
    return stt_runtime.prompt_for(getattr(args, "prompt", ""), pairs)


def _project(args: argparse.Namespace, label: str) -> str:
    """`--project` wins; otherwise the recording's own name says which project it is."""
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

    tidied = stt_polish.polish(transcription.text, glossary=pairs)
    transcript = stt_transcript.write_transcript(
        stt_runtime.transcript_dir(), label=label, source_name=source.name,
        transcription=transcription, now=now, polish=tidied,
    )
    summary: dict[str, object] = {
        "label": label,
        "project": project,
        "source": source.name,
        "transcript_path": str(transcript),
        "model": transcription.model,
        "chars": len(transcription.text),
        "polish": stt_runtime.polish_summary(tidied),
        "coverage": (
            {
                "ratio": round(transcription.coverage.ratio, 4),
                "complete": transcription.coverage.complete,
                "duration_ms": transcription.coverage.duration_ms,
                "gaps": len(transcription.coverage.gaps),
            }
            if transcription.coverage is not None
            else None
        ),
        "drive_link": stt_runtime.publish_transcript(transcript, label, now, project),
        "meeting_exit": None,
    }
    if not chain:
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    meeting_exit = _run_meeting(transcript, args, project)
    summary["meeting_exit"] = meeting_exit
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if meeting_exit == 0 else MEETING_CHAIN_EXIT


def cmd_polish(args: argparse.Namespace) -> int:
    """Re-tidy a transcript that already exists — no audio, no model, no cost."""
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
    tidied = stt_polish.polish(body, glossary=stt_runtime.merged_glossary(project))
    if not tidied.body.strip():
        print(stt_audio.EMPTY_TRANSCRIPT_NOTICE)
        return 5
    path.write_text(stt_transcript.rewrite(header, tidied, label=label), encoding="utf-8")
    path.chmod(stt_transcript.FILE_MODE)
    print(json.dumps({
        "label": label,
        "project": project,
        "transcript_path": str(path),
        "chars": len(tidied.body),
        "polish": stt_runtime.polish_summary(tidied),
        "drive_link": stt_runtime.publish_transcript(path, label, now, project),
    }, ensure_ascii=False))
    return 0


def _label_of(path: Path) -> str:
    """`2026-08-26_킥오프.md` names the meeting 킥오프 — the date prefix is placement."""
    return _DATE_PREFIX.sub("", path.stem, count=1) or path.stem


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", required=True, help="음성 파일 경로")
    parser.add_argument("--label", help="회의 라벨(기본: 파일명)")
    parser.add_argument("--project", help="과제명(기본: 파일명의 날짜가 아닌 첫 토큰)")
    parser.add_argument("--prompt", default="", help="고유명사 힌트")
    parser.add_argument("--recorded", help="녹취 텍스트 파일(테스트 전용 — 전사 API 미호출)")


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

    args = parser.parse_args(argv)
    if args.command == "polish":
        return cmd_polish(args)
    return cmd_run(args, chain=args.command == "ingest")


if __name__ == "__main__":
    raise SystemExit(main())
