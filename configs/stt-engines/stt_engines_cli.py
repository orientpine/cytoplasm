"""모델 import 전 입력·토큰을 검사하는 격리 CLI 진입점."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import wave


def positive(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("양의 정수가 필요합니다")
    return value


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="stt-engines")
    commands = parser.add_subparsers(dest="command", required=True)
    diarize = commands.add_parser("diarize")
    diarize.add_argument("--wav", required=True)
    diarize.add_argument("--num-speakers", type=positive)
    diarize.add_argument("--min", type=positive)
    diarize.add_argument("--max", type=positive)
    diarize.add_argument("--mode", choices=("exclusive", "regular"), default="exclusive")
    align = commands.add_parser("align")
    align.add_argument("--wav", required=True)
    align.add_argument("--segments", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--engine", choices=("diarize", "align"), default="diarize")
    args = parser.parse_args(argv)
    if args.command == "diarize":
        if args.num_speakers is not None and (args.min is not None or args.max is not None):
            parser.error("화자 수와 범위는 함께 지정할 수 없습니다")
        if (args.min is None) != (args.max is None):
            parser.error("--min 과 --max 를 함께 지정하십시오")
        if args.min is not None and args.min > args.max:
            parser.error("화자 범위가 역전되었습니다")
    if args.command == "prepare":
        return args
    try:
        if not args.wav.strip():
            raise ValueError("empty wav")
        args.wav = str(Path(args.wav).expanduser().resolve(strict=True))
        with wave.open(args.wav, "rb") as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
                raise ValueError("wav format")
            args.duration = audio.getnframes() / 16000
            if args.duration <= 0:
                raise ValueError("empty audio")
        if args.command == "align":
            args.records = json.loads(Path(args.segments).read_text(encoding="utf-8"))
            if not isinstance(args.records, list) or not args.records:
                raise ValueError("segments list")
            for row in args.records:
                if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                    raise ValueError("segment text")
                start, end = row.get("start"), row.get("end")
                if (not isinstance(start, (int, float)) or isinstance(start, bool)
                        or not isinstance(end, (int, float)) or isinstance(end, bool)
                        or not math.isfinite(start) or not math.isfinite(end)):
                    raise ValueError("segment times")
                if not 0 <= start < end <= args.duration:
                    raise ValueError("segment bounds")
    except (OSError, ValueError, EOFError, wave.Error):
        parser.error("16k mono PCM16 WAV 또는 segments JSON 입력이 잘못되었습니다")
    return args


def run_worker(argv: list[str], timeout: int) -> int:
    """한 번만 실행하며 취소·시간 초과는 전체 자식 그룹을 정리한다."""
    worker = Path(__file__).with_name("stt_engines_runtime.py")
    process = subprocess.Popen(
        [sys.executable, "-B", str(worker), *argv], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as failure:
        handlers = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                # 이미 종료된 그룹은 아래 communicate에서 그대로 회수한다.
                process.wait()
            process.communicate()
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        timed_out = isinstance(failure, subprocess.TimeoutExpired)
        print("STT-ENGINES-TIMEOUT" if timed_out else "STT-ENGINES-CANCELLED", file=sys.stderr)
        return 124 if timed_out else 130
    if process.returncode == 0:
        sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    return process.returncode if process.returncode >= 0 else 128 - process.returncode


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parse_args(argv)
    if not os.environ.get("HF_TOKEN", "").strip():
        print("STT-ENGINES-NO-TOKEN", file=sys.stderr)
        return 4
    try:
        timeout = positive(os.environ.get("STT_ENGINES_TIMEOUT_SECONDS", "3600"))
        if timeout > 86400:
            raise ValueError("timeout limit")
    except (ValueError, argparse.ArgumentTypeError):
        print("STT-ENGINES-INVALID-TIMEOUT", file=sys.stderr)
        return 2
    previous = signal.getsignal(signal.SIGTERM)

    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        return run_worker(argv, timeout)
    except OSError:
        print("STT-ENGINES-WORKER-FAILED", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
