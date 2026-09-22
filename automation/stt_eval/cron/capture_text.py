"""전문을 공용 문장 파서로 읽고 꾸밈을 제외한 비교값을 만든다."""
from __future__ import annotations

import importlib
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeAlias

from automation.plaud_sync.lifelog_fields import lifelog_sections, unquote_transcript
from automation.stt_eval.model import EvalRecord, EvalWord, SpeakerTag

if TYPE_CHECKING:
    from skills.speechtotext.scripts import stt_blocks, stt_speakers
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills/speechtotext/scripts"))
    stt_blocks = importlib.import_module("stt_blocks")
    stt_speakers = importlib.import_module("stt_speakers")

Surface: TypeAlias = Literal["lifelog", "meeting"]


@dataclass(frozen=True, slots=True)
class Parsed:
    sentences: tuple[tuple[str, SpeakerTag], ...]
    names: tuple[tuple[str, str], ...]


def _lines(markdown: str) -> str:
    lines: list[str] = []
    for raw in unicodedata.normalize("NFC", markdown).splitlines():
        line = " ".join(raw.split())
        line = re.sub(r"^#{1,6}\s+", "## ", line)
        lines.append(line)
    return "\n".join(lines)


def parse(markdown: str, surface: Surface) -> Parsed:
    normalized = _lines(markdown)
    if surface == "lifelog":
        section = lifelog_sections(normalized).get("## 전문")
        if section is None:
            raise ValueError("capture transcript section missing")
        body = unquote_transcript(section)
        header = body
    else:
        header, rule, body = normalized.partition("\n---\n")
        if not rule:
            body, header = normalized, ""
    names = set(stt_speakers.names(stt_speakers.parse_legend(header)).items())
    chunks: list[str] = []
    spoken: list[str] = []
    head = ""
    for raw in body.splitlines():
        line = " ".join(raw.split())
        if not line or line.startswith("- 화자:"):
            continue
        line = re.sub(r"^#{1,6}\s+", "", line)
        matched = stt_blocks.HEADER.fullmatch(line)
        if matched:
            if spoken:
                chunks.append((head + "\n" if head else "") + " ".join(spoken))
            head, spoken = line, []
            label, name = matched.group(4), matched.group(5)
            if label and label != "화자0" and name:
                names.add((label, name))
        else:
            spoken.append(line)
    if spoken:
        chunks.append((head + "\n" if head else "") + " ".join(spoken))
    sentences: list[tuple[str, SpeakerTag]] = []
    for sentence in stt_blocks.parse("\n\n".join(chunks)):
        if not sentence.text or sentence.folded:
            continue
        if sentence.attribution is not None:
            tag = SpeakerTag(sentence.attribution.kind, sentence.attribution.speakers)
        elif sentence.speaker and sentence.speaker != "화자0":
            tag = SpeakerTag("SPEAKER", (sentence.speaker,))
        else:
            tag = SpeakerTag("UNKNOWN")
        sentences.extend(_segments(sentence.text, sentence.asides, tag))
    return Parsed(tuple(sentences), tuple(sorted(names)))


def _segments(text: str, asides: tuple[stt_blocks.Aside, ...], tag: SpeakerTag) -> tuple[tuple[str, SpeakerTag], ...]:
    """문장 안 끼어듦(`[화자2: 네]`)의 낱말은 그 화자의 것으로, 나머지는 문장의 판정으로 센다."""
    parts: list[tuple[str, SpeakerTag]] = []
    cursor = 0
    for aside in asides:
        before = text[cursor:aside.start_char].strip()
        if before:
            parts.append((before, tag))
        parts.append((text[aside.start_char:aside.end_char], SpeakerTag("SPEAKER", (aside.speaker,))))
        cursor = aside.end_char
    rest = text[cursor:].strip()
    if rest:
        parts.append((rest, tag))
    return tuple(parts)


def build_record(parsed: Parsed, *, digest: str, duration_ms: int, provenance: str) -> EvalRecord:
    words: list[EvalWord] = []
    text = " ".join(sentence for sentence, _tag in parsed.sentences)
    offset = 0
    for sentence, tag in parsed.sentences:
        for match in re.finditer(r"\S+", sentence):
            words.append(EvalWord(f"w{len(words)}", offset + match.start(), offset + match.end(), None, None, tag, "missing"))
        offset += len(sentence) + 1
    return EvalRecord(digest[:8], digest, duration_ms, text, words=tuple(words), provenance=provenance)
