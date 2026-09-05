"""산출 문서의 용어를 바로잡는 **단 하나의 엔진** — 어느 스킬에도 속하지 않는다.

교정이 일어나는 자리는 음성→전사본이 아니라 전사본→산출 문서다(소유자 결정 2026-09-05).
전사본은 증거라서 되돌릴 수 없는 치환을 새기지 않는다 — 실측으로 「성금=선금」이 이 과제의
핵심어 「기성금」을 「기선금」으로 깨뜨린 적이 있고, 원문에 그렇게 새겨졌다면 원래 낱말은
어디에도 남지 않는다. 회의록·lifelog 노트처럼 사람이 읽을 문서를 만들 때 그 문서 종류의
참고 문서로 고치면, 잘못 고친 교정은 참고 문서를 고쳐 문서를 다시 만들면 회복된다.

세 스킬(speechtotext·meeting·plaud)이 같은 판정을 공유해야 하므로 여기는 automation 이다.
사본을 만들면 한쪽만 고쳐지고, 그때부터 같은 낱말이 문서마다 달라진다.

교정은 자모 단위로 판정한다. 한전기술/항정기술은 음절로는 한 글자가 통째로 다르지만 자모로는
ㄴ↔ㅇ 둘이고, 고신뢰성/고실내성은 ㄹㄴ 이 자리를 맞바꾼 전치다 — 그래서 전치를 한 번으로 세는
OSA 거리를 쓴다. 후보는 언제나 **어절의 앞머리**이고, 음절 수가 같아야 하며, 두 용어에 동시에
가까우면 아무것도 하지 않는다.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, TypeAlias

#: 표의 머리글 — Drive 에서 열었을 때 어느 칸이 무엇인지 보이라고 두지만 항목은 아니다.
GLOSSARY_HEADER: Final = ("틀린표기", "올바른표기")
#: 세 음절 미만은 이웃이 너무 많다 — 그런 교정은 명시 쌍(틀린표기,올바른표기)의 일이다.
MIN_SYLLABLES: Final = 3
#: 허용 오차 = 자모 길이 // 이 값. 실측에서 3·4·5 가 같은 결과를 냈고 실제 오인식은 1~2 였다.
CAP_DIVISOR: Final = 4

_BASE: Final = 0xAC00
_SYLLABLES: Final = 11172
_CHO: Final = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG: Final = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG: Final = ("", *"ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")

Glossary: TypeAlias = Sequence[tuple[str, str]]
Kind: TypeAlias = Literal["exact", "fuzzy"]
#: 소유자가 직접 짝지은 치환. 근접 대조를 거치지 않았으므로 신뢰도가 다르다.
EXACT: Final[Kind] = "exact"
#: 바른 용어와의 자모 거리로 기계가 판정한 교정.
FUZZY: Final[Kind] = "fuzzy"

_HANGUL_RUN: Final = re.compile(r"[가-힣]+")
_SPACING: Final = re.compile(r"(\s+)")


@dataclass(frozen=True, slots=True)
class Correction:
    """무엇이 무엇으로 바뀌었는지 — 카운트가 대답하지 못하는 것.

    문장이 아니라 **어절**만 담는다. 오탐을 찾는 데는 바뀐 낱말이면 충분하고, 문맥을
    함께 남기면 문서 본문이 로그 파일로 새어 나간다.
    """

    before: str
    after: str
    term: str
    kind: Kind


def parse_glossary(content: str) -> tuple[tuple[str, str], ...]:
    """`틀린표기,올바른표기` 한 줄에 한 항목 — 텍스트가 어디서 왔든 읽는 법은 하나다.

    구분자는 둘이고 뜻은 하나다. 참고 문서는 표이므로 행은 `,` 로 나뉘고 Drive 가 그것을
    Sheets 로 열어 준다; 소유자가 이미 써 둔 `=` 형식도 계속 읽는다. 한 행에 둘 다 있으면
    `=` 가 이기므로 값에 쉼표를 넣을 수 있다.

    한 칸짜리 행은 **바른 용어**다. 틀리는 방식은 모델이 정하지 소유자가 아니므로, 그것까지
    적게 하면 소유자가 모르는 오인식은 영영 고쳐지지 않는다.
    """
    pairs: list[tuple[str, str]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            wrong, _, right = stripped.partition("=")
        else:
            # Sheets 는 쉼표를 품은 칸을 따옴표로 감싼다 — 손으로 자르면 그 행이 깨진다.
            row = next(csv.reader([stripped]), [])
            if not row:
                continue
            second = row[1].strip() if len(row) > 1 else ""
            wrong, right = row[0], second or row[0]
        wrong, right = wrong.strip(), right.strip()
        if not wrong or not right or wrong in GLOSSARY_HEADER:
            continue
        pairs.append((wrong, right))
    return tuple(pairs)


def format_glossary(pairs: Glossary) -> str:
    """참고 문서를 다시 쓴다(캐시) — 한 칸 행은 한 칸 행 그대로 남긴다."""
    rows: list[str] = []
    for wrong, right in pairs:
        buffer: list[str] = []
        writer = csv.writer(_Sink(buffer), lineterminator="")
        writer.writerow([wrong] if wrong == right else [wrong, right])
        rows.append(buffer[0])
    return "".join(row + "\n" for row in rows)


class _Sink:
    """csv.writer 는 write() 만 부른다 — 임시 파일 없이 한 행을 받아 낸다."""

    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def write(self, text: str) -> int:
        self._sink.append(text)
        return len(text)


def prompt_hint(glossary: Glossary) -> str:
    """바른 표기만 모아 만든 힌트 — 인식 **전에** 주는 조건이지 사후 교정이 아니다."""
    names: list[str] = []
    for _wrong, right in glossary:
        if right not in names:
            names.append(right)
    return f"고유명사: {', '.join(names)}" if names else ""


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


def _repair(
    head: str, known: frozenset[str], profiles: Sequence[tuple[str, tuple[str, ...]]]
) -> str | None:
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


def apply(text: str, glossary: Glossary) -> tuple[str, tuple[Correction, ...]]:
    """소유자가 지목한 치환 먼저, 그다음 바른 용어로 남은 오인식을 고친다.

    두 칸 행은 소유자가 직접 짝지은 교정이라 문자열 그대로 바꾸고, 바른 표기 전부(한 칸 행과
    두 칸 행의 오른쪽)는 근접 대조의 목표가 된다 — 그래서 이미 쓰인 1:1 참고 문서도 고쳐 적지
    않고 그대로 더 많이 잡는다(실측: 그 파일의 바른 용어만으로 놓쳤던 오인식 7회를 더 고쳤다).
    """
    substituted = text
    exact: list[Correction] = []
    for wrong, right in glossary:
        if wrong == right:
            continue
        found = substituted.count(wrong)
        if found:
            substituted = substituted.replace(wrong, right)
            exact.extend(
                Correction(before=wrong, after=right, term=right, kind=EXACT) for _ in range(found)
            )
    substituted, repaired = correct(substituted, tuple(right for _wrong, right in glossary))
    return substituted, (*exact, *repaired)
