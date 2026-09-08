"""러너의 외부 후보 정의와 녹음 목록을 실행 전에 검증한다."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias, cast


@dataclass(frozen=True, slots=True)
class Candidate:
    label: str
    env_overrides: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Recording:
    audio_sha256: str
    drive_file_id: str
    duration_ms: int


Manifest: TypeAlias = Path | Sequence[Mapping[str, object]]
Configs: TypeAlias = Path | Sequence[Candidate]
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class RunnerInputError(ValueError):
    """실험 입력 또는 원장이 올바르지 않다."""


def sha(value: object) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise RunnerInputError("audio/config sha256")
    return value


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RunnerInputError("object required")
    if not all(isinstance(key, str) for key in cast(dict[object, object], value)):
        raise RunnerInputError("string keys required")
    return cast(dict[str, object], value)


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [mapping(cast(object, json.loads(line))) for line in path.read_text(encoding="utf-8").splitlines() if line]


def candidates(source: Configs) -> tuple[Candidate, ...]:
    if isinstance(source, Path):
        raw = cast(object, json.loads(source.read_text(encoding="utf-8")))
        if not isinstance(raw, list):
            raise RunnerInputError("candidates list")
        parsed: list[Candidate] = []
        for value in cast(list[object], raw):
            row = mapping(value)
            if set(row) != {"label", "env_overrides"}:
                raise RunnerInputError("candidate fields")
            overrides = mapping(row["env_overrides"])
            label = row["label"]
            if not isinstance(label, str) or not all(isinstance(v, str) for v in overrides.values()):
                raise RunnerInputError("candidate strings")
            parsed.append(Candidate(label, tuple(cast(dict[str, str], overrides).items())))
        result = tuple(parsed)
    else:
        result = tuple(source)
    labels: set[str] = set()
    for candidate in result:
        label = candidate.label
        if not label or len(label.encode()) > 128 or any(ord(c) < 32 for c in label):
            raise RunnerInputError("candidate label")
        if label in labels:
            raise RunnerInputError("duplicate label")
        labels.add(label)
        keys: set[str] = set()
        for key, value in candidate.env_overrides:
            if not re.fullmatch(r"SPEECHTOTEXT_[A-Z0-9_]+", key):
                raise RunnerInputError("candidate env key")
            if "\0" in value or key in keys:
                raise RunnerInputError("candidate env value")
            keys.add(key)
    return result


def recordings(source: Manifest) -> tuple[Recording, ...]:
    rows = read_jsonl(source) if isinstance(source, Path) else source
    result: list[Recording] = []
    seen: set[str] = set()
    for row in rows:
        digest = sha(row.get("audio_sha256"))
        file_id, duration = row.get("drive_file_id"), row.get("duration_ms")
        if not isinstance(file_id, str) or not file_id or "\0" in file_id:
            raise RunnerInputError("drive_file_id")
        if type(duration) is not int or duration < 0:
            raise RunnerInputError("duration_ms")
        if digest in seen:
            raise RunnerInputError("duplicate audio")
        seen.add(digest)
        result.append(Recording(digest, file_id, duration))
    return tuple(result)


def private_root(path: Path) -> Path:
    """stt_window_store와 같은 .git 조상 판정이며 심링크도 해석한다."""
    root = path.expanduser().resolve()
    if any((ancestor / ".git").exists() for ancestor in (root, *root.parents)):
        raise RunnerInputError("STT-EVAL-ROOT-REFUSED")
    return root


MODEL_KEYS = ("SPEECHTOTEXT_WHISPER_MODEL", "SPEECHTOTEXT_VAD_MODEL",
              "SPEECHTOTEXT_DIARIZE_SEGMENTATION", "SPEECHTOTEXT_DIARIZE_EMBEDDING")


def resolve_environment(env: Mapping[str, str]) -> dict[str, str]:
    """자식 cwd를 tmp로 옮기기 전에 명시된 로컬 파일 경로를 고정한다."""
    result = dict(env)
    for key, raw in env.items():
        if raw and (key in MODEL_KEYS or key.startswith("SPEECHTOTEXT_") and key.endswith("_BIN") and "/" in raw):
            expanded = str(Path(env["HOME"]) / raw[2:]) if raw.startswith("~/") and "HOME" in env else raw
            result[key] = str(Path(expanded).expanduser().resolve())
    return result


def cpu_max_ms(env: Mapping[str, str]) -> int | None:
    """호출자의 CPU-only 상한 선언을 읽는다. CUDA 복구 시 키를 지워 해제한다."""
    if "STT_ENGINES_CPU_MAX_MS" not in env:
        return None
    maximum = int(env["STT_ENGINES_CPU_MAX_MS"] or "600000")
    if maximum <= 0:
        raise RunnerInputError("CPU maximum")
    return maximum


def cpu_limited(candidate: Candidate, recording: Recording, maximum: int | None) -> bool:
    # 미측정(0)은 짧다는 근거가 아니다. 부모 기본값과 무관하게 ASR은 그대로 실행한다.
    return (maximum is not None and (recording.duration_ms == 0 or recording.duration_ms > maximum)
            and any(key.startswith(("SPEECHTOTEXT_DIARIZE_", "SPEECHTOTEXT_ALIGN_"))
                    for key, _ in candidate.env_overrides))


def model_missing(env: Mapping[str, str]) -> bool:
    if not env.get("SPEECHTOTEXT_WHISPER_MODEL", "").strip():
        return True
    return any(env.get(key) and not Path(env[key]).is_file() for key in MODEL_KEYS)
