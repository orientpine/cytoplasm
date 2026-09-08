"""Stable lifelog note names and destinations."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import PurePosixPath
from typing import Final

from automation import term_correction
from automation.typing_compat import override

from .lifelog_fields import DEFAULT_TIMEZONE, local_stamp
from .lifelog_model import LifelogRecording

__all__ = [
    "PlaudNoteError",
    "corrected_title",
    "display_name",
    "lifelog_relpath",
    "note_title",
    "recording_stamp",
]

_LIFELOG_ROOT: Final = PurePosixPath("000_PARA/Area/Lifelog")
_SLUG_LIMIT: Final = 60
_DIGEST_LENGTH: Final = 12
_DATE_PREFIX_RE: Final = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|\d{8}|\d{4}_\d{2}_\d{2})[-_T]?")


@dataclass(frozen=True, slots=True)
class PlaudNoteError(Exception):
    """Raised when a recording has no usable timestamp for note placement."""

    recording_id: str

    @override
    def __str__(self) -> str:
        return f"PLAUD recording {self.recording_id!r} has no valid timestamp"


def recording_stamp(recording: LifelogRecording, tz: tzinfo = DEFAULT_TIMEZONE) -> datetime:
    """Recording start in the note's zone; the one place an unusable timestamp fails."""
    stamp = local_stamp(recording, tz)
    if stamp is None:
        raise PlaudNoteError(recording.id)
    return stamp


# Plaud 의 AI 제목은 Plaud 요약과 **같은 때** 만들어진다. 그런데 로컬 전사 경로는 클라우드
# 요약도 전사도 빈 녹음을 골라 동결하므로, 로컬 전사가 필요한 녹음은 언제나 제목이 없고
# name 에는 시각만 남는다 — 2026-09-07 실측: "2026-09-07 11:39:11" → 슬러그 "113911",
# frontmatter 제목도 그 시각 그대로였다. 글자가 하나도 없는 이름은 이름이 아니다.
# 반대로 Plaud 가 붙여 준 이름은 사람이 읽으라고 고른 것이므로 기계 제목이 덮지 않는다.
def display_name(recording: LifelogRecording, generated: str = "") -> str:
    """이름 자리에 설 문자열 — 글자 없는 이름일 때만 생성 제목이 대신한다."""
    name = _normalized_text(recording.name)
    if any(character.isalpha() for character in name):
        return name
    return _normalized_text(generated) or name


def note_title(recording: LifelogRecording, stamp: datetime, *, generated: str = "") -> str:
    """Return the uncorrected owner-facing note title."""
    return corrected_title(recording, stamp, (), generated=generated)[0]


def corrected_title(
    recording: LifelogRecording,
    stamp: datetime,
    glossary: term_correction.Glossary,
    *,
    generated: str = "",
) -> tuple[str, tuple[term_correction.Correction, ...]]:
    """제목도 사람이 읽는 문장이라 고친다 — 그러나 **경로는 고치지 않는다**.

    Plaud 가 붙이는 녹음 이름은 음성에서 나오므로 본문과 같은 오인식을 안고 온다. 파일
    이름의 슬러그는 그 이름의 원문에서 계속 뽑는다: 경로가 참고 문서를 따라 움직이면
    용어집을 한 줄 고친 날 같은 녹음이 노트 둘로 갈라진다.
    """
    name, corrections = term_correction.apply(display_name(recording, generated) or "녹음", glossary)
    return f"{name} ({stamp.date().isoformat()})", corrections


def lifelog_relpath(
    recording: LifelogRecording, stamp: datetime, *, generated: str = ""
) -> PurePosixPath:
    slug = _slug_for_name(display_name(recording, generated))
    digest = hashlib.sha256(recording.id.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]
    filename = f"{stamp.date().isoformat()}-{slug}--{digest}.md"
    return _LIFELOG_ROOT / str(stamp.year) / filename


def _normalized_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _slug_for_name(name: str) -> str:
    allowed = "".join(
        character for character in name if character.isalnum() or character in {" ", "-", "_"}
    )
    slug = "-".join(allowed.split())[:_SLUG_LIMIT].strip("-_")
    slug = _DATE_PREFIX_RE.sub("", slug).strip("-_")
    return slug or "recording"
