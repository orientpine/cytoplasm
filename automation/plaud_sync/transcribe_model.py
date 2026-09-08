"""Local transcription value types and injected effects contract (no I/O)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path
from typing import Final, Literal, Protocol, TypeAlias

from automation import term_correction

from .audio import AudioSource
from .fetch import CloudTranscript
from .lifelog_model import ExtractionOutcome, LifelogRecording
from .model import PlaudSyncRecord

Outcome: TypeAlias = Literal["planned", "fallback", "retry", "stale", "waiting", "abandoned"]
DEFAULT_MAX_ATTEMPTS: Final = 2
ENVIRONMENT_EXIT_CODES: Final = frozenset({3, 4})


class TranscribeError(RuntimeError):
    def __init__(self, reason: str, *, counted: bool) -> None:
        super().__init__(reason)
        self.reason: str = reason
        self.counted: bool = counted


@dataclass(frozen=True, slots=True)
class CliResult:
    returncode: int
    transcript_path: Path | None
    model: str
    detail: str


@dataclass(frozen=True, slots=True)
class LocalTranscript:
    markdown: str
    legend: str
    body: str
    model: str

    @property
    def source_label(self) -> str:
        return f"로컬 전사 {self.model}" + (" · 화자 분리" if self.legend else "")

    @property
    def note_text(self) -> str:
        return "\n\n".join(part for part in (self.legend, self.body) if part)


class TranscribeEffects(Protocol):
    @property
    def tz(self) -> tzinfo: ...

    def extract(self, recording: LifelogRecording) -> ExtractionOutcome: ...

    def glossary(self) -> term_correction.Glossary: ...

    def record_corrections(
        self, recording: LifelogRecording, corrections: Sequence[term_correction.Correction]
    ) -> None: ...

    def draft_body(self, recording_id: str) -> str | None: ...

    def fetch_source(self, recording_id: str) -> AudioSource: ...

    def fetch_summary(self, recording_id: str) -> str: ...

    def fetch_transcript(self, recording_id: str) -> CloudTranscript: ...

    def download(self, source: AudioSource) -> Path: ...

    def transcribe(self, audio: Path, label: str) -> CliResult: ...

    def read_transcript(self, path: Path) -> str: ...

    def store_transcript(self, stem: str, markdown: str) -> Path: ...

    def commit(
        self, before: PlaudSyncRecord, after: PlaudSyncRecord, body: str | None
    ) -> bool: ...

    def archive_audio(self, path: Path, source: AudioSource, stem: str) -> None: ...

    def discard_audio(self, path: Path) -> None: ...
