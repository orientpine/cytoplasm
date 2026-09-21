"""voice catalog 의 순수 절반 — 무엇을 자를지, 카탈로그가 어떻게 생겼는지. I/O 는 없다.

전사본 블록 헤더 `[HH:MM:SS] 화자N` 은 시작 시각만 갖는다. 블록은 같은 화자의 연속 문장이므로
한 블록의 구간은 그 시작부터 **다음 블록의 시작**까지이고, 마지막 블록은 오디오 길이를 알
때만 구간이 된다 — 모르는 끝을 지어내면 다음 화자의 목소리가 등록본에 섞인다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Protocol

SCHEMA_VERSION: Final = 1
CATALOG_ENV: Final = "SPEECHTOTEXT_VOICE_CATALOG"
DEFAULT_ROOT: Final = "~/.hermes/speechtotext/voice-catalog"
CATALOG_FILE: Final = "catalog.json"
SAMPLES_DIR: Final = "samples"
UNKNOWN_LABEL: Final = "화자0"
DEFAULT_PER_BLOCK_MS: Final = 30_000
DEFAULT_TOTAL_MS: Final = 60_000
DEFAULT_MIN_BLOCK_MS: Final = 1_500
_UNSAFE: Final = re.compile(r"[^\w]")


class CatalogError(RuntimeError):
    """카탈로그를 읽거나 둘 수 없는 이유 — 호출자가 표식과 함께 stderr 로 낸다."""


class SentenceLike(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def start_ms(self) -> int | None: ...

    @property
    def speaker(self) -> str: ...


@dataclass(frozen=True, slots=True, order=True)
class Interval:
    start_ms: int
    end_ms: int

    @property
    def length_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True, slots=True)
class SpeakerStat:
    blocks: int
    speech_ms: int


@dataclass(frozen=True, slots=True)
class Sample:
    wav: str
    sha256: str
    seconds: float
    recording_id: str
    transcript_stem: str
    speaker_label: str
    intervals: tuple[Interval, ...]


@dataclass(frozen=True, slots=True)
class Person:
    name: str
    created_at: str
    samples: tuple[Sample, ...]


@dataclass(frozen=True, slots=True)
class Catalog:
    people: tuple[Person, ...] = ()
    version: int = SCHEMA_VERSION


def catalog_root(env: Mapping[str, str]) -> Path:
    raw = env.get(CATALOG_ENV, "").strip() or DEFAULT_ROOT
    root = Path(raw).expanduser()
    for candidate in (root, *root.parents):
        if (candidate / ".git").exists():
            raise CatalogError(
                f"CATALOG-ROOT-REFUSED {root} 가 git 체크아웃 안에 있습니다 — "
                f"등록된 목소리가 저장소에 섞이지 않게 {CATALOG_ENV} 를 체크아웃 밖으로 두세요"
            )
    return root


def block_starts(sentences: Iterable[SentenceLike]) -> tuple[tuple[str, int], ...]:
    return tuple(
        (sentence.speaker, sentence.start_ms)
        for sentence in sentences
        if sentence.start_ms is not None
    )


def _blocks(
    sentences: Iterable[SentenceLike], duration_ms: int | None
) -> tuple[tuple[str, Interval | None], ...]:
    starts = block_starts(sentences)
    closed: list[tuple[str, Interval | None]] = []
    for index, (speaker, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else duration_ms
        closed.append((speaker, Interval(start, end) if end is not None and end > start else None))
    return tuple(closed)


def speaker_summary(
    sentences: Iterable[SentenceLike], *, duration_ms: int | None = None
) -> dict[str, SpeakerStat]:
    summary: dict[str, SpeakerStat] = {}
    for speaker, interval in _blocks(sentences, duration_ms):
        if not speaker or speaker == UNKNOWN_LABEL:
            continue
        previous = summary.get(speaker, SpeakerStat(0, 0))
        summary[speaker] = SpeakerStat(
            previous.blocks + 1,
            previous.speech_ms + (interval.length_ms if interval else 0),
        )
    return summary


def plan_segments(
    sentences: Iterable[SentenceLike],
    label: str,
    *,
    duration_ms: int | None = None,
    per_block_max_ms: int = DEFAULT_PER_BLOCK_MS,
    total_max_ms: int = DEFAULT_TOTAL_MS,
    min_block_ms: int = DEFAULT_MIN_BLOCK_MS,
) -> tuple[Interval, ...]:
    if label == UNKNOWN_LABEL:
        return ()
    candidates = [
        Interval(interval.start_ms, interval.start_ms + min(interval.length_ms, per_block_max_ms))
        for speaker, interval in _blocks(sentences, duration_ms)
        if speaker == label and interval is not None and interval.length_ms >= min_block_ms
    ]
    candidates.sort(key=lambda interval: (-interval.length_ms, interval.start_ms))
    chosen: list[Interval] = []
    remaining = total_max_ms
    for interval in candidates:
        if remaining <= 0:
            break
        take = min(interval.length_ms, remaining)
        chosen.append(Interval(interval.start_ms, interval.start_ms + take))
        remaining -= take
    return tuple(sorted(chosen))


def upsert(catalog: Catalog, name: str, sample: Sample, *, created_at: str) -> Catalog:
    key = (sample.recording_id, sample.speaker_label)
    people = list(catalog.people)
    for index, person in enumerate(people):
        if person.name != name:
            continue
        samples = tuple(
            sample if (old.recording_id, old.speaker_label) == key else old for old in person.samples
        )
        if all((old.recording_id, old.speaker_label) != key for old in person.samples):
            samples = (*samples, sample)
        people[index] = replace(person, samples=samples)
        return replace(catalog, people=tuple(people))
    people.append(Person(name, created_at, (sample,)))
    return replace(catalog, people=tuple(people))


def remove(catalog: Catalog, name: str) -> Catalog:
    kept = tuple(person for person in catalog.people if person.name != name)
    return catalog if len(kept) == len(catalog.people) else replace(catalog, people=kept)


def find(catalog: Catalog, name: str) -> Person | None:
    return next((person for person in catalog.people if person.name == name), None)


def to_json(catalog: Catalog) -> str:
    payload = {
        "version": catalog.version,
        "people": [
            {
                "name": person.name,
                "created_at": person.created_at,
                "samples": [
                    {
                        "wav": sample.wav,
                        "sha256": sample.sha256,
                        "seconds": sample.seconds,
                        "recording_id": sample.recording_id,
                        "transcript_stem": sample.transcript_stem,
                        "speaker_label": sample.speaker_label,
                        "intervals": [[i.start_ms, i.end_ms] for i in sample.intervals],
                    }
                    for sample in person.samples
                ],
            }
            for person in catalog.people
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def from_json(text: str) -> Catalog:
    if not text.strip():
        return Catalog()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as failure:
        raise CatalogError(f"catalog.json 을 읽을 수 없습니다: {failure.msg}") from None
    version = payload.get("version") if isinstance(payload, dict) else None
    if version != SCHEMA_VERSION:
        raise CatalogError(f"catalog.json version={version!r} 은 지원하지 않습니다 (기대 {SCHEMA_VERSION})")
    try:
        people = tuple(
            Person(
                str(person["name"]),
                str(person["created_at"]),
                tuple(
                    Sample(
                        str(sample["wav"]), str(sample["sha256"]), float(sample["seconds"]),
                        str(sample["recording_id"]), str(sample["transcript_stem"]),
                        str(sample["speaker_label"]),
                        tuple(Interval(int(start), int(end)) for start, end in sample["intervals"]),
                    )
                    for sample in person["samples"]
                ),
            )
            for person in payload["people"]
        )
    except (KeyError, TypeError, ValueError) as failure:
        raise CatalogError(f"catalog.json 필드가 깨졌습니다: {type(failure).__name__}") from None
    return Catalog(people, SCHEMA_VERSION)


def ffmpeg_filter(intervals: Iterable[Interval]) -> str:
    trims: list[str] = []
    labels: list[str] = []
    for index, interval in enumerate(intervals):
        start, end = interval.start_ms / 1000, interval.end_ms / 1000
        trims.append(f"[0:a]atrim=start={start:g}:end={end:g},asetpts=PTS-STARTPTS[s{index}]")
        labels.append(f"[s{index}]")
    return ";".join((*trims, f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[out]"))


_TITLE: Final = re.compile(r"^#\s+(.+?)\s+전사본\s*$")


def transcript_stems(header: str, file_stem: str) -> tuple[str, ...]:
    stems = [file_stem]
    for line in header.splitlines():
        matched = _TITLE.match(line.strip())
        if matched and matched.group(1) not in stems:
            stems.append(matched.group(1))
    return tuple(stems)


def sample_filename(name: str, recording_id: str, label: str) -> str:
    return f"{_UNSAFE.sub('_', name)}-{recording_id}-{label}.wav"
