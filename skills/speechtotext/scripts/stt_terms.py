"""바른 용어만 적힌 용어집으로 전사본을 고친다 — 틀리는 방식은 모델이 정한다.

1:1 용어집(틀린표기=올바른표기)은 소유자에게 **모델이 틀리는 방식을 미리 알 것**을 요구했다.
실측(2026-09-05): 실제 회의 전사본 9,625 어절은 그 1:1 용어집으로 이미 교정된 상태였는데도,
같은 용어집의 바른 용어 4개만으로 다시 훑으니 1:1 이 놓친 오인식이 7회 남아 있었다 —
열기환기·열기완기(→열교환기), 항정기술(→한전기술). 소유자가 그 표기들을 몰랐기 때문이다.

교정은 자모 단위로 판정한다. 한전기술/항정기술은 음절로는 한 글자가 통째로 다르지만 자모로는
ㄴ↔ㅇ 둘이고, 고신뢰성/고실내성은 ㄹㄴ 이 자리를 맞바꾼 전치다 — 그래서 전치를 한 번으로 세는
OSA 거리를 쓴다. 실제 오인식 네 쌍의 거리는 1~2 였고, 허용 오차를 자모 길이의 1/3·1/4·1/5 로
흔들어도 같은 코퍼스에서 결과가 같았다(무관한 낱말 교체 0건).

이 모듈이 하지 않는 것: 낱말 속 조각 치환. 소유자가 용어집에 남긴 경고가 그것이다 —
「성금=선금」 이 이 과제의 핵심어 「기성금」 을 「기선금」 으로 깨뜨렸다. 후보는 언제나 어절의
앞머리이고, 음절 수가 같아야 하며, 두 용어에 동시에 가까우면 아무것도 하지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, TypeAlias

#: 세 음절 미만은 이웃이 너무 많다 — 그런 교정은 명시 쌍(틀린표기=올바른표기)의 일이다.
MIN_SYLLABLES: Final = 3
#: 허용 오차 = 자모 길이 // 이 값. 실측에서 3·4·5 가 같은 결과를 냈고 실제 오인식은 1~2 였다.
CAP_DIVISOR: Final = 4

_BASE: Final = 0xAC00
_SYLLABLES: Final = 11172
_CHO: Final = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG: Final = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG: Final = ("", *"ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")
Kind: TypeAlias = Literal["exact", "fuzzy"]
#: 소유자가 직접 짝지은 치환. 근접 대조를 거치지 않았으므로 신뢰도가 다르다.
EXACT: Final[Kind] = "exact"
#: 바른 용어와의 자모 거리로 기계가 판정한 교정.
FUZZY: Final[Kind] = "fuzzy"


@dataclass(frozen=True, slots=True)
class Correction:
    """무엇이 무엇으로 바뀌었는지 — 카운트가 대답하지 못하는 것.

    문장이 아니라 **어절**만 담는다. 오탐을 찾는 데는 바뀐 낱말이면 충분하고, 문맥을
    함께 남기면 전사본 본문이 로그 파일로 새어 나간다.
    """

    before: str
    after: str
    term: str
    kind: Kind


_HANGUL_RUN: Final = re.compile(r"[가-힣]+")
_SPACING: Final = re.compile(r"(\s+)")


def jamo(text: str) -> tuple[str, ...]:
    """한글 음절을 자모로 편다 — 오인식은 음절이 아니라 자모 한둘에서 갈린다."""
    spelled: list[str] = []
    for char in text:
        code = ord(char) - _BASE
        if 0 <= code < _SYLLABLES:
            spelled.append(_CHO[code // 588])
            spelled.append(_JUNG[(code % 588) // 28])
            tail = _JONG[code % 28]
            if tail:
                spelled.append(tail)
        else:
            spelled.append(char)
    return tuple(spelled)


def distance(left: Sequence[str], right: Sequence[str]) -> int:
    """전치를 한 번으로 세는 편집 거리(OSA).

    고실내성→고신뢰성은 ㄹㄴ 이 자리를 맞바꾼 것이라, 전치를 두 번으로 세면 실제 오인식이
    임계 밖으로 밀려난다.
    """
    rows, cols = len(left), len(right)
    grid = [[0] * (cols + 1) for _ in range(rows + 1)]
    for row in range(rows + 1):
        grid[row][0] = row
    for col in range(cols + 1):
        grid[0][col] = col
    for row in range(1, rows + 1):
        for col in range(1, cols + 1):
            cost = 0 if left[row - 1] == right[col - 1] else 1
            best = min(
                grid[row - 1][col] + 1, grid[row][col - 1] + 1, grid[row - 1][col - 1] + cost
            )
            transposed = (
                row > 1
                and col > 1
                and left[row - 1] == right[col - 2]
                and left[row - 2] == right[col - 1]
            )
            grid[row][col] = min(best, grid[row - 2][col - 2] + 1) if transposed else best
    return grid[rows][cols]


def tolerance(spelled: Sequence[str]) -> int:
    """그 용어가 허용하는 자모 오차 — 짧은 말일수록 좁다."""
    return max(1, len(spelled) // CAP_DIVISOR)


def _repair(head: str, known: frozenset[str], profiles: Sequence[tuple[str, tuple[str, ...]]]) -> str | None:
    """이 어절 앞머리가 어느 바른 용어의 오인식인지 — 확신할 수 있을 때만 답한다."""
    for length in sorted({len(term) for term, _ in profiles}, reverse=True):
        if len(head) < length:
            continue
        candidate = head[:length]
        if candidate in known:
            return None
        spelled = jamo(candidate)
        near = {
            term
            for term, target in profiles
            if len(term) == length and distance(spelled, target) <= tolerance(target)
        }
        if len(near) == 1:
            return next(iter(near))
        if near:
            # 두 용어에 동시에 가깝다. 동전던지기로 고르면 그 문장은 조용히 틀린다.
            return None
    return None


def correct(text: str, terms: Sequence[str]) -> tuple[str, tuple[Correction, ...]]:
    """어절 앞머리가 어떤 바른 용어의 가까운 오인식이면 그 앞머리만 바꾼다.

    띄어쓰기와 구두점은 그대로 둔다 — 교정은 낱말을 바꾸는 일이지 문장을 다시 짜는 일이 아니다.
    """
    usable = tuple(dict.fromkeys(term for term in terms if len(term) >= MIN_SYLLABLES))
    if not usable:
        return text, ()
    known = frozenset(usable)
    profiles = tuple((term, jamo(term)) for term in usable)
    pieces = _SPACING.split(text)
    repaired: list[Correction] = []
    for index, piece in enumerate(pieces):
        run = _HANGUL_RUN.search(piece)
        if run is None:
            continue
        head = run.group(0)
        fixed = _repair(head, known, profiles)
        if fixed is None:
            continue
        pieces[index] = piece[: run.start()] + fixed + head[len(fixed) :] + piece[run.end() :]
        repaired.append(Correction(before=head[: len(fixed)], after=fixed, term=fixed, kind=FUZZY))
    return "".join(pieces), tuple(repaired)
