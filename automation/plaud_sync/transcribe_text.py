"""Local transcription text conversion into the shared lifelog recording shape."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Final

from .audio import AudioSource, normalize_utc_timestamp
from .lifelog_model import LifelogRecording
from .model import PlaudSyncRecord
from .note import split_lifelog_body

SPEAKER_LEGEND_PREFIX: Final = "- 화자:"
_HEADER_RULE: Final = "\n---\n"
_NO_SUMMARY: Final = "- (요약 없음)"


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


def recording_from_source(
    source: AudioSource, draft: str, *, summary: str,
    transcript_text: str, transcript_source: str,
) -> LifelogRecording:
    """Preserve the frozen draft summary when the refreshed cloud summary is absent."""
    draft_summary, _ = split_lifelog_body(draft)
    return LifelogRecording(
        id=source.recording_id,
        name=source.name,
        created_at=normalize_utc_timestamp(source.created_at),
        start_at=normalize_utc_timestamp(source.start_at),
        duration_ms=source.duration_ms,
        summary_markdown=summary or ("" if draft_summary == _NO_SUMMARY else draft_summary),
        transcript_text=transcript_text,
        transcript_source=transcript_source,
    )
