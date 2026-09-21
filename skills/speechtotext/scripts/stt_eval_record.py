"""라이브 스킬용 평가 스냅샷의 단방향 직렬화와 평가 설정 지문."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, TypeAlias

import stt_diarize
import stt_window
from stt_local_config import LocalToolchain

PIPELINE_VERSION = "stt-eval-pipeline/2"
TimingSource: TypeAlias = Literal["token", "aligned", "segment", "missing"]


@dataclass(frozen=True, slots=True)
class SnapshotTag:
    state: Literal["SPEAKER", "UNKNOWN", "OVERLAP"]
    speakers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SnapshotWord:
    id: str
    char_start: int
    char_end: int
    start_ms: int | None
    end_ms: int | None
    tag: SnapshotTag
    timing_source: TimingSource


@dataclass(frozen=True, slots=True)
class SnapshotTurn:
    start_ms: int
    end_ms: int
    speaker: str


@dataclass(frozen=True, slots=True)
class SnapshotGap:
    start_ms: int
    end_ms: int
    reason: str


@dataclass(frozen=True, slots=True)
class EvalSnapshot:
    recording_id: str
    audio_sha256: str
    duration_ms: int
    text: str
    config_sha256: str
    words: tuple[SnapshotWord, ...] = ()
    turns: tuple[SnapshotTurn, ...] = ()
    gaps: tuple[SnapshotGap, ...] = ()
    status: Literal["ok", "partial", "failed"] = "ok"
    text_regions: tuple[tuple[int, int], ...] = ()
    der_regions: tuple[tuple[int, int], ...] = ()
    entities: tuple[()] = ()
    schema: str = "stt-eval/v1"


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _component(value: str) -> str:
    """파일이면 내용 지문, 아니면 값 그대로 — **탐침은 절대 던지지 않는다**.

    설정 값에는 경로가 아닌 것이 섞여 있고(`sherpa`·`none`), `Path.is_file()` 은 EACCES 를
    삼키지 않는다(CPython `_IGNORED_ERRNOS` 에 ENOENT·ENOTDIR·EBADF·ELOOP 만 있다). 읽을 수
    없는 cwd 에서 그 값을 상대 경로로 stat 하면 예외가 올라가고, 부르는 쪽이 OSError 를
    fail-soft 로 삼켜 **평가 스냅샷이 통째로 조용히 사라진다**(2026-09-18 노드 실측).
    같은 함정이 2026-08-26 `approval_reminder_config` 수리의 원인이었다 — 보이지 않는
    파일은 없는 파일과 같게 답해야지 던지면 안 된다.
    """
    try:
        path = Path(value).expanduser()
        return _digest(path) if value and path.is_file() else value
    except OSError:
        return value


def config_fingerprint(env: Mapping[str, str], toolchain: LocalToolchain) -> str:
    """평가 지문은 ASR 창 캐시와 별개이며 실제 파일 내용과 화자·정렬 설정까지 묶는다.

    호출자는 명시 prompt·화자 수 등 실행 시 덮어쓴 값을 env에 반영해야 한다.
    전사 코드나 백엔드 기본값을 바꾸면 PIPELINE_VERSION도 올린다.
    """
    diarize = ("BACKEND", "BIN", "MODEL", "SEGMENTATION", "EMBEDDING", "THRESHOLD",
               "MIN_SPEECH", "MIN_SILENCE", "SPEAKERS", "MAX_SPEAKERS", "THREADS",
               "MIN_SPEAKERS", "MODE", "TIMEOUT")
    align = ("BACKEND", "BIN", "MODEL", "LANGUAGE", "TIMEOUT", "DEVICE")
    defaults = {
        "SPEECHTOTEXT_DIARIZE_BACKEND": "sherpa", "SPEECHTOTEXT_ALIGN_BACKEND": "none",
        "SPEECHTOTEXT_DIARIZE_THRESHOLD": str(stt_diarize.DEFAULT_THRESHOLD),
        "SPEECHTOTEXT_DIARIZE_MIN_SPEECH": str(stt_diarize.DEFAULT_MIN_DURATION_ON),
        "SPEECHTOTEXT_DIARIZE_MIN_SILENCE": str(stt_diarize.DEFAULT_MIN_DURATION_OFF),
        "SPEECHTOTEXT_DIARIZE_MODEL": "pyannote/speaker-diarization-community-1"
        if env.get("SPEECHTOTEXT_DIARIZE_BACKEND") == "pyannote" else "",
        "SPEECHTOTEXT_ALIGN_MODEL": "kresnik/wav2vec2-large-xlsr-korean"
        if env.get("SPEECHTOTEXT_ALIGN_BACKEND") == "whisperx" else "",
    }
    components = {
        prefix + name: _component(env.get(prefix + name, defaults.get(prefix + name, "")))
        for prefix, names in (("SPEECHTOTEXT_DIARIZE_", diarize), ("SPEECHTOTEXT_ALIGN_", align))
        for name in names
    }
    material = {
        "pipeline_version": PIPELINE_VERSION, "whisper_sha256": _digest(toolchain.binary),
        "model_sha256": _digest(toolchain.model), "language": toolchain.language,
        "prompt": env.get("SPEECHTOTEXT_PROMPT", toolchain.prompt),
        "decode_flags": toolchain.decode_flags, "max_context": toolchain.max_context,
        "dtw": env.get("SPEECHTOTEXT_WHISPER_DTW", ""), "threads": toolchain.threads,
        "window_ms": toolchain.window_ms, "overlap_ms": toolchain.overlap_ms,
        "window_constants": (stt_window.DEFAULT_WINDOW_MS, stt_window.DEFAULT_OVERLAP_MS,
                             stt_window.MIN_WINDOW_MS),
        "allow_incomplete": toolchain.allow_incomplete, "repeat_limit": toolchain.repeat_limit,
        "components": components, "engines_venv": env.get("STT_ENGINES_VENV", ""),
        "engines_timeout": env.get("STT_ENGINES_TIMEOUT_SECONDS", "3600"),
    }
    serialized = json.dumps(material, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def dump_record(record: EvalSnapshot, path: Path) -> None:
    """부분 JSON이나 공개 권한의 중간 파일 없이 0600 스냅샷으로 교체한다."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".eval-", delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            _ = handle.write((json.dumps(asdict(record), sort_keys=True, ensure_ascii=True) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        _ = temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
