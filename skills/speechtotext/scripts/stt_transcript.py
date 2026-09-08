"""Render an audio transcription into the ``.md`` file the meeting skill ingests.

The transcript is the deliverable the owner asked for ("음성 파일을 text(.md)로")
AND the input the meeting skill already knows how to read, so it carries a small
provenance header — which recording, when, by which model — before the spoken
text. Naming is deterministic: re-transcribing the same meeting on the same day
updates one file instead of accumulating copies.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, cast

import stt_eval_record
import stt_polish
from stt_sentence import TimedSentence

_SEPARATORS: Final = re.compile(r'[/\\:*?"<>|\s]+')
_DASHES: Final = re.compile(r"-{2,}")
_FALLBACK_LABEL: Final = "전사본"
TIDY_PREFIX: Final = "- 다듬기:"
TOKEN_TIMING_PREFIX: Final = "- 토큰 시각:"
ATTRIBUTION_PREFIX: Final = "- 화자 배정:"
DTW_ENV: Final = "SPEECHTOTEXT_WHISPER_DTW"
DIR_MODE: Final = 0o700
FILE_MODE: Final = 0o600


class TranscriptionLike(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def model(self) -> str: ...


def safe_label(label: str) -> str:
    """Filesystem-safe, NFC-normalized label (never empty)."""
    collapsed = _DASHES.sub("-", _SEPARATORS.sub("-", unicodedata.normalize("NFC", label)))
    return collapsed.strip("-. ") or _FALLBACK_LABEL


def token_timing_line(env: Mapping[str, str] = os.environ, *, aligned: bool = False) -> str:
    """전사에 쓴 토큰 시각의 출처를 헤더 한 줄로 남긴다."""
    if aligned:
        return f"{TOKEN_TIMING_PREFIX} aligned:whisperx"
    preset = (env.get(DTW_ENV) or "").strip()
    return f"{TOKEN_TIMING_PREFIX} dtw:{preset}" if preset else f"{TOKEN_TIMING_PREFIX} offsets"


def transcript_name(label: str, on: datetime) -> str:
    """``<YYYY-MM-DD>_<label>.md`` — the date prefix mirrors the Drive outputs convention."""
    return f"{on:%Y-%m-%d}_{safe_label(label)}.md"


def render(
    *,
    label: str,
    source_name: str,
    transcription: TranscriptionLike,
    now: datetime,
    polish: stt_polish.Polished | None = None,
    extra_lines: Sequence[str] = (),
) -> str:
    """Provenance header + the spoken text — tidied into blocks when asked.

    ``extra_lines`` are pre-formatted header lines (the speaker legend, today) that
    belong to whoever computed them; this module only decides where they sit.
    """
    sentences = cast(Sequence[TimedSentence], getattr(transcription, "sentences", ()))
    aligned = any(ref.word.timing_source == "aligned" for sentence in sentences for ref in sentence.words)
    mode = getattr(transcription, "attribution_mode", "legacy")
    assignment_line = f"{ATTRIBUTION_PREFIX} {mode}\n" if mode is not None else ""
    coverage = getattr(transcription, "coverage", None)
    coverage_line = f"- 전사 커버리지: {coverage.summary()}\n" if coverage is not None else ""
    tidy_line = f"{TIDY_PREFIX} {polish.summary()}\n" if polish is not None else ""
    extras = "".join(f"{line}\n" for line in extra_lines)
    body = polish.body if polish is not None else transcription.text.strip()
    return (
        f"# {label} 전사본\n\n"
        f"- 원본 음성: {source_name}\n"
        f"- 전사 시각: {now:%Y-%m-%d %H:%M} KST\n"
        f"- 전사 모델: {transcription.model}\n"
        f"{token_timing_line(aligned=aligned)}\n"
        f"{assignment_line}{coverage_line}{tidy_line}{extras}\n"
        "---\n\n"
        f"{body}\n"
    )


def rewrite(
    header: str,
    polish: stt_polish.Polished,
    *,
    label: str,
    extra_lines: Sequence[str] = (),
    managed_prefixes: Sequence[str] = (TIDY_PREFIX,),
) -> str:
    """Re-tidy an existing transcript, keeping its provenance header intact.

    Every line this pass owns is replaced rather than appended, so running it twice
    returns the same document — the transcript already on disk was written before
    tidying existed and must be repairable without re-paying for transcription.
    """
    lines = [
        line
        for line in header.rstrip("\n").splitlines()
        if not any(line.startswith(prefix) for prefix in managed_prefixes)
    ]
    if not lines:
        lines = [f"# {label} 전사본", ""]
    lines.append(f"{TIDY_PREFIX} {polish.summary()}")
    lines.extend(extra_lines)
    return "\n".join(lines) + "\n\n---\n\n" + polish.body + "\n"


def write_transcript(
    directory: Path,
    *,
    label: str,
    source_name: str,
    transcription: TranscriptionLike,
    now: datetime,
    polish: stt_polish.Polished | None = None,
    extra_lines: Sequence[str] = (),
) -> Path:
    """Write the transcript owner-only; returns the path meeting will ingest."""
    directory.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    directory.chmod(DIR_MODE)
    target = directory / transcript_name(label, now)
    target.write_text(
        render(
            label=label, source_name=source_name, transcription=transcription,
            now=now, polish=polish, extra_lines=extra_lines,
        ),
        encoding="utf-8",
    )
    target.chmod(FILE_MODE)
    snapshot: object = getattr(transcription, "eval_record", None)
    try:
        snapshot_path = target.with_suffix(".eval.json")
        # 같은 이름의 재전사에서 이전 가설을 새 전사 결과로 잘못 이동하지 않는다.
        snapshot_path.unlink(missing_ok=True)
        if isinstance(snapshot, stt_eval_record.EvalSnapshot):
            stt_eval_record.dump_record(snapshot, snapshot_path)
    except (OSError, ValueError, TypeError) as error:
        print(f"STT-EVAL-SNAPSHOT-FAIL reason={type(error).__name__}", file=sys.stderr)
    return target
