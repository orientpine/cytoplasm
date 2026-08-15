from __future__ import annotations

import pytest

from automation.memory_relocate.model import RelocationRecord
from automation.memory_relocate.render import (
    MAX_MESSAGE_CHARS,
    RENDER_VERSION,
    RenderError,
    render_relocation_approval,
)


def _record() -> RelocationRecord:
    return RelocationRecord(
        version=1,
        source_kind="memory",
        entry_sha256="a" * 64,
        note_relpath="Areas/research-memory.md",
        note_plan_sha256="b" * 64,
        reclaimable_chars=321,
        action_hash=f"sha256:{'c' * 64}",
        status="proposed",
        kind="memory_relocation",
        surface="owner_dm",
        channel_id="owner-dm-channel",
        policy_version=1,
        message_id=None,
        created_at="2026-07-31T10:00:00Z",
        approved_at=None,
        written_at=None,
        reconciled_at=None,
        remote_ref=None,
        note_content_sha256=None,
        rag_source_key=None,
        rag_fingerprint=None,
        backup_path=None,
        last_block_reason=None,
    )


def test_render_relocation_approval_when_inputs_repeat_is_frozen_and_complete() -> None:
    # Given: one persisted relocation and cha's raw multiline memory entry.
    record = _record()
    entry_text = "선호 도구: uv\n원문 기호도 보존: `x → y`"

    # When: the owner-DM approval message is independently re-rendered twice.
    first = render_relocation_approval(record, entry_text=entry_text)
    second = render_relocation_approval(record, entry_text=entry_text)

    # Then: both byte sequences match and every consent-bearing fact is visible.
    assert first.encode("utf-8") == second.encode("utf-8")
    assert RENDER_VERSION in first
    assert "memory→Obsidian 재배치 승인" in first
    assert entry_text in first
    assert record.note_relpath in first
    assert str(record.reclaimable_chars) in first
    assert record.action_hash in first
    assert (
        "승인(✅) 시 이 항목은 자체 메모리(ambient)에서 **삭제**되고 이후에는 "
        "**recall(검색)로만** 찾을 수 있게 됩니다 — 취소는 ⛔."
    ) in first


def test_render_relocation_approval_when_message_exceeds_limit_fails_closed() -> None:
    # Given: an entry that cannot fit even without the approval metadata.
    entry_text = "가" * MAX_MESSAGE_CHARS

    # When / Then: rendering refuses instead of truncating binding-relevant content.
    with pytest.raises(RenderError):
        _ = render_relocation_approval(_record(), entry_text=entry_text)
