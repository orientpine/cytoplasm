"""Execution-state and destination contracts for todo result envelopes."""
from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Literal

import pytest

from tests.unit.test_todo_origin_thread import _FakeTransport, _thread_factory
from tests.unit.test_todo_result_notice import LEGACY_BODIES, record as record, saved_record as saved_record

import todo_approval_runtime as runtime
import todo_confirm_reaction_watch as watch
from todo_approval_model import TodoApprovalRecord, TodoApprovalSpec
from todo_approval_store import TodoApprovalStore
from automation.interop import origin_notice
from automation.interop.owner_message import Action, OwnerMessage, Ref, Result


@pytest.mark.parametrize(("outcome", "detail"), [
    ("DONE", "executed"), ("CANCELLED", "cancelled"), ("EXPIRED", "expired"),
])
def test_supplies_execution_envelope_when_result_is_terminal(
    record: dict[str, str], monkeypatch: pytest.MonkeyPatch, outcome: str,
    detail: Literal["executed", "cancelled", "expired"],
) -> None:
    # Given: a terminal result whose existing content is already safe to disclose.
    envelopes: list[OwnerMessage | None] = []
    deliver = origin_notice.deliver
    def capture(**kwargs):
        envelopes.append(kwargs.get("message"))
        return deliver(**kwargs)
    monkeypatch.setattr(origin_notice, "deliver", capture)
    # When: it is delivered through the real facade.
    runtime.notify_result(record, LEGACY_BODIES[outcome], thread_name="할일", outcome=outcome,
                          transport=_FakeTransport(), transport_factory=_thread_factory([]))
    # Then: execution state, masked subject, original facts, and saved card coordinates survive.
    [message] = envelopes
    assert isinstance(message, OwnerMessage)
    assert message.detail == Result(outcome=detail)
    assert message.fact == LEGACY_BODIES[outcome]
    assert message.subject_key == record["id"]
    assert message.subject == (record["id"] if outcome == "CANCELLED" else record["title"])
    assert message.location == Ref(scope="message", space="guild", guild_id="111",
                                   channel_id="222", message_id="333", search=("Discord 검색", record["id"]))
    assert (message.owner, message.agent_next, message.recovery) == (Action(verb="none"), None, "not_applicable")


@pytest.mark.parametrize("guild", ["111", ""])
@pytest.mark.parametrize("same_thread", [False, True])
def test_fallback_locator_matches_destination_when_thread_send_fails(
    record: dict[str, str], guild: str, same_thread: bool,
) -> None:
    # Given: saved coordinates and a failed primary thread sender (no link probing).
    routing = {**record, "approval_guild_id": guild, "channel_id": "222" if same_thread else "444"}
    transport = _FakeTransport()
    def failed_factory(_channel: str):
        raise RuntimeError("injected primary failure")
    # When: a completed registration falls back to the stored channel.
    runtime.notify_result(routing, LEGACY_BODIES["DONE"], thread_name="할일", outcome="DONE",
                          transport=transport, transport_factory=failed_factory)
    # Then: foreign destinations get a card URL or search key, never a guessed DM link.
    [(channel, body)] = transport.posts
    assert channel == routing["channel_id"]
    assert len(body.splitlines()) == 5
    if guild and not same_thread:
        assert "https://discord.com/channels/111/222/333" in body
    elif not same_thread:
        assert routing["id"] in body.splitlines()[2]
    else:
        assert "https://discord.com/channels/" not in body
    assert "/@me/" not in body
    assert transport.api_calls == []
    print(body)


def test_origin_routing_retains_card_coordinates_when_generation_is_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an approval record with distinct card and instruction message IDs.
    monkeypatch.setenv("TODO_APPROVAL_ROOT", str(tmp_path))
    store = TodoApprovalStore(tmp_path)
    spec = TodoApprovalSpec(
        "todo:sha256:fixture", "sha256:fixture", "target", "masked", "todo", "owner-dm", "444", 7,
        origin_message_id="666", approval_thread_id="222", approval_guild_id="111", title="합성 작업",
    )
    store.bind_message(store.prepare(spec, datetime(2026, 9, 1, tzinfo=UTC)), "333")
    # When: the execution notifier reads the generation's routing facts.
    routing = runtime.origin_record("sha256:fixture")
    # Then: the envelope receives the actual approval card, not the instruction.
    assert routing is not None
    assert (routing.get("approval_guild_id"), routing.get("message_id"), routing.get("title")) == (
        "111", "333", "합성 작업",
    )


@pytest.mark.parametrize("outcome", ["CANCELLED", "EXPIRED"])
def test_terminal_producers_retain_card_coordinates_when_notifying(
    saved_record: TodoApprovalRecord, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    # Given: a saved card and an injected, successful Discord delivery boundary.
    envelopes: list[OwnerMessage | None] = []
    deliver = origin_notice.deliver
    def capture(**kwargs):
        envelopes.append(kwargs.get("message"))
        return deliver(**kwargs)
    monkeypatch.setattr(origin_notice, "deliver", capture)
    transport = _FakeTransport()
    factory = _thread_factory([])
    monkeypatch.setattr(runtime, "notify_result", partial(
        runtime.notify_result, transport=transport, transport_factory=factory,
    ))
    # When: the terminal producer reports cancellation or expiry.
    if outcome == "CANCELLED":
        watch._notify_cancelled(saved_record, transport, transport_factory=factory)
    else:
        runtime.notify_expired(saved_record)
    # Then: both producers preserve saved card coordinates and their masked subject.
    [message] = envelopes
    assert isinstance(message, OwnerMessage)
    assert (message.location.guild_id, message.location.message_id) == ("111", "333")
    assert message.subject == (saved_record.action_hash[:19] if outcome == "CANCELLED" else saved_record.title)
