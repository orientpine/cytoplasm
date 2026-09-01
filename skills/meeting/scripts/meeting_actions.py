"""Side-effect builders for meeting ingest: Kanban, milestones.yaml, note, #team.

Everything here is pure/deterministic except `write_note` and
`update_milestones` (file writes under the agent home). Sensitive meetings:
#team stays suppressed and cards default to a generic label + note pointer;
an item whose public strings (title/deadline/basis) hit NO deterministic
rule may carry an informative [민감회의] card (fail-closed without rules).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

import meeting_gate
import meeting_minutes
import meeting_template
from meeting_llm import ActionItem, Extraction
from meeting_milestones import _emit_milestones as _emit_milestones
from meeting_milestones import _parse_milestones as _parse_milestones
from meeting_milestones import _yaml_str as _yaml_str
from meeting_milestones import update_milestones as update_milestones

_TITLE_MAX = 80


@dataclass(frozen=True, slots=True)
class PlannedCard:
    """One owner-action Kanban card and its required dispatcher guard."""

    title: str
    body: str
    idempotency_key: str

    def argv(self) -> list[str]:
        return [
            "kanban",
            "create",
            self.title,
            "--body",
            self.body,
            "--created-by",
            "meeting-skill",
            "--idempotency-key",
            self.idempotency_key,
            "--json",
        ]

    def argv_sequence(self, card_id: str) -> tuple[list[str], list[str]]:
        """Create the card, then block it for the owner before any worker can claim it."""
        return self.argv(), [
            "kanban", "block", "--kind", "needs_input", card_id,
            "Needs human owner action; do not dispatch an LLM worker.",
        ]


def _clip(text: str) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat[: _TITLE_MAX - 1] + "…" if len(flat) > _TITLE_MAX else flat


def _item_publishable(
    item: ActionItem, rules: tuple[meeting_gate.TagRule, ...] | None
) -> bool:
    """Item-level recheck: True only when rules exist and NO tag hits the public strings."""
    if rules is None:
        return False  # fail-closed
    public = "\n".join((item.title, item.deadline or "", item.basis))
    return not meeting_gate.evaluate(public, rules).tags


def sanitize_card(
    item: ActionItem,
    *,
    sensitive: bool,
    seq: int,
    note_name: str,
    ref: str,
    rules: tuple[meeting_gate.TagRule, ...] | None = None,
    project: str = "",
) -> PlannedCard:
    """Card for MY item. Sensitive -> generic title, pointer-only body — unless
    the item's public strings pass an item-level recheck against the same
    deterministic rules (then an informative [민감회의] card)."""
    if sensitive and _item_publishable(item, rules):
        deadline = f" (마감 {item.deadline})" if item.deadline else ""
        title = _clip(f"[민감회의] {item.title}{deadline}")
        body = _clip(f"근거: {item.basis}") if item.basis else "회의록 추출 항목"
        body += "\n(민감 회의 문서 — 항목 문자열 규칙 재검사 통과)"
        body += f"\n출처: ~/notes/meetings/{note_name}"
    elif sensitive:
        title = f"[민감] 회의 액션아이템 {seq}"
        body = (
            f"상세는 로컬 노트 참조: ~/notes/meetings/{note_name} (mode 700). "
            "민감 태그 회의라 카드에는 내용을 싣지 않습니다."
        )
    else:
        deadline = f" (마감 {item.deadline})" if item.deadline else ""
        title = _clip(f"{item.title}{deadline}")
        body = _clip(f"근거: {item.basis}") if item.basis else "회의록 추출 항목"
        body += meeting_minutes.source_block(note_name, project)
    return PlannedCard(
        title=title, body=body, idempotency_key=f"meeting:{ref}:todo:{seq}"
    )


def plan_cards(
    extraction: Extraction,
    *,
    sensitive: bool,
    note_name: str,
    ref: str,
    rules: tuple[meeting_gate.TagRule, ...] | None = None,
    project: str = "",
) -> tuple[PlannedCard, ...]:
    """Plan one owner-action card per MY todo, blocked from LLM dispatch."""
    return tuple(
        sanitize_card(
            item, sensitive=sensitive, seq=seq, note_name=note_name, ref=ref,
            rules=rules, project=project,
        )
        for seq, item in enumerate(extraction.todos, start=1)
    )


def note_date(extraction: Extraction, *, now: datetime) -> date:
    """The meeting's own date when it parses, else the processing date."""
    try:
        return date.fromisoformat(extraction.meeting.date or "")
    except ValueError:
        return now.date()


def note_name(extraction: Extraction, *, ref: str, now: datetime) -> str:
    """The note's file name. Merging the action tables needs it BEFORE the note is
    rendered, so the name has exactly one definition and both callers read it here."""
    return f"{note_date(extraction, now=now).isoformat()}-meeting-{ref}.md"


def write_note(
    notes_dir: Path,
    *,
    label: str,
    kind: str,
    original_text: str,
    extraction: Extraction,
    sensitive: bool,
    ref: str,
    now: datetime,
    evidence_footer: str = "",
    slide_notes: tuple[str, ...] = (),
    reference_notes: tuple[str, ...] = (),
    action_sections: Sequence[str] = (),
    template: meeting_template.Template | None = None,
) -> Path:
    """Persist one owner-only note; missing or invalid meeting dates use processing date."""
    note_path = notes_dir / note_name(extraction, ref=ref, now=now)
    notes_dir.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        meeting_minutes.render(
            label=label,
            kind=kind,
            extraction=extraction,
            original_text=original_text,
            sensitive=sensitive,
            ref=ref,
            now=now,
            evidence_footer=evidence_footer,
            slide_notes=slide_notes,
            reference_notes=reference_notes,
            action_sections=action_sections,
            template=template,
        ),
        encoding="utf-8",
    )
    note_path.chmod(0o600)
    return note_path


def format_team_post(
    others: tuple[ActionItem, ...], *, agent_id: str, ref: str, now: datetime
) -> str | None:
    """Interop v0 (§1) report block sharing OTHER people's action items."""
    if not others:
        return None
    summary_lines = ["회의 액션아이템 공유 — 각자 에이전트가 처리해 주세요."]
    for item in others:
        deadline = item.deadline or "미정"
        summary_lines.append(
            f"[{item.owner or '담당미정'}] {item.title} (마감 {deadline}) — 근거: {item.basis}"
        )
    payload = {
        "version": "v0",
        "agent_id": agent_id,
        "task_id": f"meeting-{ref}",
        "status": "done",
        "summary": "\n".join(summary_lines),
        "links": [],
        "timestamp": now.isoformat(),
    }
    return f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"


def format_notify(
    *,
    label: str,
    sensitive: bool,
    cards: int,
    milestones_added: int,
    others: int,
    note_name: str,
    team_posted: bool,
    project: str = "",
    action_id_exhausted: bool = False,
) -> str:
    """Sanitized completion notice for the originating DM/channel."""
    lines = [f"회의록 처리 완료: {label}" if not sensitive else "회의록 처리 완료 (민감 문서)"]
    lines.append(f"- 내 액션아이템 카드: {cards}건 (Kanban)")
    lines.append(f"- 마일스톤 갱신: {milestones_added}건 (milestones.yaml)")
    if action_id_exhausted:
        lines.append("- 관리번호 소진 안내: 신규 Action Item은 관리번호 없이 회의록에 기록했습니다")
    if sensitive:
        lines.append("- 민감 태그 문서: 비-GLM 모델로 처리, 상세는 로컬 노트에만 보관")
        lines.append(f"- 타인 항목 {others}건은 공유 채널에 게시하지 않음 (로컬 노트 참조)")
    elif team_posted:
        lines.append(f"- 타인 액션아이템 {others}건 → #team 규약 게시 완료")
    if not sensitive:
        lines.append(
            f"- 과제: {project} — action item 원장 갱신"
            if project
            else "- 과제 미지정 — 관리번호 없이 표만 그렸습니다(원장 미갱신). "
            "`--project <과제명>` 을 주거나 파일명에 과제명을 넣으세요."
        )
    lines.append(f"- 노트: ~/notes/meetings/{note_name}")
    return "\n".join(lines)
