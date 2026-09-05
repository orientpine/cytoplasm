"""The transcript body as blocks of one-sentence-per-line, with the timings kept.

The owner read a 94-minute transcript as 140 paragraph lines, the longest 1,137
characters, and could not find anything in it. A sentence is the unit a person
reads, so a sentence is a line here. whisper.cpp already reports when each token
was spoken; the old path joined the segments into one string and dropped those
timings, which is why a line could never say when it was said. render() writes
this grammar and parse() reads it back, so a transcript already on disk (space-
joined paragraphs) and a freshly tidied one settle on the same document.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

import stt_gap

# A word whose segment carried no offsets: the timing is absent, not zero.
UNKNOWN_MS: Final = -1
UNKNOWN_CLOCK: Final = "--:--:--"
NO_NAMES: Final[Mapping[str, str]] = MappingProxyType({})

_SENTENCE_END: Final = re.compile(r"(?<=[.!?\u2026])\s+")
_WHITESPACE: Final = re.compile(r"\s+")
_SPECIAL_TOKEN: Final = re.compile(r"^\[_.*\]$")
_BLANK_LINE: Final = re.compile(r"\n[ \t]*\n")
HEADER: Final = re.compile(
    r"^\[(\d{2}|--):(\d{2}|--):(\d{2}|--)\](?:\s+(화자\d+))?(?:\s+·\s+(.+?))?\s*$"
)

# A block closes once it is both long enough to be one and short enough to stay
# one — whichever bound is reached last. Only used when nobody is attributed.
MIN_SENTENCES: Final = 4
MIN_CHARS: Final = 180


@dataclass(frozen=True, slots=True)
class TimedWord:
    """One whisper token — or a whole segment, when its tokens are unusable."""
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class TimedSentence:
    """A sentence and what is known about it: when it was said, and by whom."""
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    speaker: str = ""


@dataclass(frozen=True, slots=True)
class Block:
    """Consecutive sentences that share a speaker — the unit under one header."""
    speaker: str
    start_ms: int | None
    sentences: tuple[str, ...]


def hhmmss(ms: int) -> str:
    """Milliseconds as the clock the header prints."""
    seconds = max(ms, 0) // 1000
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def split_sentences(text: str) -> tuple[str, ...]:
    """Split on sentence enders only — a break needs punctuation AND whitespace,
    so `1.2%` and `27년 10월` stay whole."""
    normalized = normalize(text)
    if not normalized:
        return ()
    return tuple(part for part in _SENTENCE_END.split(normalized) if part)


def _offsets(payload: Mapping[str, object]) -> tuple[int, int]:
    offsets = payload.get("offsets")
    if not isinstance(offsets, dict):
        return UNKNOWN_MS, UNKNOWN_MS
    start, end = offsets.get("from"), offsets.get("to")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        return int(start), int(end)
    return UNKNOWN_MS, UNKNOWN_MS


def words_from_whisper(segments: object) -> tuple[TimedWord, ...]:
    """whisper.cpp `-ojf` segments -> the spoken words, each with its own span.

    Special tokens (`[_BEG_]`, `[_TT_520]`) are decoder bookkeeping, never speech.
    A segment with no usable token still said something, so its text survives as
    one word carrying the segment's own offsets.
    """
    words: list[TimedWord] = []
    for segment in segments if isinstance(segments, list) else []:
        if not isinstance(segment, dict):
            continue
        seg_start, seg_end = _offsets(segment)
        raw = segment.get("tokens")
        found: list[TimedWord] = []
        for token in raw if isinstance(raw, list) else []:
            if not isinstance(token, dict):
                continue
            text = str(token.get("text", ""))
            if not text.strip() or _SPECIAL_TOKEN.match(text.strip()):
                continue
            start, end = _offsets(token)
            found.append(TimedWord(
                text,
                seg_start if start == UNKNOWN_MS else start,
                seg_end if end == UNKNOWN_MS else end,
            ))
        if not found and str(segment.get("text", "")).strip():
            found = [TimedWord(str(segment["text"]), seg_start, seg_end)]
        words.extend(found)
    return tuple(words)


def sentences_from_words(words: Sequence[TimedWord]) -> tuple[TimedSentence, ...]:
    """Concatenate the words back into speech, then cut it into sentences.

    A gap marker is not speech — it is the transcript saying which minutes are
    missing — so it is never concatenated with the words around it. Measured on the
    2026-09-04 recording: the window before the gap ended without punctuation, the
    marker was appended to it, and the sentence that came out spanned 916 seconds;
    the speaker splitter then cut it into eleven pieces and the owner read `[전사`.
    """
    made: list[TimedSentence] = []
    spoken: list[TimedWord] = []
    for word in words:
        if not stt_gap.is_marker(word.text):
            spoken.append(word)
            continue
        made.extend(_spoken_sentences(spoken))
        spoken = []
        made.append(_marker_sentence(word))
    made.extend(_spoken_sentences(spoken))
    return tuple(made)


def _marker_sentence(word: TimedWord) -> TimedSentence:
    """One gap marker as one sentence, keeping the span of the window it stands for."""
    return TimedSentence(
        word.text.strip(),
        None if word.start_ms == UNKNOWN_MS else word.start_ms,
        None if word.end_ms == UNKNOWN_MS else word.end_ms,
    )


def _spoken_sentences(words: Sequence[TimedWord]) -> tuple[TimedSentence, ...]:
    """A run of spoken words, joined back together and cut on sentence enders."""
    pieces: list[str] = []
    spans: list[tuple[int, int, TimedWord]] = []
    cursor = 0
    for word in words:
        piece = unicodedata.normalize("NFC", word.text)
        if not piece:
            continue
        # Two words only need a space when neither side already brought one.
        if pieces and not pieces[-1][-1].isspace() and not piece[0].isspace():
            pieces.append(" ")
            cursor += 1
        pieces.append(piece)
        spans.append((cursor, cursor + len(piece), word))
        cursor += len(piece)
    text = "".join(pieces)

    starts, ends = [0], []
    for match in _SENTENCE_END.finditer(text):
        ends.append(match.start())
        starts.append(match.end())
    ends.append(len(text))
    sentences: list[TimedSentence] = []
    for begin, finish in zip(starts, ends, strict=True):
        body = normalize(text[begin:finish])
        if not body:
            continue
        covering = [word for start, end, word in spans if start < finish and end > begin]
        known_starts = [w.start_ms for w in covering if w.start_ms != UNKNOWN_MS]
        known_ends = [w.end_ms for w in covering if w.end_ms != UNKNOWN_MS]
        sentences.append(TimedSentence(
            body,
            min(known_starts) if known_starts else None,
            max(known_ends) if known_ends else None,
        ))
    return tuple(sentences)


def sentences_from_text(text: str) -> tuple[TimedSentence, ...]:
    """Sentences from plain text — the API backend reports no timings at all."""
    return tuple(TimedSentence(text=sentence) for sentence in split_sentences(text))


def group(sentences: Sequence[TimedSentence]) -> tuple[Block, ...]:
    """Blocks: a run of one speaker, or — with nobody attributed — a paragraph.

    A gap marker gets a block of its own, with the timestamp of the minutes it stands
    for. It is the one line in the document that is about the document, so burying it
    mid-paragraph is exactly where the owner would not look for it.
    """
    attributed = any(sentence.speaker for sentence in sentences)
    blocks: list[Block] = []
    chunk: list[TimedSentence] = []
    size = 0
    for sentence in sentences:
        if stt_gap.is_marker(sentence.text):
            if chunk:
                blocks.append(_block(chunk))
                chunk, size = [], 0
            blocks.append(_block((sentence,)))
            continue
        if chunk and attributed and sentence.speaker != chunk[0].speaker:
            blocks.append(_block(chunk))
            chunk, size = [], 0
        chunk.append(sentence)
        size += len(sentence.text) + 1
        if not attributed and len(chunk) >= MIN_SENTENCES and size >= MIN_CHARS:
            blocks.append(_block(chunk))
            chunk, size = [], 0
    if chunk:
        blocks.append(_block(chunk))
    return tuple(blocks)


def _block(chunk: Sequence[TimedSentence]) -> Block:
    texts = tuple(sentence.text for sentence in chunk)
    return Block(chunk[0].speaker, chunk[0].start_ms, texts)


def header_line(block: Block, names: Mapping[str, str] = NO_NAMES) -> str:
    """`[HH:MM:SS] 화자N · 이름` — empty when neither timing nor speaker is known."""
    if block.start_ms is None and not block.speaker:
        return ""
    clock = UNKNOWN_CLOCK if block.start_ms is None else hhmmss(block.start_ms)
    if not block.speaker:
        return f"[{clock}]"
    name = names.get(block.speaker, "")
    return f"[{clock}] {block.speaker}" + (f" · {name}" if name else "")


def render(blocks: Sequence[Block], names: Mapping[str, str] = NO_NAMES) -> str:
    """The document body: blocks split by a blank line, one sentence per line."""
    rendered: list[str] = []
    for block in blocks:
        head = header_line(block, names)
        lines = ([head] if head else []) + list(block.sentences)
        if lines:
            rendered.append("\n".join(lines))
    return "\n\n".join(rendered)


def parse(body: str) -> tuple[TimedSentence, ...]:
    """Read a body back — a tidied one, or the space-joined paragraphs of 2026-08.

    Only a block's first sentence keeps the header's timestamp: the header says
    when the block started, and timing the rest would be a claim the document
    never made."""
    sentences: list[TimedSentence] = []
    for chunk in _BLANK_LINE.split(body):
        lines = [line for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        speaker, start_ms = "", None
        matched = HEADER.match(lines[0].strip())
        if matched is not None:
            speaker = matched.group(4) or ""
            start_ms = _clock_ms(matched.group(1), matched.group(2), matched.group(3))
            lines = lines[1:]
        first = True
        for line in lines:
            for text in split_sentences(line):
                when = start_ms if first else None
                sentences.append(TimedSentence(text, when, None, speaker))
                first = False
    return tuple(sentences)


def _clock_ms(hours: str, minutes: str, seconds: str) -> int | None:
    if not (hours.isdigit() and minutes.isdigit() and seconds.isdigit()):
        return None
    return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000
