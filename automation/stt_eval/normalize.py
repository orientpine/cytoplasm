"""ko-cer-v1 문자 오류율 정규화."""

from __future__ import annotations

import re
import unicodedata

NORMALIZATION_VERSION = "ko-cer-v1"
_NUMERIC_ATOM = re.compile(r"[+−-]?(?:\d+(?:[.,:/-]\d+)*|\.\d+)(?:\s*%)?")


def _without_punctuation(text: str) -> str:
    return "".join(character for character in text if not unicodedata.category(character).startswith("P"))


def normalize_cer(text: str, *, keep_spaces: bool = False) -> str:
    """NFC, 소문자화, 숫자 원자 보존으로 CER 입력을 정규화한다."""
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text).casefold()).strip()
    parts: list[str] = []
    cursor = 0
    for atom in _NUMERIC_ATOM.finditer(normalized):
        parts.append(_without_punctuation(normalized[cursor:atom.start()]))
        parts.append(atom.group())
        cursor = atom.end()
    parts.append(_without_punctuation(normalized[cursor:]))
    result = re.sub(r"\s+", " ", "".join(parts)).strip()
    return result if keep_spaces else result.replace(" ", "")
