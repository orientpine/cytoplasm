"""서드파티 엔진·원자적 모델 캐시는 이 격리 프로세스만 소유한다."""

from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import fcntl
from functools import partial
import importlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from time import time_ns
import traceback
from urllib.parse import urlsplit
import wave

from stt_engines_cli import parse_args

DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
ALIGNMENT_MODEL = "kresnik/wav2vec2-large-xlsr-korean"
# 의존성·모델·정렬 정책 변경 시 세대를 바꿔 낡은 캐시와 분리한다.
CACHE_VERSION = "v1-pyannote4.0.7-whisperx3.8.6-torch2.8.0"


def outside_checkout(path: Path) -> Path:
    home = path.expanduser().resolve()
    checkout = os.environ.get("STT_ENGINES_CHECKOUT_ROOT")
    roots = [Path(checkout).resolve()] if checkout else []
    roots += [p for p in Path(__file__).resolve().parents if (p / ".git").exists()]
    roots += [p for p in home.parents if (p / ".git").exists()]
    if any(home == root or home.is_relative_to(root) for root in roots):
        raise ValueError("runtime state inside checkout; set HF_HOME outside checkout")
    return home


def cache_root(engine: str) -> Path:
    home = outside_checkout(Path(os.environ.get("HF_HOME", "~/.cache/stt-engines/huggingface")))
    return home / "stt-engines" / CACHE_VERSION / engine


def debug_dump(failure: Exception) -> None:
    if os.environ.get("STT_ENGINES_DEBUG") != "1":
        return
    try:
        home = Path(os.environ.get("HF_HOME", "~/.cache/stt-engines/huggingface")).expanduser()
        directory = outside_checkout(home.parent / "stt-engines-debug")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, filename = tempfile.mkstemp(prefix=f"{time_ns()}-", suffix=".log", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            traceback.print_exception(failure, file=stream)
    except (OSError, ValueError) as dump_failure:
        print("STT-ENGINES-DEBUG-FAILED " + type(dump_failure).__name__, file=sys.stderr)
        return
    print(filename, file=sys.stderr)


def configure_downloads() -> None:
    """고정한 HF 0.36 전송의 숨은 재시도까지 끈다(스트리밍도 단일 시도)."""
    download = importlib.import_module("huggingface_hub.file_download")
    setattr(download, "http_backoff", partial(download.http_backoff, max_retries=0))

    def once(url, temp_file, *, proxies=None, resume_size=0, headers=None, expected_size=None, **kwargs):
        headers = dict(headers or {})
        if resume_size:
            headers["Range"] = f"bytes={resume_size}-"
        with download.http_backoff(
            method="GET", url=url, headers=headers, proxies=proxies,
            stream=True, timeout=30,
        ) as response:
            download.hf_raise_for_status(response)
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                temp_file.write(chunk)
        if expected_size is not None and temp_file.tell() != expected_size:
            raise OSError("incomplete model download")

    setattr(download, "http_get", once)


@contextmanager
def model_cache(engine: str):
    root = cache_root(engine)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "cache.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ready, pending = root / "ready", root / "pending"
        # SIGKILL 잔해는 다음 단독 실행에서만 제거한다. 완성 캐시는 건드리지 않는다.
        if pending.exists():
            shutil.rmtree(pending)
        offline = ready.is_dir() or os.environ.get("HF_HUB_OFFLINE") == "1"
        if offline and not ready.is_dir():
            raise FileNotFoundError("offline cache missing")
        active = ready if ready.is_dir() else pending
        active.mkdir(exist_ok=True, mode=0o700)
        environment = {
            "HF_HUB_OFFLINE": "1" if offline else "0",
            "TRANSFORMERS_OFFLINE": "1" if offline else "0",
            "HF_HUB_CACHE": str(active / "hub"),
            "HUGGINGFACE_HUB_CACHE": str(active / "hub"),
            "TORCH_HOME": str(active / "torch"),
            "NLTK_DATA": str(active / "nltk"),
            "XDG_CACHE_HOME": str(active / "xdg"),
            "MPLCONFIGDIR": str(active / "matplotlib"),
            "HF_HUB_DISABLE_TELEMETRY": "1", "PYANNOTE_METRICS_ENABLED": "0",
            "HF_HUB_DISABLE_XET": "1", "HF_HUB_ENABLE_HF_TRANSFER": "0",
            "HF_HUB_ETAG_TIMEOUT": "30", "HF_HUB_DOWNLOAD_TIMEOUT": "30",
            "HF_HUB_DISABLE_PROGRESS_BARS": "1", "DO_NOT_TRACK": "1",
        }
        before = {key: os.environ.get(key) for key in environment}
        os.environ.update(environment)
        try:
            yield active, offline
            if active == pending:
                pending.rename(ready)
        finally:
            for key, value in before.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if pending.exists():
                shutil.rmtree(pending)


def samples(wav: str):
    import numpy as np

    with wave.open(wav, "rb") as audio:
        data = audio.readframes(audio.getnframes())
        if len(data) != audio.getnframes() * 2:
            raise ValueError("truncated wav")
    return np.frombuffer(data, dtype="<i2").astype("float32") / 32768.0


def diarize(args, cache: Path, offline: bool, device: str) -> str:
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(
        DIARIZATION_MODEL, token=os.environ["HF_TOKEN"], cache_dir=str(cache / "hub"),
    )
    if pipeline is None:
        raise RuntimeError("pipeline unavailable")
    pipeline.to(torch.device(device))
    if args.command == "prepare":
        return ""
    options = {}
    if args.num_speakers is not None:
        options["num_speakers"] = args.num_speakers
    elif args.min is not None:
        options.update(min_speakers=args.min, max_speakers=args.max)
    result = pipeline({"waveform": torch.from_numpy(samples(args.wav)).unsqueeze(0), "sample_rate": 16000}, **options)
    annotation = result.exclusive_speaker_diarization if args.mode == "exclusive" else result.speaker_diarization
    labels = {}
    turns = []
    for turn, _, label in annotation.itertracks(yield_label=True):
        if not (math.isfinite(turn.start) and math.isfinite(turn.end) and 0 <= turn.start < turn.end):
            raise ValueError("invalid model turn")
        speaker = labels.setdefault(label, len(labels))
        turns.append((turn.start, turn.end, speaker))
    return "".join(f"{start:.3f} -- {end:.3f} speaker_{speaker:02d}\n" for start, end, speaker in sorted(turns))


def align(args, cache: Path, offline: bool, device: str) -> str:
    import nltk
    from whisperx.alignment import align as force_align, load_align_model

    try:
        nltk.data.find("tokenizers/punkt_tab/english/")
    except LookupError:
        if offline:
            raise
        if not nltk.download("punkt_tab", download_dir=str(cache / "nltk"), quiet=True, raise_on_error=True):
            raise OSError("punkt download failed")
    model, metadata = load_align_model(
        language_code="ko", device=device, model_name=ALIGNMENT_MODEL,
        model_dir=str(cache / "hub"), model_cache_only=offline,
    )
    if args.command == "prepare":
        return ""
    audio = samples(args.wav)
    records = []
    for segment in args.records:
        result = force_align(
            [segment], model, metadata, audio, device,
            return_char_alignments=True, interpolate_method="ignore",
        )
        chars = [char for row in result["segments"] for char in row.get("chars", [])]
        # 완전히 정렬 실패하거나 upstream이 원문을 누락하면 시각을 지어내지 않는다.
        if "".join(char["char"] for char in chars) != segment["text"]:
            chars = [{"char": char} for char in segment["text"]]
        for char in chars:
            start, end, score = (char.get(key) for key in ("start", "end", "score"))
            record = {"text": char["char"], "start_ms": None, "end_ms": None, "score": None}
            if (char["char"].lower() in metadata["dictionary"]
                    and isinstance(start, (int, float)) and isinstance(end, (int, float))
                    and isinstance(score, (int, float))
                    and all(math.isfinite(v) for v in (start, end, score))
                    and 0 <= start <= end <= args.duration and 0 <= score <= 1):
                record.update(start_ms=round(start * 1000), end_ms=round(end * 1000), score=score)
            records.append(record)
    return json.dumps(records, ensure_ascii=False, allow_nan=False) + "\n"


def gated_url(failure: BaseException | None) -> str | None:
    """중첩된 HF 응답에서 게이트 저장소 URL만 추출하고 쿼리·원문은 버린다."""
    seen = set()
    while failure is not None and id(failure) not in seen:
        seen.add(id(failure))
        response = getattr(failure, "response", None)
        if response is not None:
            url = urlsplit(response.url)
            gated = response.headers.get("X-Error-Code") == "GatedRepo" or type(failure).__name__ == "GatedRepoError"
            matched = re.match(r"^/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:/|$)", url.path)
            if gated and url.hostname == "huggingface.co" and matched:
                return "https://huggingface.co/" + matched[1]
        failure = failure.__cause__ or failure.__context__
    return None


def run(args) -> int:
    engine = args.engine if args.command == "prepare" else args.command
    with model_cache(engine) as (cache, offline):
        # 서드파티가 예외에 토큰·원문을 끼워 출력할 수 있어 원시 로그는 공개하지 않는다.
        # 실패는 main이 유형·게이트 센티널로 반드시 다시 알린다.
        previous = logging.root.manager.disable
        diagnostics = sys.stderr
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            logging.disable(logging.CRITICAL)
            try:
                configure_downloads()
                import torch

                device = "cuda" if torch.cuda.is_available() else "cpu"
                if device == "cpu":
                    print("STT-ENGINES-CPU-ONLY", file=diagnostics)
                output = (diarize if engine == "diarize" else align)(args, cache, offline, device)
            finally:
                logging.disable(previous)
    sys.stdout.write(output)
    return 0


def main() -> int:
    args = parse_args(sys.argv[1:])
    if not os.environ.get("HF_TOKEN", "").strip():
        print("STT-ENGINES-NO-TOKEN", file=sys.stderr)
        return 4
    try:
        return run(args)
    except Exception as failure:
        debug_dump(failure)
        url = gated_url(failure)
        if url:
            print("STT-ENGINES-GATED " + url, file=sys.stderr)
            return 5
        print("STT-ENGINES-ERROR " + type(failure).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
