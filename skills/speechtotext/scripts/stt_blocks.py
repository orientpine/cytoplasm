"""The transcript body as blocks of one-sentence-per-line, with the timings kept.

The owner read a 94-minute transcript as 140 paragraph lines, the longest 1,137
characters, and could not find anything in it. A sentence is the unit a person
reads, so a sentence is a line here. whisper.cpp already reports when each token
was spoken; the old path joined the segments into one string and dropped those
timings, which is why a line could never say when it was said. render() writes
this grammar and parse() reads it back, so a transcript already on disk (space-
joined paragraphs) and a freshly tidied one settle on the same document.

A short, well-evidenced interjection inside another speaker's sentence stays on
that sentence's line as `[화자2: 네]` (stt_asides) instead of opening a block.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Final

import stt_asides
import stt_gap
from stt_asides import Aside as Aside
from stt_attribute import SpeakerTag
from stt_sentence import UNKNOWN_MS as UNKNOWN_MS
from stt_sentence import SentenceWord as SentenceWord
from stt_sentence import TimedSentence as _Sentence
from stt_sentence import TimedWord as TimedWord
from stt_sentence import normalize as normalize
from stt_sentence import split_sentences as split_sentences
from stt_sentence import spoken_sentences as _spoken_sentences

UNKNOWN_CLOCK: Final = "--:--:--"
NO_NAMES: Final[Mapping[str, str]] = MappingProxyType({})

_SPECIAL_TOKEN: Final = re.compile(r"^\[_.*\]$")
_BLANK_LINE: Final = re.compile(r"(<details>.*?</details>)|\n[ \t]*\n", re.DOTALL)
HEADER: Final = re.compile(
    r"^\[(\d{2}|--):(\d{2}|--):(\d{2}|--)\](?:\s+(화자\d+))?(?:\s+·\s+(.+?))?\s*$"
)

# A block closes once it is both long enough to be one and short enough to stay
# one — whichever bound is reached last. This bound holds whether or not anybody is
# attributed: a 179-sentence lifelog whose every word landed on one speaker came back
# as a single block (2026-09-07 실측), which is the same wall the sentence-per-line
# work removed. A speaker label is not a reason to stop paragraphing.
MIN_SENTENCES: Final = 4
MIN_CHARS: Final = 180


@dataclass(frozen=True, slots=True)
class TimedSentence(_Sentence):
    """토큰 좌표 문장에 문서가 직렬화할 화자 판정과 문장 안의 끼어듦을 덧붙인다."""
    attribution: SpeakerTag | None = None
    asides: tuple[Aside, ...] = ()


@dataclass(frozen=True, slots=True)
class Block:
    """Consecutive sentences that share a speaker — the unit under one header."""
    speaker: str
    start_ms: int | None
    sentences: tuple[str, ...]
    folded: str = ""
    attribution: SpeakerTag | None = None
    # Parallel to `sentences`, and empty when no sentence carries an aside.
    asides: tuple[tuple[Aside, ...], ...] = ()


def hhmmss(ms: int) -> str:
    """Milliseconds as the clock the header prints."""
    seconds = max(ms, 0) // 1000
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


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
                timing_source="segment" if start == UNKNOWN_MS else "token",
            ))
        if not found and str(segment.get("text", "")).strip():
            found = [TimedWord(str(segment["text"]), seg_start, seg_end,
                               str(segment.get("folded", "")), "segment")]
        # A segment always begins a new word. whisper marks that with a leading space on
        # the segment's first token (measured 36/36 on the node's Korean output,
        # docs/qa/PLQ1/summary.md); a build that omits it would glue a sentence-ending
        # period to the next speaker's first syllable. Gap markers are the document
        # talking about itself, not speech, so they are left exactly as they are.
        if words and found and not found[0].text[:1].isspace():
            if not stt_gap.is_marker(found[0].text):
                found[0] = replace(found[0], text=" " + found[0].text)
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
    made: list[_Sentence] = []
    spoken: list[TimedWord] = []
    source_offset = 0
    for index, word in enumerate(words):
        if not stt_gap.is_marker(word.text):
            spoken.append(word)
            continue
        made.extend(_spoken_sentences(spoken, source_offset))
        spoken = []
        source_offset = index + 1
        made.append(_marker_sentence(word))
    made.extend(_spoken_sentences(spoken, source_offset))
    return tuple(TimedSentence(s.text, s.start_ms, s.end_ms, s.speaker, s.folded, s.words)
                 for s in made)


def _marker_sentence(word: TimedWord) -> TimedSentence:
    """One gap marker as one sentence, keeping the span of the window it stands for."""
    return TimedSentence(
        word.text.strip(),
        None if word.start_ms == UNKNOWN_MS else word.start_ms,
        None if word.end_ms == UNKNOWN_MS else word.end_ms,
        folded=word.folded,
    )


def sentences_from_text(text: str) -> tuple[TimedSentence, ...]:
    """Sentences from plain text — the API backend reports no timings at all."""
    return tuple(TimedSentence(text=sentence) for sentence in split_sentences(text))


def group(sentences: Sequence[TimedSentence]) -> tuple[Block, ...]:
    """Blocks: a run of one speaker, cut again wherever that run outgrows a paragraph.

    A gap marker gets a block of its own, with the timestamp of the minutes it stands
    for. It is the one line in the document that is about the document, so burying it
    mid-paragraph is exactly where the owner would not look for it.
    """
    attributed = any(_group_tag(sentence) for sentence in sentences)
    blocks: list[Block] = []
    chunk: list[TimedSentence] = []
    size = 0
    for sentence in sentences:
        if stt_gap.is_marker(sentence.text) or sentence.folded:
            if chunk:
                blocks.append(_block(chunk))
                chunk, size = [], 0
            blocks.append(_block((sentence,)))
            continue
        if chunk and attributed and _group_tag(sentence) != _group_tag(chunk[0]):
            blocks.append(_block(chunk))
            chunk, size = [], 0
        chunk.append(sentence)
        size += len(sentence.text) + 1
        if len(chunk) >= MIN_SENTENCES and size >= MIN_CHARS:
            blocks.append(_block(chunk))
            chunk, size = [], 0
    if chunk:
        blocks.append(_block(chunk))
    return tuple(blocks)


def _group_tag(sentence: TimedSentence) -> SpeakerTag | None:
    return sentence.attribution or (SpeakerTag("SPEAKER", (sentence.speaker,)) if sentence.speaker else None)


def _block(chunk: Sequence[TimedSentence]) -> Block:
    kept = tuple(sentence for sentence in chunk if sentence.text)
    texts = tuple(sentence.text for sentence in kept)
    first = chunk[0]
    if stt_gap.is_marker(first.text):
        return Block("", first.start_ms, texts, first.folded)
    asides = tuple(sentence.asides for sentence in kept) if any(s.asides for s in kept) else ()
    return Block(first.speaker, first.start_ms, texts, first.folded, first.attribution, asides)


def header_line(block: Block, names: Mapping[str, str] = NO_NAMES) -> str:
    """`[HH:MM:SS] 화자N · 이름` — empty when neither timing nor speaker is known."""
    if block.start_ms is None and not block.speaker:
        return ""
    clock = UNKNOWN_CLOCK if block.start_ms is None else hhmmss(block.start_ms)
    if not block.speaker:
        return f"[{clock}]"
    name = names.get(block.speaker, "") if block.speaker != "화자0" else ""
    if block.speaker == "화자0" and block.attribution is not None:
        tag = block.attribution
        name = "UNKNOWN" if tag.kind == "UNKNOWN" else f"OVERLAP({','.join(tag.speakers)})"
    return f"[{clock}] {block.speaker}" + (f" · {name}" if name else "")


def render(blocks: Sequence[Block], names: Mapping[str, str] = NO_NAMES) -> str:
    """The document body: blocks split by a blank line, one sentence per line."""
    rendered: list[str] = []
    for block in blocks:
        head = header_line(block, names)
        spoken = [stt_asides.display(text, block.asides[index] if block.asides else (), names)
                  for index, text in enumerate(block.sentences)]
        lines = ([head] if head else []) + spoken
        if lines:
            rendered.append("\n".join(lines))
        if block.folded:
            rendered.append(block.folded)
    return "\n\n".join(rendered)


def parse(body: str) -> tuple[TimedSentence, ...]:
    """Read a body back — a tidied one, or the space-joined paragraphs of 2026-08.

    Only a block's first sentence keeps the header's timestamp: the header says
    when the block started, and timing the rest would be a claim the document
    never made."""
    sentences: list[TimedSentence] = []
    for chunk in filter(None, _BLANK_LINE.split(body)):
        if chunk.startswith("<details>"):
            sentences.append(TimedSentence("", folded=chunk))
            continue
        lines = [line for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        speaker, start_ms = "", None
        attribution = None
        matched = HEADER.match(lines[0].strip())
        if matched is not None:
            speaker = matched.group(4) or ""
            attribution = _parse_attribution(matched.group(5) or "") if speaker == "화자0" else None
            start_ms = _clock_ms(matched.group(1), matched.group(2), matched.group(3))
            lines = lines[1:]
        first = True
        for line in lines:
            for text, asides in _spoken_line(line):
                when = start_ms if first else None
                gap = stt_gap.is_marker(text)
                sentences.append(TimedSentence(text, when, None, "" if gap else speaker,
                                               attribution=None if gap else attribution,
                                               asides=() if gap else asides))
                first = False
    return tuple(sentences)


def _spoken_line(line: str) -> tuple[tuple[str, tuple[Aside, ...]], ...]:
    """Asides come off before the sentence split, so a marker never cuts a sentence."""
    plain, found = stt_asides.extract(normalize(line))
    made: list[tuple[str, tuple[Aside, ...]]] = []
    cursor = 0
    for text in split_sentences(plain):
        at = plain.find(text, cursor)
        cursor = at + len(text) if at >= 0 else cursor
        made.append((text, stt_asides.within(found, at, at + len(text)) if at >= 0 else ()))
    return tuple(made)


def _parse_attribution(suffix: str) -> SpeakerTag | None:
    """화자0 접미만 읽고 깨진 목록은 판정 없는 옛 헤더처럼 둔다."""
    if suffix == "UNKNOWN":
        return SpeakerTag("UNKNOWN")
    if re.fullmatch(r"OVERLAP\(화자[1-9]\d*(?:,화자[1-9]\d*)+\)", suffix):
        speakers = tuple(suffix[8:-1].split(","))
        if len(set(speakers)) == len(speakers):
            return SpeakerTag("OVERLAP", speakers)
    return None


def _clock_ms(hours: str, minutes: str, seconds: str) -> int | None:
    if not (hours.isdigit() and minutes.isdigit() and seconds.isdigit()):
        return None
    return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000
