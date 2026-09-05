"""Turn a raw transcript into something a person can actually read.

The local backend joins every whisper segment into one line — a real 94-minute
recording arrived as 38,216 characters without a single break. That is a faithful
transcript and an unusable document.

This module tidies without interpreting: sentence boundaries, paragraphs, and the
single deletion that is safe — a sentence repeated back to back. Everything else
survives, because the failure this skill keeps guarding against is a lost sentence,
never a duplicated one. Reading meaning out of the words (decisions, action items,
milestones) is the meeting skill's job and this module does not reach into it.

낱말 자체는 손대지 않는다. 전사본은 증거라서 되돌릴 수 없는 치환을 새기지 않고, 용어 교정은
이 전사본으로 산출 문서를 만들 때 그 문서에 건다(docs/guide/용어-교정-규약.md).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import stt_blocks

_RULE_LINE: Final = re.compile(r"(?m)^---\s*$\n?")
# The body's grammar (sentence boundary, block size, header) lives in one place so
# that writing a transcript and reading it back cannot drift apart.
_MIN_SENTENCES: Final = stt_blocks.MIN_SENTENCES
_MIN_CHARS: Final = stt_blocks.MIN_CHARS


@dataclass(frozen=True, slots=True)
class Polished:
    """A readable transcript body plus what tidying it actually did."""

    body: str
    sentences: int
    paragraphs: int
    collapsed: int
    blocks: tuple[stt_blocks.Block, ...] = ()
    timed: tuple[stt_blocks.TimedSentence, ...] = ()

    def summary(self) -> str:
        """What the document IS — never what this pass did.

        `collapsed` is a delta: re-tidying an already-tidy transcript legitimately
        reports 0, so putting it in the file's own header makes the document rewrite its
        own receipt on every pass and never settle. It belongs to the run, and the CLI
        reports it there.
        """
        return f"문장 {self.sentences:,}개 · 문단 {self.paragraphs:,}개"


def normalize(text: str) -> str:
    return stt_blocks.normalize(text)


def split_sentences(text: str) -> tuple[str, ...]:
    """Split on sentence enders only — a break needs punctuation AND whitespace,
    so `1.2%` and `27년 10월` stay whole."""
    return stt_blocks.split_sentences(text)


def collapse_repeats(sentences: Sequence[str]) -> tuple[tuple[str, ...], int]:
    """Drop a sentence only when it repeats the one immediately before it.

    A sentence that merely recurs later in the meeting is someone saying the same
    thing again, which is content. Only the back-to-back run is decoder noise.
    """
    kept: list[str] = []
    collapsed = 0
    for sentence in sentences:
        if kept and sentence == kept[-1]:
            collapsed += 1
            continue
        kept.append(sentence)
    return tuple(kept), collapsed


def paragraphs(sentences: Sequence[str]) -> tuple[str, ...]:
    blocks: list[str] = []
    chunk: list[str] = []
    size = 0
    for sentence in sentences:
        chunk.append(sentence)
        size += len(sentence) + 1
        if len(chunk) >= _MIN_SENTENCES and size >= _MIN_CHARS:
            blocks.append(" ".join(chunk))
            chunk, size = [], 0
    if chunk:
        blocks.append(" ".join(chunk))
    return tuple(blocks)


def polish_sentences(
    sentences: Sequence[stt_blocks.TimedSentence],
    *,
    names: Mapping[str, str] = stt_blocks.NO_NAMES,
) -> Polished:
    """Tidy sentences that already know when they were said and by whom.

    이 자리에서 낱말을 바꾸지 않는다 — 전사본에 새긴 잘못된 교정은 원래 낱말을 지워 버리고,
    그때는 참고 문서를 고쳐도 되살릴 데가 없다. 접기는 문장 단위라 각 줄의 시각이 그 줄의
    말에 그대로 붙어 있다.
    """
    kept: list[stt_blocks.TimedSentence] = []
    collapsed = 0
    for sentence in sentences:
        if kept and sentence.text == kept[-1].text:
            collapsed += 1
            continue
        kept.append(sentence)
    blocks = stt_blocks.group(kept)
    return Polished(
        body=stt_blocks.render(blocks, names),
        sentences=len(kept),
        paragraphs=len(blocks),
        collapsed=collapsed,
        blocks=blocks,
        timed=tuple(kept),
    )


def polish(text: str) -> Polished:
    """Readable body + the receipt of what changed. Nothing but exact repeats is lost.

    The text handed in may be a legacy space-joined paragraph body or an already
    tidied one; both are read through the same grammar, so tidying settles.
    """
    return polish_sentences(stt_blocks.parse(text))


def split_document(markdown: str) -> tuple[str, str]:
    """Separate the provenance header from the spoken body of an existing transcript."""
    parts = _RULE_LINE.split(markdown, maxsplit=1)
    if len(parts) != 2:
        return "", markdown
    return parts[0], parts[1].lstrip("\n")
