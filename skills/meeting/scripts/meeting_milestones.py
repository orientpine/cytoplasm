"""Milestone state-file serialization and updates for meeting ingest."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from meeting_llm import ActionItem


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
