"""CLI: ``python3 -m automation.approval_kpi --root <dir> [--json]``.

Read-only. It opens ledgers below ``--root``, prints one markdown row per approval kind
joined with the static TTL/reminder policy, and exits 0 with ``no records`` when the
root is missing or holds nothing this tool can interpret.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from automation.approval_kpi.aggregate import aggregate
from automation.approval_kpi.model import KindStats
from automation.approval_kpi.policy_table import POLICY_TABLE, PolicyEntry, unguarded_kinds
from automation.approval_kpi.readers import read_root

_COLUMNS = (
    "kind", "count", "decided", "per_day", "p50_s", "p95_s",
    "re-request", "manual", "ttl_s", "reminder",
)


def _policy(kind: str) -> PolicyEntry | None:
    for entry in POLICY_TABLE:
        if entry.kind == kind:
            return entry
    return None


def _seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


def _row(stats: KindStats) -> tuple[str, ...]:
    entry = _policy(stats.kind)
    return (
        stats.kind,
        str(stats.count),
        str(stats.decided),
        f"{stats.per_day:.2f}",
        _seconds(stats.p50_seconds),
        _seconds(stats.p95_seconds),
        f"{stats.rerequest_rate:.2f}",
        f"{stats.manual_reaction_rate:.2f}",
        "n/a" if entry is None else entry.ttl_text,
        "n/a" if entry is None else entry.reminder_text,
    )


def render_markdown(rows: tuple[KindStats, ...], skips: dict[str, int]) -> str:
    """The report: one row per kind, then the skip ledger and the unguarded kinds."""
    lines = [
        "| " + " | ".join(_COLUMNS) + " |",
        "| " + " | ".join("---" for _ in _COLUMNS) + " |",
    ]
    lines += ["| " + " | ".join(_row(stats)) + " |" for stats in rows]
    skipped = ", ".join(f"{reason}={count}" for reason, count in sorted(skips.items()))
    lines.append("")
    lines.append(f"skipped: {skipped or 'none'}")
    unguarded = unguarded_kinds()
    lines.append(f"no TTL and no reminder: {', '.join(unguarded) or 'none found'}")
    return "\n".join(lines)


def _payload(rows: tuple[KindStats, ...], skips: dict[str, int]) -> str:
    return json.dumps(
        {
            "kinds": [dict(zip(_COLUMNS, _row(stats), strict=True)) for stats in rows],
            "skipped": dict(sorted(skips.items())),
            "unguarded_kinds": list(unguarded_kinds()),
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m automation.approval_kpi",
        description="Read-only approval-ledger KPI aggregator (K9).",
    )
    _ = parser.add_argument("--root", required=True, help="directory holding the ledgers")
    _ = parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args(argv)

    skips: dict[str, int] = {}
    rows = aggregate(read_root(Path(args.root).expanduser(), skips))
    if not rows:
        print("no records")
        return 0
    print(_payload(rows, skips) if args.json else render_markdown(rows, skips))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
