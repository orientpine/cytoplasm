"""New owner-ko-v2 card selection; kept outside FS3-frozen test files."""
from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Protocol, TYPE_CHECKING, runtime_checkable

import pytest

from automation.interop import owner_message as om
from automation.memory_relocate.model import RelocationRecord
from automation.memory_relocate import render as memory_render
from automation.plaud_sync.model import PlaudSyncRecord
from automation.plaud_sync import render as plaud_render
from tests.unit.test_plaud_sync_owner_cards import (
    ENTRY,
    PREVIEW,
    todo_gate,
)

_REPO = Path(__file__).resolve().parents[2]
_TODO_SCRIPTS = _REPO / "skills" / "todo" / "scripts"
_PATENT_PACKAGE = _REPO / "skills" / "patent-prep"
sys.path.insert(0, str(_TODO_SCRIPTS))
sys.path.insert(0, str(_PATENT_PACKAGE))

if TYPE_CHECKING:
    from skills.todo.scripts import todo_approval_render as todo_render
else:
    import todo_approval_render as todo_render


class _PatentFields(Protocol):
    @property
    def slug(self) -> str: ...
    @property
    def plaintext_sha256(self) -> str: ...
    @property
    def dest_folder_id(self) -> str: ...
    @property
    def mode(self) -> str: ...
    @property
    def expiry_ts(self) -> int: ...
    @property
    def render_version(self) -> int: ...


@runtime_checkable
class _PatentRenderModule(Protocol):
    @property
    def render_approval(self) -> Callable[[_PatentFields], str]: ...


@dataclass(frozen=True, slots=True)
class _PatentPayload:
    slug: str
    plaintext_sha256: str
    dest_folder_id: str
    mode: str
    expiry_ts: int
    render_version: int


_patent_module: ModuleType = importlib.import_module("scripts.patent_export_render")
assert isinstance(_patent_module, _PatentRenderModule)
patent_render: _PatentRenderModule = _patent_module


def _plaud_record() -> PlaudSyncRecord:
    return PlaudSyncRecord(
        version=1,
        recording_id="rec-001",
        recorded_at="2026-09-01T08:00:00Z",
        note_relpath="000_PARA/Area/Lifelog/2026/note.md",
        note_title="standup",
        body_sha256="a" * 64,
        action_hash="sha256:" + "b" * 64,
        status="planned",
        kind="obsidian-write",
        surface="agent-chat-thread",
        channel_id="",
        policy_version=8,
        message_id=None,
        created_at="2026-09-01T09:00:00Z",
        approved_at=None,
        written_at=None,
        remote_ref=None,
        note_content_sha256=None,
        last_block_reason=None,
    )


def _memory_record() -> RelocationRecord:
    return RelocationRecord(
        version=1,
        source_kind="memory",
        entry_sha256="a" * 64,
        note_relpath="Areas/research-memory.md",
        note_plan_sha256="b" * 64,
        reclaimable_chars=321,
        action_hash="sha256:" + "c" * 64,
        status="proposed",
        kind="memory_relocation",
        surface="agent-chat-thread",
        channel_id="",
        policy_version=8,
        message_id=None,
        created_at="2026-09-01T09:00:00Z",
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


def _capture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[om.OwnerMessage], str]:
    messages: list[om.OwnerMessage] = []
    footer = "참조: fixture"

    def render(message: om.OwnerMessage, *, destination: om.Ref) -> str:
        assert destination.scope == "self"
        messages.append(message)
        return f"카드 시작\n{footer}"

    monkeypatch.setattr(om, "render", render)
    return messages, footer


def test_todo_v3_selects_owner_ko_v2_and_keeps_title_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages, footer = _capture(monkeypatch)
    intent = replace(
        todo_gate(tmp_path).intent,
        title="분기별 연구개발 보고서 제출 일정 확인",
    )

    card = todo_render.render_todo_approval(intent)

    assert todo_render.RENDER_VERSION == "todo-render-v3"
    assert len(messages) == 1
    assert messages[0].render_version == "owner-ko-v2"
    assert intent.title in f"{messages[0].subject}\n{messages[0].fact}"
    assert "\n" in messages[0].fact
    assert card.splitlines() == ["카드 시작", f"argv hash: {intent.action_hash}", footer]


def test_todo_v3_real_card_keeps_full_title_beyond_short_reference(
    tmp_path: Path,
) -> None:
    title = "분기별 연구개발 보고서 제출 일정 확인"
    intent = replace(todo_gate(tmp_path).intent, title=title)

    card = todo_render.render_todo_approval(intent)

    assert f"> 제목: {title}" in card
    assert f"-# 참조: `{title[:8]}`" in card
    assert title[:8] != title


def test_patent_v3_selects_owner_ko_v2_and_preserves_binding_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages, footer = _capture(monkeypatch)
    payload = _PatentPayload(
        slug="masked-export",
        plaintext_sha256="sha256:" + "a" * 64,
        dest_folder_id="folder-bound",
        mode="enc",
        expiry_ts=1_800_003_600,
        render_version=3,
    )

    card = patent_render.render_approval(payload)

    assert len(messages) == 1
    assert messages[0].render_version == "owner-ko-v2"
    assert "\n" in messages[0].fact
    assert card.splitlines() == [
        "카드 시작",
        f"sha256: {payload.plaintext_sha256}",
        f"dest_folder_id: {payload.dest_folder_id}",
        f"expiry_ts: {payload.expiry_ts}",
        f"mode={payload.mode}",
        footer,
    ]


def test_plaud_v6_selects_owner_ko_v2_and_quotes_preview_as_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages, footer = _capture(monkeypatch)
    record = _plaud_record()

    card = plaud_render.render_plaud_approval(record, preview=PREVIEW)

    assert plaud_render.RENDER_VERSION == "plaud-sync-render-v6"
    assert len(messages) == 1
    assert messages[0].render_version == "owner-ko-v2"
    assert PREVIEW in messages[0].fact
    assert "\n" in messages[0].fact
    assert card.splitlines() == [
        "카드 시작",
        f"- action_hash: `{record.action_hash}`",
        footer,
    ]


def test_plaud_v6_keeps_the_full_bounded_preview_within_message_limit() -> None:
    record = replace(
        _plaud_record(),
        recording_id="00000000-0000-4000-8000-000000000001",
        note_title="주간 회의 검토와 다음 일정 조율 및 자료 준비에 관한 논의",
        note_relpath=(
            "000_PARA/Area/Lifelog/2026/"
            "2026-09-08-주간-회의-검토와-다음-일정-조율-및-자료-준비에-관한-논의.md"
        ),
    )
    preview_lines = tuple(
        f"{index}: " + "가" * (plaud_render.PREVIEW_LINE_CHARS - len(f"{index}: "))
        for index in range(plaud_render.PREVIEW_LINES)
    )
    preview = "\n".join(preview_lines)

    card = plaud_render.render_plaud_approval(record, preview=preview)

    assert len(card) <= plaud_render.MAX_MESSAGE_CHARS
    assert [line[2:] for line in card.splitlines() if line.startswith("> ")] == [
        "녹음 시각: 2026-09-01T08:00:00Z",
        f"대상 노트: {record.note_relpath}",
        "판본: plaud-sync-render-v6",
        "내용 미리보기:",
        *preview_lines,
    ]
    assert card.count(record.action_hash) == 1


def test_memory_v3_selects_owner_ko_v2_and_keeps_multiline_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages, footer = _capture(monkeypatch)
    record = _memory_record()

    card = memory_render.render_relocation_approval(record, entry_text=ENTRY)

    assert memory_render.RENDER_VERSION == "mc-reloc-render-v3"
    assert len(messages) == 1
    assert messages[0].render_version == "owner-ko-v2"
    assert ENTRY in messages[0].fact
    assert "\n" in messages[0].fact
    assert card.splitlines() == [
        "카드 시작",
        f"- action_hash: `{record.action_hash}`",
        footer,
    ]
