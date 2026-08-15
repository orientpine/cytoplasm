from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from .binding import PromotionReceipt, entry_digest, promotion_key
from .model import MemoryEntry, MemoryFile, MemoryKind
from .promotion import PromotionProposal, build_proposal
from .reclaim import order_by_reclaim
from .reporting import PromotedItem
from .state import PromotionRecord
from .watch_steps import BlockedItem, legacy_covers


@dataclass(frozen=True, slots=True)
class SelectionResult:
    promotions: Mapping[str, PromotionRecord]
    promoted: tuple[PromotedItem, ...]
    blocked: tuple[BlockedItem, ...]
    attempts: int


def select_promotions(
    *,
    candidates: Mapping[MemoryKind, tuple[MemoryEntry, ...]],
    final_files: Mapping[MemoryKind, MemoryFile],
    promotions: Mapping[str, PromotionRecord],
    promote: Callable[[PromotionProposal], PromotionReceipt | None],
    timestamp: str,
    dry_run: bool,
    max_promotions: int,
) -> SelectionResult:
    updated_promotions = dict(promotions)
    promoted: list[PromotedItem] = []
    blocked: list[BlockedItem] = []
    attempts = 0
    for kind, _index, entry in order_by_reclaim(candidates, final_files):
        digest = entry_digest(kind, entry.text)
        key = promotion_key(kind, digest)
        if legacy_covers(updated_promotions, entry):
            continue
        existing = updated_promotions.get(key)
        if existing is not None and existing.status != "prepared":
            continue
        if attempts >= max_promotions:
            break
        proposal = build_proposal(entry.text, source_kind=kind)
        record = existing or PromotionRecord(
            source_kind=kind,
            entry_sha256=digest,
            slug=proposal.slug,
            created_at=timestamp,
            note_sha256="",
            draft_id=None,
            confirm_message_id=None,
            status="prepared",
            posted_at=None,
            reconciled_at=None,
            backup_path=None,
            last_block_reason=None,
        )
        updated_promotions[key] = record
        attempts += 1
        if dry_run:
            blocked.append(BlockedItem(key, "dry_run", entry.text))
            continue
        receipt = promote(proposal)
        if receipt is None:
            updated_promotions[key] = replace(record, last_block_reason="post_failed")
            blocked.append(BlockedItem(key, "post_failed", entry.text))
            continue
        updated_promotions[key] = replace(
            record,
            status="posted",
            draft_id=receipt.draft_id,
            confirm_message_id=receipt.confirm_message_id,
            note_sha256=receipt.note_sha256,
            posted_at=timestamp,
            last_block_reason=None,
        )
        promoted.append((proposal, receipt))
    return SelectionResult(updated_promotions, tuple(promoted), tuple(blocked), attempts)
