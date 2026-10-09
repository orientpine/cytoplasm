from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from ..contracts import DocRecord
from ..contracts.ids import stable_id
from .ingest import RawDoc


def normalize(raw: RawDoc) -> DocRecord:
    if not raw.raw_text or not raw.raw_text.strip():
        raise ValueError("Missing required field: raw_text")

    return DocRecord(
        doc_id=stable_id(raw.path, raw.raw_text[:200]),
        source_type=raw.source_type,
        participants=_participants(raw.meta),
        author=_optional_text(raw.meta.get("from")) or _optional_text(raw.meta.get("author")),
        datetime=_parse_datetime(raw.meta.get("date")),
        raw_text=raw.raw_text,
        lang=_detect_lang(raw.raw_text),
        confidence=1.0,
        source_url=_optional_text(raw.meta.get("source_url")),
    )


def _participants(meta: dict[str, object]) -> list[str]:
    speakers = meta.get("speakers", [])
    if isinstance(speakers, str):
        speaker_names = [speakers]
    elif isinstance(speakers, Iterable):
        speaker_names = [str(speaker) for speaker in speakers]
    else:
        speaker_names = []

    if speaker_names:
        return speaker_names

    sender = meta.get("from", "")
    return ["" if sender is None else str(sender)]


def _optional_text(value: object) -> str | None:
    if value is None:
        return None

    text = str(value)
    return text or None


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value

    text = str(value).strip()
    if not text:
        return None

    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _detect_lang(text: str) -> str:
    korean_chars = sum("\uac00" <= char <= "\ud7a3" for char in text)
    return "ko" if korean_chars / len(text) >= 0.1 else "en"
