from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ApprovalState(StrEnum):
    PENDING = "pending"
    EXPIRED = "expired"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class TodoApprovalSpec:
    key: str
    action_hash: str
    target_id: str
    argv_summary: str
    kind: str
    surface: str
    channel_id: str
    policy_version: int
    origin_channel_id: str = ""
    origin_message_id: str = ""
    tasklist: str = ""
    title: str = ""
    notes: str | None = None
    due: str | None = None


@dataclass(frozen=True, slots=True)
class TodoApprovalRecord:
    key: str
    generation: int
    action_hash: str
    target_id: str
    argv_summary: str
    message_id: str | None
    created_at: datetime
    state: ApprovalState
    outcome: str | None
    kind: str
    surface: str
    channel_id: str
    policy_version: int
    origin_channel_id: str = ""
    origin_message_id: str = ""
    tasklist: str = ""
    title: str = ""
    notes: str | None = None
    due: str | None = None
