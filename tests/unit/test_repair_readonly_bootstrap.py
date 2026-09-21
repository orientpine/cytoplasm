"""Reader bootstrap never creates a kind or ticket approval thread."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from automation.interop.approval_surface import ApprovalKind, ApprovalSurface, ChannelFacts, POLICY_VERSION
from automation.repair import repair_ops_cli as cli, repair_ops_discord as discord, repair_ops_reaction_watch as watch
from automation.repair.repair_ops_approval import repair_action_hash
from automation.repair.repair_ops_pending import PendingRepairApproval, PendingRepairApprovalStore, approval_request_content
from tests.unit.test_repair_approval_binding import (
    AGENT_CHAT_CHANNEL_ID, NOW, OPS_THREAD_ID, OWNER_ID, TICKET,
    _FakeDiscordHttp, _OpsDirectory,
)
from tests.unit.test_repair_ops_residuals import config_at


@dataclass(frozen=True, slots=True)
class ReaderRuntime:
    directory: _OpsDirectory
    http: _FakeDiscordHttp
    pending: PendingRepairApproval


@pytest.fixture
def reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ReaderRuntime:
    directory = _OpsDirectory(OPS_THREAD_ID, ChannelFacts(11, "stored", (), AGENT_CHAT_CHANNEL_ID))
    pending = PendingRepairApproval(
        TICKET, "patch.diff", repair_action_hash(TICKET, "patch.diff"), "nonce", "message", NOW,
        kind=ApprovalKind.REPAIR, surface=ApprovalSurface.AGENT_CHAT_THREAD,
        channel_id=OPS_THREAD_ID, policy_version=POLICY_VERSION,
    )
    http = _FakeDiscordHttp(messages={pending.message_id: approval_request_content(pending)})
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", OWNER_ID)
    monkeypatch.setenv("REPAIR_APPROVAL_PENDING_ROOT", str(tmp_path / "pending"))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.setattr(discord, "directory_for_ops", lambda *args: directory)
    monkeypatch.setattr(discord, "_open_discord", http)
    return ReaderRuntime(directory, http, pending)


def test_reader_uses_stored_binding_when_bootstrapped(reader: ReaderRuntime) -> None:
    # Given: a stored approval thread, with no need to create another surface.
    # When: the reader bootstraps and polls the stored message.
    bound = discord.configured_discord().for_pending(reader.pending)
    content = bound.content(reader.pending.message_id)
    # Then: binding and reads use only that record, with no thread/message creation.
    assert bound.binding.channel_id == reader.pending.channel_id
    assert content == reader.http.messages[reader.pending.message_id]
    assert reader.directory.thread_calls == reader.directory.request_specs == reader.http.posts == []


def test_unbound_transport_refuses_posting_when_no_record_is_bound(reader: ReaderRuntime) -> None:
    # Given: the read-only bootstrap has no approved destination.
    api = discord.configured_discord()
    # When / Then: accidental posting is refused at the transport boundary.
    with pytest.raises(discord.RepairDiscordError, match="binding"):
        api.post_approval("synthetic")
    assert reader.http.posts == []


def test_watcher_opens_no_thread_when_pending_store_is_empty(
    reader: ReaderRuntime, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a real watcher entry point with an empty isolated store.
    monkeypatch.setattr(watch, "load_approval_reminder_config", lambda: None)
    # When: one read-only tick runs.
    result = watch.main()
    # Then: an empty tick creates no Discord artifact.
    assert result == 0
    assert reader.directory.thread_calls == reader.directory.request_specs == reader.http.posts == []


def test_apply_reader_opens_no_thread_when_owner_has_not_reacted(reader: ReaderRuntime, tmp_path: Path) -> None:
    # Given: an existing approval card and no owner reaction.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(reader.pending)
    config = config_at(tmp_path)
    from dataclasses import replace
    config = replace(config, ticket_id=reader.pending.ticket_id)
    # When: the apply-approved entry checks the stored card through the real API.
    result = cli._apply_approved(config)
    # Then: it refuses without creating a kind/request thread or posting a message.
    assert result == 1
    assert reader.directory.thread_calls == reader.directory.request_specs == reader.http.posts == []
