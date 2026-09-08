"""전사 정확도 평가 레코드의 불변 자료형과 JSON 경계."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, NoReturn, TypeAlias, TypeGuard, cast

SpeakerState: TypeAlias = Literal["SPEAKER", "UNKNOWN", "OVERLAP"]
EntityKind: TypeAlias = Literal["name", "number", "date"]
EvalStatus: TypeAlias = Literal["ok", "partial", "failed"]
TimingSource: TypeAlias = Literal["token", "aligned", "segment", "missing"]
TimeRange: TypeAlias = tuple[int, int]

_SCHEMA = "stt-eval/v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RECORD_KEYS = frozenset({"schema", "recording_id", "audio_sha256", "duration_ms", "text", "words", "turns", "text_regions", "der_regions", "entities", "gaps", "status", "config_sha256"})


class EvalRecordError(ValueError):
    """평가 레코드가 stt-eval/v1 계약을 만족하지 않는다."""


@dataclass(frozen=True, slots=True)
class SpeakerTag:
    state: SpeakerState
    speakers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvalWord:
    id: str
    char_start: int
    char_end: int
    start_ms: int | None
    end_ms: int | None
    tag: SpeakerTag
    timing_source: TimingSource


@dataclass(frozen=True, slots=True)
class EvalTurn:
    start_ms: int
    end_ms: int
    speaker: str


@dataclass(frozen=True, slots=True)
class EvalEntity:
    id: str
    kind: EntityKind
    char_start: int
    char_end: int
    canonical: str
    accepted: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvalGap:
    start_ms: int
    end_ms: int
    reason: str


@dataclass(frozen=True, slots=True)
class EvalRecord:
    recording_id: str
    audio_sha256: str
    duration_ms: int
    text: str
    words: tuple[EvalWord, ...] = ()
    turns: tuple[EvalTurn, ...] = ()
    text_regions: tuple[TimeRange, ...] = ()
    der_regions: tuple[TimeRange, ...] = ()
    entities: tuple[EvalEntity, ...] = ()
    gaps: tuple[EvalGap, ...] = ()
    status: EvalStatus = "ok"
    config_sha256: str | None = None
    schema: str = _SCHEMA
    provenance: str | None = None


def _fail(field: str, detail: str) -> NoReturn:
    raise EvalRecordError(f"{field}: {detail}")


def _is_mapping(value: object) -> TypeGuard[dict[str, object]]:
    if not isinstance(value, dict):
        return False
    raw = cast(dict[object, object], value)
    return all(isinstance(key, str) for key in raw)


def _is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _mapping(value: object, field: str, keys: frozenset[str]) -> dict[str, object]:
    if not _is_mapping(value):
        _fail(field, "객체여야 합니다")
    actual = set(value)
    if actual != set(keys):
        _fail(field, f"필드 불일치 missing={sorted(keys - actual)} unknown={sorted(actual - keys)}")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        _fail(field, "문자열이어야 합니다")
    return value


def _integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(field, "정수여야 합니다")
    return value


def _list(value: object, field: str) -> list[object]:
    if not _is_list(value):
        _fail(field, "배열이어야 합니다")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    return tuple(_string(item, f"{field}[{index}]") for index, item in enumerate(_list(value, field)))


def _sha256(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    parsed = _string(value, field)
    if _SHA256.fullmatch(parsed) is None:
        _fail(field, "64자리 소문자 16진 sha256이어야 합니다")
    return parsed


def _required_sha256(value: object, field: str) -> str:
    parsed = _sha256(value, field)
    if parsed is None:
        _fail(field, "null이 될 수 없습니다")
    return parsed


def _speaker_state(value: object, field: str) -> SpeakerState:
    state = _string(value, field)
    if state == "SPEAKER" or state == "UNKNOWN" or state == "OVERLAP":
        return state
    _fail(field, "알 수 없는 상태입니다")


def _entity_kind(value: object, field: str) -> EntityKind:
    kind = _string(value, field)
    if kind == "name" or kind == "number" or kind == "date":
        return kind
    _fail(field, "알 수 없는 종류입니다")


def _status(value: object) -> EvalStatus:
    status = _string(value, "status")
    if status == "ok" or status == "partial" or status == "failed":
        return status
    _fail("status", "알 수 없는 상태입니다")


def _timing_source(value: object, field: str) -> TimingSource:
    source = _string(value, field)
    if source == "token" or source == "aligned" or source == "segment" or source == "missing":
        return source
    _fail(field, "알 수 없는 시각 출처입니다")


def _span(data: dict[str, object], field: str, text_length: int) -> tuple[int, int]:
    start = _integer(data["char_start"], f"{field}.char_start")
    end = _integer(data["char_end"], f"{field}.char_end")
    if start < 0 or end < start or end > text_length:
        _fail(f"{field}.char_end", "텍스트 범위 안의 단조 스팬이어야 합니다")
    return start, end


def _interval(start: object, end: object, field: str, duration_ms: int) -> TimeRange:
    parsed_start = _integer(start, f"{field}.start_ms")
    parsed_end = _integer(end, f"{field}.end_ms")
    if parsed_start < 0:
        _fail(f"{field}.start_ms", "음수가 될 수 없습니다")
    if parsed_end < parsed_start or parsed_end > duration_ms:
        _fail(f"{field}.end_ms", "녹음 범위 안의 단조 구간이어야 합니다")
    return parsed_start, parsed_end


def _tag(value: object, field: str) -> SpeakerTag:
    data = _mapping(value, field, frozenset({"state", "speakers"}))
    return SpeakerTag(_speaker_state(data["state"], f"{field}.state"), _strings(data["speakers"], f"{field}.speakers"))


def _word(value: object, index: int, text_length: int, duration_ms: int) -> EvalWord:
    field = f"words[{index}]"
    data = _mapping(value, field, frozenset({"id", "char_start", "char_end", "start_ms", "end_ms", "tag", "timing_source"}))
    char_start, char_end = _span(data, field, text_length)
    start, end = data["start_ms"], data["end_ms"]
    if start is None and end is None:
        start_ms, end_ms = None, None
    elif start is None or end is None:
        _fail(field, "start_ms와 end_ms는 함께 null이어야 합니다")
    else:
        start_ms, end_ms = _interval(start, end, field, duration_ms)
    return EvalWord(_string(data["id"], f"{field}.id"), char_start, char_end, start_ms, end_ms, _tag(data["tag"], f"{field}.tag"), _timing_source(data["timing_source"], f"{field}.timing_source"))


def _turn(value: object, index: int, duration_ms: int) -> EvalTurn:
    field = f"turns[{index}]"
    data = _mapping(value, field, frozenset({"start_ms", "end_ms", "speaker"}))
    start_ms, end_ms = _interval(data["start_ms"], data["end_ms"], field, duration_ms)
    return EvalTurn(start_ms, end_ms, _string(data["speaker"], f"{field}.speaker"))


def _entity(value: object, index: int, text_length: int) -> EvalEntity:
    field = f"entities[{index}]"
    data = _mapping(value, field, frozenset({"id", "kind", "char_start", "char_end", "canonical", "accepted"}))
    char_start, char_end = _span(data, field, text_length)
    return EvalEntity(_string(data["id"], f"{field}.id"), _entity_kind(data["kind"], f"{field}.kind"), char_start, char_end, _string(data["canonical"], f"{field}.canonical"), _strings(data["accepted"], f"{field}.accepted"))


def _gap(value: object, index: int, duration_ms: int) -> EvalGap:
    field = f"gaps[{index}]"
    data = _mapping(value, field, frozenset({"start_ms", "end_ms", "reason"}))
    start_ms, end_ms = _interval(data["start_ms"], data["end_ms"], field, duration_ms)
    return EvalGap(start_ms, end_ms, _string(data["reason"], f"{field}.reason"))


def _ranges(value: object, field: str, duration_ms: int) -> tuple[TimeRange, ...]:
    ranges: list[TimeRange] = []
    for index, item in enumerate(_list(value, field)):
        pair = _list(item, f"{field}[{index}]")
        if len(pair) != 2:
            _fail(f"{field}[{index}]", "두 정수의 배열이어야 합니다")
        ranges.append(_interval(pair[0], pair[1], f"{field}[{index}]", duration_ms))
    return tuple(ranges)


def _provenance(value: object) -> str | None:
    if value is None:
        return None
    parsed = _string(value, "provenance")
    matched = re.fullmatch(r"owner-edit:(lifelog|meeting):(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))", parsed)
    if matched is None:
        _fail("provenance", "owner-edit:표면:ISO-8601 시각이어야 합니다")
    try:
        _ = datetime.fromisoformat(matched.group(2))
    except ValueError:
        _fail("provenance", "유효한 시각이어야 합니다")
    return parsed


def _record(value: object) -> EvalRecord:
    keys = _RECORD_KEYS | {"provenance"} if _is_mapping(value) and "provenance" in value else _RECORD_KEYS
    data = _mapping(value, "record", keys)
    schema = _string(data["schema"], "schema")
    if schema != _SCHEMA:
        _fail("schema", f"{_SCHEMA!r}이어야 합니다")
    duration_ms = _integer(data["duration_ms"], "duration_ms")
    if duration_ms < 0:
        _fail("duration_ms", "음수가 될 수 없습니다")
    text = _string(data["text"], "text")
    return EvalRecord(
        _string(data["recording_id"], "recording_id"), _required_sha256(data["audio_sha256"], "audio_sha256"), duration_ms, text,
        tuple(_word(item, index, len(text), duration_ms) for index, item in enumerate(_list(data["words"], "words"))),
        tuple(_turn(item, index, duration_ms) for index, item in enumerate(_list(data["turns"], "turns"))),
        _ranges(data["text_regions"], "text_regions", duration_ms), _ranges(data["der_regions"], "der_regions", duration_ms),
        tuple(_entity(item, index, len(text)) for index, item in enumerate(_list(data["entities"], "entities"))),
        tuple(_gap(item, index, duration_ms) for index, item in enumerate(_list(data["gaps"], "gaps"))),
        _status(data["status"]), _sha256(data["config_sha256"], "config_sha256", optional=True), schema, _provenance(data.get("provenance")),
    )


def _serialize(record: EvalRecord) -> dict[str, object]:
    return {
        **({"provenance": record.provenance} if record.provenance is not None else {}),
        "schema": record.schema, "recording_id": record.recording_id, "audio_sha256": record.audio_sha256, "duration_ms": record.duration_ms, "text": record.text,
        "words": [{"id": word.id, "char_start": word.char_start, "char_end": word.char_end, "start_ms": word.start_ms, "end_ms": word.end_ms, "tag": {"state": word.tag.state, "speakers": list(word.tag.speakers)}, "timing_source": word.timing_source} for word in record.words],
        "turns": [{"start_ms": turn.start_ms, "end_ms": turn.end_ms, "speaker": turn.speaker} for turn in record.turns], "text_regions": [list(region) for region in record.text_regions], "der_regions": [list(region) for region in record.der_regions],
        "entities": [{"id": entity.id, "kind": entity.kind, "char_start": entity.char_start, "char_end": entity.char_end, "canonical": entity.canonical, "accepted": list(entity.accepted)} for entity in record.entities],
        "gaps": [{"start_ms": gap.start_ms, "end_ms": gap.end_ms, "reason": gap.reason} for gap in record.gaps], "status": record.status, "config_sha256": record.config_sha256,
    }


def load_record(path: Path) -> EvalRecord:
    """JSON 파일을 검증해 평가 레코드로 읽는다."""
    try:
        raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise EvalRecordError(f"record: 읽을 수 없는 JSON입니다 ({error})") from error
    return _record(raw)


def dump_record(record: EvalRecord, path: Path) -> None:
    """검증한 평가 레코드를 JSON 파일로 쓴다."""
    parsed = _record(_serialize(record))
    try:
        _ = path.write_text(json.dumps(_serialize(parsed), ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as error:
        raise EvalRecordError(f"record: 쓸 수 없습니다 ({error})") from error
