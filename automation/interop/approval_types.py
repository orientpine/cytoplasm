"""Dependency-neutral identity and observation types for owner approvals."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    key: str
    action_hash: str
    message_id: str
    channel_id: str
    created_at: str


class Probe(StrEnum):
    BOUND_PENDING = "bound-pending"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    MISSING = "missing"
    BINDING_MISMATCH = "binding-mismatch"
    UNVERIFIABLE = "unverifiable"
