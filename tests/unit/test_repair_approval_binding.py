from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.interop import approval_surface
from automation.interop.approval_lease import ApprovalLease
from automation.interop.approval_lifecycle import ApprovalRequest, DecisionWatcher, WatchOutcome, WatchVerdict
from automation.interop.approval_surface import (
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    ChannelFacts,
)
from automation.repair.repair_ops_approval import repair_action_hash
from automation.repair import repair_ops_reaction_watch
from automation.repair import repair_ops_discord
from automation.repair.repair_ops_discord import RepairDiscordApi, RepairDiscordError
from automation.repair.repair_ops_pending import (
    PendingRepairApproval,
    PendingRepairApprovalStore,
    PostingOwnerApproval,
)
from automation.repair.repair_ops_reaction_watch import RepairApprovalWatcher


OWNER_ID = "280680578314010625"
APPROVALS_CHANNEL_ID = "1528936606856122421"
OTHER_CHANNEL_ID = "1528936606856122422"
OPS_OWNER_DM_CHANNEL_ID = "1528936606856122423"
AGENT_OWNER_DM_CHANNEL_ID = "1528936606856122424"
NOW = datetime(2026, 7, 26, 9, 0, tzinfo=UTC)
BINDING = ApprovalBinding(
    ApprovalKind.REPAIR,
    ApprovalSurface.SKILL_APPROVALS,
    APPROVALS_CHANNEL_ID,
    4,
)


@dataclass(frozen=True, slots=True)
class _PostingTransport:
    posts: list[str] = field(default_factory=list)
    reactions: list[tuple[str, str]] = field(default_factory=list)

    def post_approval(self, content: str) -> str:
        self.posts.append(content)
        return "message-1"

    def add_reaction(self, message_id: str, emoji: str) -> None:
        self.reactions.append((message_id, emoji))


@dataclass(frozen=True, slots=True)
class _Directory:
    facts: ChannelFacts

    def owner_dm(self) -> str:
        return "1528936606856122423"

    def skill_approvals(self) -> str:
        return APPROVALS_CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        if channel_id != APPROVALS_CHANNEL_ID:
            raise AssertionError(f"unexpected channel lookup: {channel_id}")
        return self.facts


@dataclass(frozen=True, slots=True)
class _OpsDirectory:
    owner_dm_channel_id: str
    owner_dm_facts: ChannelFacts | None
    owner_dm_calls: list[str] = field(default_factory=list)

    def owner_dm(self) -> str:
        self.owner_dm_calls.append(self.owner_dm_channel_id)
        return self.owner_dm_channel_id

    def skill_approvals(self) -> str:
        return APPROVALS_CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        if channel_id == APPROVALS_CHANNEL_ID:
            return ChannelFacts(0, "approvals", ())
        if channel_id == self.owner_dm_channel_id and self.owner_dm_facts is not None:
            return self.owner_dm_facts
        raise approval_surface.ApprovalSurfaceError("repair credential cannot describe this DM")


@dataclass(frozen=True, slots=True)
class _Commands:
    def apply(self, pending: PendingRepairApproval) -> bool:
        del pending
        return True

    def discard(self, pending: PendingRepairApproval, reason: str) -> bool:
        del pending, reason
        return True


def test_repair_request_persists_its_binding(tmp_path: Path) -> None:
    # Given: the ops posting flow has the resolved guild binding.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    approval = PostingOwnerApproval(
        OWNER_ID,
        store,
        _PostingTransport(),
        now=lambda: NOW,
        nonce=lambda: "a" * 32,
        binding=BINDING,
    )

    # When: it posts one repair approval request.
    patch = tmp_path / "plans" / "repair-1" / "patch.diff"
    patch.parent.mkdir(parents=True, exist_ok=True)
    _ = patch.write_text(
        "diff --git a/automation/mod.py b/automation/mod.py\n"
        "--- a/automation/mod.py\n"
        "+++ b/automation/mod.py\n"
        "@@ -1,2 +1,2 @@\n context\n-old\n+new\n",
        encoding="utf-8",
    )
    assert approval.permits("repair-1", patch) is False
    pending = store.get("repair-1")

    # Then: all binding facts round-trip as a concrete Discord snowflake.
    assert pending is not None
    assert (
        pending.kind,
        pending.surface,
        pending.channel_id,
        pending.policy_version,
    ) == (
        ApprovalKind.REPAIR,
        ApprovalSurface.SKILL_APPROVALS,
        APPROVALS_CHANNEL_ID,
        BINDING.policy_version,
    )
    assert pending.channel_id.isdigit()
    assert pending.channel_id != "approvals"


def test_repair_watch_reads_the_channel_from_the_stored_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a record is bound to #approvals while a resolver would now choose another id.
    pending = PendingRepairApproval(
        "repair-1",
        "patch.diff",
        repair_action_hash("repair-1", "patch.diff"),
        "nonce",
        "message-1",
        NOW,
        kind=ApprovalKind.REPAIR,
        surface=ApprovalSurface.SKILL_APPROVALS,
        channel_id=APPROVALS_CHANNEL_ID,
        policy_version=approval_surface.POLICY_VERSION,
    )
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(pending)
    seen_channels: list[str] = []

    def unexpected_resolution(
        kind: ApprovalKind,
        directory: approval_surface.ChannelDirectory,
        owner_id: str,
        skill_channel_id: str | None = None,
    ) -> ApprovalBinding:
        del kind, directory, owner_id, skill_channel_id
        return ApprovalBinding(
            ApprovalKind.REPAIR,
            ApprovalSurface.SKILL_APPROVALS,
            OTHER_CHANNEL_ID,
            approval_surface.POLICY_VERSION,
        )

    def capture_request(
        request: ApprovalRequest,
        watcher: DecisionWatcher,
        lease: ApprovalLease,
    ) -> WatchVerdict:
        del watcher, lease
        seen_channels.append(request.channel_id)
        return WatchVerdict(WatchOutcome.WAITING)

    monkeypatch.setattr(approval_surface, "resolve_new_binding", unexpected_resolution)
    monkeypatch.setattr(repair_ops_reaction_watch, "resolve_owner_decision", capture_request)
    watcher = RepairApprovalWatcher(
        store,
        _PostingTransport(),
        _Commands(),
        OWNER_ID,
        tmp_path / "audit.jsonl",
        now=lambda: NOW,
    )

    # When: the watcher maps the stored pending request to the shared lifecycle.
    watcher.run_once()

    # Then: no newly resolved channel can retarget the existing approval.
    assert seen_channels == [APPROVALS_CHANNEL_ID]


def test_assert_surface_accepts_the_guild_approvals_channel_and_refuses_a_mismatch() -> None:
    # Given: the repair bot's directory identifies the real guild approval surface.
    valid_directory = _Directory(ChannelFacts(0, "approvals", ()))
    discord = RepairDiscordApi("ops-token", BINDING, valid_directory, OWNER_ID)

    # When / Then: the exact policy binding is accepted.
    discord.assert_surface(BINDING)

    # Given: the same snowflake is described as a non-approval guild channel.
    invalid_directory = _Directory(ChannelFacts(0, "other-channel", ()))
    mismatched = RepairDiscordApi("ops-token", BINDING, invalid_directory, OWNER_ID)

    # When / Then: channel facts that contradict the binding fail closed.
    with pytest.raises(RepairDiscordError, match="approval surface"):
        mismatched.assert_surface(BINDING)


def test_repair_request_posts_to_its_own_bot_dm(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the repair bot can open and describe its own DM with the owner.
    directory = _OpsDirectory(
        OPS_OWNER_DM_CHANNEL_ID,
        ChannelFacts(1, "", (OWNER_ID,)),
    )
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "credential")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", OWNER_ID)
    monkeypatch.setattr(repair_ops_discord, "directory_for_ops", lambda token, owner: directory)

    # When: repair resolves the transport for a new owner approval request.
    discord = repair_ops_discord.configured_discord()

    # Then: every post is bound to the DM opened by the repair bot itself.
    assert directory.owner_dm_calls == [OPS_OWNER_DM_CHANNEL_ID]
    assert discord.binding == ApprovalBinding(
        ApprovalKind.REPAIR,
        ApprovalSurface.OWNER_DM,
        OPS_OWNER_DM_CHANNEL_ID,
        approval_surface.POLICY_VERSION,
    )
    assert all(
        approval_surface.surface_at_policy(ApprovalKind.REPAIR, version)
        is ApprovalSurface.SKILL_APPROVALS
        for version in range(5)
    )


def test_repair_refuses_a_dm_id_opened_by_another_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: another bot's owner DM cannot be described with the repair credential.
    directory = _OpsDirectory(AGENT_OWNER_DM_CHANNEL_ID, None)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "credential")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", OWNER_ID)
    monkeypatch.setattr(repair_ops_discord, "directory_for_ops", lambda token, owner: directory)

    # When / Then: repair fails closed instead of reusing the other bot's DM id.
    with pytest.raises(RepairDiscordError, match="surface cannot be resolved"):
        _ = repair_ops_discord.configured_discord()
