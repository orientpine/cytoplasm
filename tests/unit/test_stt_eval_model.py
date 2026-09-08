from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from automation.stt_eval.model import (
    EvalEntity,
    EvalGap,
    EvalRecord,
    EvalRecordError,
    EvalTurn,
    EvalWord,
    SpeakerTag,
    dump_record,
    load_record,
)


SHA = "a" * 64
CONFIG_SHA = "b" * 64


def _record() -> EvalRecord:
    return EvalRecord(
        recording_id="recording-1",
        audio_sha256=SHA,
        duration_ms=1_000,
        text="가나다",
        words=(
            EvalWord("word-1", 0, 2, 0, 500, SpeakerTag("SPEAKER", ("speaker-1",)), "token"),
            EvalWord("word-2", 2, 3, None, None, SpeakerTag("UNKNOWN"), "missing"),
        ),
        turns=(EvalTurn(0, 800, "speaker-1"),),
        text_regions=((0, 800),),
        der_regions=((0, 1_000),),
        entities=(EvalEntity("entity-1", "number", 1, 3, "12", ("일이",)),),
        gaps=(EvalGap(800, 1_000, "gap"),),
        status="partial",
        config_sha256=CONFIG_SHA,
    )


def _raw(path: Path) -> dict[str, object]:
    _ = dump_record(_record(), path)
    parsed = cast(object, json.loads(path.read_text(encoding="utf-8")))
    assert isinstance(parsed, dict)
    return cast(dict[str, object], parsed)


def _rows(raw: dict[str, object], section: str) -> list[dict[str, object]]:
    rows = cast(list[object], raw[section])
    result: list[dict[str, object]] = []
    for row in rows:
        assert isinstance(row, dict)
        result.append(cast(dict[str, object], row))
    return result


def test_dump_load_roundtrip_preserves_every_serialized_field(tmp_path: Path) -> None:
    original = _record()
    path = tmp_path / "record.json"
    _ = dump_record(original, path)
    assert load_record(path) == original


@pytest.mark.parametrize("audio_sha256", ["a" * 63, "g" * 64])
def test_load_rejects_invalid_audio_sha256_with_field_name(tmp_path: Path, audio_sha256: str) -> None:
    path = tmp_path / "record.json"
    raw = _raw(path)
    raw["audio_sha256"] = audio_sha256
    _ = path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(EvalRecordError, match="audio_sha256"):
        _ = load_record(path)


@pytest.mark.parametrize(("field", "value"), [("schema", "stt-eval/v2"), ("status", "unknown")])
def test_load_rejects_unknown_schema_or_status_with_field_name(
    tmp_path: Path, field: str, value: str
) -> None:
    path = tmp_path / "record.json"
    raw = _raw(path)
    raw[field] = value
    _ = path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(EvalRecordError, match=field):
        _ = load_record(path)


def test_load_rejects_reversed_word_span_with_field_name(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    raw = _raw(path)
    words = _rows(raw, "words")
    words[0]["char_start"] = 2
    words[0]["char_end"] = 1
    _ = path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(EvalRecordError, match="char_end"):
        _ = load_record(path)


def test_load_rejects_negative_milliseconds_with_field_name(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    raw = _raw(path)
    turns = _rows(raw, "turns")
    turns[0]["start_ms"] = -1
    _ = path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(EvalRecordError, match="start_ms"):
        _ = load_record(path)


@pytest.mark.parametrize(("section", "field", "value"), [("words", "state", "OTHER"), ("entities", "kind", "place")])
def test_load_rejects_unknown_tag_state_or_entity_kind_with_field_name(
    tmp_path: Path, section: str, field: str, value: str
) -> None:
    path = tmp_path / "record.json"
    raw = _raw(path)
    rows = _rows(raw, section)
    if section == "words":
        cast(dict[str, object], rows[0]["tag"])[field] = value
    else:
        rows[0][field] = value
    _ = path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(EvalRecordError, match=field):
        _ = load_record(path)


def test_dump_rejects_invalid_config_sha256_at_the_boundary(tmp_path: Path) -> None:
    with pytest.raises(EvalRecordError, match="config_sha256"):
        _ = dump_record(replace(_record(), config_sha256="bad"), tmp_path / "record.json")
