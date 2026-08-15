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
from datetime import datetime
from pathlib import Path

import meeting_gate
from meeting_llm import ActionItem, Extraction

_TITLE_MAX = 80


@dataclass(frozen=True, slots=True)
class PlannedCard:
    """One Kanban card ready to be created (argv form, secrets-free)."""

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
        body += f"\n출처: ~/notes/meetings/{note_name}"
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
) -> tuple[PlannedCard, ...]:
    """Plan one unassigned card per MY todo (dispatcher skips unassigned)."""
    return tuple(
        sanitize_card(
            item, sensitive=sensitive, seq=seq, note_name=note_name, ref=ref, rules=rules
        )
        for seq, item in enumerate(extraction.todos, start=1)
    )


def _yaml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _emit_milestones(entries: list[dict]) -> str:
    lines = [
        "# Managed by the meeting skill (W2-3). Consumed by W3 reminders.",
        "milestones:",
    ]
    for entry in entries:
        lines.append(f"  - title: {_yaml_str(entry['title'])}")
        lines.append(f"    deadline: {_yaml_str(entry['deadline'])}")
        lines.append(f"    basis: {_yaml_str(entry['basis'])}")
        lines.append(f"    source: {_yaml_str(entry['source'])}")
        lines.append(f"    added: {_yaml_str(entry['added'])}")
    return "\n".join(lines) + "\n"


def _parse_milestones(raw: str) -> list[dict]:
    try:
        import yaml  # noqa: PLC0415

        data = yaml.safe_load(raw)
        entries = data.get("milestones") if isinstance(data, dict) else None
        return [dict(entry) for entry in entries or [] if isinstance(entry, dict)]
    except ModuleNotFoundError:
        entries = []
        current: dict | None = None
        for line in raw.splitlines():
            matched = re.match(r"^  - title: (.+)$", line)
            if matched:
                current = {"title": json.loads(matched.group(1))}
                entries.append(current)
            elif current is not None:
                keyed = re.match(r"^    (deadline|basis|source|added): (.+)$", line)
                if keyed:
                    current[keyed.group(1)] = json.loads(keyed.group(2))
        return entries


def update_milestones(
    state_file: Path,
    milestones: tuple[ActionItem, ...],
    *,
    sensitive: bool,
    note_name: str,
    ref: str,
    now: datetime,
) -> int:
    """Merge new milestones into milestones.yaml, deduped on (title, deadline)."""
    existing = (
        _parse_milestones(state_file.read_text(encoding="utf-8"))
        if state_file.exists()
        else []
    )
    seen = {(entry.get("title"), entry.get("deadline")) for entry in existing}
    added = 0
    for seq, item in enumerate(milestones, start=1):
        title = (
            f"[민감] 회의 마일스톤 {seq} — 상세: ~/notes/meetings/{note_name}"
            if sensitive
            else item.title
        )
        deadline = item.deadline or "미정"
        if (title, deadline) in seen:
            continue
        existing.append(
            {
                "title": title,
                "deadline": deadline,
                "basis": "로컬 노트 참조" if sensitive else (item.basis or ""),
                "source": f"meeting:{note_name}",
                "added": now.isoformat(timespec="seconds"),
            }
        )
        seen.add((title, deadline))
        added += 1
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(_emit_milestones(existing), encoding="utf-8")
    return added


def _items_block(header: str, items: tuple[ActionItem, ...]) -> list[str]:
    lines = [f"## {header}", ""]
    if not items:
        lines.append("- (없음)")
    for item in items:
        owner = f"[{item.owner}] " if item.owner else ""
        deadline = item.deadline or "미정"
        lines.append(f"- {owner}{item.title} — 마감: {deadline} — 근거: {item.basis}")
    lines.append("")
    return lines


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
) -> Path:
    """Write the full-detail meeting note (W2-4 frontmatter-compatible)."""
    note_name = f"{now.strftime('%Y-%m-%d')}-meeting-{ref}.md"
    tags = ["meeting", "w2-3"] + (["patent-sensitive"] if sensitive else [])
    stamp = now.isoformat(timespec="seconds")
    lines = [
        "---",
        f"title: {_yaml_str(f'회의: {label}')}",
        f"tags: [{', '.join(tags)}]",
        f"created: {stamp}",
        f"updated: {stamp}",
        "links: []",
        "---",
        "",
        f"# 회의 요약 ({kind}, {now.strftime('%Y-%m-%d')})",
        "",
        "## 결정사항",
        "",
    ]
    lines += [f"- {decision}" for decision in extraction.decisions] or ["- (없음)"]
    lines.append("")
    lines += _items_block("내 액션아이템", extraction.todos)
    lines += _items_block("마일스톤", extraction.milestones)
    lines += _items_block("타인 액션아이템", extraction.others)
    lines += ["## 원문", "", "```", original_text.rstrip(), "```", ""]
    notes_dir.mkdir(parents=True, exist_ok=True)
    note_path = notes_dir / note_name
    note_path.write_text("\n".join(lines), encoding="utf-8")
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
) -> str:
    """Sanitized completion notice for the originating DM/channel."""
    lines = [f"회의록 처리 완료: {label}" if not sensitive else "회의록 처리 완료 (민감 문서)"]
    lines.append(f"- 내 액션아이템 카드: {cards}건 (Kanban)")
    lines.append(f"- 마일스톤 갱신: {milestones_added}건 (milestones.yaml)")
    if sensitive:
        lines.append("- 민감 태그 문서: 비-GLM 모델로 처리, 상세는 로컬 노트에만 보관")
        lines.append(f"- 타인 항목 {others}건은 공유 채널에 게시하지 않음 (로컬 노트 참조)")
    elif team_posted:
        lines.append(f"- 타인 액션아이템 {others}건 → #team 규약 게시 완료")
    lines.append(f"- 노트: ~/notes/meetings/{note_name}")
    return "\n".join(lines)
