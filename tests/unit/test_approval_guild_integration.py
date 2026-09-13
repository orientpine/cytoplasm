"""Real approval posting and draft persistence through injected Discord REST."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from email.message import Message
from importlib import import_module
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote

import pytest

from automation.interop.approval_lifecycle import ApprovalIntent, PostedApproval
from automation.interop.approval_surface import ApprovalBinding, ApprovalKind, ApprovalSurface
from automation.interop.reaction_approval import DiscordTransport
from automation.obsidian_write.config import ObsidianWriteConfig
from automation.plaud_sync import effects_live
from automation.plaud_sync.model import PlaudSyncState
from automation.plaud_sync.store import load_state, save_note_body, save_state
from tests.unit.test_approval_directory import FakeApi
from tests.unit.test_plaud_sync_effects import _BASE, _BODY
from tests.unit.test_mail_budget_repair_approval_characterization import _new_mail_draft, _new_budget_draft
budget_core = import_module("budget_core")
budget_gate = import_module("budget_gate")
triage_core = import_module("triage_core")
triage_gate = import_module("triage_gate")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/calendar/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/wiki/scripts"))
calendar_core = import_module("calendar_core")
calendar_gate = import_module("calendar_gate")
wiki_gate = import_module("wiki_gate")
wiki_approval = import_module("wiki_approval")


@pytest.mark.parametrize("guild", [None, "111"])
def test_approval_guild_is_persisted_when_live_plaud_posts(guild: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a real persisted note, directory, lifecycle, transport, lease, and journal.
    config = tmp_path / "interop.json"
    config.write_text(json.dumps({"agent_chat_channel_id": "222"}), encoding="utf-8")
    monkeypatch.setenv("INTEROP_CONFIG", str(config))
    monkeypatch.setattr(effects_live, "load_config", lambda: ObsidianWriteConfig("git@example.invalid:owner/vault.git", tmp_path / "clone"))
    record = replace(_BASE, status="planned", message_id=None, channel_id="", approval_thread_id=None)
    state_path = tmp_path / "plaud.json"
    save_state(state_path, PlaudSyncState(1, None, {record.recording_id: record}))
    save_note_body(tmp_path, record.recording_id, _BODY)
    api = FakeApi("identity-a", {
        ("POST", "/channels/222/messages"): {"id": "666"},
        ("POST", "/channels/222/messages/666/threads"): {"id": "444", "guild_id": guild},
        ("GET", "/channels/444"): {"type": 11, "name": "request", "parent_id": "222"},
        ("POST", "/channels/444/messages"): {"id": "555"},
        ("PUT", f"/channels/444/messages/555/reactions/{quote('✅', safe='')}/@me"): None,
        ("PUT", f"/channels/444/messages/555/reactions/{quote('⛔', safe='')}/@me"): None,
    })
    monkeypatch.setattr(DiscordTransport, "api", staticmethod(api))
    effects = effects_live.build_effects(state_path=state_path, token="identity-a", owner_id="333", now=datetime(2026, 9, 1, tzinfo=UTC))
    # When: the public live-effects entry point posts the approval card.
    receipt = effects.post_approval(record)
    # Then: real durable storage carries the message, thread, guild, and original hash.
    assert receipt == ("555", "444")
    stored = load_state(state_path).records[record.recording_id]
    assert (stored.approval_thread_id, stored.approval_guild_id, stored.action_hash) == ("444", guild, record.action_hash)
    assert len(api.calls) == 6


def test_approval_guild_error_path_leaves_record_unchanged_when_discord_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a complete local note but a refused thread-create call.
    config = tmp_path / "interop.json"
    config.write_text(json.dumps({"agent_chat_channel_id": "222"}), encoding="utf-8")
    monkeypatch.setenv("INTEROP_CONFIG", str(config))
    monkeypatch.setattr(effects_live, "load_config", lambda: ObsidianWriteConfig("git@example.invalid:owner/vault.git", tmp_path / "clone"))
    record = replace(_BASE, status="planned", message_id=None, channel_id="", approval_thread_id=None)
    path = tmp_path / "plaud.json"
    save_state(path, PlaudSyncState(1, None, {record.recording_id: record}))
    save_note_body(tmp_path, record.recording_id, _BODY)
    api = FakeApi("identity-a", {("POST", "/channels/222/messages"): HTTPError("https://discord.test", 403, "refused", Message(), None)})
    monkeypatch.setattr(DiscordTransport, "api", staticmethod(api))
    effects = effects_live.build_effects(state_path=path, token="identity-a", owner_id="333", now=datetime(2026, 9, 1, tzinfo=UTC))
    # When: the public entry point encounters the refusal.
    receipt = effects.post_approval(record)
    # Then: no new exception or partial record is introduced.
    assert receipt is None
    assert load_state(path).records[record.recording_id] == record
    assert len(api.calls) == 1


@pytest.mark.parametrize("producer", ["mail", "budget", "calendar", "wiki"])
def test_guild_is_durable_when_dictionary_producer_binds(producer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: actual producer stores rooted in this test, with distinct routing metadata.
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path))
    monkeypatch.setenv("BUDGET_GATE_DIR", str(tmp_path))
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path))
    monkeypatch.setattr(wiki_gate, "GATE_DIR", tmp_path)
    binding = ApprovalBinding(ApprovalKind.TODO, ApprovalSurface.AGENT_CHAT_THREAD, "444", 8, "111")
    # When: the producer stamps its existing draft through the real durable writer.
    match producer:
        case "mail":
            draft = _new_mail_draft()
            triage_gate.set_approval_binding(draft, kind="reply", surface=str(binding.surface), channel_id=binding.channel_id, policy_version=8, approval_thread_id=binding.channel_id, approval_guild_id=binding.guild_id)
            stored = triage_gate.load_draft(draft["id"])
            digest = triage_core.draft_sha256(stored)
        case "budget":
            draft = _new_budget_draft()
            budget_gate.set_message_id(draft, "555", binding)
            stored = budget_gate.load_draft(draft["id"])
            digest = budget_core.draft_sha256(stored)
        case "calendar":
            draft = calendar_gate.create_draft(action="delete", argv=("gws", "calendar", "events", "delete"), calendar_id="primary", event_id="synthetic", summary="request", start="", end="", channel_id="")
            calendar_gate.bind_approval_thread(draft, binding.channel_id, binding.guild_id)
            stored = calendar_gate.load_draft(draft["id"])
            digest = calendar_core.draft_sha256(stored)
        case "wiki":
            draft = {"id": "synthetic", "status": "pending", "sha256": "a" * 64}
            gate = wiki_approval.WikiApprovalGate(draft, binding)
            gate.commit(ApprovalIntent("wiki:create:synthetic", draft["sha256"], "444"), PostedApproval("555", "444"), "2026-09-01T00:00:00Z")
            stored = wiki_gate.load_draft(draft["id"])
            digest = stored["sha256"]
        case _:
            raise AssertionError(producer)
    # Then: both coordinates survive disk and adding metadata cannot rehash content.
    assert (stored["approval_thread_id"], stored["approval_guild_id"]) == ("444", "111")
    assert digest == draft["sha256"]


def test_guild_survives_when_watch_merges_mid_tick_binding() -> None:
    # Given: posting persisted guild metadata after the tick took its snapshot.
    watch = import_module("automation.plaud_sync.cron.plaud_sync_watch")
    step = import_module("automation.plaud_sync.watch_step")
    before = PlaudSyncState(1, None, {_BASE.recording_id: _BASE})
    persisted = PlaudSyncState(1, None, {_BASE.recording_id: replace(_BASE, approval_guild_id="111")})
    result = step.ResolveResult(before, (), (), ())
    # When: the real watch merge adopts the effect's routing metadata.
    merged = watch._merge_effect_bindings(before, result, persisted)
    # Then: the final snapshot cannot erase the guild written during posting.
    assert merged.state.records[_BASE.recording_id].approval_guild_id == "111"
    assert merged.state.records[_BASE.recording_id].action_hash == _BASE.action_hash


@pytest.mark.parametrize("cache_valid", [True, False])
def test_guild_survives_final_save_when_memory_watch_posts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_valid: bool) -> None:
    # Given: real cron state, live effects, lifecycle and transport; only external I/O is injected.
    from automation.memory_relocate import effects_live as memory_effects
    from automation.memory_relocate.binding import RelocationHashFields, relocation_action_hash
    from automation.memory_relocate.cron import memory_relocate_watch as watch
    from automation.memory_relocate.model import RelocationState, record_key
    from automation.memory_relocate.store import load_state as load_memory, save_state as save_memory
    from tests.unit.test_memory_relocate_effects_live import _record

    config = tmp_path / "interop.json"
    config.write_text(json.dumps({"agent_chat_channel_id": "222"}), encoding="utf-8")
    monkeypatch.setenv("INTEROP_CONFIG", str(config))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "identity-a")
    monkeypatch.setattr(memory_effects, "load_config", lambda: ObsidianWriteConfig("git@example.invalid:owner/vault.git", tmp_path / "clone"))
    monkeypatch.setattr(memory_effects, "recover_entry_text", lambda memory_dir, record: "synthetic memory")
    monkeypatch.setattr(watch, "_owner_id", lambda: "333")
    path = tmp_path / "state.json"
    monkeypatch.setattr(watch, "STATE_PATH", path)
    monkeypatch.setattr(watch, "MEMORY_DIR", tmp_path / "memories")
    monkeypatch.setattr(watch, "RAG_STATE_PATH", tmp_path / "rag.json")
    legacy = _record(entry_text="synthetic memory")
    expected_hash = relocation_action_hash(RelocationHashFields(legacy.source_kind, legacy.entry_sha256, legacy.note_relpath, legacy.note_plan_sha256))
    record = replace(legacy, channel_id="", message_id=None, action_hash=expected_hash)
    key = record_key(record.source_kind, record.entry_sha256)
    save_memory(path, RelocationState(1, {key: record}))
    if not cache_valid:
        (tmp_path / "approval-directory.json").write_text("{", encoding="utf-8")
    api = FakeApi("identity-a", {
        ("POST", "/channels/222/messages"): {"id": "666"},
        ("POST", "/channels/222/messages/666/threads"): {"id": "444", "guild_id": "111"},
        ("GET", "/channels/444"): {"type": 11, "name": "request", "parent_id": "222"},
        ("POST", "/channels/444/messages"): {"id": "555"},
        ("PUT", f"/channels/444/messages/555/reactions/{quote('✅', safe='')}/@me"): None,
        ("PUT", f"/channels/444/messages/555/reactions/{quote('⛔', safe='')}/@me"): None,
    })
    monkeypatch.setattr(DiscordTransport, "api", staticmethod(api))
    # When: the actual runnable cron entry point completes its final durable save.
    result = watch.run_once(datetime(2026, 9, 1, tzinfo=UTC))
    # Then: posting succeeds even without cache, and available guild metadata survives the tick.
    stored = load_memory(path).relocations[key]
    assert result.posted == (key,)
    assert (stored.message_id, stored.channel_id, stored.approval_guild_id) == ("555", "444", "111" if cache_valid else None)
    assert stored.action_hash == expected_hash
    assert len(api.calls) == 6


def test_guild_is_durable_when_coordination_commits(tmp_path: Path) -> None:
    # Given: a real coordination producer and JSONL store, not a pre-stamped record.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/coordination/scripts"))
    coordination = import_module("coordination_approval")
    pending = import_module("coordination_pending")
    payload = coordination.CoordinationApprovalPayload(
        draft={"id": "synthetic", "sha256": "a" * 64, "created": "2026-09-01T00:00:00Z"},
        slot="2026-09-02T09:00:00+00:00", summary="request", correlation="coord-synthetic",
        duration_min=30, content="request",
    )
    binding = ApprovalBinding(ApprovalKind.COORDINATION, ApprovalSurface.AGENT_CHAT_THREAD, "444", 8, "111")
    store = pending.PendingConfirmStore(tmp_path / "pending.jsonl")
    gate = coordination.CoordinationApprovalGate(payload, store, "333", binding)
    intent = coordination.confirm_intent(payload, binding)
    # When: the real producer commits the posted receipt through its durable writer.
    gate.commit(intent, PostedApproval("555", "444"), "2026-09-01T00:00:00Z")
    # Then: coordinates originate in the binding, and the consent digest remains unchanged.
    stored, = store.load()
    assert (stored.approval_thread_id, stored.approval_guild_id, stored.sha256) == ("444", "111", payload.draft["sha256"])
