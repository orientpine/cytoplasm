"""Byte receipts for the three substring-bound approval producers (OMUX 23)."""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from automation.memory_relocate import render as memory_render
from automation.plaud_sync import render as plaud_render
from tests.unit.test_memory_relocate_render import _record as memory_record
from tests.unit.test_plaud_sync_render import _BASE as PLAUD_RECORD
from tests.unit.test_todo_approval_producer import _runtime, FakeDirectory, FakeTransport

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/todo/scripts"))
if TYPE_CHECKING:
    from skills.todo.scripts.todo_approval import TodoApprovalGate, TodoApprovalIntent
    from skills.todo.scripts.todo_approval_store import TodoApprovalStore
else:
    from todo_approval import TodoApprovalGate, TodoApprovalIntent
    from todo_approval_store import TodoApprovalStore

PREVIEW = "첫째 줄\n둘째 줄"
ENTRY = "선호 도구: uv\n원문 기호도 보존: `x → y`"
PLAUD_V3 = '[PLAUD lifelog 저장 승인 | plaud-sync-render-v3]\n- 녹음 id: `rec-001`\n- 녹음 시각: 2026-09-01T08:00:00Z\n- 대상 노트: `000_PARA/Area/Lifelog/2026/2026-09-01-standup--abcdef123456.md`\n- action_hash: `sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`\n\n내용 미리보기(상위 5줄):\n> 첫째 줄\n> 둘째 줄\n\n승인(✅) 시 이 녹음의 요약+전문 노트가 Obsidian vault에 저장되고 recall 검색에 인제스트됩니다 — 취소는 ⛔.'
PLAUD_V4 = '옵시디언 노트 승인 요청: **standup (2026-09-01)**\n[PLAUD lifelog | plaud-sync-render-v4]\n- 녹음 id: `rec-001`\n- 녹음 시각: 2026-09-01T08:00:00Z\n- 대상 노트: `000_PARA/Area/Lifelog/2026/2026-09-01-standup--abcdef123456.md`\n- action_hash: `sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`\n\n내용 미리보기(요약 우선 · 최대 7줄):\n> 첫째 줄\n> 둘째 줄\n\n승인(✅): 이 메시지에 반응 → 요약+전문을 Obsidian vault에 저장하고 recall 검색에 인제스트\n수정 요청: 승인 대신 이 스레드에 수정할 내용을 답글로 남겨 주세요\n취소(⛔): 이 메시지에 반응 → 저장 취소'
MEMORY_V1 = '[memory→Obsidian 재배치 승인 | mc-reloc-render-v1]\n원본 메모리 항목\n───\n선호 도구: uv\n원문 기호도 보존: `x → y`\n───\n- 대상 노트: `Areas/research-memory.md`\n- 회수 예상 문자 수: 321\n- action_hash: `sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc`\n\n승인(✅) 시 이 항목은 자체 메모리(ambient)에서 **삭제**되고 이후에는 **recall(검색)로만** 찾을 수 있게 됩니다 — 취소는 ⛔.'
TODO_V1 = 'Google Tasks 등록 승인\n제목: 합성 과제\n기한: 2026-09-12\nargv hash: sha256:abc\n이 메시지에 ✅ 실행 / ⛔ 취소'


def todo_gate(tmp_path: Path) -> TodoApprovalGate:
    runtime = _runtime(import_module("todo_approval"), TodoApprovalStore(tmp_path / "state"),
                       FakeTransport({}, []), FakeDirectory([]),
                       [datetime(2026, 9, 11, 12, tzinfo=UTC)], tmp_path)
    return TodoApprovalGate(
        TodoApprovalIntent("sha256:abc", "target", "masked", "합성 과제", "2026-09-12"),
        runtime,
    )


@pytest.mark.parametrize(("version", "expected"), [("plaud-sync-render-v3", PLAUD_V3), ("plaud-sync-render-v4", PLAUD_V4)], ids=["v3", "v4"])
def test_legacy_plaud_bytes_when_replayed(version: str, expected: str) -> None:
    # Given a fixed synthetic recording; when replayed; then shipped bytes are frozen.
    assert plaud_render.render_plaud_approval(PLAUD_RECORD, preview=PREVIEW, render_version=version) == expected


def test_legacy_memory_bytes_when_replayed() -> None:
    # Given fixed fields; when rendered; then the base card is reproduced.
    assert memory_render.render_relocation_approval(memory_record(), entry_text=ENTRY, render_version="mc-reloc-render-v1") == MEMORY_V1


def test_legacy_todo_bytes_when_replayed(tmp_path: Path) -> None:
    # Given fixed fields; when rendered; then the base card is reproduced.
    if TYPE_CHECKING:
        from skills.todo.scripts.todo_approval_render import render_todo_approval
    else:
        from todo_approval_render import render_todo_approval
    assert render_todo_approval(todo_gate(tmp_path).intent, render_version="todo-render-v1") == TODO_V1


@pytest.mark.parametrize("producer", ["plaud", "memory", "todo"])
def test_envelope_is_used_when_a_new_card_is_rendered(producer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given the real renderer with an observing boundary; when a new card renders;
    # then it passes the complete independent envelope contract.
    from automation.interop import owner_message as om
    expected = {
        "plaud": om.OwnerMessage(
            "rec-001", "PLAUD 노트: standup (2026-09-01)",
            "2026-09-01T08:00:00Z; 000_PARA/Area/Lifelog/2026/2026-09-01-standup--abcdef123456.md; plaud-sync-render-v5",
            om.Ref("self"), om.Action("react", om.Ref("self"), "✅ 승인 / ⛔ 취소"),
            "Obsidian 저장·recall 인제스트; 수정은 이 스레드에 답글", "not_applicable",
            om.Approval(None, "저장 취소")),
        "memory": om.OwnerMessage(
            "Areas/research-memory.md", "memory→Obsidian 재배치",
            "선호 도구: uv\n원문 기호도 보존: `x → y`; 회수 예상 문자 수: 321; mc-reloc-render-v2",
            om.Ref("self"), om.Action("react", om.Ref("self"), "✅ 승인 / ⛔ 취소"),
            "저장·인제스트 확인 후 자체 메모리(ambient) 삭제; 이후 recall(검색)로만 조회",
            "not_applicable", om.Approval(None, "원본 유지")),
        "todo": om.OwnerMessage(
            "합성 과제", "Google Tasks 등록", "기한: 2026-09-12; todo-render-v2",
            om.Ref("self"), om.Action("react", om.Ref("self"), "✅ 승인 / ⛔ 취소"),
            "Google Tasks 등록", "not_applicable", om.Approval(None, "등록 취소")),
    }
    observed = []
    real_render = om.render

    def observe(message: om.OwnerMessage, *, destination: om.Ref) -> str:
        observed.append((message, destination))
        return real_render(message, destination=destination)

    monkeypatch.setattr(om, "render", observe)
    match producer:
        case "plaud":
            plaud_render.render_plaud_approval(PLAUD_RECORD, preview=PREVIEW)
        case "memory":
            memory_render.render_relocation_approval(memory_record(), entry_text=ENTRY)
        case "todo":
            todo_gate(tmp_path)._render()
    assert observed == [(expected[producer], om.Ref("self"))]


@pytest.mark.parametrize("producer", ["plaud", "memory"])
def test_render_version_roundtrips_outside_schema_when_selected(producer: str) -> None:
    # Given a new presentation field on a legacy schema; when parsed; then preserve it.
    from automation.plaud_sync import model as p
    from automation.memory_relocate import model as m
    if producer == "plaud":
        raw = p.serialize_record(PLAUD_RECORD)
        raw["render_version"] = "plaud-sync-render-v5"
        try:
            parsed = p.parse_record(raw)
        except p.PlaudSyncError:
            pytest.fail("new render version is not accepted outside the schema version")
        assert p.serialize_record(parsed) == raw
    else:
        raw = m.serialize_state(m.RelocationState(1, {"memory:" + "a" * 64: memory_record()}))
        relocations = cast(dict[str, dict[str, str | int | None]], raw["relocations"])
        relocations["memory:" + "a" * 64]["render_version"] = "mc-reloc-render-v2"
        try:
            parsed = m.parse_state(raw)
        except m.RelocationError:
            pytest.fail("new render version is not accepted outside the schema version")
        assert m.serialize_state(parsed) == raw


def test_plaud_binding_is_not_cut_when_title_is_oversized() -> None:
    # Given metadata larger than Discord; when rendered; then no partial card escapes.
    with pytest.raises(plaud_render.PlaudRenderError):
        plaud_render.render_plaud_approval(replace(PLAUD_RECORD, note_title="제" * 1901))
