"""Calendar result masking, optional-runtime compatibility and watcher delivery."""
from __future__ import annotations

import builtins
from dataclasses import asdict, replace
from datetime import UTC, datetime
from importlib import import_module
from types import SimpleNamespace
from typing import Final
from unittest.mock import Mock

import pytest

from automation.interop import origin_notice
from automation.interop.owner_message import OwnerMessage, Ref, Result, render

calendar = import_module("test_calendar_confirm_reactions")
confirm = calendar.calendar_confirm
watch = calendar.watch

# Captured with repr() from untouched ddcae0e5f, not rendered by the current producer.
_LEGACY_NOTICES: Final = {
    "done": "✅ 캘린더 등록 실행 완료 (draft abc123) — 소유자 ✅ 승인",
    "cancelled": "⛔ 캘린더 등록 취소 (draft abc123) — 소유자 ⛔ 리액션으로 취소되었습니다.",
    "expired": "⌛ 캘린더 등록 만료 취소 (draft abc123) — 확정 시간이 지나 취소되었습니다.",
    "": (
        "🧹 캘린더 등록 초안 자동 정리 (draft abc123) — "
        "승인 요청 DM이 게시되지 않은 채 24시간이 지나 폐기했습니다. 필요하면 다시 요청해 주세요."
    ),
}


@pytest.fixture
def notice(monkeypatch):
    # Given real delivery with deterministic injected Discord transports.
    posts: list[tuple[str, str]] = []
    fallback: list[str] = []
    deliver = Mock(wraps=origin_notice.deliver)
    monkeypatch.setattr(origin_notice, "deliver", deliver)
    monkeypatch.setattr(confirm, "owner_id", lambda: "111")
    monkeypatch.setattr(confirm, "send_owner_dm", lambda _owner, body: fallback.append(body))
    monkeypatch.setattr(confirm, "_api", lambda *_args: {"name": "calendar"})
    monkeypatch.setattr(confirm, "_thread_transport", lambda channel: SimpleNamespace(
        send=lambda body: (posts.append((channel, body)), SimpleNamespace(message_id="555"))[1:]
    ))
    record = calendar._origin_record(
        approval_guild_id="111", approval_thread_id="222", dm_message_id="333",
        origin_channel_id="444", origin_message_id="666",
    )
    record["attendees"] = ["private-attendee@example.invalid"]
    return SimpleNamespace(record=record, posts=posts, fallback=fallback, deliver=deliver)


@pytest.mark.parametrize("outcome", ["done", "cancelled", "expired"])
@pytest.mark.parametrize("runtime", ["module_missing", "old_signature"])
def test_legacy_bytes_are_preserved_when_runtime_predates_envelopes(notice, monkeypatch, outcome, runtime):
    # Given the current producer and either old-runtime failure mode.
    content = {"done": watch._executed_notice, "cancelled": watch._cancelled_notice,
               "expired": watch._expired_notice}[outcome](notice.record, "abc123")
    if runtime == "module_missing":
        original = builtins.__import__

        def import_without_envelope(name, *args, **kwargs):
            if name == "automation.interop.owner_message":
                raise ImportError("optional envelope unavailable")
            return original(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", import_without_envelope)
    else:
        def legacy_deliver(*, api, transport_factory, record, thread_name, content, fallback, outcome=None):
            return transport_factory(record["approval_thread_id"]).send(content)[-1].message_id

        monkeypatch.setattr(confirm, "_origin_notice", lambda: SimpleNamespace(
            deliver=legacy_deliver, ThreadOutcome=origin_notice.ThreadOutcome,
        ))
    # When the result is sent.
    result = confirm.notify_result(notice.record, content, outcome)
    # Then the old signature accepts it and the exact legacy bytes survive.
    assert result == "555"
    assert [(channel, body.encode("utf-8")) for channel, body in notice.posts] == [
        ("222", _LEGACY_NOTICES[outcome].encode("utf-8"))
    ]
    assert notice.fallback == []


def test_masked_fallback_is_preserved_when_thread_transport_fails(notice, monkeypatch):
    # Given a thread send failure and today's masked result.
    content = watch._executed_notice(notice.record, "abc123")
    monkeypatch.delattr(origin_notice, "ACCEPTS_OWNER_MESSAGE")

    def fail_transport(_channel):
        raise OSError("injected transport failure")

    monkeypatch.setattr(confirm, "_thread_transport", fail_transport)
    # When delivery falls back.
    confirm.notify_result(notice.record, content, "done")
    # Then the fallback preserves the exact bytes without event content.
    assert [body.encode("utf-8") for body in notice.fallback] == [
        _LEGACY_NOTICES["done"].encode("utf-8")
    ]
    calendar._assert_calendar_content_masked(notice.fallback[0])
    assert notice.record["attendees"][0] not in notice.fallback[0]


@pytest.mark.parametrize("outcome,expected", [("done", "executed"), ("cancelled", "cancelled"),
                                             ("expired", "expired"), ("", "cancelled")])
def test_envelope_is_masked_when_result_is_delivered(notice, outcome, expected):
    # Given the existing masked producer text, not the private draft content.
    content = {"done": watch._executed_notice, "cancelled": watch._cancelled_notice,
               "expired": watch._expired_notice, "": watch._orphan_notice}[outcome](notice.record, "abc123")
    # When the result is delivered through the real facade.
    confirm.notify_result(notice.record, content, outcome)
    # Then the envelope retains only action/id identifiers and executed-state detail.
    message = notice.deliver.call_args.kwargs.get("message")
    assert isinstance(message, OwnerMessage)
    assert message.subject_key == "abc123"
    assert message.fact.encode("utf-8") == _LEGACY_NOTICES[outcome].encode("utf-8")
    assert message.detail == Result(outcome=expected)
    assert message.location == Ref(scope="message", space="guild", guild_id="111", channel_id="222",
                                   message_id="333", search=("Discord 검색", "abc123"))
    assert message.owner.verb == "none"
    assert message.agent_next is None
    calendar._assert_calendar_content_masked(str(asdict(message)))
    assert notice.record["attendees"][0] not in str(asdict(message))
    assert len(notice.posts[0][1].splitlines()) == 5


@pytest.mark.parametrize("guild", ["111", ""])
def test_fallback_renders_card_location_when_thread_delivery_fails(notice, monkeypatch, guild):
    # Given known or legacy guild coordinates and an unavailable thread transport.
    notice.record["approval_guild_id"] = guild

    def fail_transport(_channel):
        raise OSError("injected transport failure")

    monkeypatch.setattr(confirm, "_thread_transport", fail_transport)
    content = watch._executed_notice(notice.record, "abc123")
    # When the real facade rerenders for the owner fallback.
    confirm.notify_result(notice.record, content, "done")
    # Then it uses the approval card, never the origin message or a guessed DM link.
    body = notice.fallback[0]
    calendar._assert_calendar_content_masked(body)
    assert notice.record["attendees"][0] not in body
    assert "@me" not in body
    if guild:
        assert "https://discord.com/channels/111/222/333" in body
    else:
        assert "https://discord.com/channels/" not in body
        assert "Discord 검색 / abc123" in body


@pytest.mark.parametrize("reaction,expected", [("✅", "executed"), ("⛔", "cancelled"), ("", "expired")])
def test_watcher_retains_pending_card_when_command_consumes_entry(tmp_path, monkeypatch, notice, reaction, expected):
    # Given a real watcher tick and a command that consumes its pending entry.
    entry = calendar._entry
    monkeypatch.setattr(calendar, "_entry", lambda **kwargs: replace(entry(**kwargs), dm_message_id="333"))
    original_confirm = calendar.FakeCommands.confirm
    original_discard = calendar.FakeCommands.discard

    def consume_confirm(commands, pending, owner):
        original_confirm(commands, pending, owner)
        calendar.PendingConfirmStore(tmp_path / "pending-confirms.jsonl").drop(pending)

    def consume_discard(commands, draft_id):
        original_discard(commands, draft_id)
        calendar.PendingConfirmStore(tmp_path / "pending-confirms.jsonl").drop(replace(entry(), dm_message_id="333"))

    monkeypatch.setattr(calendar.FakeCommands, "confirm", consume_confirm)
    monkeypatch.setattr(calendar.FakeCommands, "discard", consume_discard)
    record = calendar._origin_record(approval_thread_id=calendar.APPROVAL_THREAD, approval_guild_id="111")
    # When the existing watcher executes or cancels after an owner reaction.
    result = calendar._run_approval_thread(tmp_path, monkeypatch,
        {reaction: ({"id": "cha-owner", "bot": False},)}, record=record,
        created=datetime(2026, 7, 15, tzinfo=UTC) if expected == "expired" else None)
    # Then the envelope still has the consumed approval card and the real outcome.
    message = notice.deliver.call_args.kwargs.get("message")
    assert isinstance(message, OwnerMessage)
    assert message.location.message_id == "333"
    assert message.detail == Result(outcome=expected)
    calendar._assert_calendar_content_masked(result.thread_posts[0][1])


def test_watcher_sends_no_result_when_execution_fails(tmp_path, monkeypatch, notice):
    # Given an approved request whose execution fails.
    def fail_confirm(_commands, _pending, _owner):
        raise OSError("injected execution failure")

    monkeypatch.setattr(calendar.FakeCommands, "confirm", fail_confirm)
    record = calendar._origin_record(approval_thread_id=calendar.APPROVAL_THREAD)
    # When the real watcher attempts the command.
    with pytest.raises(watch.ConfirmBatchError):
        calendar._run_approval_thread(tmp_path, monkeypatch,
            {"✅": ({"id": "cha-owner", "bot": False},)}, record=record)
    # Then approval alone never produces an executed result.
    assert notice.deliver.call_count == 0


def test_masking_assertion_rejects_title_when_envelope_is_contaminated(notice):
    # Given a valid envelope from the producer.
    confirm.notify_result(notice.record, watch._executed_notice(notice.record, "abc123"), "done")
    message = notice.deliver.call_args.kwargs.get("message")
    assert isinstance(message, OwnerMessage)
    contaminated = replace(message, subject=calendar.SECRET_SUMMARY)
    # When a title-bearing envelope is rendered.
    body = render(contaminated, destination=Ref(scope="none"))
    # Then the same privacy assertion rejects it (renderer is not a masking layer).
    with pytest.raises(AssertionError):
        calendar._assert_calendar_content_masked(body)
