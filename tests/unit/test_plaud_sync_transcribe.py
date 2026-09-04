"""``automation.plaud_sync.transcribe`` — the pure local-transcription step.

Separate from the sync/watch_step suites: those pin discovery and the approval FSM,
while this step sits between them (``transcribing`` → ``planned``) and owns its own
failure classes — environment failures retry without counting, recording failures
count toward the cap and end in a cloud-transcript fallback. Effects are faked here;
the live bindings have their own file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import tzinfo
from pathlib import Path
from typing import Final

from automation.plaud_sync.audio import AudioSource
from automation.plaud_sync.lifelog_fields import DEFAULT_TIMEZONE
from automation.plaud_sync.lifelog_model import (
    ExtractionOutcome,
    ExtractionSkipped,
    LifelogExtractError,
    LifelogExtraction,
)
from automation.plaud_sync.model import PlaudSyncRecord, PlaudSyncState
from automation.plaud_sync.note import LifelogRecording, render_lifelog_body, split_lifelog_body
from automation.plaud_sync.transcribe import (
    CliResult,
    TranscribeError,
    candidates,
    process,
    run_step,
    split_transcript,
)

_SKIPPED: Final = ExtractionSkipped("테스트")
_DRAFT_RECORDING: Final = LifelogRecording(
    id="rec-001",
    name="standup",
    created_at="2026-09-01T08:05:00",
    start_at="2026-09-01T08:00:00",
    duration_ms=60000,
    summary_markdown="- 초안 요약",
    transcript_text="[00:05 · speaker_1] 클라우드 전사 문장",
)
_DRAFT_BODY: Final = render_lifelog_body(_DRAFT_RECORDING, extraction=_SKIPPED)
_RECORD: Final = PlaudSyncRecord(
    version=1,
    recording_id="rec-001",
    recorded_at="2026-09-01T08:00:00",
    note_relpath="000_PARA/Area/Lifelog/2026/2026-09-01-standup--08008c284627.md",
    note_title="standup (2026-09-01)",
    body_sha256=hashlib.sha256(_DRAFT_BODY.encode("utf-8")).hexdigest(),
    action_hash=f"sha256:{'b' * 64}",
    status="transcribing",
    kind="obsidian-write",
    surface="agent-chat-thread",
    channel_id="",
    policy_version=8,
    message_id=None,
    created_at="2026-09-01T09:00:00",
    approved_at=None,
    written_at=None,
    remote_ref=None,
    note_content_sha256=None,
    last_block_reason=None,
)
_SOURCE: Final = AudioSource(
    recording_id="rec-001",
    name="standup",
    created_at="2026-09-01T08:05:00",
    start_at="2026-09-01T08:00:00",
    duration_ms=60000,
    url="https://bucket.invalid/files/rec-001.mp3?X-Amz-Signature=abc",
    suffix=".mp3",
)
_TRANSCRIPT_MD: Final = (
    "# 2026-09-01-standup--08008c284627 전사본\n\n"
    "- 원본 음성: rec-001.mp3\n"
    "- 전사 시각: 2026-09-04 13:00 KST\n"
    "- 전사 모델: local:ggml-large-v3-turbo-q5_0\n"
    "- 전사 커버리지: 100% (0 gaps)\n"
    "- 다듬기: 문장 3 · 중복 0 · 치환 0\n"
    "- 화자: 화자1=김민수(자기소개) · 화자2=미상\n\n"
    "---\n\n"
    "[00:00:05] 화자1 · 김민수\n"
    "안녕하세요 저는 김민수입니다.\n"
    "오늘 안건은 출시 일정입니다.\n\n"
    "[00:00:20] 화자2\n"
    "네 알겠습니다.\n"
)


@dataclass
class FakeEffects:
    draft: str | None = _DRAFT_BODY
    source: AudioSource | Exception = _SOURCE
    summary: str = "- 새 요약"
    download_error: Exception | None = None
    results: list[CliResult] = field(default_factory=list)
    markdown: str = _TRANSCRIPT_MD
    commit_ok: bool = True
    commits: list[tuple[PlaudSyncRecord, PlaudSyncRecord, str | None]] = field(default_factory=list)
    stored: list[tuple[str, str]] = field(default_factory=list)
    discarded: list[Path] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    def draft_body(self, recording_id: str) -> str | None:
        assert recording_id == "rec-001"
        return self.draft

    def fetch_source(self, recording_id: str) -> AudioSource:
        if isinstance(self.source, Exception):
            raise self.source
        return self.source

    def fetch_summary(self, recording_id: str) -> str:
        return self.summary

    tz: tzinfo = DEFAULT_TIMEZONE
    extraction: ExtractionOutcome = _SKIPPED

    def extract(self, recording: LifelogRecording) -> ExtractionOutcome:
        return self.extraction

    def download(self, source: AudioSource) -> Path:
        if self.download_error is not None:
            raise self.download_error
        return Path("/tmp/plaud-audio/rec-001.mp3")

    def transcribe(self, audio: Path, label: str) -> CliResult:
        self.labels.append(label)
        return self.results.pop(0)

    def read_transcript(self, path: Path) -> str:
        assert path == Path("/work/2026-09-04_x.md")
        return self.markdown

    def store_transcript(self, stem: str, markdown: str) -> Path:
        self.stored.append((stem, markdown))
        return Path(f"/state/transcripts/{stem}.md")

    def commit(self, before: PlaudSyncRecord, after: PlaudSyncRecord, body: str | None) -> bool:
        self.commits.append((before, after, body))
        return self.commit_ok

    def discard_audio(self, path: Path) -> None:
        self.discarded.append(path)


def _ok() -> CliResult:
    return CliResult(0, Path("/work/2026-09-04_x.md"), "local:ggml-large-v3-turbo-q5_0", "")


def _fail(code: int) -> CliResult:
    return CliResult(code, None, "", f"notice for rc {code}")


def test_process_when_cli_succeeds_then_record_is_planned_with_the_local_transcript() -> None:
    effects = FakeEffects(results=[_ok()])

    assert process(_RECORD, effects=effects, max_attempts=2) == "planned"

    (before, after, body) = effects.commits[0]
    assert before == _RECORD
    assert after.status == "planned"
    assert after.transcribe_attempts == 0
    assert after.last_block_reason is None
    assert body is not None
    summary, transcript = split_lifelog_body(body)
    assert summary == "- 새 요약", "the summary is refreshed from Plaud at finalize time"
    assert transcript.startswith("- 화자: 화자1=김민수(자기소개) · 화자2=미상\n\n[00:00:05] 화자1 · 김민수\n")
    assert "네 알겠습니다." in transcript
    assert "클라우드 전사 문장" not in body
    assert body.rstrip().endswith(" · 전사: 로컬 전사 local:ggml-large-v3-turbo-q5_0 · 화자 분리")
    assert after.body_sha256 == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert after.action_hash != _RECORD.action_hash
    assert after.note_relpath == _RECORD.note_relpath
    assert effects.labels == ["2026-09-01-standup--08008c284627"]
    assert effects.stored == [("2026-09-01-standup--08008c284627", _TRANSCRIPT_MD)]
    assert effects.discarded == [Path("/tmp/plaud-audio/rec-001.mp3")]


def test_process_when_diarization_was_unavailable_then_source_line_does_not_claim_speakers() -> None:
    markdown = _TRANSCRIPT_MD.replace("- 화자: 화자1=김민수(자기소개) · 화자2=미상\n", "")
    effects = FakeEffects(results=[_ok()], markdown=markdown)
    assert process(_RECORD, effects=effects, max_attempts=2) == "planned"
    body = effects.commits[0][2]
    assert body is not None
    assert body.rstrip().endswith(" · 전사: 로컬 전사 local:ggml-large-v3-turbo-q5_0")
    assert split_lifelog_body(body)[1].startswith("[00:00:05] 화자1 · 김민수\n")


def test_process_when_local_toolchain_is_missing_then_waits_without_counting() -> None:
    effects = FakeEffects(results=[_fail(4)])

    assert process(_RECORD, effects=effects, max_attempts=2) == "retry"

    (_before, after, body) = effects.commits[0]
    assert after.status == "transcribing"
    assert after.transcribe_attempts == 0
    assert after.last_block_reason == "rc=4 notice for rc 4"
    assert body is None
    assert effects.stored == [] and effects.discarded == []


def test_process_when_recording_fails_below_the_cap_then_counts_and_retries() -> None:
    effects = FakeEffects(results=[_fail(5)])
    assert process(_RECORD, effects=effects, max_attempts=2) == "retry"
    after = effects.commits[0][1]
    assert (after.status, after.transcribe_attempts) == ("transcribing", 1)
    assert after.last_block_reason == "rc=5 notice for rc 5"


def test_process_when_recording_fails_at_the_cap_then_falls_back_to_the_cloud_transcript() -> None:
    effects = FakeEffects(results=[_fail(5)])
    once_failed = replace(_RECORD, transcribe_attempts=1, last_block_reason="rc=5 notice for rc 5")

    assert process(once_failed, effects=effects, max_attempts=2) == "fallback"

    (_before, after, body) = effects.commits[0]
    assert after.status == "planned"
    assert after.transcribe_attempts == 2
    assert after.last_block_reason is None
    assert body is not None
    assert split_lifelog_body(body)[1] == "[00:05 · speaker_1] 클라우드 전사 문장"
    assert " · 전사: PLAUD 클라우드 전사(로컬 전사 2회 실패: rc=5 notice for rc 5)" in body
    assert effects.stored == [], "no local transcript exists to store"


def test_process_when_download_breaks_the_cap_then_it_counts_as_a_recording_failure() -> None:
    effects = FakeEffects(download_error=TranscribeError("오디오가 상한을 넘는다", counted=True))
    assert process(_RECORD, effects=effects, max_attempts=3) == "retry"
    after = effects.commits[0][1]
    assert (after.transcribe_attempts, after.last_block_reason) == (1, "오디오가 상한을 넘는다")


def test_process_when_source_fetch_fails_then_retries_without_counting() -> None:
    effects = FakeEffects(source=TranscribeError("MCP: Not authenticated", counted=False))
    assert process(_RECORD, effects=effects, max_attempts=2) == "retry"
    after = effects.commits[0][1]
    assert (after.transcribe_attempts, after.last_block_reason) == (0, "MCP: Not authenticated")


def test_process_when_frozen_draft_is_missing_then_reports_and_waits() -> None:
    effects = FakeEffects(draft=None)
    assert process(_RECORD, effects=effects, max_attempts=2) == "retry"
    assert "동결 본문" in (effects.commits[0][1].last_block_reason or "")


def test_process_when_commit_is_stale_then_nothing_else_is_touched() -> None:
    effects = FakeEffects(results=[_ok()], commit_ok=False)
    assert process(_RECORD, effects=effects, max_attempts=2) == "stale"
    assert effects.discarded == []


def test_candidates_orders_by_recorded_time_and_honours_the_limit() -> None:
    later = replace(_RECORD, recording_id="rec-003", recorded_at="2026-09-03T08:00:00")
    earlier = replace(_RECORD, recording_id="rec-002", recorded_at="2026-08-30T08:00:00")
    planned = replace(_RECORD, recording_id="rec-004", status="planned")
    state = PlaudSyncState(1, None, {r.recording_id: r for r in (later, _RECORD, earlier, planned)})
    assert [r.recording_id for r in candidates(state, limit=2)] == ["rec-002", "rec-001"]
    assert candidates(state, limit=0) == ()


def test_run_step_processes_each_candidate_and_reports_its_outcome() -> None:
    state = PlaudSyncState(1, None, {"rec-001": _RECORD})
    effects = FakeEffects(results=[_ok()])
    assert run_step(state, effects=effects, limit=1, max_attempts=2) == (("rec-001", "planned"),)


def test_split_transcript_separates_legend_and_body_and_tolerates_a_headerless_file() -> None:
    legend, body = split_transcript(_TRANSCRIPT_MD)
    assert legend == "- 화자: 화자1=김민수(자기소개) · 화자2=미상"
    assert body.startswith("[00:00:05] 화자1 · 김민수\n안녕하세요")
    assert body.endswith("네 알겠습니다.")
    assert split_transcript("그냥 본문\n") == ("", "그냥 본문")


def test_render_lifelog_body_when_transcript_source_is_given_then_source_line_names_it() -> None:
    body = render_lifelog_body(replace(_DRAFT_RECORDING, transcript_source="로컬 전사 local:x"), extraction=_SKIPPED)
    assert body.rstrip().endswith("출처: PLAUD 녹음 rec-001 · 2026-09-01T08:00:00 · 1분 0초 · 전사: 로컬 전사 local:x")
    assert render_lifelog_body(_DRAFT_RECORDING, extraction=_SKIPPED) == _DRAFT_BODY


# ---- v2 (B안, 2026-09-04): 사람·장소·결정·할 일 추출은 로컬 전사가 들어온 finalize 에서 돈다 ----


def test_process_runs_the_extractor_on_the_local_transcript_and_writes_its_fields() -> None:
    seen: list[LifelogRecording] = []

    class Extracting(FakeEffects):
        def extract(self, recording: LifelogRecording) -> ExtractionOutcome:
            seen.append(recording)
            return LifelogExtraction(people=("김민수",), places=("회의실",))

    effects = Extracting(results=[_ok()])

    assert process(_RECORD, effects=effects, max_attempts=2) == "planned"

    body = effects.commits[0][2]
    assert body is not None
    assert "- 사람:: [[김민수]]" in body
    assert "- 장소:: 회의실" in body
    assert len(seen) == 1
    assert "네 알겠습니다." in seen[0].transcript_text, "the extractor must see the LOCAL transcript"
    assert seen[0].summary_markdown == "- 새 요약"


def test_process_when_extraction_fails_then_the_record_waits_without_counting_an_attempt() -> None:
    # 전송·파싱 실패는 노드·LLM 쪽 사정이다 — 전사 실패처럼 세면 두 번 만에 클라우드 폴백으로 떨어진다.
    class Failing(FakeEffects):
        def extract(self, recording: LifelogRecording) -> ExtractionOutcome:
            raise LifelogExtractError("glm-main timed out")

    effects = Failing(results=[_ok()])

    assert process(_RECORD, effects=effects, max_attempts=2) == "retry"

    (before, after, body) = effects.commits[0]
    assert before == _RECORD
    assert body is None
    assert after.status == "transcribing"
    assert after.transcribe_attempts == 0
    assert after.last_block_reason is not None and "추출" in after.last_block_reason
    assert effects.stored == []


def test_fallback_also_runs_the_extractor_on_the_cloud_transcript() -> None:
    seen: list[LifelogRecording] = []

    class Extracting(FakeEffects):
        def extract(self, recording: LifelogRecording) -> ExtractionOutcome:
            seen.append(recording)
            return LifelogExtraction(people=("박영희",))

    effects = Extracting(results=[_fail(1)])
    once_failed = replace(_RECORD, transcribe_attempts=1)

    assert process(once_failed, effects=effects, max_attempts=2) == "fallback"

    body = effects.commits[0][2]
    assert body is not None and "- 사람:: [[박영희]]" in body
    assert len(seen) == 1 and "클라우드 전사 문장" in seen[0].transcript_text
