from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.plaud_sync.effects_live import (
    build_effects,
    note_plan_for,
    result_notice_text,
    thread_candidates,
)
from automation.plaud_sync.model import PlaudSyncRecord
from automation.plaud_sync.store import save_note_body
from automation.obsidian_write.config import ObsidianWriteConfig

_BODY = "## 요약\n\n- x\n\n## 전문\n\n말씀\n"

_BASE = PlaudSyncRecord(
    version=1,
    recording_id="rec-001",
    recorded_at="2026-09-01T08:00:00Z",
    note_relpath="000_PARA/Area/Lifelog/2026/2026-09-01-standup--abcdef123456.md",
    note_title="standup (2026-09-01)",
    body_sha256=hashlib.sha256(_BODY.encode("utf-8")).hexdigest(),
    action_hash=f"sha256:{'b' * 64}",
    status="approved",
    kind="obsidian-write",
    surface="agent-chat-thread",
    channel_id="chan-1",
    policy_version=8,
    message_id="msg-1",
    created_at="2026-09-01T09:00:00Z",
    approved_at=None,
    written_at=None,
    remote_ref=None,
    note_content_sha256=None,
    last_block_reason=None,
    approval_thread_id="thread-1",
)


def _record(**overrides: object) -> PlaudSyncRecord:
    return replace(_BASE, **overrides)


def test_note_plan_for_rebuilds_the_frozen_note(tmp_path: Path) -> None:
    save_note_body(tmp_path, "rec-001", _BODY)
    plan = note_plan_for(tmp_path, _record())
    assert plan is not None
    assert plan.relpath.as_posix() == _BASE.note_relpath
    assert plan.title == _BASE.note_title
    assert plan.body == _BODY


def test_note_plan_for_missing_body_is_none(tmp_path: Path) -> None:
    assert note_plan_for(tmp_path, _record()) is None


def test_note_plan_for_tampered_body_is_none(tmp_path: Path) -> None:
    save_note_body(tmp_path, "rec-001", _BODY + "변조")
    assert note_plan_for(tmp_path, _record()) is None


def test_result_notice_names_note_on_written() -> None:
    text = result_notice_text(_record(), "written")
    assert _BASE.note_relpath in text
    assert "✅" in text


def test_result_notice_names_recording_on_abandoned() -> None:
    text = result_notice_text(_record(), "abandoned")
    assert "rec-001" in text
    assert "⛔" in text


def test_thread_candidates_prefers_live_requests_of_the_same_key() -> None:
    live = _record(status="posted", message_id="msg-7", channel_id="777")
    record = _record(status="planned", message_id=None, channel_id="", approval_thread_id="222")
    assert thread_candidates(record, (live,)) == (live,)


def test_thread_candidates_falls_back_to_the_records_own_thread() -> None:
    record = _record(status="planned", message_id=None, channel_id="", approval_thread_id="222")
    (candidate,) = thread_candidates(record, ())
    assert candidate.channel_id == "222"


def test_thread_candidates_is_empty_when_no_thread_was_ever_bound() -> None:
    record = _record(status="planned", message_id=None, channel_id="", approval_thread_id=None)
    assert thread_candidates(record, ()) == ()


@dataclass(frozen=True, slots=True)
class _Sent:
    message_id: str


class _RecordingSender:
    """Stands in for the shared chunked sender origin_notice posts through."""

    sent: list[tuple[str, str]] = []
    fail: bool = False

    def __init__(self, token: str, channel_id: str) -> None:
        self.token = token
        self.channel_id = channel_id

    def send(self, body: str) -> tuple[_Sent, ...]:
        if _RecordingSender.fail:
            raise OSError("thread send refused")
        _RecordingSender.sent.append((self.channel_id, body))
        return (_Sent("notice-1"),)


class _RecordingTransport:
    """The approval transport: answers the thread read/close and the owner fallback."""

    def __init__(self, token: str, owner_id: str) -> None:
        self.token = token
        self.owner_id = owner_id
        self.calls: list[tuple[str, str, object]] = []
        self.posted: list[tuple[str, str]] = []
        self.fallback_fails = False

    def api(self, method: str, path: str, payload: object = None) -> object:
        self.calls.append((method, path, payload))
        return {"name": "rec-001", "id": "thread-1"}

    def post_message(self, channel_id: str, content: str) -> str:
        if self.fallback_fails:
            raise OSError("fallback post refused")
        self.posted.append((channel_id, content))
        return "msg-9"


def _notifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[object, _RecordingTransport]:
    """Live effects with every network/config boundary replaced by a recorder."""
    module = "automation.plaud_sync.effects_live"
    transports: list[_RecordingTransport] = []
    _RecordingSender.sent = []
    _RecordingSender.fail = False

    def make_transport(token: str, owner_id: str) -> _RecordingTransport:
        transports.append(_RecordingTransport(token, owner_id))
        return transports[-1]

    monkeypatch.setattr(
        f"{module}.load_config",
        lambda: ObsidianWriteConfig("git@example.invalid:owner/vault.git", tmp_path / "clone"),
    )
    monkeypatch.setattr(f"{module}.DiscordTransport", make_transport)
    monkeypatch.setattr(f"{module}.NoticeSender", _RecordingSender)
    monkeypatch.setattr(f"{module}.DiscordChannelDirectory", lambda *args, **kwargs: object())
    effects = build_effects(
        state_path=tmp_path / "plaud.json",
        token="t",
        owner_id="9",
        now=datetime(2026, 9, 2, 10, 0, tzinfo=UTC),
    )
    return effects, transports[0]


def test_notify_posts_the_result_into_the_request_thread_and_closes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an approved record whose approval request owns a per-request thread.
    effects, transport = _notifier(tmp_path, monkeypatch)

    # When: the terminal "written" result is announced.
    effects.notify_result(_record(), "written")

    # Then: it lands in THAT thread (no second thread, no new approval message)...
    assert _RecordingSender.sent == [("thread-1", result_notice_text(_record(), "written"))]
    # ...and the thread is renamed with the done prefix and archived, so the list of
    # active threads stays exactly the list of open requests.
    (patch,) = [call for call in transport.calls if call[0] == "PATCH"]
    assert patch[1] == "/channels/thread-1"
    assert patch[2] == {"archived": True, "name": "✅ 완료 · rec-001"}


def test_notify_closes_a_cancelled_request_with_the_cancel_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the same record, abandoned instead of written.
    effects, transport = _notifier(tmp_path, monkeypatch)

    # When: the terminal cancellation is announced.
    effects.notify_result(_record(), "abandoned")

    # Then: the thread carries the cancel status, not the done one.
    (patch,) = [call for call in transport.calls if call[0] == "PATCH"]
    assert patch[2] == {"archived": True, "name": "⛔ 취소 · rec-001"}


def test_notify_leaves_the_thread_open_for_a_non_terminal_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a record whose request is still running.
    effects, transport = _notifier(tmp_path, monkeypatch)

    # When: an intermediate acknowledgement is posted.
    effects.notify_result(_record(), "posted")

    # Then: the notice lands but the thread stays open — archiving a live request
    # would hide it from the owner's active-request list.
    assert len(_RecordingSender.sent) == 1
    assert not [call for call in transport.calls if call[0] == "PATCH"]


def test_notify_falls_back_to_the_bound_channel_when_the_thread_send_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the thread path is broken (deleted thread, permissions).
    effects, transport = _notifier(tmp_path, monkeypatch)
    _RecordingSender.fail = True

    # When: the result is announced.
    effects.notify_result(_record(), "written")

    # Then: the confirmed result still reaches the owner on the bound surface.
    assert transport.posted == [("thread-1", result_notice_text(_record(), "written"))]


def test_notify_is_best_effort_and_marks_a_total_failure_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: both the thread path and the fallback refuse.
    effects, transport = _notifier(tmp_path, monkeypatch)
    _RecordingSender.fail = True
    transport.fallback_fails = True

    # When: the tick announces the result.
    effects.notify_result(_record(), "written")

    # Then: the tick survives (notices never change exit codes, receipts or the
    # store) and the loss is visible instead of silent.
    assert "NOTIFY-FAIL" in capsys.readouterr().err


def test_notify_without_any_bound_surface_does_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a record that never reached Discord.
    effects, transport = _notifier(tmp_path, monkeypatch)

    # When: a result is announced for it.
    effects.notify_result(_record(channel_id="", approval_thread_id=None), "written")

    # Then: nothing is posted anywhere — there is no surface to post to.
    assert _RecordingSender.sent == []
    assert transport.posted == []
