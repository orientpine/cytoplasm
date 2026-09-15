"""Stable lifelog note names and destinations.

소유자 결정(2026-09-15, A안): 파일명 stem = ``YYYY-MM-DD HHMM <제목>`` 이고 frontmatter
``title`` 과 ``# H1`` 도 그 stem 이다. 해시 접미도 괄호 날짜도 없다 — 같은 날 녹음이 시각순으로
서고, 사이드바에 보이는 이름이 곧 제목이다. 유일성은 같은 분에 다른 녹음이 이미 앉았을 때만
`` (2)`` 접미로 지킨다(``taken``).

그 전(2026-09-14 까지)의 이름 ``<날짜>-<슬러그>--<12hex>.md`` 는 발견 시점에 정해져 레코드에
박혔고, 로컬 전사 녹음은 그때 제목이 없어 파일명이 영원히 시각(``180427``)이었다. 이제 이름은
제목이 확정되는 finalize 때 정해지고(``binding.finalize``), vault 에 한 번 쓰인 뒤에는 고정이다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import PurePosixPath
from typing import Final

from automation import term_correction
from automation.typing_compat import override

from .lifelog_fields import DEFAULT_TIMEZONE, local_stamp
from .lifelog_model import LifelogRecording

__all__ = [
    "FALLBACK_TITLE",
    "STEM_RE",
    "WORD_SEPARATOR",
    "PlaudNoteError",
    "corrected_title",
    "display_name",
    "lifelog_relpath",
    "note_destination",
    "note_stem",
    "note_title",
    "recording_stamp",
    "stem_title",
    "strip_recording_stamp",
]

_LIFELOG_ROOT: Final = PurePosixPath("000_PARA/Area/Lifelog")
_TITLE_LIMIT: Final = 60
FALLBACK_TITLE: Final = "녹음"
#: ``2026-09-10_1041_출산_당일_병원_이동과_입실_준비`` · 선택적 ``_(2)`` 충돌 접미 — 공백 없음(소유자 지시 2026-09-15).
STEM_RE: Final = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4})_([^\s]+?)(?:_\((\d+)\))?$")
WORD_SEPARATOR: Final = "_"
#: Obsidian 이 파일명에 허용하지 않는 글자(``* " \ / < > : | ?``)와 링크에서 뜻을 갖는 글자(``# ^ [ ]``).
_FORBIDDEN_RE: Final = re.compile(r'[*"\\/<>:|?#^\[\]]')
#: Plaud 가 이름 앞에 붙이는 날짜·시각 토큰 하나 — 날짜(``2026-09-02``·``20260902``·``2026_09_02``·``09-02``)
#: 또는 시각(``09:02``·``09:02:00``·``13-05-22``·``130522``·``1041``)과 뒤따르는 구분자.
_STAMP_TOKEN_RE: Final = re.compile(
    r"^(?:\d{4}[-_.]?\d{2}[-_.]?\d{2}|\d{2}[-_.]\d{2}|\d{1,2}:\d{2}(?::\d{2})?|\d{2}[-_]\d{2}[-_]\d{2}|\d{6}|\d{4})"
    r"(?:T|[\s\-_:.]+|$)"
)


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


def strip_recording_stamp(name: str) -> str:
    """이름 앞머리의 날짜·시각 토큰을 걷어낸다 — 파일명이 이미 그것을 말한다."""
    text = _normalized_text(name)
    while True:
        match = _STAMP_TOKEN_RE.match(text)
        if match is None:
            return text
        text = text[match.end() :].lstrip()


# Plaud 의 AI 제목은 Plaud 요약과 **같은 때** 만들어진다. 그런데 로컬 전사 경로는 클라우드
# 요약도 전사도 빈 녹음을 골라 동결하므로, 로컬 전사가 필요한 녹음은 언제나 제목이 없고
# name 에는 시각만 남는다 — 2026-09-07 실측: "2026-09-07 11:39:11". 글자가 하나도 없는
# 이름은 이름이 아니다. 반대로 Plaud 가 붙여 준 이름은 사람이 읽으라고 고른 것이므로 기계
# 제목이 덮지 않는다.
def display_name(recording: LifelogRecording, generated: str = "") -> str:
    """이름 자리에 설 문자열(날짜·시각 앞머리 제거) — 글자 없는 이름일 때만 생성 제목이 대신한다."""
    name = strip_recording_stamp(recording.name)
    if _has_letters(name):
        return name
    return strip_recording_stamp(generated) or name


def stem_title(name: str) -> str:
    """stem 의 제목 부분 — 금지문자·공백은 ``_`` 로, 60자 상한, 글자가 없으면 ``녹음``."""
    cleaned = " ".join(_FORBIDDEN_RE.sub(" ", _normalized_text(name)).split())
    cleaned = cleaned[:_TITLE_LIMIT].rstrip(" ._")
    if not _has_letters(cleaned):
        return FALLBACK_TITLE
    return WORD_SEPARATOR.join(cleaned.split())


def note_stem(stamp: datetime, title: str) -> str:
    return f"{stamp:%Y-%m-%d}{WORD_SEPARATOR}{stamp:%H%M}{WORD_SEPARATOR}{title}"


def note_destination(
    recording: LifelogRecording,
    stamp: datetime,
    glossary: term_correction.Glossary = (),
    *,
    generated: str = "",
    taken: Collection[PurePosixPath] = frozenset(),
) -> tuple[PurePosixPath, tuple[term_correction.Correction, ...]]:
    """(vault 경로, 이름을 고치며 바뀐 어절) — 제목은 그 경로의 ``stem`` 이다.

    용어집 교정은 여기서 **한 번**, stem 을 처음 정할 때 이름에 걸린다. 한 번 쓰인 노트는 경로가
    고정이므로(``binding.finalize``) 용어집을 한 줄 고친 날 같은 녹음이 노트 둘로 갈라지지 않는다.
    """
    name, corrections = term_correction.apply(display_name(recording, generated), glossary)
    stem = note_stem(stamp, stem_title(name))
    relpath = _LIFELOG_ROOT / str(stamp.year) / f"{stem}.md"
    ordinal = 2
    while relpath in taken:
        relpath = relpath.with_name(f"{stem}{WORD_SEPARATOR}({ordinal}).md")
        ordinal += 1
    return relpath, corrections


def lifelog_relpath(
    recording: LifelogRecording,
    stamp: datetime,
    *,
    generated: str = "",
    taken: Collection[PurePosixPath] = frozenset(),
) -> PurePosixPath:
    return note_destination(recording, stamp, generated=generated, taken=taken)[0]


def corrected_title(
    recording: LifelogRecording,
    stamp: datetime,
    glossary: term_correction.Glossary,
    *,
    generated: str = "",
) -> tuple[str, tuple[term_correction.Correction, ...]]:
    """Return the owner-facing note title (= the fresh stem) and the words fixed to make it."""
    relpath, corrections = note_destination(recording, stamp, glossary, generated=generated)
    return relpath.stem, corrections


def note_title(recording: LifelogRecording, stamp: datetime, *, generated: str = "") -> str:
    """Return the uncorrected owner-facing note title."""
    return corrected_title(recording, stamp, (), generated=generated)[0]


def _normalized_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _has_letters(text: str) -> bool:
    return any(character.isalpha() for character in text)
