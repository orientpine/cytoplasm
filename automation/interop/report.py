"""Formatter and strict parser for Interop Protocol v0 task reports."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final


PROTOCOL_VERSION: Final = "v0"
_REPORT_PATTERN: Final = re.compile(r"^```json\n(\{[\s\S]*\})\n```$")
_REQUIRED_KEYS: Final = frozenset(
    {"version", "agent_id", "task_id", "status", "summary", "links", "timestamp"}
)


class ReportStatus(StrEnum):
    """The three normative task state changes."""

    START = "start"
    DONE = "done"
    BLOCKED = "blocked"


# Interop 규약 §1.3: `summary`는 공유 `#agents-log`에 게시되므로 내부 상세·민감
# 본문이 새지 않도록 작성 시 결정론적으로 마스킹한다. 순서 의존적(긴 패턴 먼저).
_SUMMARY_MASKS: Final = (
    # secret/token 모양 — repair_redaction 계열과 동일
    (re.compile(r"sk-[A-Za-z0-9_-]{6,}"), "[MASKED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[MASKED_TOKEN]"),
    (re.compile(r"\b(?:Bearer|Bot)\s+\S+", re.IGNORECASE), "[MASKED_AUTH]"),
    # 내부 파일 경로·소스 식별자 — dir/dir/file.ext 또는 단일 file.ext
    (re.compile(r"\b(?:[\w.-]+/)*[\w.-]+\.(?:py|pyi|ts|tsx|js|sh|yaml|yml|json|md|rs|go|toml|cfg|ini)\b"), "[MASKED_PATH]"),
    # PII: 이메일
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[MASKED_EMAIL]"),
    # Discord/snowflake 모양의 긴 숫자 식별자
    (re.compile(r"\b\d{17,19}\b"), "[MASKED_ID]"),
)


def mask_summary(summary: str) -> str:
    """Deterministically mask protocol §1.3-forbidden detail from a summary.

    Enforced at the single write chokepoint (:func:`format_report`) so every
    emission path is covered. Masks file paths / source identifiers, secret and
    token shapes, email PII, and snowflake-shaped ids. Activity-level Korean or
    English prose passes through unchanged.
    """
    masked = summary
    for pattern, replacement in _SUMMARY_MASKS:
        masked = pattern.sub(replacement, masked)
    return masked


@dataclass(frozen=True, slots=True)
class TaskReport:
    """A validated Interop Protocol v0 report value."""

    agent_id: str
    task_id: str
    status: ReportStatus
    summary: str
    links: tuple[str, ...]
    timestamp: datetime


def format_report(report: TaskReport) -> str:
    """Render a report as the spec's standalone parser-extractable JSON block."""
    payload = {
        "version": PROTOCOL_VERSION,
        "agent_id": report.agent_id,
        "task_id": report.task_id,
        "status": report.status.value,
        "summary": mask_summary(report.summary),
        "links": list(report.links),
        "timestamp": report.timestamp.isoformat(),
    }
    return f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"


def parse_report(message: str) -> TaskReport | None:
    """Parse only a complete, conformant Interop Protocol v0 report block."""
    matched = _REPORT_PATTERN.fullmatch(message)
    if matched is None:
        return None
    try:
        payload = json.loads(matched.group(1))
        if not isinstance(payload, dict) or set(payload) != _REQUIRED_KEYS:
            return None
        version = payload["version"]
        agent_id = payload["agent_id"]
        task_id = payload["task_id"]
        status = payload["status"]
        summary = payload["summary"]
        links = payload["links"]
        timestamp = payload["timestamp"]
        if (
            version != PROTOCOL_VERSION
            or not isinstance(agent_id, str)
            or not isinstance(task_id, str)
            or not isinstance(status, str)
            or not isinstance(summary, str)
            or not isinstance(links, list)
            or not all(isinstance(link, str) for link in links)
            or not isinstance(timestamp, str)
        ):
            return None
        return TaskReport(
            agent_id=agent_id,
            task_id=task_id,
            status=ReportStatus(status),
            summary=summary,
            links=tuple(links),
            timestamp=datetime.fromisoformat(timestamp),
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
