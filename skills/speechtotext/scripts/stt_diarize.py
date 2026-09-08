"""sherpa 바이너리 또는 격리 pyannote CLI로 화자 turn을 얻는다."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import Field, dataclass, replace
from pathlib import Path
from typing import ClassVar, Final, Literal, Protocol, TypeVar

import stt_gap
import stt_split
from stt_attribute import SpeakerTag

# 임계값은 임베딩 모델과 세그먼트 파편 가드에 **함께** 붙는 값이다 — 둘 중 하나를 바꾸면
# 다시 재야 한다. 1.0 은 소유자가 화자 수를 확인한 라이프로그 두 녹음의 노드 실측에서 나왔다
# (2026-09-07 · eres2net_base · min-duration 0.5/0.8 · 발화 시간 5% 이상만 화자로 셈):
#   272.5초 정답 3인 → 0.6:25 · 0.8:18 · **1.0:4** · 1.2:2 · 1.35:1
#   549.4초 정답 4인 → 0.6:2  · 0.8:7  · **1.0:4** · 1.2:2 · 1.35:1
# 직전 기본값 1.35 는 두 녹음을 **둘 다 화자 1명**으로 뭉갰다. 그때 4.5분 녹음의 턴은 12개·
# 중앙값 16.65초였다 — 화자 수 이전에 발화 구분 자체가 사라진다(소유자 지적, 2026-09-07).
# 1.0 에서는 40턴·139턴이다.
# 1.35 를 고른 FU6 표는 --min-duration 을 주지 **않고** 잰 것이라 실행 조건과 달랐다:
# 임계값은 그 가드와 함께 붙는 값이므로 그 표는 이 파이프라인의 값을 고른 것이 아니었다.
# 과분할은 상한 재클러스터링과 잔여 프룬(RESIDUAL_SHARE_FLOOR)으로 복구되지만 **과병합은
# 복구 수단이 없다** — 그래서 값은 낮은 쪽으로 치우친다. 올리는 변경에는 확인된 화자 수를
# 가진 녹음의 실측이 필요하다. 증적: docs/qa/FU6/diarize-threshold-validation.md(1.35 근거),
# docs/qa/PLD1/(이 값의 근거).
DEFAULT_THRESHOLD: Final = 1.0
DEFAULT_TIMEOUT: Final = 3600.0
MAX_THREADS: Final = 8
DEFAULT_MAX_SPEAKERS: Final = 8
# on 은 그 길이 미만 조각을 **버리고**, off 는 같은 화자의 그 간격 미만을 **재귀적으로 이어
# 붙인다** — 이름이 닮았을 뿐 하는 일이 다르고, 발화 구분을 지운 쪽은 off 였다.
# on=0.5: sherpa 기본 0.3 은 far-field 에 너무 짧다 — 0.5 초 미만 조각의 임베딩은 불안정해
# 가짜 화자를 만든다(2026-09-06 실측 133초 2인: 기본값이면 강제 k=4·8 이 모두 화자 3,
# 0.5 면 2). 그래서 이 값은 유지한다.
# off=0.0: 0.8 이던 동안 4.5분 라이프로그가 턴 12개·중앙값 16.65초로 나왔다 — 화자 수
# 이전에 발화 구분 자체가 문서에 없었다(소유자 지적, 2026-09-07). 같은 임계값 1.0 에서
# 잰 노드 실측이고 **실질 화자 수는 어느 행에서도 바뀌지 않았다**:
#   272.5초  off 0.8 → 40턴  · off 0.0 → 82턴
#   549.4초  off 0.8 → 139턴 · off 0.0 → 193턴
# 이어 붙인 침묵이 speech time 에도 섞여 261.8초로 부풀었다(실제 244.3초). 0 은 병합을
# 끄는 값일 뿐 조각을 늘리지 않는다 — 조각은 on 이 막는다. 세 가드를 th=1.0 에서 나란히
# 재면 동시 발화 몫이 0.5/0.0 에서 가장 낮았다(2.6% · 0.3/0.5 는 4.6% · 0/0 은 4.2%);
# 동시 발화는 stt_attribute 에서 화자0 OVERLAP 이 되므로 낮을수록 미상 블록이 적다.
DEFAULT_MIN_DURATION_ON: Final = 0.5
DEFAULT_MIN_DURATION_OFF: Final = 0.0
NEAREST_LIMIT_MS: Final = 2000
# 발화 시간이 이 몫에 못 미치는 군집은 화자가 아니라 군집화의 잔여물이다. 노드 실측
# (2026-09-07 · 임계값 1.0): 군집 6개 중 넷이 36.0/28.8/17.3/17.0% 였고 나머지 둘은
# 0.7%·0.2% 였다. 발화 구조를 되찾으려면 임계값을 낮춰야 하는데(배포 기본값 1.35 는
# 4.5분 녹음을 12턴·중앙값 16.65초로 냈다), 낮추면 이런 부스러기가 함께 생긴다.
# 5% 는 FU6 이 임계값을 고를 때 소유자 승인 아래 쓴 바로 그 기준이다.
RESIDUAL_SHARE_FLOOR: Final = 0.05
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
    segmentation: Path | None
    embedding: Path | None
    threshold: float
    threads: int
    timeout: float
    max_speakers: int
    min_duration_on: float = DEFAULT_MIN_DURATION_ON
    min_duration_off: float = DEFAULT_MIN_DURATION_OFF
    #: 소유자가 아는 화자 수. 선언되면 임계값 추정도 상한 보수도 돌지 않는다.
    speakers: int | None = None
    backend: Literal["sherpa", "pyannote"] = "sherpa"
    mode: Literal["regular", "exclusive"] = "regular"


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
    """기본 sherpa는 모델도 확인하고, pyannote는 격리 CLI만 요구한다."""
    backend = env.get("SPEECHTOTEXT_DIARIZE_BACKEND", "sherpa")
    if backend != "sherpa" and backend != "pyannote":
        print(f"DIARIZE-FAIL backend={backend}", file=sys.stderr)
        return None
    mode = env.get("SPEECHTOTEXT_DIARIZE_MODE", "regular") if backend == "pyannote" else "regular"
    if mode != "regular" and mode != "exclusive":
        print(f"DIARIZE-FAIL backend={backend} mode={mode}", file=sys.stderr)
        return None
    binary = Path(env.get("SPEECHTOTEXT_DIARIZE_BIN", "")).expanduser()
    segmentation = embedding = None
    if backend == "sherpa":
        segmentation = Path(env.get("SPEECHTOTEXT_DIARIZE_SEGMENTATION", "")).expanduser()
        embedding = Path(env.get("SPEECHTOTEXT_DIARIZE_EMBEDDING", "")).expanduser()
    if not all(path.is_file() for path in (binary, segmentation, embedding) if path is not None):
        if backend == "pyannote":
            print("DIARIZE-FAIL backend=pyannote missing-cli", file=sys.stderr)
        return None
    return DiarizeToolchain(
        binary=binary,
        segmentation=segmentation,
        embedding=embedding,
        threshold=_positive_float(env.get("SPEECHTOTEXT_DIARIZE_THRESHOLD"), DEFAULT_THRESHOLD),
        threads=_positive_int(env.get("SPEECHTOTEXT_DIARIZE_THREADS"), _default_threads()),
        timeout=_positive_float(env.get("SPEECHTOTEXT_DIARIZE_TIMEOUT"), DEFAULT_TIMEOUT),
        max_speakers=_positive_int(env.get("SPEECHTOTEXT_DIARIZE_MAX_SPEAKERS"), DEFAULT_MAX_SPEAKERS),
        min_duration_on=_positive_float(
            env.get("SPEECHTOTEXT_DIARIZE_MIN_SPEECH"), DEFAULT_MIN_DURATION_ON
        ),
        min_duration_off=_positive_float(
            env.get("SPEECHTOTEXT_DIARIZE_MIN_SILENCE"), DEFAULT_MIN_DURATION_OFF
        ),
        speakers=_declared_int(env.get("SPEECHTOTEXT_DIARIZE_SPEAKERS")),
        backend="pyannote" if backend == "pyannote" else "sherpa",
        mode="exclusive" if mode == "exclusive" else "regular",
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
    """선언 화자 수를 우선한다. sherpa 과분할만 한 번 보수하고 pyannote는 CLI 상한을 쓴다.

    sherpa의 num-clusters는 강제 분할이 아닌 상한이다. 확인된 133초 2인 표본은
    k=3·4·6·8 모두 2명이었고 15분 회의는 k=3·4를 반환했다. 라벨 폐기보다 보수가 낫다.
    """
    declared = num_speakers if num_speakers is not None else toolchain.speakers
    turns = _speaker_turns(_run(wav, toolchain, declared))
    speakers = len({turn.speaker for turn in turns})
    # 상한 판정 **전에** 남기는 영구 진단 — 이 한 줄이 임계값 결정의 근거가 된다.
    print(
        f"DIARIZE-CLUSTERS speakers={speakers} threshold={toolchain.threshold} turns={len(turns)}",
        file=sys.stderr,
    )
    if declared is not None:
        return turns
    if toolchain.backend == "pyannote" or speakers <= toolchain.max_speakers:
        return substantial(turns)
    print(
        f"DIARIZE-RECLUSTERED speakers={speakers} max={toolchain.max_speakers}",
        file=sys.stderr,
    )
    repaired = _speaker_turns(_run(wav, toolchain, toolchain.max_speakers))
    remaining = len({turn.speaker for turn in repaired})
    if remaining > toolchain.max_speakers:
        # 상한으로 다시 묶어도 넘친다면 분리기가 이 오디오를 읽지 못한 것이다.
        print(
            f"DIARIZE-OVERSEGMENTED speakers={remaining} max={toolchain.max_speakers}",
            file=sys.stderr,
        )
        return ()
    return substantial(repaired)


def _argv(wav: Path, toolchain: DiarizeToolchain, count: int | None) -> list[str]:
    """백엔드 CLI 계약에 맞춰 선언 화자 수 또는 추정 상한을 전달한다."""
    if toolchain.backend == "pyannote":
        bounds = (["--num-speakers", str(count)] if count is not None
                  else ["--min", "1", "--max", str(toolchain.max_speakers)])
        return [str(toolchain.binary), "diarize", "--wav", str(wav), "--mode", toolchain.mode, *bounds]
    return [
        str(toolchain.binary),
        f"--segmentation.pyannote-model={toolchain.segmentation}",
        f"--embedding.model={toolchain.embedding}",
        f"--segmentation.num-threads={toolchain.threads}",
        f"--embedding.num-threads={toolchain.threads}",
        f"--min-duration-on={toolchain.min_duration_on:g}",
        f"--min-duration-off={toolchain.min_duration_off:g}",
        (
            f"--clustering.num-clusters={count}"
            if count is not None
            else f"--clustering.cluster-threshold={toolchain.threshold}"
        ),
        str(wav),
    ]


def _run(wav: Path, toolchain: DiarizeToolchain, count: int | None) -> str:
    """자식 실패는 DiarizeError로 올려 기존 호출자가 전사를 계속하게 한다."""
    environment = os.environ.copy()
    if toolchain.backend == "sherpa":
        library = str(toolchain.binary.parent.parent / "lib")
        existing = environment.get("LD_LIBRARY_PATH", "")
        environment["LD_LIBRARY_PATH"] = f"{library}:{existing}" if existing else library
    try:
        completed = subprocess.run(  # noqa: S603 - resolved local executable and model paths
            _argv(wav, toolchain, count),
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
        raise DiarizeError(
            f"diarization failed rc={completed.returncode}: {_tail(completed.stderr)}"
        )
    return completed.stdout


def _speaker_turns(stdout: str) -> tuple[Turn, ...]:
    turns = parse_output(stdout)
    if not turns:
        raise DiarizeError("diarization failed: no speaker turns")
    return turns

# 판정이 stt_attribute 가 아니라 여기 있는 이유: 배정기는 받은 turn 을 근거로 신뢰해야 한다.
# 거기에 몫 바닥을 두면 1ms 겹침을 OVERLAP 으로 지키는 계약(그 회귀가 실제로 이 위치를
# 거부했다)과 짧은 맞장구가 함께 죽는다. "이 군집은 군집화의 잔여물이다" 는 분리기만 아는
# 사실이다. 전부 바닥 아래면 전원을 남긴다 — 라벨 0개는 뭉뚱그린 화자보다 나쁘다(상한
# 보수와 같은 이유). 근거는 주석에 둔다: 이 파일은 F2 250 LOC 한도에 붙어 있다.
def substantial(turns: tuple[Turn, ...], floor: float = RESIDUAL_SHARE_FLOOR) -> tuple[Turn, ...]:
    """잔여 군집의 turn 을 뺀다 — 그 낱말은 근거를 잃어 화자0 으로 남고 화자가 되지 않는다."""
    spoken = {speaker: sum(t.end_ms - t.start_ms for t in turns if t.speaker == speaker)
              for speaker in {turn.speaker for turn in turns}}
    total = sum(spoken.values())
    kept = {speaker for speaker, said in spoken.items() if said >= total * floor}
    return tuple(turn for turn in turns if turn.speaker in kept) or turns


def assign(sentences: Iterable[SentenceT], turns: Iterable[Turn]) -> tuple[SentenceT, ...]:
    """레거시 문장을 겹침·최근접 근거로 배정하고 무근거는 화자0 UNKNOWN으로 남긴다.

    직전 문장의 화자는 근거가 아니다. 한 문장에는 화자 하나만 붙는다. 그래서 화자보다 긴 문장이 들어오면 분리 결과가
    문서에 도달할 수 없다 — 먼저 화자 경계에서 쪼갠 뒤에 이름을 붙인다(stt_split).
    """
    available = tuple(turns)
    labels: dict[int, str] = {}
    assigned: list[SentenceT] = []
    for sentence in stt_split.split_on_turns(sentences, available):
        # 전사 실패 표지는 아무도 하지 않은 말이다. 화자를 붙이면 그 몇 분을 잃었다는
        # 사실이 누군가의 발언으로 읽힌다.
        if stt_gap.is_marker(sentence.text):
            assigned.append(sentence)
            continue
        speaker = _speaker_for(sentence, available)
        tag = (SpeakerTag("SPEAKER", (labels.setdefault(speaker, f"화자{len(labels) + 1}"),))
               if speaker is not None else SpeakerTag("UNKNOWN"))
        # 공개 경계는 attribution 필드가 없는 옛 동결 문장도 받는다.
        made = replace(sentence, speaker=tag.label)
        if "attribution" in sentence.__dataclass_fields__:
            made = replace(made, attribution=tag)
        assigned.append(made)
    return tuple(assigned)


def _speaker_for(sentence: _TimedSentence, turns: tuple[Turn, ...]) -> int | None:
    if sentence.start_ms is None or sentence.end_ms is None:
        return None
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
    return None


def _default_threads() -> int:
    return min(os.cpu_count() or 1, MAX_THREADS)


def _positive_float(raw: str | None, default: float) -> float:
    try:
        value = float(raw) if raw is not None else default
    except ValueError:
        return default
    return value if value > 0 else default


def _declared_int(raw: str | None) -> int | None:
    """선언된 화자 수. 빈 값과 0 과 오타는 선언이 아니라 「모른다」로 접는다."""
    value = (raw or "").strip()
    return int(value) if value.isdigit() and int(value) > 0 else None


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
