"""Offline speaker diarization through sherpa-onnx's standalone binary."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import Field, dataclass, replace
from pathlib import Path
from typing import ClassVar, Final, Protocol, TypeVar

import stt_gap
import stt_split

DEFAULT_THRESHOLD: Final = 0.9
DEFAULT_TIMEOUT: Final = 3600.0
MAX_THREADS: Final = 8
NEAREST_LIMIT_MS: Final = 2000
_TURN: Final = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*--\s*(\d+(?:\.\d+)?)\s+speaker_(\d+)\s*$"
)


class DiarizeError(RuntimeError):
    """The speaker diarization binary did not produce usable turns."""


@dataclass(frozen=True, slots=True)
class Turn:
    start_ms: int
    end_ms: int
    speaker: int


@dataclass(frozen=True, slots=True)
class DiarizeToolchain:
    binary: Path
    segmentation: Path
    embedding: Path
    threshold: float
    threads: int
    timeout: float


class _TimedSentence(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Field[object]]]

    @property
    def text(self) -> str: ...

    @property
    def start_ms(self) -> int | None: ...

    @property
    def end_ms(self) -> int | None: ...

    @property
    def speaker(self) -> str: ...


SentenceT = TypeVar("SentenceT", bound=_TimedSentence)


def resolve_toolchain(env: Mapping[str, str]) -> DiarizeToolchain | None:
    """Return the local dependencies, or ``None`` when diarization is unavailable."""
    raw_binary = env.get("SPEECHTOTEXT_DIARIZE_BIN", "")
    raw_segmentation = env.get("SPEECHTOTEXT_DIARIZE_SEGMENTATION", "")
    raw_embedding = env.get("SPEECHTOTEXT_DIARIZE_EMBEDDING", "")
    if not raw_binary or not raw_segmentation or not raw_embedding:
        return None
    binary = Path(raw_binary).expanduser()
    segmentation = Path(raw_segmentation).expanduser()
    embedding = Path(raw_embedding).expanduser()
    if not all(path.is_file() for path in (binary, segmentation, embedding)):
        return None
    return DiarizeToolchain(
        binary=binary,
        segmentation=segmentation,
        embedding=embedding,
        threshold=_positive_float(env.get("SPEECHTOTEXT_DIARIZE_THRESHOLD"), DEFAULT_THRESHOLD),
        threads=_positive_int(env.get("SPEECHTOTEXT_DIARIZE_THREADS"), _default_threads()),
        timeout=_positive_float(env.get("SPEECHTOTEXT_DIARIZE_TIMEOUT"), DEFAULT_TIMEOUT),
    )


def parse_output(text: str) -> tuple[Turn, ...]:
    """Extract only turn records from sherpa-onnx's mixed diagnostic stdout."""
    found: list[Turn] = []
    for line in text.splitlines():
        matched = _TURN.match(line)
        if matched is not None:
            start, end, speaker = matched.groups()
            found.append(Turn(round(float(start) * 1000), round(float(end) * 1000), int(speaker)))
    return tuple(sorted(found, key=lambda turn: turn.start_ms))


def diarize(
    wav: Path, toolchain: DiarizeToolchain, *, num_speakers: int | None = None
) -> tuple[Turn, ...]:
    """Run sherpa-onnx and return its raw speaker-cluster turns."""
    argv = [
        str(toolchain.binary),
        f"--segmentation.pyannote-model={toolchain.segmentation}",
        f"--embedding.model={toolchain.embedding}",
        f"--segmentation.num-threads={toolchain.threads}",
        f"--embedding.num-threads={toolchain.threads}",
        (
            f"--clustering.num-clusters={num_speakers}"
            if num_speakers is not None
            else f"--clustering.cluster-threshold={toolchain.threshold}"
        ),
        str(wav),
    ]
    environment = os.environ.copy()
    library = str(toolchain.binary.parent.parent / "lib")
    existing = environment.get("LD_LIBRARY_PATH", "")
    environment["LD_LIBRARY_PATH"] = f"{library}:{existing}" if existing else library
    try:
        completed = subprocess.run(  # noqa: S603 - resolved local executable and model paths
            argv,
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=toolchain.timeout,
        )
    except OSError as failure:
        raise DiarizeError(f"diarization failed: {type(failure).__name__}: ") from None
    except subprocess.TimeoutExpired as failure:
        raise DiarizeError(f"diarization failed: timeout: {_tail(failure.stderr)}") from None
    if completed.returncode != 0:
        raise DiarizeError(f"diarization failed rc={completed.returncode}: {_tail(completed.stderr)}")
    turns = parse_output(completed.stdout)
    if not turns:
        raise DiarizeError("diarization failed: no speaker turns")
    return turns


def assign(sentences: Iterable[SentenceT], turns: Iterable[Turn]) -> tuple[SentenceT, ...]:
    """Copy sentences with stable transcript labels chosen from timed turns.

    한 문장에는 화자 하나만 붙는다. 그래서 화자보다 긴 문장이 들어오면 분리 결과가
    문서에 도달할 수 없다 — 먼저 화자 경계에서 쪼갠 뒤에 이름을 붙인다(stt_split).
    """
    available = tuple(turns)
    labels: dict[int, str] = {}
    assigned: list[SentenceT] = []
    previous = ""
    for sentence in stt_split.split_on_turns(sentences, available):
        # 전사 실패 표지는 아무도 하지 않은 말이다. 화자를 붙이면 그 몇 분을 잃었다는
        # 사실이 누군가의 발언으로 읽힌다.
        if stt_gap.is_marker(sentence.text):
            assigned.append(sentence)
            continue
        speaker = _speaker_for(sentence, available, previous)
        if isinstance(speaker, int):
            label = labels.setdefault(speaker, f"화자{len(labels) + 1}")
        else:
            label = speaker
        # The public boundary accepts duck-typed frozen dataclasses from stt_blocks.
        assigned.append(replace(sentence, speaker=label))
        previous = label
    return tuple(assigned)


def _speaker_for(sentence: _TimedSentence, turns: tuple[Turn, ...], previous: str) -> int | str:
    if sentence.start_ms is None or sentence.end_ms is None:
        return previous
    overlaps = [
        (min(sentence.end_ms, turn.end_ms) - max(sentence.start_ms, turn.start_ms), turn)
        for turn in turns
    ]
    positive = [(overlap, turn) for overlap, turn in overlaps if overlap > 0]
    if positive:
        return max(positive, key=lambda item: item[0])[1].speaker
    midpoint = (sentence.start_ms + sentence.end_ms) / 2
    nearest = min(turns, key=lambda turn: abs(((turn.start_ms + turn.end_ms) / 2) - midpoint), default=None)
    if nearest is not None and abs(((nearest.start_ms + nearest.end_ms) / 2) - midpoint) <= NEAREST_LIMIT_MS:
        return nearest.speaker
    return previous


def _default_threads() -> int:
    return min(os.cpu_count() or 1, MAX_THREADS)


def _positive_float(raw: str | None, default: float) -> float:
    try:
        value = float(raw) if raw is not None else default
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_int(raw: str | None, default: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        return default
    return value if value > 0 else default


def _tail(stderr: str | bytes | None) -> str:
    if isinstance(stderr, bytes):
        return stderr.decode("utf-8", "replace").strip()[-200:]
    return (stderr or "").strip()[-200:]
