"""Infer and preserve speaker names without making transcription depend on naming."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol, TypeAlias

SPEAKERS_PREFIX: Final = "- 화자:"
_LABEL: Final = re.compile(r"^화자\d+$")
_INTRODUCTION: Final = re.compile(
    r"(?:저는|제가|저희는|저는요)\s*(?:[가-힣]{2,12}(?:의|에서(?:\s*온)?|소속)?\s+)?"
    + r"([가-힣]{2,4})\s*(?:이라고\s*합니다|라고\s*합니다|입니다요|이고요|입니다)\.?"
)
_TITLE: Final = re.compile(
    r"([가-힣]{2,4})\s*(?:박사|연구원|교수|팀장|책임|선임|수석|과장|부장|차장|대리|대표|소장|원장|위원|사원|주임)(?:님)?\s*(?:이라고\s*합니다|라고\s*합니다|입니다)\.?"
)
_LEGEND_ENTRY: Final = re.compile(
    r"(화자\d+)=(.*?)(?:\s+\[(.*?)\])?(?=\s+·\s+화자\d+=|$)"
)
_SEPARATOR: Final = re.compile(r"\s*(?:,|;|·)\s*")
_STOPLIST: Final = frozenset(
    {"담당", "책임", "소속", "여기", "저희", "오늘", "이번", "발표", "회의", "참석", "대표로"}
)


class SentenceLike(Protocol):
    """The sentence fields naming needs, without coupling to transcript blocks."""

    @property
    def text(self) -> str: ...

    @property
    def start_ms(self) -> int | None: ...

    @property
    def speaker(self) -> str: ...


@dataclass(frozen=True, slots=True)
class SpeakerName:
    label: str
    name: str
    source: str


SpeakerMap: TypeAlias = tuple[SpeakerName, ...]


def _timestamp(start_ms: int | None) -> str:
    if start_ms is None:
        return "자기소개"
    seconds = start_ms // 1_000
    return f"자기소개 {seconds // 3_600:02d}:{seconds % 3_600 // 60:02d}:{seconds % 60:02d}"


def _candidates(text: str) -> tuple[str, ...]:
    found: list[tuple[int, str]] = []
    for pattern in (_INTRODUCTION, _TITLE):
        found.extend((match.start(), match.group(1)) for match in pattern.finditer(text))
    return tuple(name for _position, name in sorted(found) if name not in _STOPLIST)


def infer(
    sentences: Sequence[SentenceLike], *, known_names: Sequence[str] = ()
) -> SpeakerMap:
    """Name each observed speaker only from their own early self-introductions."""
    labels: list[str] = []
    evidence: dict[str, list[tuple[str, int | None]]] = {}
    seen_sentences: dict[str, int] = {}
    known = set(known_names)
    for sentence in sentences:
        label = sentence.speaker
        if not label:
            continue
        if label not in evidence:
            labels.append(label)
            evidence[label] = []
            seen_sentences[label] = 0
        if seen_sentences[label] < 12:
            evidence[label].extend((name, sentence.start_ms) for name in _candidates(sentence.text))
        seen_sentences[label] += 1

    selected: dict[str, tuple[str, int | None]] = {}
    for label in labels:
        choices = evidence[label]
        if choices:
            selected[label] = next((choice for choice in choices if choice[0] in known), choices[0])
    claims: dict[str, int] = {}
    for name, _start_ms in selected.values():
        claims[name] = claims.get(name, 0) + 1
    inferred: list[SpeakerName] = []
    for label in labels:
        choice = selected.get(label)
        if choice is None or claims[choice[0]] != 1:
            inferred.append(SpeakerName(label, "", ""))
            continue
        inferred.append(SpeakerName(label, choice[0], _timestamp(choice[1])))
    return tuple(inferred)


def parse_override(spec: str) -> SpeakerMap:
    """Read the compact owner setting while ignoring incomplete manual edits."""
    parsed: list[SpeakerName] = []
    seen: set[str] = set()
    for entry in _SEPARATOR.split(spec.strip()):
        label, separator, name = entry.partition("=")
        label, name = label.strip(), name.strip()
        if not separator or not _LABEL.fullmatch(label) or not name or label in seen:
            continue
        parsed.append(SpeakerName(label, name, "소유자"))
        seen.add(label)
    return tuple(parsed)


def parse_llm(items: Sequence[Mapping[str, object]]) -> SpeakerMap:
    """Accept only structurally valid suggestions from an untrusted model response."""
    parsed: list[SpeakerName] = []
    seen: set[str] = set()
    for item in items:
        label = item.get("label")
        raw_name = item.get("name")
        if not isinstance(label, str) or not _LABEL.fullmatch(label) or label in seen:
            continue
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        parsed.append(SpeakerName(label, name, "LLM"))
        seen.add(label)
    return tuple(parsed)


def _kind(speaker: SpeakerName) -> int:
    if speaker.source == "소유자":
        return 3
    if speaker.source.startswith("자기소개"):
        return 2
    if speaker.source == "LLM":
        return 1
    return 0


def merge(*maps: SpeakerMap) -> SpeakerMap:
    """Combine sources by trust, retaining an LLM disagreement as provenance."""
    grouped: dict[str, list[SpeakerName]] = {}
    for speaker_map in maps:
        for speaker in speaker_map:
            grouped.setdefault(speaker.label, []).append(speaker)
    merged: list[SpeakerName] = []
    for entries in grouped.values():
        named = [entry for entry in entries if entry.name]
        if not named:
            merged.append(max(entries, key=_kind))
            continue
        winner = max(named, key=_kind)
        if _kind(winner) == 2:
            llm = next((entry for entry in named if _kind(entry) == 1), None)
            if llm:
                suffix = "LLM" if llm.name == winner.name else f"LLM 제안: {llm.name}"
                winner = SpeakerName(winner.label, winner.name, f"{winner.source} · {suffix}")
        merged.append(winner)
    return tuple(merged)


def names(speakers: SpeakerMap) -> dict[str, str]:
    return {speaker.label: speaker.name for speaker in speakers if speaker.name}


def render_legend(speakers: SpeakerMap) -> str:
    if not speakers:
        return ""
    entries = [
        f"{speaker.label}={speaker.name} [{speaker.source}]"
        if speaker.name
        else f"{speaker.label}=미상"
        for speaker in speakers
    ]
    return f"{SPEAKERS_PREFIX} " + " · ".join(entries)


def parse_legend(header: str) -> SpeakerMap:
    for line in header.splitlines():
        if not line.startswith(SPEAKERS_PREFIX):
            continue
        entries: list[SpeakerName] = []
        for match in _LEGEND_ENTRY.finditer(line[len(SPEAKERS_PREFIX) :].strip()):
            label, name, source = (part.strip() if part else "" for part in match.groups())
            entries.append(SpeakerName(label, "" if name == "미상" else name, source))
        return tuple(entries)
    return ()
