from __future__ import annotations

from dataclasses import replace

from automation.plaud_sync.binding import finalize
from automation.plaud_sync.lifelog_fields import DEFAULT_TIMEZONE
from automation.plaud_sync.lifelog_model import ExtractionSkipped, LifelogExtraction, LifelogRecording
from automation.plaud_sync.model import PlaudSyncRecord
from automation.plaud_sync.note import plan_lifelog_note

_SKIPPED = ExtractionSkipped("테스트")
_TITLELESS_RECORDING = LifelogRecording(
    id="rec-titleless",
    name="2026-09-01 08:00:00",
    created_at="2026-09-01T08:05:00Z",
    start_at="2026-09-01T08:00:00Z",
    duration_ms=60_000,
    summary_markdown="- 초안 요약",
    transcript_text="[00:00] 초안 전문",
)
_RECORD = PlaudSyncRecord(
    version=1,
    recording_id=_TITLELESS_RECORDING.id,
    recorded_at=_TITLELESS_RECORDING.start_at,
    note_relpath="",
    note_title="",
    body_sha256="a" * 64,
    action_hash=f"sha256:{'b' * 64}",
    status="transcribing",
    kind="obsidian-write",
    surface="agent-chat-thread",
    channel_id="",
    policy_version=8,
    message_id=None,
    created_at="2026-09-01T09:00:00Z",
    approved_at=None,
    written_at=None,
    remote_ref=None,
    note_content_sha256=None,
    last_block_reason=None,
)


def test_plan_lifelog_note_when_first_transcription_generates_a_title_then_uses_that_title_for_its_path() -> None:
    # Given
    extraction = LifelogExtraction(title="생성된 제목")

    # When
    plan = plan_lifelog_note(_TITLELESS_RECORDING, extraction=extraction)

    # Then
    assert plan.relpath.name == "2026-09-01_1700_생성된_제목.md"


def test_finalize_when_reprocessing_a_written_titleless_recording_then_keeps_its_first_note_path() -> None:
    # Given: the placeholder note already reached the vault (remote_ref is the receipt).
    first_plan = plan_lifelog_note(_TITLELESS_RECORDING, extraction=_SKIPPED)
    regenerated_plan = plan_lifelog_note(
        _TITLELESS_RECORDING, extraction=LifelogExtraction(title="생성된 제목")
    )
    reprocessing = replace(
        _RECORD,
        note_relpath=first_plan.relpath.as_posix(),
        note_title=first_plan.title,
        remote_ref="refs/remotes/origin/main",
    )
    assert first_plan.relpath != regenerated_plan.relpath

    # When
    after, body, _corrections = finalize(
        reprocessing,
        _TITLELESS_RECORDING,
        extraction=LifelogExtraction(title="생성된 제목"),
        tz=DEFAULT_TIMEZONE,
    )

    # Then: the file is upserted in place and the title follows its stem.
    assert after.note_relpath == first_plan.relpath.as_posix()
    assert after.note_relpath != regenerated_plan.relpath.as_posix()
    assert after.note_title == first_plan.title == "2026-09-01_1700_녹음"
    assert "\ntitle: 2026-09-01_1700_녹음\n" in body


def test_finalize_when_the_placeholder_was_never_written_then_the_generated_title_names_the_note() -> None:
    # Given: discovery parked the record under the placeholder name; nothing reached the vault.
    first_plan = plan_lifelog_note(_TITLELESS_RECORDING, extraction=_SKIPPED)
    parked = replace(_RECORD, note_relpath=first_plan.relpath.as_posix(), note_title=first_plan.title)

    # When
    after, body, _corrections = finalize(
        parked,
        _TITLELESS_RECORDING,
        extraction=LifelogExtraction(title="생성된 제목"),
        tz=DEFAULT_TIMEZONE,
    )

    # Then
    assert after.note_relpath == "000_PARA/Area/Lifelog/2026/2026-09-01_1700_생성된_제목.md"
    assert after.note_title == "2026-09-01_1700_생성된_제목"
    assert "\ntitle: 2026-09-01_1700_생성된_제목\n" in body
