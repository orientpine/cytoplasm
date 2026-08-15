from __future__ import annotations

from pathlib import Path

from automation.memory_curator import state as state_module
from automation.memory_curator.apply import CurationResult, apply_curation
from automation.memory_curator.model import MemoryFile, MemoryKind
from automation.memory_curator.reporting import OwnerReport, build_report

_KINDS: tuple[MemoryKind, ...] = ("memory", "user")


def _owner_report(
    tmp_path: Path,
    events: tuple[state_module.PendingOwnerEvent, ...],
    near_cap_kinds: tuple[MemoryKind, ...] = (),
) -> OwnerReport:
    compacted: dict[MemoryKind, CurationResult] = {
        kind: apply_curation(tmp_path, kind, dry_run=True) for kind in _KINDS
    }
    final_files: dict[MemoryKind, MemoryFile] = {
        "memory": MemoryFile("memory", ()),
        "user": MemoryFile("user", ()),
    }
    return OwnerReport(
        compacted=compacted,
        events=events,
        final_files=final_files,
        near_cap_kinds=near_cap_kinds,
    )


def test_build_report_when_only_events_are_due_omits_near_cap_line(tmp_path: Path) -> None:
    # Given: persisted posted and deleted events with no cooldown-approved near-cap notice.
    events = (
        state_module.PendingOwnerEvent(
            "promotion-z#posted", "posted", "persisted preview z", "principle", "draft-z", None
        ),
        state_module.PendingOwnerEvent(
            "promotion-a#deleted", "deleted", "persisted preview a", None, None, 41
        ),
        state_module.PendingOwnerEvent(
            "promotion-b#posted", "posted", "persisted preview b", "decision", "draft-b", None
        ),
    )

    # When: the owner summary is rendered from the durable outbox.
    report = build_report(_owner_report(tmp_path, events))

    # Then: both phases render once in deterministic order without a near-cap warning.
    assert report is not None
    assert "삭제 완료(트윈 저장 검증 후): 1건, 41자 확보" in report
    assert "- 'persisted preview a'" in report
    assert "트윈 승격 제안 2건 — DM ✅ 시 자체 메모리에서 삭제됩니다:" in report
    assert "- 저장 draft-b: 'persisted preview b' → decision" in report
    assert "- 저장 draft-z: 'persisted preview z' → principle" in report
    assert report.index("draft-b") < report.index("draft-z")
    assert "⚠️" not in report


def test_build_report_when_event_preview_is_persisted_does_not_retruncate(
    tmp_path: Path,
) -> None:
    # Given: a pre-masked preview longer than the live preview helper's limit.
    persisted_preview = "already-masked-preview-kept-verbatim"
    event = state_module.PendingOwnerEvent(
        "promotion-a#posted", "posted", persisted_preview, "principle", "draft-a", None
    )

    # When: the event is rendered.
    report = build_report(_owner_report(tmp_path, (event,)))

    # Then: persisted content is emitted verbatim rather than truncated a second time.
    assert report is not None
    assert persisted_preview in report
    assert "…" not in report


def test_build_report_when_nothing_is_due_returns_none(tmp_path: Path) -> None:
    # Given: no compaction, owner events, or cooldown-approved near-cap kinds.
    # When: the report is rendered.
    report = build_report(_owner_report(tmp_path, ()))

    # Then: the caller has no DM to send.
    assert report is None
