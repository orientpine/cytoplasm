"""ASR 후보 파일은 현재 로컬 전사 환경변수 계약만 사용한다."""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest


_REPO = Path(__file__).resolve().parents[2]
_CANDIDATES = _REPO / "configs" / "stt-eval" / "candidates-asr.json"
_ENV_SOURCES = (
    _REPO / "skills" / "speechtotext" / "scripts" / "stt_local.py",
    _REPO / "skills" / "speechtotext" / "scripts" / "stt_local_config.py",
    _REPO / "skills" / "speechtotext" / "scripts" / "stt_window_run.py",
)
_MODEL_KEY = "SPEECHTOTEXT_WHISPER_MODEL"
_DTW_KEY = "SPEECHTOTEXT_WHISPER_DTW"
_NTH_KEY = "SPEECHTOTEXT_WHISPER_NO_SPEECH"
_SNS_KEY = "SPEECHTOTEXT_WHISPER_SUPPRESS_NONSPEECH"


def _environment_names() -> set[str]:
    names: set[str] = set()
    for source in _ENV_SOURCES:
        names.update(cast(list[str], re.findall(r"SPEECHTOTEXT_[A-Z0-9_]+", source.read_text(encoding="utf-8"))))
    return names


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("후보는 문자열 키 객체여야 한다")
    raw = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in raw):
        raise ValueError("후보는 문자열 키 객체여야 한다")
    return cast(dict[str, object], raw)


def _load() -> list[dict[str, object]]:
    assert _CANDIDATES.is_file(), f"후보 파일이 없다: {_CANDIDATES}"
    data = cast(object, json.loads(_CANDIDATES.read_text(encoding="utf-8")))
    assert isinstance(data, list), "후보 최상위는 목록이어야 한다"
    return [_object(value) for value in cast(list[object], data)]


def _validate(candidates: Sequence[dict[str, object]]) -> None:
    labels: set[str] = set()
    variants: dict[str, set[tuple[bool, bool]]] = {}
    for candidate in candidates:
        if set(candidate) != {"label", "env_overrides"}:
            raise ValueError("후보 필드는 label, env_overrides뿐이어야 한다")
        label = candidate["label"]
        parameters = candidate["env_overrides"]
        if not isinstance(label, str) or not label or label in labels:
            raise ValueError("후보 label은 비어 있지 않고 유일해야 한다")
        if not isinstance(parameters, dict):
            raise ValueError("env_overrides는 문자열 맵이어야 한다")
        raw_parameters = cast(dict[object, object], parameters)
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in raw_parameters.items()):
            raise ValueError("env_overrides는 문자열 맵이어야 한다")
        overrides = cast(dict[str, str], raw_parameters)
        unknown = set(overrides) - _environment_names()
        if unknown:
            raise ValueError(f"알 수 없는 SPEECHTOTEXT 환경변수: {sorted(unknown)}")
        model = overrides.get(_MODEL_KEY)
        if not isinstance(model, str) or not model.startswith("~/whisper.cpp/models/ggml-"):
            raise ValueError("후보마다 whisper.cpp 모델 경로가 필요하다")
        labels.add(label)
        variants.setdefault(Path(model).name, set()).add(
            (_DTW_KEY in overrides, overrides.get(_NTH_KEY) == "0.5" and overrides.get(_SNS_KEY) == "1")
        )
    expected = {
        "ggml-large-v3-turbo-q5_0.bin",
        "ggml-large-v3-q8_0.bin",
        "ggml-large-v3.bin",
    }
    if set(variants) != expected:
        raise ValueError("모델 세트가 turbo-q5_0, large-v3-q8_0, large-v3-f16과 일치하지 않는다")
    if any(flags != {(False, False), (True, False), (False, True), (True, True)} for flags in variants.values()):
        raise ValueError("각 모델은 DTW on/off와 -nth 0.5 -sns 변형을 모두 가져야 한다")


def test_asr_candidates_match_the_engine_config_contract() -> None:
    candidates = _load()
    _validate(candidates)


def test_asr_candidates_reject_an_unknown_environment_key() -> None:
    candidates = _load()
    malformed = [
        {"label": candidate["label"], "env_overrides": dict(cast(dict[str, str], candidate["env_overrides"]))}
        for candidate in candidates
    ]
    overrides = cast(dict[str, str], malformed[0]["env_overrides"])
    overrides["SPEECHTOTEXT_NOT_REAL"] = "1"
    with pytest.raises(ValueError, match="알 수 없는"):
        _validate(malformed)
