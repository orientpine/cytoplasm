"""An owner ✅ authorises the patch that was shown, and nothing else."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.repair.repair_ops_approval import (
    ApprovalReaction,
    ManualOwnerApproval,
    manual_approval_text,
    repair_action_hash,
)
from automation.repair.repair_ops_core import RepairAgent, RepairPhase, RepairPlan, SandboxVerdict
from automation.repair.repair_ops_git import RepairOpsError
from automation.repair.repair_ops_pending import (
    APPROVE_EMOJI,
    PendingRepairApprovalStore,
    PostingOwnerApproval,
)
from automation.repair.repair_patch_binding import content_action_hash, load_patch_artifact

OWNER_ID = "280680578314010625"
CHANNEL = "1528936606856122423"
TICKET = "t_repair01"
NOW = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)

PATCH_A = """diff --git a/automation/mod.py b/automation/mod.py
--- a/automation/mod.py
+++ b/automation/mod.py
@@ -1,2 +1,2 @@
 context
-old
+harmless replacement
"""

PATCH_B = """diff --git a/automation/mod.py b/automation/mod.py
--- a/automation/mod.py
+++ b/automation/mod.py
@@ -1,2 +1,2 @@
 context
-old
+hostile replacement PATCH_BODY_SENTINEL_9F3A
"""


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(body, encoding="utf-8")
    return path


def _approved_hash(patch: Path) -> str:
    artifact = load_patch_artifact(patch)
    return content_action_hash(TICKET, patch.name, artifact.patch_sha256, artifact.changes)


def _reaction(text: str) -> ApprovalReaction:
    return ApprovalReaction("message-1", OWNER_ID, CHANNEL, text, False)


# ----------------------------------------------------------------- apply gate


@dataclass
class _Planner:
    plan_value: RepairPlan

    def plan(self, ticket_id: str, private_log: str) -> RepairPlan:
        del ticket_id, private_log
        return self.plan_value


@dataclass
class _Sandbox:
    def validate(self, plan: RepairPlan) -> SandboxVerdict:
        del plan
        return SandboxVerdict(True, True)


@dataclass
class _Repository:
    applied: list[Path] = field(default_factory=list)

    def apply(self, patch_path: Path) -> str:
        self.applied.append(patch_path)
        return "repair-commit"

    def register_bank(self, scenario_path: Path) -> str | None:
        del scenario_path
        return None

    def bank_passes(self, scenario_path: Path | None) -> bool:
        del scenario_path
        return True

    def bank_allows_apply(self) -> bool:
        return True

    def revert(self, commit: str) -> None:
        del commit


@dataclass
class _Tickets:
    def complete(self, ticket_id: str, summary: str) -> None:
        del ticket_id, summary

    def reopen(self, ticket_id: str, summary: str) -> None:
        del ticket_id, summary


@dataclass
class _Docs:
    document: Path

    def write(self, plan: RepairPlan, commit: str) -> Path:
        del plan, commit
        return self.document


def _agent(tmp_path: Path, patch: Path, approval: ManualOwnerApproval) -> tuple[RepairAgent, _Repository]:
    plan = RepairPlan(TICKET, "redacted diagnosis", tmp_path / "repro.sh", patch)
    repository = _Repository()
    agent = RepairAgent(
        _Planner(plan), _Sandbox(), approval, repository, _Tickets(), _Docs(tmp_path / "patch.md")
    )
    return agent, repository


def test_approval_of_the_shown_patch_still_applies(tmp_path: Path) -> None:
    # Given: cha approved exactly the patch that is on disk.
    patch = _write(tmp_path / "plans" / TICKET / "patch.diff", PATCH_A)
    approval = ManualOwnerApproval(OWNER_ID, _reaction(manual_approval_text(TICKET, _approved_hash(patch))), CHANNEL)
    agent, repository = _agent(tmp_path, patch, approval)

    # When: the watcher-dispatched apply runs.
    outcome = agent.repair(TICKET, "private repair log")

    # Then: the normal path is untouched.
    assert outcome.phase is RepairPhase.COMPLETED
    assert repository.applied == [patch]


def test_patch_replaced_after_the_owner_reacted_is_never_applied(tmp_path: Path) -> None:
    # Given: cha approved patch A, and patch B now sits under the same name.
    patch = _write(tmp_path / "plans" / TICKET / "patch.diff", PATCH_A)
    approval = ManualOwnerApproval(OWNER_ID, _reaction(manual_approval_text(TICKET, _approved_hash(patch))), CHANNEL)
    _ = _write(patch, PATCH_B)
    agent, repository = _agent(tmp_path, patch, approval)

    # When / Then: the apply refuses loudly, so the watcher sees a non-zero child
    # and neither retires the record nor writes an "approved" audit row.
    with pytest.raises(RepairOpsError):
        _ = agent.repair(TICKET, "private repair log")
    assert repository.applied == []


def test_legacy_name_only_approval_can_never_authorise_an_apply(tmp_path: Path) -> None:
    # Given: a record approved under the old name-only binding.
    patch = _write(tmp_path / "plans" / TICKET / "patch.diff", PATCH_A)
    legacy = manual_approval_text(TICKET, repair_action_hash(TICKET, patch.name))
    agent, repository = _agent(tmp_path, patch, ManualOwnerApproval(OWNER_ID, _reaction(legacy), CHANNEL))

    # When / Then: an approval that never saw the content cannot authorise it.
    with pytest.raises(RepairOpsError):
        _ = agent.repair(TICKET, "private repair log")
    assert repository.applied == []


def test_a_bot_reaction_is_still_rejected_before_the_patch_is_read(tmp_path: Path) -> None:
    # Given: a bot reaction carrying otherwise valid text, and no patch at all.
    missing = tmp_path / "plans" / TICKET / "patch.diff"
    bot = ApprovalReaction("message-1", OWNER_ID, CHANNEL, manual_approval_text(TICKET, "x"), True)

    # When / Then: identity is checked first, so a missing patch never even matters.
    assert ManualOwnerApproval(OWNER_ID, bot, CHANNEL).permits(TICKET, missing) is False


# ------------------------------------------------------------- posting/supersede


@dataclass
class _Surface:
    calls: list[str] = field(default_factory=list)
    messages: dict[str, str] = field(default_factory=dict)
    reactions: dict[tuple[str, str], tuple[tuple[str, bool], ...]] = field(default_factory=dict)
    posts: list[str] = field(default_factory=list)

    def post_approval(self, content: str) -> str:
        message_id = f"msg-{len(self.posts) + 1}"
        self.posts.append(content)
        self.messages[message_id] = content
        self.calls.append(f"post:{message_id}")
        return message_id

    def add_reaction(self, message_id: str, emoji: str) -> None:
        del message_id, emoji

    def content(self, message_id: str) -> str:
        return self.messages.get(message_id, "")

    def reaction_users(self, message_id: str, emoji: str) -> tuple[tuple[str, bool], ...]:
        return self.reactions.get((message_id, emoji), ())

    def delete_message(self, message_id: str) -> None:
        self.calls.append(f"delete:{message_id}")
        _ = self.messages.pop(message_id, None)


def _posting(tmp_path: Path, surface: _Surface) -> PostingOwnerApproval:
    return PostingOwnerApproval(
        OWNER_ID,
        PendingRepairApprovalStore(tmp_path / "pending"),
        surface,
        now=lambda: NOW,
        nonce=lambda: "a" * 32,
    )


def test_the_posted_request_is_bound_to_the_patch_bytes(tmp_path: Path) -> None:
    # Given: a green sandbox and one patch on disk.
    patch = _write(tmp_path / "plans" / TICKET / "patch.diff", PATCH_A)
    surface = _Surface()
    approval = _posting(tmp_path, surface)

    # When: the request is posted.
    assert approval.permits(TICKET, patch) is False
    record = approval.store.get(TICKET)

    # Then: the record carries the summary and the hash covers the bytes,
    # and the body itself never appears in the posted message.
    assert record is not None
    assert record.action_hash == _approved_hash(patch)
    assert record.patch_sha256 == load_patch_artifact(patch).patch_sha256
    assert record.changes is not None
    assert record.patch_source_path == str(patch)
    assert "PATCH_BODY_SENTINEL_9F3A" not in surface.posts[0]
    assert "harmless replacement" not in surface.posts[0]


def test_changing_the_patch_before_any_decision_supersedes_the_request(tmp_path: Path) -> None:
    # Given: a live request bound to patch A, untouched by cha.
    patch = _write(tmp_path / "plans" / TICKET / "patch.diff", PATCH_A)
    surface = _Surface()
    approval = _posting(tmp_path, surface)
    assert approval.permits(TICKET, patch) is False

    # When: the patch content changes under the same file name.
    _ = _write(patch, PATCH_B)
    assert approval.permits(TICKET, patch) is False

    # Then: the stale message dies before its record and one fresh request lives.
    assert surface.calls.index("delete:msg-1") < surface.calls.index("post:msg-2")
    record = approval.store.get(TICKET)
    assert record is not None
    assert record.action_hash == _approved_hash(patch)
    assert len(surface.posts) == 2


def test_an_unreadable_patch_posts_nothing(tmp_path: Path) -> None:
    # Given: no patch where the plan says one should be.
    surface = _Surface()
    approval = _posting(tmp_path, surface)

    # When / Then: the gate stays silent rather than asking cha to approve nothing.
    assert approval.permits(TICKET, tmp_path / "plans" / TICKET / "patch.diff") is False
    assert surface.posts == []
    assert approval.store.get(TICKET) is None


def test_a_legacy_record_the_owner_ignored_is_superseded_by_a_bound_request(tmp_path: Path) -> None:
    # Given: an old name-only request still live and unreacted.
    patch = _write(tmp_path / "plans" / TICKET / "patch.diff", PATCH_A)
    surface = _Surface()
    approval = _posting(tmp_path, surface)
    store = approval.store
    legacy_content = _legacy_record(store, surface, patch)

    # When: the producer reaches the boundary with a content-bound intent.
    assert approval.permits(TICKET, patch) is False

    # Then: the legacy message is deleted first and replaced by the v2 request.
    assert surface.calls == ["delete:msg-legacy", "post:msg-1"]
    assert legacy_content not in surface.posts
    record = store.get(TICKET)
    assert record is not None
    assert record.content_binding_version == 2


def test_a_legacy_record_the_owner_approved_is_left_for_the_watcher(tmp_path: Path) -> None:
    # Given: cha already reacted ✅ on the old name-only request.
    patch = _write(tmp_path / "plans" / TICKET / "patch.diff", PATCH_A)
    surface = _Surface()
    approval = _posting(tmp_path, surface)
    _ = _legacy_record(approval.store, surface, patch)
    surface.reactions[("msg-legacy", APPROVE_EMOJI)] = ((OWNER_ID, False),)

    # When: a content-bound request tries to take its place.
    assert approval.permits(TICKET, patch) is False

    # Then: an owner decision is never destroyed — it is deferred, and the apply
    # path is what refuses it (see the legacy apply test above).
    assert surface.calls == []
    assert surface.posts == []


def _legacy_record(store: PendingRepairApprovalStore, surface: _Surface, patch: Path) -> str:
    """Plant a pre-content-binding record and its already-posted message."""
    from automation.repair.repair_ops_pending import PendingRepairApproval, approval_request_content

    legacy = PendingRepairApproval(
        TICKET,
        patch.name,
        repair_action_hash(TICKET, patch.name),
        "b" * 32,
        "msg-legacy",
        NOW,
    )
    store.save(legacy)
    content = approval_request_content(legacy)
    surface.messages["msg-legacy"] = content
    return content
