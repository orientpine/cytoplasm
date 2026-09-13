"""Approved ticks deliver masked results without changing completion or thread routing."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from automation import owner_notice
from automation.interop import owner_message

calendar = import_module("test_calendar_confirm_reactions")
watch = calendar.watch


@pytest.fixture
def notice_wire(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Real facade and renderer; only the final network edge is replaced."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-token")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "222")
    sent: list[str] = []
    monkeypatch.setattr(owner_notice, "send_notice", lambda token, channel, body: sent.append(body))
    return sent


@pytest.fixture
def approved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notice_wire: list[str]) -> SimpleNamespace:
    record = {
        "id": "abc123", "action": "create", "summary": "private-title",
        "attendees": ["private-attendee@example.invalid"], "start": "private-start",
        "end": "private-end", "location": "private-room", "event_id": "private-event",
    }
    entry = replace(calendar._entry(), dm_channel_id="222", channel_id="222", dm_message_id="333")
    store = calendar.PendingConfirmStore(tmp_path / "pending-confirms.jsonl")
    store.append(entry)
    discord = calendar.FakeDiscord({"✅": ({"id": "111", "bot": False},)})
    commands = calendar.FakeCommands()
    monkeypatch.setattr(watch.calendar_gate, "list_drafts", lambda: [])

    def run() -> None:
        watch.run_once(
            store=store, owner_id="111", discord=discord, commands=commands,
            draft_sha256=lambda _draft: "sha-123", draft_record=lambda _draft: record,
            now=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
        )

    return SimpleNamespace(record=record, store=store, discord=discord, commands=commands, run=run, notices=notice_wire)


def test_approved_without_thread_receives_owner_notice(approved: SimpleNamespace) -> None:
    approved.run()
    assert approved.notices == [
        "대상: 캘린더 등록 (abc123)\n"
        "사실: ✅ 캘린더 등록 실행 완료 (draft abc123) — 소유자 ✅ 승인 (실행 완료)\n"
        "위치: 링크 없음 (공간 미상); 검색: Discord 검색 / abc123\n"
        "인계: 소유자: 조치 없음; 다음: 추가 실행 없음\n"
        "되돌리기: 해당 없음"
    ]
    assert [entry.draft_id for entry in approved.commands.confirmed] == ["abc123"]
    assert approved.store.load() == ()


def test_approved_thread_bytes_and_done_archive(
    approved: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved.record.update(approval_thread_id="222", approval_guild_id="111")
    transport = Mock()
    transport.send.return_value = (SimpleNamespace(message_id="444"),)
    api = Mock(return_value={"id": "222", "name": "캘린더 · abc123"})
    monkeypatch.setattr(calendar.calendar_confirm, "_api", api)
    factory = Mock(return_value=transport)
    monkeypatch.setattr(calendar.calendar_confirm, "_thread_transport", factory)
    notify = Mock(wraps=calendar.calendar_confirm.notify_result)
    monkeypatch.setattr(calendar.calendar_confirm, "notify_result", notify)
    approved.run()
    assert notify.call_args.args[1:] == (
        "✅ 캘린더 등록 실행 완료 (draft abc123) — 소유자 ✅ 승인", "done",
    )
    factory.assert_called_once_with("222")
    transport.send.assert_called_once_with(
        "대상: 캘린더 등록 (abc123)\n"
        "사실: ✅ 캘린더 등록 실행 완료 (draft abc123) — 소유자 ✅ 승인 (실행 완료)\n"
        "위치: 여기\n"
        "인계: 소유자: 조치 없음; 다음: 추가 실행 없음\n"
        "되돌리기: 해당 없음"
    )
    assert api.call_args.args == (
        "PATCH", "/channels/222", {"archived": True, "name": "✅ 완료 · 캘린더 · abc123"},
    )
    assert approved.discord.sent_messages == []


@pytest.mark.parametrize("field,expected", [
    ("subject_key", "abc123"), ("subject", "캘린더 등록"),
    ("fact", "✅ 캘린더 등록 실행 완료 (draft abc123) — 소유자 ✅ 승인"),
    ("location.scope", "message"), ("location.space", "unknown"),
    ("location.guild_id", None), ("location.channel_id", None),
    ("location.message_id", None), ("location.url", None),
    ("location.search", ("Discord 검색", "abc123")),
    ("owner.verb", "none"), ("owner.target", None), ("owner.argument", None),
    ("agent_next", None), ("recovery", "not_applicable"),
    ("detail.outcome", "executed"), ("contract_version", 1),
    ("render_version", "owner-ko-v1"),
])
def test_approved_envelope_field(
    approved: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, field: str, expected: object,
) -> None:
    spy = Mock(wraps=owner_message.render)
    monkeypatch.setattr(owner_message, "render", spy)
    approved.run()
    assert spy.call_count == 1
    actual = spy.call_args.args[0]
    for part in field.split("."):
        actual = getattr(actual, part)
    assert actual == expected


def test_approved_detail_kind(approved: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Mock(wraps=owner_message.render)
    monkeypatch.setattr(owner_message, "render", spy)
    approved.run()
    assert type(spy.call_args.args[0].detail) is owner_message.Result


@pytest.mark.parametrize("private", [
    "private-title", "private-attendee@example.invalid", "private-start",
    "private-end", "private-room", "private-event",
])
def test_approved_masks_calendar_content(approved: SimpleNamespace, private: str) -> None:
    approved.run()
    [body] = approved.notices
    assert private not in body


def test_approved_transport_failure_returns_and_purges(
    approved: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    send = Mock(side_effect=OSError("synthetic transport unavailable"))
    monkeypatch.setattr(owner_notice, "send_notice", send)
    assert approved.run() is None
    assert send.call_count == 1
    assert "calendar-confirm-watch owner notification failed:" in capsys.readouterr().err
    assert approved.store.load() == ()
    assert [entry.draft_id for entry in approved.commands.confirmed] == ["abc123"]
    approved.run()
    assert send.call_count == 1
    assert [entry.draft_id for entry in approved.commands.confirmed] == ["abc123"]
