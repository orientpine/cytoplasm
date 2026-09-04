"""Read-only KPI aggregator over the owner-approval ledgers (K9).

Nothing in this package writes, posts, or asks for an approval: it opens ledger files
that other components already produced, skips what it cannot interpret with certainty,
and reports per-kind volume, wait percentiles, and re-request rate. The static TTL and
reminder policy per kind lives in :mod:`automation.approval_kpi.policy_table`.
"""
from __future__ import annotations

from automation.approval_kpi.aggregate import aggregate
from automation.approval_kpi.model import ApprovalEvent, KindStats
from automation.approval_kpi.policy_table import POLICY_TABLE, PolicyEntry
from automation.approval_kpi.readers import read_posting_journal, read_root, read_skill_gate_log

__all__ = [
    "POLICY_TABLE",
    "ApprovalEvent",
    "KindStats",
    "PolicyEntry",
    "aggregate",
    "read_posting_journal",
    "read_root",
    "read_skill_gate_log",
]
