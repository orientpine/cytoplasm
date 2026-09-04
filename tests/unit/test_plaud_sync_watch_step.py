from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from automation.plaud_sync.model import PlaudSyncRecord, PlaudSyncState
from automation.plaud_sync.watch_step import ResolveEffects, resolve_tick

_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


_BASE = PlaudSyncRecord(
    version=1,
    recording_id="rec-001",
    recorded_at="2026-09-01T08:00:00Z",
    note_relpath="000_PARA/Area/Lifelog/2026/2026-09-01-standup--abcdef123456.md",
    note_title="standup (2026-09-01)",
    body_sha256="a" * 64,
    action_hash=f"sha256:{'b' * 64}",
    status="planned",
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


def _record(**overrides: object) -> PlaudSyncRecord:
    return replace(_BASE, **overrides)


def _state(*records: PlaudSyncRecord) -> PlaudSyncState:
    return PlaudSyncState(
        version=1,
        last_poll_at=None,
        records={record.recording_id: record for record in records},
    )


class Effects:
    def __init__(
        self,
        *,
        post: tuple[str, str] | None = ("msg-1", "chan-1"),
        verdict: str = "pending",
        write: tuple[str, str] | None = ("origin/main", "c" * 64),
    ) -> None:
        self.post_result = post
        self.verdict = verdict
        self.write_result = write
        self.posted: list[str] = []
        self.probed: list[str] = []
        self.written: list[str] = []
        self.notified: list[tuple[str, str]] = []

    def effects(self) -> ResolveEffects:
        return ResolveEffects(
            post_approval=self._post,
            probe_reaction=self._probe,
            write_obsidian=self._write,
            notify_result=self._notify,
            now=_NOW,
        )

    def _post(self, record: PlaudSyncRecord) -> tuple[str, str] | None:
        self.posted.append(record.recording_id)
        return self.post_result

    def _probe(self, record: PlaudSyncRecord) -> str:
        self.probed.append(record.recording_id)
        return self.verdict

    def _write(self, record: PlaudSyncRecord) -> tuple[str, str] | None:
        self.written.append(record.recording_id)
        return self.write_result

    def _notify(self, record: PlaudSyncRecord, outcome: str) -> None:
        self.notified.append((record.recording_id, outcome))


def test_planned_record_posts_and_binds_message() -> None:
    effects = Effects()
    result = resolve_tick(_state(_record()), effects=effects.effects())
    resolved = result.state.records["rec-001"]
    assert result.posted == ("rec-001",)
    assert resolved.status == "posted"
    assert (resolved.message_id, resolved.channel_id) == ("msg-1", "chan-1")


def test_failed_post_leaves_record_planned() -> None:
    effects = Effects(post=None)
    result = resolve_tick(_state(_record()), effects=effects.effects())
    assert result.posted == ()
    assert result.state.records["rec-001"].status == "planned"


def test_max_posts_throttles_batch() -> None:
    effects = Effects()
    records = tuple(_record(recording_id=f"rec-{index:03d}") for index in range(5))
    result = resolve_tick(_state(*records), effects=effects.effects(), max_posts=2)
    assert len(result.posted) == 2
    unposted = [
        record for record in result.state.records.values() if record.status == "planned"
    ]
    assert len(unposted) == 3


def test_owner_approval_writes_in_the_same_tick() -> None:
    # 2026-09-02 실측: ✅ 를 읽는 틱과 저장하는 틱이 달라 저장까지 최대 20분이 걸렸다.
    effects = Effects(verdict="approved")
    posted = _record(status="posted", message_id="msg-1", channel_id="chan-1")
    result = resolve_tick(_state(posted), effects=effects.effects())
    resolved = result.state.records["rec-001"]
    assert effects.written == ["rec-001"]
    assert resolved.status == "written"
    assert resolved.approved_at == _NOW.isoformat()
    assert resolved.written_at == _NOW.isoformat()
    assert result.written == ("rec-001",)
    assert effects.notified == [("rec-001", "written")]


def test_owner_approval_with_failed_write_stays_approved_for_retry() -> None:
    effects = Effects(verdict="approved", write=None)
    posted = _record(status="posted", message_id="msg-1", channel_id="chan-1")
    result = resolve_tick(_state(posted), effects=effects.effects())
    resolved = result.state.records["rec-001"]
    assert effects.written == ["rec-001"]
    assert resolved.status == "approved"
    assert resolved.approved_at == _NOW.isoformat()
    assert result.written == ()
    assert effects.notified == []


def test_owner_cancel_abandons_and_notifies() -> None:
    effects = Effects(verdict="cancelled")
    posted = _record(status="posted", message_id="msg-1", channel_id="chan-1")
    result = resolve_tick(_state(posted), effects=effects.effects())
    assert result.abandoned == ("rec-001",)
    assert result.state.records["rec-001"].status == "abandoned"
    assert effects.notified == [("rec-001", "abandoned")]


def test_missing_message_returns_to_planned_for_repost() -> None:
    effects = Effects(verdict="missing")
    posted = _record(status="posted", message_id="msg-1", channel_id="chan-1")
    result = resolve_tick(_state(posted), effects=effects.effects())
    resolved = result.state.records["rec-001"]
    assert resolved.status == "planned"
    assert resolved.message_id is None


def test_approved_record_writes_then_notifies() -> None:
    effects = Effects()
    approved = _record(status="approved", message_id="msg-1", channel_id="chan-1")
    result = resolve_tick(_state(approved), effects=effects.effects())
    resolved = result.state.records["rec-001"]
    assert result.written == ("rec-001",)
    assert resolved.status == "written"
    assert resolved.remote_ref == "origin/main"
    assert resolved.note_content_sha256 == "c" * 64
    assert resolved.written_at == _NOW.isoformat()
    assert effects.notified == [("rec-001", "written")]


def test_failed_write_keeps_record_approved_for_retry() -> None:
    effects = Effects(write=None)
    approved = _record(status="approved", message_id="msg-1", channel_id="chan-1")
    result = resolve_tick(_state(approved), effects=effects.effects())
    assert result.written == ()
    assert result.state.records["rec-001"].status == "approved"
    assert effects.notified == []


def test_terminal_records_are_untouched() -> None:
    effects = Effects()
    terminal = (
        _record(status="written", message_id="msg-1", channel_id="chan-1"),
        _record(recording_id="rec-002", status="abandoned"),
    )
    result = resolve_tick(_state(*terminal), effects=effects.effects())
    assert (effects.posted, effects.probed, effects.written) == ([], [], [])
    assert result.state.records["rec-001"] == terminal[0]
    assert result.state.records["rec-002"] == replace(terminal[1], recording_id="rec-002")
