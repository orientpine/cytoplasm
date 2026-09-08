"""격리 CLI의 문자 정렬을 원본 토큰 시각에 되돌린다.

기본 none은 입출력 없이 원본을 반환한다. whisperx는 SPEECHTOTEXT_ALIGN_BIN,
STT_ENGINES_VENV/bin/stt-engines, PATH 순으로 실행 파일을 고른다.
공백 외 한 문자라도 정렬 불가면 해당 토큰 전체를 보존하며 시각을 보간하지 않는다.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from stt_blocks import TimedSentence, TimedWord, sentences_from_words


@dataclass(frozen=True, slots=True)
class _Character:
    text: str
    start_ms: int | None
    end_ms: int | None


class _AlignError(ValueError):
    """외부 원문 대신 고정 사유만 기록하는 정렬 경계 오류."""


def align_words(
    wav: Path, words: tuple[TimedWord, ...], *, env: Mapping[str, str],
) -> tuple[TimedWord, ...]:
    """단어 순서·내용·접힌 증거를 보존하고, 실패하면 원본 객체를 반환한다."""
    backend = env.get("SPEECHTOTEXT_ALIGN_BACKEND", "none").strip() or "none"
    if backend == "none":
        return words
    try:
        if backend != "whisperx":
            raise _AlignError("invalid-backend")
        sentences = tuple(sentence for sentence in sentences_from_words(words)
                          if sentence.words and sentence.start_ms is not None
                          and sentence.end_ms is not None
                          and 0 <= sentence.start_ms < sentence.end_ms)
        if not sentences:
            return words
        records = [{"text": s.text, "start": s.start_ms / 1000, "end": s.end_ms / 1000}
                   for s in sentences if s.start_ms is not None and s.end_ms is not None]
        binary = env.get("SPEECHTOTEXT_ALIGN_BIN", "").strip()
        if not binary:
            venv = env.get("STT_ENGINES_VENV", "").strip()
            binary = str(Path(venv).expanduser() / "bin/stt-engines") if venv else "stt-engines"
        timeout = int(env.get("STT_ENGINES_TIMEOUT_SECONDS", "3600"))
        if not 1 <= timeout <= 86400:
            raise _AlignError("invalid-timeout")
        with tempfile.TemporaryDirectory(prefix="stt-align-") as directory:
            segments = Path(directory) / "segments.json"
            _ = segments.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
            segments.chmod(0o600)
            completed = subprocess.run(
                [str(Path(binary).expanduser()), "align", "--wav", str(wav), "--segments", str(segments)],
                capture_output=True, timeout=timeout + 30, check=False, env=dict(env),
            )
        if completed.returncode:
            raise _AlignError(f"rc={completed.returncode}")
        characters = _parse(completed.stdout, "".join(s.text for s in sentences),
                            max(s.end_ms for s in sentences if s.end_ms is not None))
        return _apply(words, sentences, characters)
    except _AlignError as failure:
        print(f"ALIGN-FAIL {failure}", file=sys.stderr)
    except (OSError, subprocess.TimeoutExpired, ValueError) as failure:
        print(f"ALIGN-FAIL {type(failure).__name__}", file=sys.stderr)
    return words


def _parse(raw: bytes, expected: str, limit_ms: int) -> tuple[_Character, ...]:
    """문자 개수·원문·유한 시각·null 묶음은 외부 프로세스 경계에서 검증한다."""
    try:
        payload = cast(object, json.loads(raw))
    except (ValueError, UnicodeError):
        raise _AlignError("invalid-output") from None
    if not isinstance(payload, list):
        raise _AlignError("invalid-output")
    rows = cast(list[object], payload)
    if len(rows) != len(expected):
        raise _AlignError("invalid-output")
    made: list[_Character] = []
    for item, char in zip(rows, expected, strict=True):
        if not isinstance(item, dict):
            raise _AlignError("invalid-output")
        row = cast(dict[str, object], item)
        if (row.get("text") != char
                or not {"start_ms", "end_ms", "score"} <= row.keys()):
            raise _AlignError("invalid-output")
        start, end, score = row["start_ms"], row["end_ms"], row["score"]
        if start is None and end is None and score is None:
            made.append(_Character(char, None, None))
        elif (isinstance(start, int) and not isinstance(start, bool)
              and isinstance(end, int) and not isinstance(end, bool)
              and 0 <= start <= end <= limit_ms
              and isinstance(score, (int, float)) and not isinstance(score, bool)
              and math.isfinite(score) and 0 <= score <= 1):
            made.append(_Character(char, start, end))
        else:
            raise _AlignError("invalid-output")
    return tuple(made)


def _apply(
    words: tuple[TimedWord, ...], sentences: Sequence[TimedSentence], characters: Sequence[_Character],
) -> tuple[TimedWord, ...]:
    """NFC·문장 절단의 원본 좌표를 재사용해 BPE 개수와 문자 개수를 혼동하지 않는다."""
    collected: dict[int, list[_Character]] = {}
    offset = 0
    for sentence in sentences:
        for ref in sentence.words:
            collected.setdefault(ref.source_index, []).extend(
                char for char in characters[offset + ref.start_char:offset + ref.end_char]
                if not char.text.isspace()
            )
        offset += len(sentence.text)
    made = list(words)
    for index, chars in collected.items():
        if not chars or any(c.start_ms is None or c.end_ms is None for c in chars):
            continue
        made[index] = replace(words[index], timing_source="aligned",
                              start_ms=min(c.start_ms for c in chars if c.start_ms is not None),
                              end_ms=max(c.end_ms for c in chars if c.end_ms is not None))
    return tuple(made)
