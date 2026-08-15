"""Pure report builder for a curation result.

Turns a :class:`~automation.memory_curator.apply.CurationResult` into a
stable ``memory-curator-v1`` JSON-able dict: cap accounting, what the
autonomous pass freed, and the durable-judgment entries flagged for the
owner-gated twin promotion (bodies included so the caller can render a
proposal — never auto-applied).
"""

from __future__ import annotations

from .apply import CurationResult

SCHEMA = "memory-curator-v1"


def build_report(result: CurationResult) -> dict[str, object]:
    plan = result.plan
    return {
        "schema": SCHEMA,
        "kind": result.kind,
        "changed": result.changed,
        "original_chars": plan.original_chars,
        "compacted_chars": plan.compacted_chars,
        "char_cap": plan.char_cap,
        "fill_ratio": round(plan.compacted_chars / plan.char_cap, 4),
        "freed_chars": plan.freed_chars,
        "headroom_after": plan.headroom_after,
        "near_cap": plan.near_cap,
        "backup": str(result.backup_path) if result.backup_path is not None else None,
        "promotion_candidates": [entry.text for entry in plan.promotion_candidates],
    }
