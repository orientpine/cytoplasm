"""Ordered, fail-closed orchestration for one memory-curator cron tick."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

from .alerting import (
    ActionableState,
    AlertDecision,
    EntryStatus,
    NearCapBucket,
    bucket_for,
    decide_alert,
    signature,
)
from .apply import CurationResult, apply_curation
from .binding import PromotionReceipt, entry_digest, promotion_key
from .candidates import select_promotions
from .classify_model import EntryVerdict
from .curator import curate
from .model import MemoryEntry, MemoryFile, MemoryKind
from .promotion import PromotionProposal
from .reporting import DeletedItem, OwnerReport, PromotedItem, build_report
from .state import CuratorState, StateError
from .state_store import load_state, save_state
from .watch_steps import (
    MANUAL_REASONS,
    BlockedItem,
    ReconcileRequest,
    candidate_status,
    make_owner_events,
    read_native,
    reconcile_promotions,
)

_KINDS: Final[tuple[MemoryKind, ...]] = ("memory", "user")
_UTC_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True, slots=True)
class CycleResult:
    compacted: tuple[CurationResult, ...]
    promoted: tuple[PromotedItem, ...]
    deleted: tuple[DeletedItem, ...]
    blocked: tuple[BlockedItem, ...]
    near_cap_kinds: tuple[MemoryKind, ...]
    alert_decision: AlertDecision
    alerted: bool


class EntryClassifier(Protocol):
    def classify(
        self,
        entries_by_kind: Mapping[MemoryKind, tuple[MemoryEntry, ...]],
    ) -> tuple[EntryVerdict, ...]: ...


def _with_classifier_candidates(
    candidates: Mapping[MemoryKind, tuple[MemoryEntry, ...]],
    entries_by_kind: Mapping[MemoryKind, tuple[MemoryEntry, ...]],
    classifier: EntryClassifier,
) -> dict[MemoryKind, tuple[MemoryEntry, ...]]:
    try:
        verdicts = classifier.classify(entries_by_kind)
    except Exception:
        return dict(candidates)

    twin_digests = {
        kind: {
            entry_digest(kind, verdict.entry_text)
            for verdict in verdicts
            if verdict.source_kind == kind and verdict.route == "TWIN"
        }
        for kind in _KINDS
    }
    combined: dict[MemoryKind, tuple[MemoryEntry, ...]] = {}
    for kind in _KINDS:
        selected = list(candidates[kind])
        selected_digests = {entry_digest(kind, entry.text) for entry in selected}
        for entry in entries_by_kind[kind]:
            digest = entry_digest(kind, entry.text)
            if digest in twin_digests[kind] and digest not in selected_digests:
                selected_digests.add(digest)
                selected.append(entry)
        combined[kind] = tuple(selected)
    return combined


def run_cycle(
    memory_dir: Path,
    state_path: Path,
    *,
    promote: Callable[[PromotionProposal], PromotionReceipt | None],
    alert: Callable[[str], bool],
    read_twin: Callable[[str], bytes | None],
    proposal_alive: Callable[[str], bool] = lambda _draft_id: True,
    now: datetime | None = None,
    max_promotions: int = 3,
    classifier: EntryClassifier | None = None,
) -> CycleResult:
    """Run one cycle with durable owner-event checkpoints around summary delivery."""
    state = load_state(state_path)
    current_time = now or datetime.now(UTC)
    timestamp = current_time.strftime(_UTC_FORMAT)
    dry_run = os.environ.get("MEMORY_CURATOR_DRY_RUN") == "1"

    compacted: dict[MemoryKind, CurationResult] = {
        kind: apply_curation(memory_dir, kind, dry_run=dry_run, now=current_time)
        for kind in _KINDS
    }
    reconciled = reconcile_promotions(
        ReconcileRequest(
            memory_dir=memory_dir,
            promotions=state.promotions,
            read_twin=read_twin,
            proposal_alive=proposal_alive,
            now=current_time,
            timestamp=timestamp,
            dry_run=dry_run,
        )
    )
    promotions = dict(reconciled.promotions)

    final_files: dict[MemoryKind, MemoryFile] = {}
    candidates: dict[MemoryKind, tuple[MemoryEntry, ...]] = {}
    for kind in _KINDS:
        if dry_run:
            plan = compacted[kind].plan
        else:
            _, current_file = read_native(memory_dir, kind)
            plan = curate(current_file)
        final_files[kind] = plan.compacted
        candidates[kind] = plan.promotion_candidates
    if classifier is not None:
        entries_by_kind: dict[MemoryKind, tuple[MemoryEntry, ...]] = {
            kind: final_files[kind].entries for kind in _KINDS
        }
        candidates = _with_classifier_candidates(candidates, entries_by_kind, classifier)

    selection = select_promotions(
        candidates=candidates,
        final_files=final_files,
        promotions=promotions,
        promote=promote,
        timestamp=timestamp,
        dry_run=dry_run,
        max_promotions=max_promotions,
    )
    promotions = selection.promotions
    promoted = selection.promoted
    blocked = [*reconciled.blocked, *selection.blocked]

    buckets: dict[MemoryKind, NearCapBucket] = {
        kind: bucket_for(final_files[kind].fill_ratio) for kind in _KINDS
    }
    entries: dict[str, EntryStatus] = {
        promotion_key(kind, entry_digest(kind, entry.text)): candidate_status(
            kind,
            entry,
            promotions,
        )
        for kind in _KINDS
        for entry in candidates[kind]
    }
    actionable = ActionableState(
        buckets=buckets,
        entries=entries,
        manual_reasons=tuple(sorted(set(reconciled.reasons) & MANUAL_REASONS)),
    )
    current_signature = signature(actionable)
    alert_outcome = decide_alert(current_signature, state.alert, current_time)
    near_cap_kinds: tuple[MemoryKind, ...] = tuple(
        kind for kind in _KINDS if buckets[kind] != "ok"
    )
    current_events = make_owner_events(
        tuple(promoted),
        tuple(item for item in reconciled.deleted if item.applied),
    )
    pending = dict(state.pending_owner_events)
    for event in current_events:
        existing_event = pending.setdefault(event.key, event)
        if existing_event != event:
            raise StateError(f"owner event payload changed: {event.key}")
    events_due = tuple(sorted(pending.values(), key=lambda event: event.key))
    near_cap_due = alert_outcome.decision == "send"
    should_send = bool(events_due) or near_cap_due
    checkpoint_alert = (
        replace(state.alert, last_observed_signature=current_signature)
        if near_cap_due
        else alert_outcome.next_alert_state
    )
    sent = False
    if should_send:
        save_state(
            state_path,
            CuratorState(state.version, promotions, checkpoint_alert, pending),
        )
        report = build_report(
            OwnerReport(
                compacted,
                events_due,
                final_files,
                near_cap_kinds if near_cap_due else (),
            )
        )
        if not dry_run and report is not None:
            sent = alert(report)
        if sent:
            for event in events_due:
                del pending[event.key]
            final_alert = (
                alert_outcome.next_alert_state if near_cap_due else checkpoint_alert
            )
            save_state(
                state_path,
                CuratorState(state.version, promotions, final_alert, pending),
            )
    else:
        save_state(
            state_path,
            CuratorState(
                state.version,
                promotions,
                alert_outcome.next_alert_state,
                pending,
            ),
        )
    return CycleResult(
        compacted=tuple(compacted[kind] for kind in _KINDS),
        promoted=tuple(promoted),
        deleted=reconciled.deleted,
        blocked=tuple(blocked),
        near_cap_kinds=near_cap_kinds,
        alert_decision=alert_outcome.decision,
        alerted=sent,
    )
