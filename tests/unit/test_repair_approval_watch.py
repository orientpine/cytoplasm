from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path


import pytest

from automation.repair import repair_ops_cli
from automation.repair.repair_ops_adapters import CodexPlanner, StaticPlanner
from automation.repair.repair_ops_cli import RepairOpsConfig
from automation.repair.repair_ops_pending import CANCEL_EMOJI, APPROVE_EMOJI, PendingRepairApproval, PendingRepairApprovalStore, PostingOwnerApproval
from automation.repair.repair_ops_reaction_watch import RepairApprovalWatcher
from automation.repair.repair_patch_binding import content_action_hash, load_patch_artifact


OWNER_ID = "280680578314010625"
NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
PATCH_BODY = (
    "diff --git a/automation/mod.py b/automation/mod.py\n"
    "--- a/automation/mod.py\n"
    "+++ b/automation/mod.py\n"
    "@@ -1,2 +1,2 @@\n"
    " context\n"
    "-old\n"
    "+new\n"
)


def _patch(tmp_path: Path) -> Path:
    """A real patch: the approval is bound to its bytes, so it must exist."""
    target = tmp_path / "plans" / "t-repair-1" / "patch.diff"
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = target.write_text(PATCH_BODY, encoding="utf-8")
    return target


def _expected_hash(patch: Path) -> str:
    artifact = load_patch_artifact(patch)
    return content_action_hash("t-repair-1", patch.name, artifact.patch_sha256, artifact.changes)


@dataclass
class FakeDiscord:
    message_content: str = ""
    reactions: dict[str, tuple[tuple[str, bool], ...]] = field(default_factory=dict)
    posts: list[str] = field(default_factory=list)
    added_reactions: list[tuple[str, str]] = field(default_factory=list)

    def post_approval(self, content: str) -> str:
        self.posts.append(content)
        self.message_content = content
        return "approval-message-1"

    def add_reaction(self, message_id: str, emoji: str) -> None:
        self.added_reactions.append((message_id, emoji))

    def content(self, message_id: str) -> str:
        del message_id
        return self.message_content

    def reaction_users(self, message_id: str, emoji: str) -> tuple[tuple[str, bool], ...]:
        del message_id
        return self.reactions.get(emoji, ())


@dataclass
class FakeRepairCommands:
    applied: list[PendingRepairApproval] = field(default_factory=list)
    discarded: list[tuple[PendingRepairApproval, str]] = field(default_factory=list)

    def apply(self, pending: PendingRepairApproval) -> bool:
        self.applied.append(pending)
        return True

    def discard(self, pending: PendingRepairApproval, reason: str) -> bool:
        self.discarded.append((pending, reason))
        return True


def _pending(
    store: PendingRepairApprovalStore, discord: FakeDiscord, patch: Path
) -> PendingRepairApproval:
    posted = PostingOwnerApproval(OWNER_ID, store, discord, now=lambda: NOW, nonce=lambda: "a" * 32)
    assert posted.permits("t-repair-1", patch) is False
    pending = store.get("t-repair-1")
    assert pending is not None
    return pending


def _watcher(
    store: PendingRepairApprovalStore, discord: FakeDiscord, commands: FakeRepairCommands, audit_log: Path
) -> RepairApprovalWatcher:
    return RepairApprovalWatcher(store, discord, commands, OWNER_ID, audit_log, now=lambda: NOW)


def test_post_when_sandbox_is_green_then_binds_hash_and_preadds_owner_reactions(tmp_path: Path) -> None:
    # Given: a green repair has a repository-only patch and no prior approval request.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    discord = FakeDiscord()
    approval = PostingOwnerApproval(OWNER_ID, store, discord, now=lambda: NOW, nonce=lambda: "a" * 32)

    # When: RepairAgent reaches the production owner-approval boundary.
    patch = _patch(tmp_path)
    permitted = approval.permits("t-repair-1", patch)

    # Then: it waits after posting a request bound to the patch BYTES, showing the
    # changed file and its deltas so cha's ✅ is consent to this code change.
    expected_hash = _expected_hash(patch)
    artifact = load_patch_artifact(patch)
    pending = store.get("t-repair-1")
    assert permitted is False
    assert pending == PendingRepairApproval(
        "t-repair-1",
        "patch.diff",
        expected_hash,
        "a" * 32,
        "approval-message-1",
        NOW,
        content_binding_version=2,
        patch_sha256=artifact.patch_sha256,
        changes=artifact.changes,
        patch_source_path=str(patch),
    )
    assert discord.posts == [
        "[repair] 승인 요청\n"
        + "- ticket: `t-repair-1`\n"
        + f"- action_hash: `{expected_hash}`\n"
        + f"- patch_sha256: `{artifact.patch_sha256}`\n"
        + "- changed_files: 1 total, +1/-1\n"
        + "  - automation/mod.py (+1/-1)\n"
        + "- repair_nonce: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`\n"
        + "- sandbox: PASS (offline-subset bank + repro GREEN)\n"
        + f"- patch_body: 비노출 — ops 호스트의 `{patch}` 에서 확인\n"
        + "- cha가 이 메시지에 ✅ 승인 또는 ⛔ 취소 리액션"
    ]
    assert discord.added_reactions == [("approval-message-1", APPROVE_EMOJI), ("approval-message-1", CANCEL_EMOJI)]
    # The path is announced on purpose so cha can inspect it; the BODY never is.
    assert "+new" not in discord.posts[0]
    assert "diff --git" not in discord.posts[0]


@pytest.mark.parametrize(
    ("reactions", "content", "expected_apply"),
    [
        ({APPROVE_EMOJI: ((OWNER_ID, False),)}, None, True),
        ({APPROVE_EMOJI: ((OWNER_ID, True),)}, None, False),
        ({APPROVE_EMOJI: (("other-user", False),)}, None, False),
        ({APPROVE_EMOJI: ((OWNER_ID, False),)}, "wrong hash and nonce", False),
    ],
)
def test_poller_when_reaction_is_not_a_bound_non_bot_owner_approve_then_does_not_apply(
    tmp_path: Path,
    reactions: dict[str, tuple[tuple[str, bool], ...]],
    content: str | None,
    expected_apply: bool,
) -> None:
    # Given: one pending repair request and an approval-channel reaction set.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    discord = FakeDiscord(reactions=reactions)
    pending = _pending(store, discord, _patch(tmp_path))
    if content is not None:
        discord.message_content = content
    commands = FakeRepairCommands()

    # When: the no-agent watcher polls that exact pending message.
    _watcher(store, discord, commands, tmp_path / "approvals.jsonl").run_once()

    # Then: only cha's own non-bot ✅ on the hash/nonce-bound message dispatches apply.
    assert (commands.applied == [pending]) is expected_apply
    assert store.get("t-repair-1") == (None if expected_apply else pending)


def test_poller_when_owner_reacts_cancel_and_approve_then_cancel_has_priority(tmp_path: Path) -> None:
    # Given: cha reacted with both terminal emojis on the same bound request.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    discord = FakeDiscord(reactions={APPROVE_EMOJI: ((OWNER_ID, False),), CANCEL_EMOJI: ((OWNER_ID, False),)})
    pending = _pending(store, discord, _patch(tmp_path))
    commands = FakeRepairCommands()

    # When: the watcher evaluates the reactions.
    _watcher(store, discord, commands, tmp_path / "approvals.jsonl").run_once()

    # Then: ⛔ wins, no patch apply is requested, and the pending state is removed.
    assert commands.applied == []
    assert commands.discarded == [(pending, "owner_cancelled")]
    assert store.get("t-repair-1") is None


def test_poller_when_pending_approval_expires_then_discards_without_apply(tmp_path: Path) -> None:
    # Given: a request is beyond the approval TTL.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    discord = FakeDiscord()
    pending = _pending(store, discord, _patch(tmp_path))
    commands = FakeRepairCommands()

    # When: the watcher polls at the first instant after its TTL.
    expired_now = NOW + timedelta(hours=24, seconds=1)
    RepairApprovalWatcher(store, discord, commands, OWNER_ID, tmp_path / "approvals.jsonl", now=lambda: expired_now).run_once()

    # Then: it discards the request and never asks the repair lifecycle to apply it.
    assert commands.applied == []
    assert commands.discarded == [(pending, "approval_expired")]
    assert store.get("t-repair-1") is None


def test_poller_when_owner_approves_then_records_manual_reaction_audit_without_patch_body(tmp_path: Path) -> None:
    # Given: cha approved a bound pending repair.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    discord = FakeDiscord(reactions={APPROVE_EMOJI: ((OWNER_ID, False),)})
    _ = _pending(store, discord, _patch(tmp_path))
    commands = FakeRepairCommands()
    audit_log = tmp_path / "approvals.jsonl"

    # When: the watcher dispatches that approval.
    _watcher(store, discord, commands, audit_log).run_once()

    # Then: its durable audit row names manual_reaction without leaking a patch body.
    audit = audit_log.read_text(encoding="utf-8")
    assert '"action":"repair.approval"' in audit
    assert '"method":"manual_reaction"' in audit
    assert f'"owner_id":"{OWNER_ID}"' in audit
    assert "diff --git" not in audit


def test_cli_when_e2e_mode_lacks_a_signed_injection_then_refuses_ambiguous_production_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: E2E_TEST_MODE is set but no event, signature, or secret is present.
    config = RepairOpsConfig("t-repair-1", tmp_path, tmp_path, tmp_path, tmp_path / "approvals.jsonl", None, None)

    def configured(_argv: list[str]) -> RepairOpsConfig:
        return config

    monkeypatch.setattr(repair_ops_cli, "_config", configured)
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", OWNER_ID)
    monkeypatch.setenv("E2E_TEST_MODE", "1")
    monkeypatch.delenv("REPAIR_E2E_SECRET", raising=False)

    # When: the ops entry point selects its approval path.
    status = repair_ops_cli.main()

    # Then: it refuses rather than falling through to a production Discord post.
    assert status == 2


def test_cli_defaults_to_codex_oauth_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = RepairOpsConfig("t-repair-1", tmp_path, tmp_path, tmp_path, tmp_path / "approvals.jsonl", None, None)
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.delenv("REPAIR_DIAGNOSIS_PROVIDER", raising=False)

    planner = repair_ops_cli.planner_for(config)

    assert isinstance(planner, CodexPlanner)


def test_cli_when_signed_e2e_sets_static_provider_then_uses_no_ops_secret_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the isolated signed E2E selects its fixture-only static planner.
    config = RepairOpsConfig("t-repair-1", tmp_path, tmp_path, tmp_path, tmp_path / "approvals.jsonl", None, None)
    monkeypatch.setenv("E2E_TEST_MODE", "1")
    monkeypatch.setenv("REPAIR_DIAGNOSIS_PROVIDER", "static-e2e")

    # When: the CLI builds its planner before the signed repair lifecycle starts.
    planner = repair_ops_cli.planner_for(config)

    # Then: the E2E never attempts to read a production model credential file.
    assert isinstance(planner, StaticPlanner)
