"""Pure fail-closed decisions for native-memory reconciliation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from .binding import entry_digest, parse_marker, promotion_key
from .model import MemoryEntry
from .state import PromotionRecord

BlockReason = Literal[
    "note_missing",
    "note_not_regular_file",
    "note_hash_mismatch",
    "marker_missing",
    "marker_mismatch",
    "entry_not_found",
    "entry_ambiguous",
    "proposal_withdrawn",
]


@dataclass(frozen=True, slots=True)
class ReconcileDecision:
    """Deletion verdict and the exact native entry index it authorizes."""

    verdict: Literal["delete", "skip", "terminal", "abandon"]
    reason: BlockReason | None
    delete_index: int | None


def decide_reconcile(
    record: PromotionRecord,
    note_bytes: bytes | None,
    note_is_regular_file: bool,
    entries: tuple[MemoryEntry, ...],
    *,
    proposal_alive: bool = True,
) -> ReconcileDecision:
    """Authorize deletion only when every persisted binding still matches.

    ``proposal_alive`` 는 그 제안이 아직 살아 있는지다(위키 초안이 남아 있는가).
    소유자가 ⛔ 를 누르면 게이트가 초안을 폐기하는데, 그 결정은 승격 레코드로
    전파되지 않아 레코드가 영원히 ``note_missing`` 으로 남았다 — 거절됐는데도
    시스템은 아직 산출물을 기다리는 중이라고 보고했다(2026-08-03 실측 4건).
    노트도 없고 초안도 없으면 그 제안은 다시 살아날 수 없으므로 종결한다.
    기본값이 ``True`` 인 것은 fail-closed 다 — 모르면 살아 있다고 본다."""
    if record.status == "legacy_unbound":
        return ReconcileDecision("skip", None, None)
    if record.status == "reconciled":
        return ReconcileDecision("terminal", None, None)
    if record.status not in ("prepared", "posted"):
        return ReconcileDecision("skip", None, None)

    if note_bytes is None:
        if not proposal_alive:
            return ReconcileDecision("abandon", "proposal_withdrawn", None)
        return ReconcileDecision("skip", "note_missing", None)
    if not note_is_regular_file:
        return ReconcileDecision("skip", "note_not_regular_file", None)
    if hashlib.sha256(note_bytes).hexdigest() != record.note_sha256:
        return ReconcileDecision("skip", "note_hash_mismatch", None)

    try:
        note_text = note_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return ReconcileDecision("skip", "marker_missing", None)

    marker = parse_marker(note_text)
    if marker is None:
        return ReconcileDecision("skip", "marker_missing", None)
    if (
        marker.promotion_key
        != promotion_key(record.source_kind, record.entry_sha256)
        or marker.source_kind != record.source_kind
        or marker.entry_digest != record.entry_sha256
        or marker.delete_after_persist is not True
    ):
        return ReconcileDecision("skip", "marker_mismatch", None)

    matching_indices: tuple[int, ...] = tuple(
        index
        for index, entry in enumerate(entries)
        if entry_digest(record.source_kind, entry.text) == record.entry_sha256
    )
    if not matching_indices:
        return ReconcileDecision("terminal", "entry_not_found", None)
    if len(matching_indices) >= 2:
        return ReconcileDecision("skip", "entry_ambiguous", None)
    delete_index = next(iter(matching_indices))
    return ReconcileDecision("delete", None, delete_index)
