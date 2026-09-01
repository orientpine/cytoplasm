from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final

CODE_LEN: Final = 4
SEQ_LEN: Final = 4
ID_LEN: Final = CODE_LEN + 2 + SEQ_LEN
SEQ_MAX: Final = 10**SEQ_LEN - 1
_LEADS: Final = (
    "G", "G", "N", "D", "D", "R", "M", "B", "B", "S", "S", "O", "J", "J", "C", "K", "T", "P", "H"
)
_ID_PATTERN: Final = re.compile(rf"[A-Z]{{{CODE_LEN}}}\d{{{2 + SEQ_LEN}}}\Z")


class ActionIdError(ValueError):
    """Raised when an action-item identifier cannot be used."""


@dataclass(frozen=True, slots=True)
class ActionId:
    code: str
    year: int
    seq: int

    def __post_init__(self) -> None:
        if not re.fullmatch(rf"[A-Z]{{{CODE_LEN}}}", self.code):
            raise ActionIdError(f"code must be {CODE_LEN} uppercase ASCII letters")
        if not 0 <= self.year <= 99:
            raise ActionIdError("year must be between 0 and 99")
        if not 1 <= self.seq <= SEQ_MAX:
            raise ActionIdError(f"sequence must be between 1 and {SEQ_MAX}")

    @property
    def text(self) -> str:
        return f"{self.code}{self.year:02d}{self.seq:0{SEQ_LEN}d}"


def parse_id(text: str) -> ActionId:
    try:
        matched = _ID_PATTERN.fullmatch(text)
    except TypeError as error:
        raise ActionIdError("invalid action-item identifier") from error
    if not matched:
        raise ActionIdError("invalid action-item identifier")
    return ActionId(
        text[:CODE_LEN], int(text[CODE_LEN : CODE_LEN + 2]), int(text[CODE_LEN + 2 :])
    )


def _letters(project: str) -> list[str]:
    letters: list[str] = []
    for character in unicodedata.normalize("NFC", project):
        if "A" <= character <= "Z" or "a" <= character <= "z":
            letters.append(character.upper())
        elif "\uac00" <= character <= "\ud7a3":
            letters.append(_LEADS[(ord(character) - 0xAC00) // 588])
    return letters


def candidate_code(project: str) -> str:
    return "".join(_letters(project)[:CODE_LEN]).ljust(CODE_LEN, "X")


def alternates(project: str) -> Iterator[str]:
    letters = _letters(project)
    candidate = "".join(letters[:CODE_LEN]).ljust(CODE_LEN, "X")
    seen: set[str] = set()
    for last in [candidate[-1], *letters[CODE_LEN:], *"ABCDEFGHIJKLMNOPQRSTUVWXYZ"]:
        code = f"{candidate[:CODE_LEN - 1]}{last}"
        if code not in seen:
            seen.add(code)
            yield code


def resolve_code(project: str, registry: Mapping[str, str]) -> tuple[str, dict[str, str]]:
    copied = dict(registry)
    for code, assigned in registry.items():
        if assigned == project:
            return code, copied
    for code in alternates(project):
        if code not in registry:
            copied[code] = project
            return code, copied
    raise ActionIdError("no project code remains")


def next_id(code: str, year: int, existing: Iterable[str]) -> ActionId:
    _ = ActionId(code, year, 1)
    highest = 0
    for text in existing:
        try:
            action_id = parse_id(text)
        except ActionIdError:
            continue
        if (action_id.code, action_id.year) == (code, year):
            highest = max(highest, action_id.seq)
    if highest >= SEQ_MAX:
        raise ActionIdError("sequence is exhausted")
    return ActionId(code, year, highest + 1)


def load_registry(text: str) -> dict[str, str]:
    registry: dict[str, str] = {}
    try:
        for row in csv.reader(io.StringIO(text)):
            if len(row) < 2 or not row[0]:
                continue
            if row[0] == "code" and row[1] == "project":
                continue
            registry[row[0]] = row[1]
    except (csv.Error, TypeError):
        pass
    return registry


def dump_registry(registry: Mapping[str, str]) -> str:
    rows = [["code", "project"], *([code, project] for code, project in sorted(registry.items()))]
    stream = io.StringIO()
    csv.writer(stream, lineterminator="\n").writerows(rows)
    return stream.getvalue()
