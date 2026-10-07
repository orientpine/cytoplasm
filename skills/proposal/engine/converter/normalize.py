from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Final

from ..contracts import DocRecord, SensitivityFlag
from ..contracts.ids import stable_id
from .ingest import RawDoc


KOREAN_NAME_RE = re.compile(
    r"(?:김|이|박|최|정|강|조|윤|장|임|한|오|서|신|권|황|안|송|전|홍|유|고|문|양|손|배|백|허|남|심|노|하|곽|성|차|주|우|구|민|진|지|엄|채|원|천|방|공|현|함|변|염|여|추|도|소|석|선|설|마|길|표|명|기|반|라)[\uac00-\ud7a3]{1,3}"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+82[-\s]?)?0?10[-\s]?\d{4}[-\s]?\d{4}(?!\d)")

PARTNER_RE = re.compile(r"(?:ACME|한빛로보틱스|파트너사|협력사|협력기관|partner)", re.IGNORECASE)
CONTRACT_RE = re.compile(r"(?:협약금|계약\s*금액|계약금액|계약|contract)", re.IGNORECASE)
KRW_VALUE_RE = re.compile(
    r"(?:\d[\d,]*(?:\.\d+)?\s*(?:원|억원|백만원|KRW)|\d+(?:\.\d+)?\s*B\s*KRW|억원|백만원)",
    re.IGNORECASE,
)
NDA_RE = re.compile(r"(?:\bNDA\b|기밀유지|비밀유지|비공개)", re.IGNORECASE)

_META_SENSITIVITY_MAP: Final[dict[str, SensitivityFlag]] = {
    "internal": SensitivityFlag.IP,
    "ip": SensitivityFlag.IP,
    "nda": SensitivityFlag.NDA,
    "redact": SensitivityFlag.PII,
    "pii": SensitivityFlag.PII,
}


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
        sensitivity_flag=_sensitivity_from_meta(raw.meta) or _sensitivity_flag(raw.raw_text),
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


def _sensitivity_flag(text: str) -> SensitivityFlag:
    if _has_pii(text):
        return SensitivityFlag.PII
    if _has_ip(text):
        return SensitivityFlag.IP
    if NDA_RE.search(text):
        return SensitivityFlag.NDA
    return SensitivityFlag.NONE


def _sensitivity_from_meta(meta: dict[str, object]) -> SensitivityFlag | None:
    val = meta.get("sensitivity")
    if val is None:
        return None
    return _META_SENSITIVITY_MAP.get(str(val).lower().strip())


def _has_pii(text: str) -> bool:
    has_contact = EMAIL_RE.search(text) or PHONE_RE.search(text)
    return bool(KOREAN_NAME_RE.search(text) and has_contact)


def _has_ip(text: str) -> bool:
    return bool(PARTNER_RE.search(text) and CONTRACT_RE.search(text) and KRW_VALUE_RE.search(text))
