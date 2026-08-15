"""Typed reconciliation helpers for the memory-curator cycle."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Final

from .alerting import EntryStatus
from .apply import memory_path
from .binding import PromotionReceipt, entry_digest, promotion_key
from .curator import parse_memory_file
from .deletion import DeletionError, delete_entry
from .model import MemoryEntry, MemoryFile, MemoryKind
from .promotion import PromotionProposal, content_hash
from .reconcile import decide_reconcile
from .reporting import DeletedItem, preview
from .state import PendingOwnerEvent, PromotionRecord

_VERIFICATION_REASONS: Final[frozenset[str]] = frozenset(
    {
        "note_not_regular_file",
        "note_hash_mismatch",
        "marker_missing",
        "marker_mismatch",
        "entry_ambiguous",
        "post_failed",
    }
)
MANUAL_REASONS: Final[frozenset[str]] = _VERIFICATION_REASONS - {"post_failed"}


@dataclass(frozen=True, slots=True)
class BlockedItem:
    promotion_key: str
    reason: str
    entry_text: str | None


@dataclass(frozen=True, slots=True)
class ReconcileRequest:
    memory_dir: Path
    promotions: Mapping[str, PromotionRecord]
    read_twin: Callable[[str], bytes | None]
    #: 그 제안의 위키 초안이 아직 있는가. 없으면 소유자가 ⛔ 로 철회한 것이다.
    proposal_alive: Callable[[str], bool]
    now: datetime
    timestamp: str
    dry_run: bool


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    promotions: Mapping[str, PromotionRecord]
    deleted: tuple[DeletedItem, ...]
    blocked: tuple[BlockedItem, ...]
    reasons: tuple[str, ...]


def read_native(memory_dir: Path, kind: MemoryKind) -> tuple[bytes, MemoryFile]:
    try:
        current_bytes = memory_path(memory_dir, kind).read_bytes()
    except FileNotFoundError:
        current_bytes = b""
    return current_bytes, parse_memory_file(current_bytes.decode("utf-8"), kind=kind)


def legacy_covers(
    promotions: Mapping[str, PromotionRecord],
    entry: MemoryEntry,
) -> bool:
    legacy = promotions.get(content_hash(entry.text))
    return legacy is not None and legacy.status == "legacy_unbound"


def candidate_status(
    kind: MemoryKind,
    entry: MemoryEntry,
    promotions: Mapping[str, PromotionRecord],
) -> EntryStatus:
    if legacy_covers(promotions, entry):
        return "legacy_unbound"
    digest = entry_digest(kind, entry.text)
    record = promotions.get(promotion_key(kind, digest))
    if record is None:
        return "unproposed"
    if record.last_block_reason in _VERIFICATION_REASONS:
        return "verification_blocked"
    match record.status:
        case "posted":
            return "awaiting_artifact"
        case "legacy_unbound":
            return "legacy_unbound"
        case "abandoned":
            return "declined"
        case "prepared" | "reconciled":
            return "unproposed"


def reconcile_promotions(request: ReconcileRequest) -> ReconcileResult:
    """Apply only deletion verdicts and return a new promotion-state mapping."""
    promotions = dict(request.promotions)
    deleted: list[DeletedItem] = []
    blocked: list[BlockedItem] = []
    reasons: set[str] = set()

    for key, record in tuple(promotions.items()):
        if record.status not in ("prepared", "posted"):
            continue
        note_bytes = request.read_twin(record.slug)
        current_bytes, memory_file = read_native(request.memory_dir, record.source_kind)
        decision = decide_reconcile(
            record,
            note_bytes,
            note_bytes is not None,
            memory_file.entries,
            proposal_alive=request.proposal_alive(record.draft_id or ""),
        )
        match decision.verdict:
            case "delete":
                index = decision.delete_index
                if index is None or not 0 <= index < len(memory_file.entries):
                    promotions[key] = replace(record, last_block_reason="delete_failed")
                    blocked.append(BlockedItem(key, "delete_failed", None))
                    continue
                entry = memory_file.entries[index]
                if request.dry_run:
                    remaining = memory_file.entries[:index] + memory_file.entries[index + 1 :]
                    planned = MemoryFile(record.source_kind, remaining)
                    deleted.append(
                        DeletedItem(
                            promotion_key=key,
                            source_kind=record.source_kind,
                            entry_text=entry.text,
                            freed_chars=memory_file.char_count - planned.char_count,
                            backup_path=None,
                            applied=False,
                        )
                    )
                    continue
                try:
                    outcome = delete_entry(
                        request.memory_dir,
                        record.source_kind,
                        index,
                        expected_bytes=current_bytes,
                        now=request.now,
                    )
                except DeletionError:
                    promotions[key] = replace(record, last_block_reason="delete_failed")
                    blocked.append(BlockedItem(key, "delete_failed", entry.text))
                else:
                    promotions[key] = replace(
                        record,
                        status="reconciled",
                        reconciled_at=request.timestamp,
                        backup_path=str(outcome.backup_path),
                        last_block_reason=None,
                    )
                    deleted.append(
                        DeletedItem(
                            promotion_key=key,
                            source_kind=record.source_kind,
                            entry_text=entry.text,
                            freed_chars=outcome.before_chars - outcome.after_chars,
                            backup_path=outcome.backup_path,
                            applied=True,
                        )
                    )
            case "abandon":
                # 소유자가 거절했다. 네이티브 항목은 그대로 두고 레코드만 종결한다 —
                # 삭제된 것이 없다는 사실이 상태에 드러나야 한다.
                promotions[key] = replace(
                    record,
                    status="abandoned",
                    reconciled_at=request.timestamp,
                    last_block_reason=decision.reason,
                )
            case "terminal":
                promotions[key] = replace(
                    record,
                    status="reconciled",
                    reconciled_at=request.timestamp,
                    last_block_reason=decision.reason,
                )
            case "skip":
                promotions[key] = replace(record, last_block_reason=decision.reason)
                if decision.reason is not None:
                    reasons.add(decision.reason)
                    blocked.append(BlockedItem(key, decision.reason, None))

    return ReconcileResult(promotions, tuple(deleted), tuple(blocked), tuple(sorted(reasons)))


def make_owner_events(
    promoted: tuple[tuple[PromotionProposal, PromotionReceipt], ...],
    deleted_applied: tuple[DeletedItem, ...],
) -> tuple[PendingOwnerEvent, ...]:
    posted_events = tuple(
        PendingOwnerEvent(
            key=f"{proposal.promotion_key}#posted",
            phase="posted",
            preview=preview(proposal.entry_text),
            twin_kind=proposal.twin_kind,
            draft_id=receipt.draft_id,
            freed_chars=None,
        )
        for proposal, receipt in promoted
    )
    deleted_events = tuple(
        PendingOwnerEvent(
            key=f"{item.promotion_key}#deleted",
            phase="deleted",
            preview=preview(item.entry_text),
            twin_kind=None,
            draft_id=None,
            freed_chars=item.freed_chars,
        )
        for item in deleted_applied
    )
    return posted_events + deleted_events
