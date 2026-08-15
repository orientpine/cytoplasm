"""Owner-bound approval adapters for the W6-2 repair lifecycle."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from automation.interop.injection_adapter import InboundEvent, accept_test_event
from automation.interop.approval_surface import ApprovalKind, ApprovalSurface, reaction_instruction
from automation.repair.repair_ops_git import RepairOpsError
from automation.repair.repair_patch_binding import PatchBindingError, content_action_hash, load_patch_artifact


def repair_action_hash(ticket_id: str, patch_name: str) -> str:
    """Return the pre-content-binding action hash.

    Name-only, so it cannot express WHICH bytes were approved. It survives for
    exactly two reasons: the signed-injection E2E path (isolated from production
    by ``E2E_TEST_MODE``) and re-deriving the binding of records written before
    content binding shipped. It must never authorise a production apply.
    """
    return hashlib.sha256(f"repair:{ticket_id}:{patch_name}".encode()).hexdigest()


def manual_approval_text(ticket_id: str, action_hash: str) -> str:
    """Build the only approval text ManualOwnerApproval accepts."""
    return f"APPROVE repair {action_hash} ticket:{ticket_id}"


def _legacy_test_channel_name() -> str:
    return ApprovalSurface.SKILL_APPROVALS.value.removeprefix("skill-")


@dataclass(frozen=True, slots=True)
class SignedOwnerApproval:
    """Accept an owner-bound HMAC injection only in an explicitly local E2E run."""

    owner_id: str
    approval_log: Path
    event: InboundEvent | None
    signature: str | None
    secret: bytes | None
    e2e_test_mode: bool

    def permits(self, ticket_id: str, patch_path: Path) -> bool:
        """Record a W0-6-shaped approval only after checking owner, target, and HMAC."""
        if not self.e2e_test_mode or self.event is None or self.signature is None or self.secret is None:
            return False
        action_hash = repair_action_hash(ticket_id, patch_path.name)
        expected = manual_approval_text(ticket_id, action_hash)
        if self.event.user_id != self.owner_id or self.event.channel_id != _legacy_test_channel_name() or self.event.text != expected:
            return False
        if not accept_test_event(self.event, self.signature, self.secret, e2e_test_mode=True):
            return False
        self.approval_log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = {"action": "repair.approval", "approval": {"channel": reaction_instruction(ApprovalKind.REPAIR, ApprovalSurface.SKILL_APPROVALS, name_surface=True), "message_id": self.event.event_id, "method": "signed_injection_e2e", "owner_id": self.owner_id}, "hash": action_hash, "result": {"status": "approved"}, "target_id": ticket_id, "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        with self.approval_log.open("a", encoding="utf-8") as handle:
            _ = handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        _ = self.approval_log.chmod(0o600)
        return True


@dataclass(frozen=True, slots=True)
class ApprovalReaction:
    """The minimum owner reaction facts needed by the W1-8 owner gate."""

    message_id: str
    user_id: str
    channel_id: str
    text: str
    bot: bool


@dataclass(frozen=True, slots=True)
class ManualOwnerApproval:
    """Reject bot and non-owner reactions before they can reach repository apply."""

    owner_id: str
    reaction: ApprovalReaction | None
    expected_channel_id: str = _legacy_test_channel_name()

    def permits(self, ticket_id: str, patch_path: Path) -> bool:
        """Accept one non-bot owner reaction bound to the patch bytes on disk NOW.

        Identity is checked before the patch is read, so a bot or a stranger never
        causes a file access. A reaction that survives identity but names a
        different action is not merely "not approved" — it means the patch changed
        after cha decided, or the record predates content binding. That raises
        instead of returning False: the caller treats False as "still awaiting
        approval" and exits zero, which would let the watcher retire the record and
        record an approval that never authorised these bytes.
        """
        if self.reaction is None or self.reaction.bot or self.reaction.user_id != self.owner_id:
            return False
        if self.reaction.channel_id != self.expected_channel_id:
            return False
        expected = manual_approval_text(ticket_id, _content_action_hash(ticket_id, patch_path))
        if self.reaction.text != expected:
            raise RepairOpsError("owner approval does not bind the patch now on disk")
        return True


def _content_action_hash(ticket_id: str, patch_path: Path) -> str:
    try:
        artifact = load_patch_artifact(patch_path)
    except PatchBindingError as error:
        raise RepairOpsError("repair patch cannot be bound to an owner approval") from error
    return content_action_hash(ticket_id, patch_path.name, artifact.patch_sha256, artifact.changes)
