"""The line a transcript prints in place of minutes nobody could transcribe.

Three layers have to agree about this line, and on 2026-09-04 they did not. It is
written by `stt_window`, turned into sentences and blocks by `stt_blocks`, and cut
at speaker boundaries by `stt_split`. The real 61-minute recording went through all
three and the owner read `[전사` — one fragment of the marker — while the rest of it
was scattered as eleven pieces across fourteen minutes of the document: the marker
had been glued onto the unfinished sentence of the previous window, and the speaker
splitter then cut that 916-second "sentence" at every turn boundary it crossed.

Nothing downstream could tell those characters apart from speech, so the marker's
grammar lives here, in the one module all three import: the template, the clock it
prints, and — the piece that was missing — the way to recognize it again once it is
nothing but text.
"""

from __future__ import annotations

import re
from typing import Final

#: 전사하지 못한 구간을 대신하는 한 줄 — 어느 분이 비었는지 이름을 댄다. 마지막 마침표는
#: 문장 경계이기도 하다: 그것이 없으면 다음 창의 첫 문장이 표지 뒤에 그대로 달라붙는다.
MARKER: Final = "[전사 실패 구간 {start}–{end} — 이 구간만 비어 있고 나머지는 그대로입니다]."

_FIELD: Final = re.compile(r"(\{start\}|\{end\})")
_CLOCK: Final = r"\d{2,}:\d{2}:\d{2}"
# Built out of the template itself, so the writer and the reader cannot drift apart.
_MARKER: Final = re.compile(
    "".join(
        _CLOCK if part in {"{start}", "{end}"} else re.escape(part)
        for part in _FIELD.split(MARKER)
    )
)


def clock(ms: int) -> str:
    """Milliseconds as the HH:MM:SS the transcript's own headers print."""
    seconds = max(int(ms), 0) // 1000
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def marker(start_ms: int, end_ms: int) -> str:
    """The Korean line that names exactly the minutes that are missing."""
    return MARKER.format(start=clock(start_ms), end=clock(end_ms))


def is_marker(text: str) -> bool:
    """Is this text one whole marker and nothing else?

    Deliberately not "does it contain one": text that merely contains the marker is
    already the damage — a sentence somebody glued it into. The render path's job is
    to keep the marker whole, not to guess where it ends inside a longer line.
    """
    return _MARKER.fullmatch(text.strip()) is not None
