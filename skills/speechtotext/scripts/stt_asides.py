"""문장 안에 끼어든 짧은 맞장구 — 문서 문법의 한 요소(`[화자2: 네]`).

화자 분리는 한 사람이 말하는 도중에 다른 사람이 짧게 "네" 하는 순간도 화자 경계로 본다.
예전에는 그 경계마다 문장을 잘랐고, 잘린 조각마다 블록 헤더가 붙어 한 문장이 세 블록이
됐다(2026-09-22 실측: 13분 녹음이 71블록, 그중 32블록이 `화자0` 조각, 한 줄 5자 이하가
112줄 중 36줄). 확실한 근거가 있는 짧은 끼어듦은 이제 **그 문장 안에** 표시한다.

낱말은 바꾸지 않는다: 표식은 원래 낱말을 대괄호로 감쌀 뿐이고, `extract` 가 표식을 걷어
내면 원래 문장이 바이트 그대로 돌아온다. 헤더(`[00:00:27] 화자1`)는 줄 첫머리의 시각으로
시작하므로 `[화자2: …]` 로 시작하는 줄과 섞이지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

#: `[화자2: 네]` 또는 이름이 알려졌으면 `[화자2 · 김민수: 네]`. 이름은 되읽을 때 버린다 —
#: 이름의 출처는 머리말 범례이고, 렌더할 때마다 거기서 다시 붙는다.
MARKER: Final = re.compile(r"\[(화자[1-9]\d*)(?: · [^\[\]:]+)?: ([^\[\]]+)\]")
#: 표식을 깨뜨리는 글자. 말에 이것이 들어 있으면 끼어듦으로 표시하지 않는다.
RESERVED: Final = frozenset("[]")
_NAME_RESERVED: Final = frozenset("[]:")


@dataclass(frozen=True, slots=True)
class Aside:
    """문장 텍스트 안의 반열린 문자 범위와 그 말을 한 화자 라벨."""

    start_char: int
    end_char: int
    speaker: str


def display(text: str, asides: Sequence[Aside], names: Mapping[str, str]) -> str:
    """문장 한 줄 — 끼어든 말만 표식으로 감싼다. 끼어듦이 없으면 원문 그대로다."""
    parts: list[str] = []
    cursor = 0
    for aside in sorted(asides, key=lambda item: item.start_char):
        name = names.get(aside.speaker, "")
        label = aside.speaker + (f" · {name}" if name and not _NAME_RESERVED & set(name) else "")
        parts += [text[cursor:aside.start_char], f"[{label}: {text[aside.start_char:aside.end_char]}]"]
        cursor = aside.end_char
    parts.append(text[cursor:])
    return "".join(parts)


def extract(line: str) -> tuple[str, tuple[Aside, ...]]:
    """표식을 걷어 낸 원래 말과, 그 말 안에서 끼어든 범위."""
    plain: list[str] = []
    found: list[Aside] = []
    cursor = 0
    length = 0
    for match in MARKER.finditer(line):
        before = line[cursor:match.start()]
        plain.append(before)
        length += len(before)
        words = match.group(2)
        found.append(Aside(length, length + len(words), match.group(1)))
        plain.append(words)
        length += len(words)
        cursor = match.end()
    plain.append(line[cursor:])
    return "".join(plain), tuple(found)


def within(asides: Sequence[Aside], begin: int, finish: int) -> tuple[Aside, ...]:
    """[begin, finish) 조각에 걸친 끼어듦을 그 조각의 0점으로 옮긴다(걸친 만큼만)."""
    made: list[Aside] = []
    for aside in asides:
        start, end = max(begin, aside.start_char), min(finish, aside.end_char)
        if start < end:
            made.append(Aside(start - begin, end - begin, aside.speaker))
    return tuple(made)
