"""Posting adapter that stamps a resolved repair approval binding."""
from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Protocol

from automation.interop.approval_lease import FileKeyLease, PostingJournal
from automation.interop.approval_lifecycle import ApprovalIntent, request_owner_approval
from automation.interop.approval_surface import ApprovalBinding
from automation.repair.repair_ops_pending import ApprovalRequestTransport, PendingRepairApprovalStore
from automation.repair.repair_patch_binding import PatchBindingError, content_action_hash, load_patch_artifact


class BoundApprovalTransport(ApprovalRequestTransport, Protocol):
    """해석된 게시 표면은 저장할 바인딩도 함께 제공한다."""

    @property
    def binding(self) -> ApprovalBinding: ...


@dataclass(frozen=True, slots=True)
class PostingOwnerApproval:
    """Post one nonce-bound request after the existing sandbox gate passes."""

    owner_id: str
    store: PendingRepairApprovalStore
    discord: ApprovalRequestTransport | Callable[[], BoundApprovalTransport]
    now: Callable[[], datetime]
    nonce: Callable[[], str] = lambda: secrets.token_hex(16)
    binding: ApprovalBinding | None = None

    def permits(self, ticket_id: str, patch_path: Path) -> bool:
        """Keep one live owner request per ticket and always defer mutation.

        The request is bound to the patch BYTES read here, so a later edit under
        the same file name produces a different hash and supersedes this message
        instead of inheriting its approval.
        """
        from automation.repair.repair_ops_approval_gate import (
            RepairApprovalGate,
            RepairApprovalPayload,
            journal_root,
            lease_root,
            repair_approval_key,
        )

        try:
            artifact = load_patch_artifact(patch_path)
        except PatchBindingError:
            # Never ask cha to approve a patch that cannot be summarised.
            return False
        binding = self.binding
        payload = RepairApprovalPayload(
            patch_path.name, self.nonce(), self.now, binding,
            artifact.patch_sha256, artifact.changes, str(patch_path),
        )
        intent = ApprovalIntent(
            key=repair_approval_key(ticket_id),
            action_hash=content_action_hash(
                ticket_id, patch_path.name, artifact.patch_sha256, artifact.changes
            ),
            channel_id="" if binding is None else binding.channel_id,
        )
        def prepare() -> tuple[ApprovalIntent, RepairApprovalGate]:
            draft, content = payload.prepare(intent)
            # Only a fresh card resolves a new surface, after its bytes exist.
            if callable(self.discord):
                transport = self.discord()
                resolved = transport.binding
                draft = replace(
                    draft, kind=resolved.kind, surface=resolved.surface,
                    channel_id=resolved.channel_id, policy_version=resolved.policy_version,
                    approval_guild_id=resolved.guild_id,
                )
                prepared_intent = replace(intent, channel_id=resolved.channel_id)
            else:
                transport = self.discord
                prepared_intent = intent
            return prepared_intent, RepairApprovalGate(
                self.store, transport, self.owner_id, payload, prepared=(draft, content),
            )

        gate = RepairApprovalGate(self.store, self.discord, self.owner_id, payload)
        _ = request_owner_approval(
            intent,
            gate,
            FileKeyLease(lease_root(self.store.root)),
            PostingJournal(journal_root(self.store.root)),
            prepare=prepare,
        )
        return False
