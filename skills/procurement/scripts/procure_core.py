"""Pure logic for the procurement doc-drafting skill (W4-4).

Format preflight (docx/xlsx/hwpx supported; binary .hwp refused), placeholder
extraction from zip+XML containers, field schema/validation, missing-field
refusal, and the review-DM size branch. Stdlib only — no docx/openpyxl here.
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict
import xml.etree.ElementTree as ET

KST = timezone(timedelta(hours=9), "KST")
PLACEHOLDER = re.compile(r"\{\{([^{}]{1,40})\}\}")
REQUIRED_FIELDS = ("품목", "금액", "업체")
AUTO_FIELDS = ("일자",)
DM_MAX_BYTES = 25 * 1024 * 1024  # Discord DM attachment ceiling (plan: 25 MiB)

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_HWP3_MAGIC = b"HWP Document File"
_AMOUNT = re.compile(r"^\s*([0-9][0-9,\.]*)\s*(?:원|KRW)?\s*$")

QUESTIONS = {
    "품목": "구매/용역 품목명이 무엇인가요? (예: 실험용 소모품 ○○)",
    "금액": "금액이 얼마인가요? (예: 1,234,000원 — 숫자와 단위)",
    "업체": "업체(공급자)명이 무엇인가요?",
    "일자": "작성 일자를 지정할까요? (미지정 시 오늘 날짜 KST)",
}


class UnsupportedTemplate(ValueError):
    """Template format we must not generate from (exit 3)."""

    def __init__(self, message: str, conversion_request: str) -> None:
        super().__init__(message)
        self.conversion_request = conversion_request


@dataclass(frozen=True, slots=True)
class Preflight:
    """Result of template format detection."""

    format: str  # docx | xlsx | hwpx
    parser: str  # python-docx | openpyxl | zip+XML(stdlib)
    members: tuple[str, ...]


class Session(TypedDict):
    """Validated persisted answers for one template conversation."""

    id: str
    template: str
    format: str
    placeholders: list[str]
    answers: dict[str, str]


class SessionError(RuntimeError):
    """Persisted session input is absent or does not match the storage schema."""


def sessions_dir() -> Path:
    """Return the private session store, with an offline-test override."""
    return Path(os.environ.get("PROCURE_SESSION_DIR", "~/.hermes/procurement/sessions")).expanduser()


def load_session(session_id: str) -> tuple[Path, Session]:
    """Parse one persisted session into a fully typed conversation state."""
    record = sessions_dir() / f"{session_id}.json"
    if not record.is_file():
        raise SessionError(f"세션을 찾을 수 없습니다: {session_id}")
    decoded = json.loads(record.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise SessionError(f"세션 형식이 올바르지 않습니다: {session_id}")
    raw: dict[str, object] = {str(key): value for key, value in decoded.items()}
    identity = _session_text(raw, "id", session_id)
    template = _session_text(raw, "template", session_id)
    format_name = _session_text(raw, "format", session_id)
    placeholders = _session_strings(raw.get("placeholders"), session_id)
    answers = _session_answers(raw.get("answers"), session_id)
    return record, {"id": identity, "template": template, "format": format_name,
                    "placeholders": placeholders, "answers": answers}


def save_session(record: Path, session: Session) -> None:
    """Write a private session record with stable Unicode JSON encoding."""
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(session, ensure_ascii=False, indent=1), encoding="utf-8")
    record.chmod(0o600)


def _session_text(raw: dict[str, object], field: str, session_id: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str):
        raise SessionError(f"세션 형식이 올바르지 않습니다: {session_id}")
    return value


def _session_strings(value: object | None, session_id: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(field, str) for field in value):
        raise SessionError(f"세션 형식이 올바르지 않습니다: {session_id}")
    return [field for field in value if isinstance(field, str)]


def _session_answers(value: object | None, session_id: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise SessionError(f"세션 형식이 올바르지 않습니다: {session_id}")
    parsed: dict[str, str] = {}
    for key, answer in value.items():
        if not isinstance(key, str) or not isinstance(answer, str):
            raise SessionError(f"세션 형식이 올바르지 않습니다: {session_id}")
        parsed[key] = answer
    return parsed


def render_conversion_request(name: str, kind: str) -> str:
    return (
        f"CONVERSION-REQUEST 파일 '{name}' 은(는) {kind} 형식이라 자동 생성을 지원하지 않습니다.\n"
        "지원 형식: .docx / .xlsx / .hwpx 입니다.\n"
        "한글(HWP)에서 '다른 이름으로 저장 → HWPX(표준 문서)' 로 변환한 사본을 보내주시면\n"
        "그 템플릿으로 초안을 만들어 드릴게요. (이 파일로는 생성을 시도하지 않았습니다)"
    )


def detect_format(name: str, data: bytes) -> Preflight:
    """Detect docx/xlsx/hwpx by container inspection; refuse binary .hwp."""
    if data.startswith(_OLE2_MAGIC) or data.startswith(_HWP3_MAGIC) or name.lower().endswith(".hwp"):
        raise UnsupportedTemplate(
            f"binary HWP unsupported: {name}", render_conversion_request(name, "바이너리 .hwp")
        )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = tuple(archive.namelist())
    except zipfile.BadZipFile:
        raise UnsupportedTemplate(
            f"unknown container: {name}", render_conversion_request(name, "알 수 없는(비-zip)")
        ) from None
    if "word/document.xml" in members:
        return Preflight("docx", "python-docx", members)
    if "xl/workbook.xml" in members:
        return Preflight("xlsx", "openpyxl", members)
    if "Contents/section0.xml" in members or _mimetype(members, data) == "application/hwp+zip":
        return Preflight("hwpx", "zip+XML(stdlib)", members)
    raise UnsupportedTemplate(
        f"zip without a supported document body: {name}",
        render_conversion_request(name, "지원 목록 밖 zip"),
    )


def preflight_path(path: Path) -> Preflight:
    """Read and inspect one template path through the single container gate."""
    return detect_format(path.name, path.read_bytes())


def _mimetype(members: tuple[str, ...], data: bytes) -> str:
    if "mimetype" not in members:
        return ""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read("mimetype").decode("utf-8", "replace").strip()


def extract_placeholders(preflight: Preflight, data: bytes) -> tuple[str, ...]:
    """Scan the document XML members for {{필드}} placeholders (dedup, ordered)."""
    prefixes = {"docx": "word/", "xlsx": "xl/", "hwpx": "Contents/"}[preflight.format]
    found: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in preflight.members:
            if not (member.startswith(prefixes) and member.endswith(".xml")):
                continue
            raw = archive.read(member).decode("utf-8", "replace")
            try:
                text = "".join(ET.fromstring(raw).itertext())
            except ET.ParseError:
                text = raw
            for match in PLACEHOLDER.finditer(text):
                field = match[1].strip()
                if field and field not in found:
                    found.append(field)
    return tuple(found)


def normalize_amount(raw: str) -> str:
    """Parse a Korean currency figure; reject non-numeric answers."""
    match = _AMOUNT.match(raw)
    if not match:
        raise ValueError(f"금액을 숫자로 인식하지 못했습니다: {raw!r} (예: 1,234,000원)")
    digits = match[1].replace(",", "").rstrip(".")
    if not digits.replace(".", "").isdigit() or float(digits) <= 0:
        raise ValueError(f"금액이 양수가 아닙니다: {raw!r}")
    whole = int(float(digits))
    return f"{whole:,}원"


def validate_answer(field: str, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"'{field}' 값이 비어 있습니다")
    return normalize_amount(cleaned) if field == "금액" else cleaned


def default_fields(now: datetime | None = None) -> dict[str, str]:
    moment = (now or datetime.now(tz=KST)).astimezone(KST)
    return {"일자": moment.strftime("%Y-%m-%d")}


def missing_fields(placeholders: tuple[str, ...], answers: dict[str, str]) -> tuple[str, ...]:
    """Every template placeholder must be answered; AUTO_FIELDS default in."""
    filled = set(answers) | {f for f in AUTO_FIELDS if f in placeholders}
    return tuple(f for f in placeholders if f not in filled)


def render_refusal(missing: tuple[str, ...]) -> str:
    listed = "\n".join(f"  - {field}: {QUESTIONS.get(field, '값을 알려주세요')}" for field in missing)
    return (
        "GENERATION-REFUSED 필수 항목이 비어 있어 초안을 생성하지 않았습니다.\n"
        f"누락 항목 {len(missing)}건:\n{listed}"
    )


def review_mode(size_bytes: int, max_bytes: int = DM_MAX_BYTES) -> str:
    """Size branch for the review DM: attach vs Drive link."""
    return "attach" if size_bytes <= max_bytes else "drive-link"


def xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )
