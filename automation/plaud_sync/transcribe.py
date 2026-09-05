"""Local transcription stage — ``transcribing`` → ``planned`` with the node's own transcript.

Discovery freezes a recording with Plaud's cloud draft but marks it ``transcribing``
(2026-09-04, owner request): the approval card must quote the transcript the NODE
produced — whisper.cpp + sherpa speaker diarization through the governed speechtotext
CLI — not Plaud's. This module is the pure step: which records to work on, how a CLI
result becomes note text, and what a failure means. Every effect (MCP, S3,
subprocess, locks, state writes) is injected; ``transcribe_live`` binds them.

Two failure classes, on purpose. An *environment* failure (no toolchain, governed
refusal, MCP or network error) retries every tick without counting — the owner fixes
the node and the recording waits visibly in ``plaud 상태``. A *recording* failure (the
CLI refused or timed out on this audio, the download broke the cap) counts toward
``max_attempts``; at the cap the record is promoted with the cloud transcript and the
note's source line says so, because a lifelog note stuck forever helps nobody.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import tzinfo
from pathlib import Path, PurePosixPath
from typing import Final, Literal, Protocol, TypeAlias

from automation import term_correction

from .audio import AudioSource
from .binding import finalize
from .fetch import CloudTranscript
from .lifelog_model import ExtractionOutcome, LifelogExtractError
from .model import PlaudSyncRecord, PlaudSyncState
from .note import LifelogRecording, split_lifelog_body

Outcome: TypeAlias = Literal["planned", "fallback", "retry", "stale"]

DEFAULT_MAX_ATTEMPTS: Final = 2
#: speechtotext exit codes that describe the node, not the recording: 3 = governed copy
#: refusal, 4 = local toolchain missing (the CLI never falls back to the network).
ENVIRONMENT_EXIT_CODES: Final = frozenset({3, 4})
SPEAKER_LEGEND_PREFIX: Final = "- 화자:"
_HEADER_RULE: Final = "\n---\n"
_REASON_LIMIT: Final = 200
_NO_SUMMARY: Final = "- (요약 없음)"
_NO_TRANSCRIPT: Final = "- (전사 없음)"


class TranscribeError(RuntimeError):
    def __init__(self, reason: str, *, counted: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.counted = counted


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

    def discard_audio(self, path: Path) -> None: ...


def candidates(state: PlaudSyncState, *, limit: int) -> tuple[PlaudSyncRecord, ...]:
    waiting = sorted(
        (record for record in state.records.values() if record.status == "transcribing"),
        key=lambda record: (record.recorded_at, record.recording_id),
    )
    return tuple(waiting[: max(limit, 0)])


def split_transcript(markdown: str) -> tuple[str, str]:
    header, rule, body = markdown.partition(_HEADER_RULE)
    if not rule:
        return "", markdown.strip()
    legend = next(
        (line.strip() for line in header.splitlines() if line.startswith(SPEAKER_LEGEND_PREFIX)),
        "",
    )
    return legend, body.strip()


def transcript_stem(record: PlaudSyncRecord) -> str:
    return PurePosixPath(record.note_relpath).stem


def _recording(
    source: AudioSource,
    draft: str,
    *,
    summary: str,
    transcript_text: str,
    transcript_source: str,
) -> LifelogRecording:
    draft_summary, _ = split_lifelog_body(draft)
    return LifelogRecording(
        id=source.recording_id,
        name=source.name,
        created_at=source.created_at,
        start_at=source.start_at,
        duration_ms=source.duration_ms,
        summary_markdown=summary or ("" if draft_summary == _NO_SUMMARY else draft_summary),
        transcript_text=transcript_text,
        transcript_source=transcript_source,
    )


def _promote(
    record: PlaudSyncRecord, recording: LifelogRecording, effects: TranscribeEffects
) -> tuple[PlaudSyncRecord, str]:
    """finalize with the extractor run on THIS recording — the local transcript when there is one.

    Extraction waits for the local transcript on purpose (sync.DRAFT_EXTRACTION_REASON): it
    carries speaker names where the cloud draft has speaker_1 labels. A LifelogExtractError
    propagates so the caller parks the record without counting a transcription attempt.

    교정 참고 문서는 이 노트를 얼리기 직전에 읽는다 — 카드가 붙은 뒤에는 본문을 고칠 수 없다.
    """
    promoted, body, corrections = finalize(
        record,
        recording,
        extraction=effects.extract(recording),
        tz=effects.tz,
        glossary=effects.glossary(),
    )
    effects.record_corrections(recording, corrections)
    return promoted, body


def _local_transcript(
    source: AudioSource, record: PlaudSyncRecord, effects: TranscribeEffects
) -> tuple[LocalTranscript, Path]:
    audio = effects.download(source)
    result = effects.transcribe(audio, transcript_stem(record))
    reason = f"rc={result.returncode} {result.detail}".strip()
    if result.returncode in ENVIRONMENT_EXIT_CODES:
        raise TranscribeError(reason, counted=False)
    if result.returncode != 0 or result.transcript_path is None:
        raise TranscribeError(reason, counted=True)
    markdown = effects.read_transcript(result.transcript_path)
    legend, body = split_transcript(markdown)
    if not body:
        raise TranscribeError("전사본 본문이 비어 있다", counted=True)
    return LocalTranscript(markdown, legend, body, result.model), audio


def _block(
    record: PlaudSyncRecord, effects: TranscribeEffects, reason: str, *, attempts: int | None = None
) -> Outcome:
    blocked = replace(
        record,
        last_block_reason=reason[:_REASON_LIMIT],
        transcribe_attempts=record.transcribe_attempts if attempts is None else attempts,
    )
    return "retry" if effects.commit(record, blocked, None) else "stale"


def _fallback(
    record: PlaudSyncRecord,
    source: AudioSource,
    draft: str,
    effects: TranscribeEffects,
    *,
    attempts: int,
    reason: str,
) -> Outcome:
    draft_summary, _ = split_lifelog_body(draft)
    summary = effects.fetch_summary(record.recording_id) or (
        "" if draft_summary == _NO_SUMMARY else draft_summary
    )
    cloud = effects.fetch_transcript(record.recording_id)
    missing = [
        name
        for name, content, placeholder in (
            ("요약", summary, _NO_SUMMARY),
            ("전사", cloud.text, _NO_TRANSCRIPT),
        )
        if not content.strip() or content == placeholder
    ]
    if len(missing) == 2:
        return _block(record, effects, f"클라우드 폴백 보류: {'과 '.join(missing)}가 없다", attempts=attempts)
    recording = _recording(
        source,
        draft,
        summary=summary,
        transcript_text=cloud.text,
        transcript_source=f"{cloud.source_label}(로컬 전사 {attempts}회 실패: {reason[:80]})",
    )
    try:
        promoted, body = _promote(replace(record, transcribe_attempts=attempts), recording, effects)
    except LifelogExtractError as error:
        return _block(record, effects, f"추출: {error}", attempts=attempts)
    return "fallback" if effects.commit(record, promoted, body) else "stale"


def process(record: PlaudSyncRecord, *, effects: TranscribeEffects, max_attempts: int) -> Outcome:
    draft = effects.draft_body(record.recording_id)
    if draft is None:
        return _block(record, effects, "동결 본문이 없다 — notes/<id>.md 를 확인한다")
    try:
        source = effects.fetch_source(record.recording_id)
    except TranscribeError as error:
        return _block(record, effects, error.reason)
    try:
        transcript, audio = _local_transcript(source, record, effects)
    except TranscribeError as error:
        if not error.counted:
            return _block(record, effects, error.reason)
        attempts = record.transcribe_attempts + 1
        if attempts < max_attempts:
            return _block(record, effects, error.reason, attempts=attempts)
        return _fallback(record, source, draft, effects, attempts=attempts, reason=error.reason)
    recording = _recording(
        source,
        draft,
        summary=effects.fetch_summary(record.recording_id),
        transcript_text=transcript.note_text,
        transcript_source=transcript.source_label,
    )
    try:
        promoted, body = _promote(record, recording, effects)
    except LifelogExtractError as error:
        return _block(record, effects, f"추출: {error}")
    _ = effects.store_transcript(transcript_stem(promoted), transcript.markdown)
    if not effects.commit(record, promoted, body):
        return "stale"
    effects.discard_audio(audio)
    return "planned"


def run_step(
    state: PlaudSyncState,
    *,
    effects: TranscribeEffects,
    limit: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> tuple[tuple[str, Outcome], ...]:
    return tuple(
        (record.recording_id, process(record, effects=effects, max_attempts=max_attempts))
        for record in candidates(state, limit=limit)
    )
