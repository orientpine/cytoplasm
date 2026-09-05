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

import csv
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import stt_blocks
import stt_gap
import stt_terms

GLOSSARY_ENV: Final = "SPEECHTOTEXT_GLOSSARY"
DEFAULT_GLOSSARY: Final = "~/.hermes/speechtotext/glossary.txt"
#: 표의 머리글 — Drive 에서 열었을 때 어느 칸이 무엇인지 보이라고 두지만 항목은 아니다.
GLOSSARY_HEADER: Final = ("틀린표기", "올바른표기")

_RULE_LINE: Final = re.compile(r"(?m)^---\s*$\n?")
# The body's grammar (sentence boundary, block size, header) lives in one place so
# that writing a transcript and reading it back cannot drift apart.
_MIN_SENTENCES: Final = stt_blocks.MIN_SENTENCES
_MIN_CHARS: Final = stt_blocks.MIN_CHARS

Glossary = Sequence[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class Polished:
    """A readable transcript body plus what tidying it actually did."""

    body: str
    sentences: int
    paragraphs: int
    collapsed: int
    substitutions: int
    blocks: tuple[stt_blocks.Block, ...] = ()
    timed: tuple[stt_blocks.TimedSentence, ...] = ()
    #: 무엇이 무엇으로 바뀌었는지. substitutions 는 이제 이것의 길이다 — 카운트만으로는
    #: 퍼지 교정이 낱말을 잘못 고쳐도 사후에 찾을 방법이 없다.
    corrections: tuple[stt_terms.Correction, ...] = ()

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
    """`틀린표기,올바른표기` per row — a two-column table, wherever the text came from.

    Two separators, one meaning. The glossary is a table, so a row is `,` separated and
    Drive opens it as a spreadsheet; the `=` form still reads because a file the owner
    already wrote must not stop working when the name of the file changes. `=` wins when
    a row has both, so a value may contain a comma.

    A `#` row is a note — that is where the writing example lives — and the header row is
    a column label, not an entry.
    """
    pairs: list[tuple[str, str]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            wrong, _, right = stripped.partition("=")
        else:
            # Sheets quotes a field that contains a comma, so the row is read as CSV
            # rather than split by hand — a hand split turns `"서울, 부산",…` into garbage.
            row = next(csv.reader([stripped]), [])
            if not row:
                continue
            # 한 칸짜리 행은 **바른 용어**다. 틀리는 방식은 모델이 정하지 소유자가 아니므로,
            # 그것까지 적게 하면 소유자가 모르는 오인식은 영영 고쳐지지 않는다.
            second = row[1].strip() if len(row) > 1 else ""
            wrong, right = row[0], second or row[0]
        wrong, right = wrong.strip(), right.strip()
        if not wrong or not right or wrong in GLOSSARY_HEADER:
            continue
        pairs.append((wrong, right))
    return tuple(pairs)


def prompt_hint(glossary: Glossary) -> str:
    """The correct spellings, as the hint the transcriber is given before it guesses."""
    names: list[str] = []
    for _wrong, right in glossary:
        if right not in names:
            names.append(right)
    return f"고유명사: {', '.join(names)}" if names else ""


def normalize(text: str) -> str:
    return stt_blocks.normalize(text)


def split_sentences(text: str) -> tuple[str, ...]:
    """Split on sentence enders only — a break needs punctuation AND whitespace,
    so `1.2%` and `27년 10월` stay whole."""
    return stt_blocks.split_sentences(text)


def apply_glossary(
    text: str, glossary: Glossary
) -> tuple[str, tuple[stt_terms.Correction, ...]]:
    """소유자가 지목한 치환 먼저, 그다음 바른 용어로 남은 오인식을 고친다.

    두 칸 행은 소유자가 직접 짝지은 교정이라 문자열 그대로 바꾸고, 바른 표기 전부(한 칸 행과
    두 칸 행의 오른쪽)는 근접 대조의 목표가 된다 — 그래서 이미 쓰인 1:1 용어집도 고쳐 적지
    않고 그대로 더 많이 잡는다(실측: 그 파일의 바른 용어만으로 놓쳤던 오인식 7회를 더 고쳤다).
    """
    substituted = text
    exact: list[stt_terms.Correction] = []
    for wrong, right in glossary:
        if wrong == right:
            continue
        found = substituted.count(wrong)
        if found:
            substituted = substituted.replace(wrong, right)
            exact.extend(
                stt_terms.Correction(before=wrong, after=right, term=right, kind=stt_terms.EXACT)
                for _ in range(found)
            )
    substituted, repaired = stt_terms.correct(substituted, tuple(r for _w, r in glossary))
    return substituted, (*exact, *repaired)


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
    glossary: Glossary = (),
    names: Mapping[str, str] = stt_blocks.NO_NAMES,
) -> Polished:
    """Tidy sentences that already know when they were said and by whom.

    Correction and collapse happen per sentence rather than over one joined string,
    which is what keeps each line's timing attached to the words it belongs to.
    """
    corrections: list[stt_terms.Correction] = []
    corrected: list[stt_blocks.TimedSentence] = []
    for sentence in sentences:
        # The glossary corrects misheard speech. The gap marker is not speech, and one
        # entry matching a word inside it would quietly rewrite the line that says
        # which minutes are missing — and then nothing downstream would recognize it.
        if stt_gap.is_marker(sentence.text):
            corrected.append(sentence)
            continue
        text, changed = apply_glossary(sentence.text, glossary)
        corrections.extend(changed)
        corrected.append(
            stt_blocks.TimedSentence(
                text=text,
                start_ms=sentence.start_ms,
                end_ms=sentence.end_ms,
                speaker=sentence.speaker,
            )
        )
    kept: list[stt_blocks.TimedSentence] = []
    collapsed = 0
    for sentence in corrected:
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
        substitutions=len(corrections),
        blocks=blocks,
        timed=tuple(kept),
        corrections=tuple(corrections),
    )


def polish(text: str, *, glossary: Glossary = ()) -> Polished:
    """Readable body + the receipt of what changed. Nothing but exact repeats is lost.

    The text handed in may be a legacy space-joined paragraph body or an already
    tidied one; both are read through the same grammar, so tidying settles.
    """
    return polish_sentences(stt_blocks.parse(text), glossary=glossary)


def split_document(markdown: str) -> tuple[str, str]:
    """Separate the provenance header from the spoken body of an existing transcript."""
    parts = _RULE_LINE.split(markdown, maxsplit=1)
    if len(parts) != 2:
        return "", markdown
    return parts[0], parts[1].lstrip("\n")
