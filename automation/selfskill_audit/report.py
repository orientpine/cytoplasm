from __future__ import annotations

import getpass
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation.owner_notice import notify_owner
from automation.selfskill_audit.ledger import Action, Delta, audit, mark_reported

_ACCOUNT_LABEL: Final = re.compile(r"[a-z0-9_-]{1,32}")
_ACTION_LABELS: Final = {
    Action.CREATED: "생성",
    Action.EDITED: "편집",
    Action.ARCHIVED: "보관",
    Action.RESTORED: "복원",
}


def render_summary(deltas: tuple[Delta, ...], *, account_label: str) -> str:
    label = account_label if _ACCOUNT_LABEL.fullmatch(account_label) else "unknown"
    counts = Counter(delta.action for delta in deltas)
    count_text = " ".join(
        f"{_ACTION_LABELS[action]}={counts[action]}"
        for action in Action
        if counts[action]
    )
    lines = [f"[자체 스킬 감사] 계정={label}", f"변경={len(deltas)} {count_text}"]
    lines.extend(
        f"- {_ACTION_LABELS[delta.action]} {delta.name} sha256={delta.sha256[:12]} 출처={delta.provenance}"
        for delta in deltas
    )
    return "\n".join(lines)


def send_report(deltas: tuple[Delta, ...], *, account_label: str) -> bool:
    return notify_owner(render_summary(deltas, account_label=account_label))


def run_once(
    *,
    home: Path = Path.home(),
    account_label: str | None = None,
    now: datetime | None = None,
) -> int:
    result = audit(home, now=datetime.now(UTC) if now is None else now)
    if not result.pending_deltas:
        return 0
    label = getpass.getuser() if account_label is None else account_label
    if not send_report(result.pending_deltas, account_label=label):
        return 1
    mark_reported(result)
    return 0


def main(argv: tuple[str, ...] | None = None) -> int:
    arguments = tuple(sys.argv[1:]) if argv is None else argv
    if arguments != ("--once",):
        print("usage: python3 -m automation.selfskill_audit.report --once", file=sys.stderr)
        return 2
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
