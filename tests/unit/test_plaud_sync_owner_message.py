"""Plaud result envelope delivery, separate from the near-limit effects test module."""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from automation.interop import origin_notice, owner_message
from automation.plaud_sync import effects_live
from automation.plaud_sync.cron import plaud_sync_watch as watch
from automation.plaud_sync.model import PlaudSyncState
from automation.plaud_sync.store import load_state, save_state
from automation.plaud_sync.watch_step import resolve_tick
from tests.unit.test_plaud_sync_effects import (
    _ABANDONED, _BASE, _WRITTEN, _RecordingSender, _notifier,
)


@pytest.mark.parametrize("outcome, expected", [("written", _WRITTEN), ("abandoned", _ABANDONED)])
@pytest.mark.parametrize("missing_module", [False, True], ids=["old-signature", "missing-module"])
@pytest.mark.parametrize("fallback", [False, True], ids=["thread", "fallback"])
def test_preserves_legacy_bytes_when_runtime_predates_envelopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    outcome: str, expected: str, missing_module: bool, fallback: bool,
) -> None:
    # Given: independently captured base bytes and either old-runtime failure mode.
    effects, transport = _notifier(tmp_path, monkeypatch)
    _RecordingSender.fail = fallback
    real_deliver = origin_notice.deliver
    if missing_module:
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    else:
        # Explicit facade signature is the compatibility boundary being tested.
        def old_deliver(
            *, api: origin_notice.ApiCall,
            transport_factory: Callable[[str], _RecordingSender],
            record: dict[str, str | None], thread_name: str, content: str,
            fallback: Callable[[str], str], outcome: origin_notice.ThreadOutcome | None = None,
        ) -> str:
            return str(real_deliver(
                api=api, transport_factory=transport_factory, record=record,
                thread_name=thread_name, content=content, fallback=fallback, outcome=outcome,
            ))
        monkeypatch.delattr(origin_notice, "ACCEPTS_OWNER_MESSAGE")
        monkeypatch.setattr(origin_notice, "deliver", old_deliver)
    record = replace(_BASE, approval_guild_id="111", approval_thread_id="222", message_id="333")
    # When: the result crosses the real notifier and facade.
    effects.notify_result(record, outcome)
    # Then: neither optional capability can change today's bytes or the bound destination.
    assert (transport.posted if fallback else _RecordingSender.sent) == [("222", expected)]


@pytest.fixture
def envelopes(monkeypatch: pytest.MonkeyPatch) -> list[owner_message.OwnerMessage]:
    captured: list[owner_message.OwnerMessage] = []
    real_render = owner_message.render

    def capture(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        captured.append(message)
        return real_render(message, destination=destination)

    monkeypatch.setattr(owner_message, "render", capture)
    return captured


@pytest.mark.parametrize("fallback", [False, True], ids=["thread", "fallback"])
@pytest.mark.parametrize("guild", ["111", None], ids=["guild", "legacy"])
def test_delivers_local_envelope_when_both_paths_target_approval_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    envelopes: list[owner_message.OwnerMessage], fallback: bool, guild: str | None,
) -> None:
    # Given: distinct origin/thread/card ids and a real facade with injected wire failure.
    effects, transport = _notifier(tmp_path, monkeypatch)
    _RecordingSender.fail = fallback
    record = replace(_BASE, status="written", approval_guild_id=guild,
                     channel_id="444", approval_thread_id="222", message_id="333")
    # When: vault save completion is posted.
    effects.notify_result(record, "written")
    # Then: both routes render the same local approval-card envelope, never a self-link.
    assert envelopes
    message = envelopes[-1]
    assert message.location == owner_message.Ref(
        scope="message", space="guild" if guild else "unknown", guild_id=guild,
        channel_id="222", message_id="333", search=("Discord 검색", "rec-001"),
    )
    assert message.subject_key == "rec-001" and message.subject == record.note_relpath
    assert message.fact == _WRITTEN
    assert message.detail == owner_message.Result(outcome="executed")
    assert message.owner == owner_message.Action(verb="none")
    assert message.agent_next is None and message.recovery == "not_applicable"
    ((channel, body),) = transport.posted if fallback else _RecordingSender.sent
    assert channel == record.approval_thread_id == "222"
    assert "discord.com/channels" not in body
    assert len(body.splitlines()) == 5
    assert not any(secret in repr(message) for secret in ("말씀", record.body_sha256, record.action_hash))
    assert [call[0] for call in transport.calls] == ([] if fallback else ["GET", "PATCH"])


@pytest.mark.parametrize("fallback", [False, True], ids=["thread", "fallback"])
@pytest.mark.parametrize("cancelled", [False, True], ids=["written", "cancelled"])
def test_tick_reports_execution_state_when_effect_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    envelopes: list[owner_message.OwnerMessage], fallback: bool, cancelled: bool,
) -> None:
    # Given: a posted card; only the external decision/write and Discord wire are injected.
    effects, transport = _notifier(tmp_path, monkeypatch)
    _RecordingSender.fail = fallback
    record = replace(_BASE, status="posted", approval_guild_id="111",
                     approval_thread_id="222", message_id="333")
    effects = replace(effects, probe_reaction=lambda _: "cancelled" if cancelled else "approved",
                      write_obsidian=lambda _: ("saved-ref", "saved-digest"))
    state = PlaudSyncState(version=1, last_poll_at=None, records={record.recording_id: record})
    # When: the actual FSM consumes a decision and dispatches the live result notifier.
    result = resolve_tick(state, effects=effects)
    # Then: the committed effect and delivered envelope agree, including the failed-thread path.
    assert result.state.records[record.recording_id].status == ("abandoned" if cancelled else "written")
    assert envelopes
    message = envelopes[-1]
    assert message.detail == owner_message.Result(outcome="cancelled" if cancelled else "executed")
    assert message.fact == (_ABANDONED if cancelled else _WRITTEN)
    assert message.subject == (record.recording_id if cancelled else record.note_relpath)
    assert (transport.posted if fallback else _RecordingSender.sent)


def test_keeps_ack_raw_when_execution_has_not_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, envelopes: list[owner_message.OwnerMessage],
) -> None:
    # Given: an approved request is not an executed result.
    effects, transport = _notifier(tmp_path, monkeypatch)
    # When: a nonterminal acknowledgement is dispatched.
    effects.notify_result(_BASE, "approved")
    # Then: no terminal envelope or thread archive is invented.
    assert envelopes == []
    assert _RecordingSender.sent == [("thread-1", _ABANDONED)]
    assert transport.calls == []


@pytest.mark.parametrize("fallback", [False, True], ids=["thread", "fallback"])
def test_preserves_legacy_bytes_when_renderer_rejects_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fallback: bool,
) -> None:
    # Given: rendering is optional and can reject a well-routed producer result.
    effects, transport = _notifier(tmp_path, monkeypatch)
    _RecordingSender.fail = fallback

    def reject(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        raise owner_message.OwnerMessageError(detail="message.location")

    monkeypatch.setattr(owner_message, "render", reject)
    # When: the result is dispatched through the facade's rendering boundary.
    effects.notify_result(_BASE, "written")
    # Then: the independently captured raw bytes remain deliverable.
    assert (transport.posted if fallback else _RecordingSender.sent) == [("thread-1", _WRITTEN)]


@pytest.mark.parametrize("fallback", [False, True], ids=["thread", "fallback"])
def test_cron_main_delivers_result_when_approved_note_is_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    envelopes: list[owner_message.OwnerMessage], fallback: bool,
) -> None:
    # Given: real cron/state/FSM/notifier; cloud, vault and Discord boundaries are injected.
    effects, transport = _notifier(tmp_path, monkeypatch)
    effects = replace(effects, write_obsidian=lambda _: ("saved-ref", "saved-digest"))
    _RecordingSender.fail = fallback
    record = replace(_BASE, approval_guild_id="111", approval_thread_id="222", message_id="333")
    state_path = tmp_path / "plaud.json"
    save_state(state_path, PlaudSyncState(1, None, {record.recording_id: record}))
    monkeypatch.setattr(effects_live, "build_effects", lambda **_: effects)
    monkeypatch.setattr(watch, "STATE_PATH", state_path)
    monkeypatch.setattr(watch, "LOCK_PATH", tmp_path / "watch.lock")
    monkeypatch.setattr(watch, "_load_env_secrets", lambda: None)
    monkeypatch.setattr(watch, "_owner_id", lambda: "9")
    monkeypatch.setattr(watch, "_discover", lambda state, _now: state)
    monkeypatch.setattr(watch, "_transcribe", lambda: None)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    # When: the actual runnable entry point drives an ordinary tick, never repost mode.
    exit_code = watch.main([])
    # Then: persistence and the delivered result reflect the completed write on either wire path.
    assert exit_code == 0
    assert load_state(state_path).records[record.recording_id].status == "written"
    assert envelopes
    assert envelopes[-1].detail == owner_message.Result(outcome="executed")
    ((channel, body),) = transport.posted if fallback else _RecordingSender.sent
    assert channel == "222" and "discord.com/channels" not in body
