from __future__ import annotations

# pyright: reportCallInDefaultInitializer=false

import re
from dataclasses import dataclass
from typing import Final, Literal, TypeAlias

Destination: TypeAlias = Literal["obsidian", "drive", "local", "none", "gated"]


@dataclass(frozen=True, slots=True)
class SaveRoute:
    destinations: tuple[Destination, ...]
    reason: str
    clarify: bool


_DESTINATION_TERMS: Final[tuple[tuple[Destination, tuple[str, ...]], ...]] = (
    ("obsidian", ("옵시디언", "obsidian")),
    ("drive", ("구글 드라이브", "google drive", "드라이브", "drive")),
    ("local", ("로컬", "local")),
)
_DESTINATION_ORDER: Final[tuple[Destination, ...]] = ("obsidian", "drive", "local")
_BOTH_CUES: Final = ("둘 다", "both")
_PERSONAL_NOTE_CUES: Final = ("개인노트 저장", "개인 노트", "내 노트에")
_SAVE_CUES: Final = (
    "저장",
    "보관",
    "올려",
    "올리",
    "업로드",
    "남겨",
    "기록",
    "save",
    "store",
    "upload",
    "keep",
    "archive",
)
_GLOBAL_NO_SAVE_CUES: Final = (
    "저장하지 마",
    "저장하지마",
    "보관하지 마",
    "보관하지마",
    "don't save",
    "do not save",
    "no saving",
)
_NEGATION_PREFIX_CUES: Final = ("don't ", "do not ", "never ", "not ")
_NEGATION_SUFFIX_CUES: Final = (
    "올리지 마",
    "올리지마",
    "업로드하지 마",
    "업로드하지마",
    "저장하지 마",
    "저장하지마",
    "보관하지 마",
    "보관하지마",
    "말고",
    "제외",
    "하지 마",
    "하지마",
)
_AMBIGUITY_CUES: Final = (
    "어딘가",
    "중 하나",
    "할까",
    "할까요",
    "해도 될까",
    " or ",
)
_NEGATION_WINDOW: Final = 16


def _contains_any(text: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text for cue in cues)


def _term_spans(text: str, term: str) -> tuple[tuple[int, int], ...]:
    pattern = rf"\b{re.escape(term)}\b" if term.isascii() else re.escape(term)
    return tuple((match.start(), match.end()) for match in re.finditer(pattern, text))


def _is_negated(text: str, start: int, end: int) -> bool:
    left = text[max(0, start - _NEGATION_WINDOW) : start]
    right = text[end : end + _NEGATION_WINDOW]
    return _contains_any(left, _NEGATION_PREFIX_CUES) or _contains_any(
        right,
        _NEGATION_SUFFIX_CUES,
    )


def classify_save_request(
    request: str,
    *,
    has_file_artifact: bool,
    sensitivity: frozenset[str] = frozenset(),
) -> SaveRoute:
    if sensitivity:
        return SaveRoute(("gated",), "sensitive-gated", False)

    text = request.casefold()
    save_intent = _contains_any(text, _SAVE_CUES)
    personal_note = _contains_any(text, _PERSONAL_NOTE_CUES)
    destination_mentions: dict[Destination, tuple[bool, ...]] = {}
    for destination, terms in _DESTINATION_TERMS:
        negations = tuple(
            _is_negated(text, start, end)
            for term in terms
            for start, end in _term_spans(text, term)
        )
        if negations:
            destination_mentions[destination] = negations

    if _contains_any(text, _GLOBAL_NO_SAVE_CUES) and not destination_mentions:
        return SaveRoute(("none",), "no-save-intent", False)
    if save_intent and (_contains_any(text, _AMBIGUITY_CUES) or "?" in text):
        return SaveRoute((), "ambiguous", True)

    positive: set[Destination] = set()
    denied: set[Destination] = set()
    conflicting = False
    for destination, negations in destination_mentions.items():
        if all(negations):
            denied.add(destination)
        elif any(negations):
            conflicting = True
        elif save_intent:
            positive.add(destination)

    if _contains_any(text, _BOTH_CUES) and save_intent:
        positive.update(("obsidian", "drive"))
    positive.difference_update(denied)
    if conflicting:
        return SaveRoute((), "ambiguous", True)
    if positive:
        if personal_note and "obsidian" not in denied:
            positive.add("obsidian")
        destinations: tuple[Destination, ...] = tuple(
            destination for destination in _DESTINATION_ORDER if destination in positive
        )
        return SaveRoute(destinations, "explicit-destination", False)
    if denied:
        return SaveRoute((), "ambiguous", True)
    if personal_note:
        return SaveRoute(("obsidian",), "personal-note", False)
    if has_file_artifact:
        return SaveRoute(("drive",), "default-drive", False)
    if not save_intent:
        return SaveRoute(("none",), "no-save-intent", False)
    return SaveRoute((), "ambiguous", True)
