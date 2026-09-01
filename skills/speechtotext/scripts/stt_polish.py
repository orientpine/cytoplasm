"""Turn a raw transcript into something a person can actually read.

The local backend joins every whisper segment into one line — a real 94-minute
recording arrived as 38,216 characters without a single break. That is a faithful
transcript and an unusable document.

This module tidies without interpreting: sentence boundaries, paragraphs, the
owner's glossary for the names the model mishears, and the single deletion that is
safe — a sentence repeated back to back. Everything else survives, because the
failure this skill keeps guarding against is a lost sentence, never a duplicated
one. Reading meaning out of the words (decisions, action items, milestones) is the
meeting skill's job and this module does not reach into it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

GLOSSARY_ENV: Final = "SPEECHTOTEXT_GLOSSARY"
DEFAULT_GLOSSARY: Final = "~/.hermes/speechtotext/glossary.txt"

_SENTENCE_END: Final = re.compile(r"(?<=[.!?\u2026])\s+")
_WHITESPACE: Final = re.compile(r"\s+")
_RULE_LINE: Final = re.compile(r"(?m)^---\s*$\n?")
# A paragraph closes once it is both long enough to be a paragraph and short
# enough to stay one — whichever bound is reached last.
_MIN_SENTENCES: Final = 4
_MIN_CHARS: Final = 180

Glossary = Sequence[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class Polished:
    """A readable transcript body plus what tidying it actually did."""

    body: str
    sentences: int
    paragraphs: int
    collapsed: int
    substitutions: int

    def summary(self) -> str:
        """What the document IS — never what this pass did.

        `collapsed`/`substitutions` are deltas: re-tidying an already-tidy transcript
        legitimately reports 0 of each, so putting them in the file's own header makes
        the document rewrite its own receipt on every pass and never settle. They belong
        to the run, and the CLI reports them there.
        """
        return f"문장 {self.sentences:,}개 · 문단 {self.paragraphs:,}개"


def load_glossary(env: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """`틀린표기=올바른표기` per line. Absent file means an empty glossary, never a guess.

    The same file feeds the model's `--prompt` hint and this post-hoc correction, so a
    name is written down once. It stays empty by default on purpose: a guessed name
    installed into production would harden the very mishearing it guessed at.
    """
    path = Path(env.get(GLOSSARY_ENV) or DEFAULT_GLOSSARY).expanduser()
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    return parse_glossary(content)


def parse_glossary(content: str) -> tuple[tuple[str, str], ...]:
    """`틀린표기=올바른표기` per line — the one format, wherever the text came from.

    A project's glossary arrives as Drive bytes rather than a local file, and both
    must be read the same way or the two sources drift.
    """
    pairs: list[tuple[str, str]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        wrong, _, right = stripped.partition("=")
        if wrong.strip() and right.strip():
            pairs.append((wrong.strip(), right.strip()))
    return tuple(pairs)


def prompt_hint(glossary: Glossary) -> str:
    """The correct spellings, as the hint the transcriber is given before it guesses."""
    names: list[str] = []
    for _wrong, right in glossary:
        if right not in names:
            names.append(right)
    return f"고유명사: {', '.join(names)}" if names else ""


def normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def split_sentences(text: str) -> tuple[str, ...]:
    """Split on sentence enders only — a break needs punctuation AND whitespace,
    so `1.2%` and `27년 10월` stay whole."""
    normalized = normalize(text)
    if not normalized:
        return ()
    return tuple(part for part in _SENTENCE_END.split(normalized) if part)


def apply_glossary(text: str, glossary: Glossary) -> tuple[str, int]:
    substituted = text
    hits = 0
    for wrong, right in glossary:
        found = substituted.count(wrong)
        if found:
            substituted = substituted.replace(wrong, right)
            hits += found
    return substituted, hits


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


def polish(text: str, *, glossary: Glossary = ()) -> Polished:
    """Readable body + the receipt of what changed. Nothing but exact repeats is lost."""
    substituted, substitutions = apply_glossary(normalize(text), glossary)
    sentences, collapsed = collapse_repeats(split_sentences(substituted))
    blocks = paragraphs(sentences)
    return Polished(
        body="\n\n".join(blocks),
        sentences=len(sentences),
        paragraphs=len(blocks),
        collapsed=collapsed,
        substitutions=substitutions,
    )


def split_document(markdown: str) -> tuple[str, str]:
    """Separate the provenance header from the spoken body of an existing transcript."""
    parts = _RULE_LINE.split(markdown, maxsplit=1)
    if len(parts) != 2:
        return "", markdown
    return parts[0], parts[1].lstrip("\n")
