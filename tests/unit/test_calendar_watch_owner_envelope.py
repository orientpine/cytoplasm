"""No-thread watcher delivery: shipped bytes, masking and completed-entry retention."""
from __future__ import annotations

import builtins
from dataclasses import asdict
from datetime import UTC, datetime
from importlib import import_module
from typing import Final
from unittest.mock import Mock

import pytest

from automation import owner_notice
from automation.interop import owner_message
from tests.unit.test_calendar_approved_notice import notice_wire as notice_wire

calendar = import_module("test_calendar_confirm_reactions")
watch = calendar.watch

# Independent literals captured from base 42fe105ba's no-thread sends.
LEGACY: Final = {
    "done": "✅ 캘린더 등록 실행 완료 (draft abc123) — 소유자 ✅ 승인",
    "cancelled": "⛔ 캘린더 등록 취소 (draft abc123) — 소유자 ⛔ 리액션으로 취소되었습니다.",
    "expired": "⌛ 캘린더 등록 만료 취소 (draft abc123) — 확정 시간이 지나 취소되었습니다.",
    "": (
        "🧹 캘린더 등록 초안 자동 정리 (draft abc123) — "
        "승인 요청 DM이 게시되지 않은 채 24시간이 지나 폐기했습니다. 필요하면 다시 요청해 주세요."
    ),
}


def record() -> dict[str, str | list[str]]:
    return {
        "id": "abc123", "action": "create", "summary": "private-title",
        "attendees": ["private-attendee@example.invalid"], "start": "private-start",
        "end": "private-end", "location": "private-room", "event_id": "private-event",
    }


def send(discord, outcome: str) -> None:
    draft = record()
    content = {"done": watch._executed_notice, "cancelled": watch._cancelled_notice,
               "expired": watch._expired_notice, "": watch._orphan_notice}[outcome](draft, "abc123")
    watch._notify_result(discord, draft, content, outcome)


def missing_envelope(monkeypatch) -> None:
    original = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "automation.interop.owner_message":
            raise ImportError("optional envelope unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)


@pytest.mark.parametrize("outcome", ["done", "cancelled", "expired", ""])
def test_no_thread_legacy_bytes_without_envelope(monkeypatch, outcome, notice_wire):
    missing_envelope(monkeypatch)
    discord = calendar.FakeDiscord({})
    send(discord, outcome)
    assert notice_wire == [LEGACY[outcome]]
    assert discord.sent_messages == []


@pytest.mark.parametrize("expired", [False, True])
def test_no_thread_transport_failure_purges_completed_entry(tmp_path, monkeypatch, capsys, expired, notice_wire):
    monkeypatch.setattr(watch.calendar_gate, "list_drafts", lambda: [])
    monkeypatch.setattr(owner_notice, "send_notice", Mock(side_effect=OSError("offline")))
    store, _, commands = calendar._run(
        tmp_path, {"⛔": ({"id": "cha-owner", "bot": False},)},
        record=record(),
        created=datetime(2026, 7, 15, tzinfo=UTC) if expired else None,
    )
    assert commands.discarded == ["abc123"]
    assert store.load() == ()
    assert "calendar-confirm-watch owner notification failed:" in capsys.readouterr().err
    # An empty next tick cannot re-evaluate the already discarded draft.
    def no_second_probe(_draft_id):
        pytest.fail("completed entry was re-evaluated")

    watch.run_once(store=store, owner_id="111", discord=calendar.FakeDiscord({}),
                   commands=commands, draft_sha256=no_second_probe,
                   now=datetime(2026, 7, 17, 12, 0, tzinfo=UTC))
    assert commands.discarded == ["abc123"]


@pytest.mark.parametrize("outcome,expected", [
    ("done", "executed"), ("cancelled", "cancelled"),
    ("expired", "expired"), ("", "cancelled"),
])
def test_no_thread_envelope_fields_and_masking(monkeypatch, outcome, expected, notice_wire):
    spy = Mock(wraps=owner_message.render)
    monkeypatch.setattr(owner_message, "render", spy)
    discord = calendar.FakeDiscord({})
    send(discord, outcome)
    assert spy.call_count == 1
    message = spy.call_args.args[0]
    assert message.subject_key == "abc123"
    assert message.subject == "캘린더 등록"
    assert message.fact == LEGACY[outcome]
    assert asdict(message.location) == {
        "scope": "message", "space": "unknown", "guild_id": None,
        "channel_id": None, "message_id": None, "url": None,
        "search": ("Discord 검색", "abc123"),
    }
    assert asdict(message.owner) == {"verb": "none", "target": None, "argument": None}
    assert message.agent_next is None
    assert message.recovery == "not_applicable"
    assert asdict(message.detail) == {"outcome": expected}
    assert message.contract_version == 1
    assert message.render_version == "owner-ko-v1"
    assert asdict(spy.call_args.kwargs["destination"]) == {
        "scope": "channel", "space": "unknown", "guild_id": None,
        "channel_id": "222", "message_id": None, "url": None, "search": None,
    }
    [body] = notice_wire
    assert len(body.splitlines()) == 5
    assert "@me" not in body and "discord.com" not in body
    for private in ("private-title", "private-attendee@example.invalid", "private-start",
                    "private-end", "private-room", "private-event"):
        assert private not in body
        assert private not in str(asdict(message))


def test_no_thread_confirm_notice_shipped_bytes(notice_wire):
    discord = calendar.FakeDiscord({})
    send(discord, "done")
    assert notice_wire == [
        "대상: 캘린더 등록 (abc123)\n"
        "사실: ✅ 캘린더 등록 실행 완료 (draft abc123) — 소유자 ✅ 승인 (실행 완료)\n"
        "위치: 링크 없음 (공간 미상); 검색: Discord 검색 / abc123\n"
        "인계: 소유자: 조치 없음; 다음: 추가 실행 없음\n"
        "되돌리기: 해당 없음"
    ]


@pytest.mark.parametrize("failure", ["render", "builder", "import", "transport"])
def test_no_thread_unexpected_notice_failure_still_purges(tmp_path, monkeypatch, capsys, failure, notice_wire):
    def broken(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    if failure == "render":
        monkeypatch.setattr(owner_message, "render", broken)
    elif failure == "builder":
        monkeypatch.setattr(import_module("calendar_result_message"), "build", broken)
    elif failure == "import":
        original = builtins.__import__

        def broken_import(name, *args, **kwargs):
            if name == "automation.interop.owner_message":
                raise RuntimeError("synthetic failure")
            return original(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", broken_import)
    else:
        monkeypatch.setattr(owner_notice, "send_notice", broken)
    monkeypatch.setattr(watch.calendar_gate, "list_drafts", lambda: [])
    store, _, commands = calendar._run(
        tmp_path, {"⛔": ({"id": "cha-owner", "bot": False},)}, record=record(),
    )
    assert commands.discarded == ["abc123"]
    assert store.load() == ()
    stderr = capsys.readouterr().err
    if failure == "import":
        # The builder's optional-runtime boundary falls back to legacy bytes.
        assert notice_wire == [LEGACY["cancelled"]]
        assert stderr == ""
    else:
        assert "calendar-confirm-watch owner notification failed:" in stderr
