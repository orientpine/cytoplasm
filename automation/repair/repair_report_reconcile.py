"""Recover missing report requests from ops-owned terminal lifecycle records."""

# pyright: reportUnnecessaryComparison=false

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, assert_never
from uuid import uuid4

from automation.repair.repair_capability import read_published
from automation.repair.repair_lifecycle import LifecycleRecord, LifecycleState, RepairLifecycleStore
from automation.repair.repair_report_queue import (
    ReportRequest,
    ack_dir,
    enqueue_if_missing_semantic,
    semantic_key,
)


_DEFAULT_LIFECYCLE_ROOT: Final = "/srv/autophagy-private/repair-state"
_REOPEN_REASON_CODES: Final[Mapping[str, str]] = {
    "sandbox gate rejected": "sandbox_rejected",
    "regression bank failed; patch reverted": "bank_failed_reverted",
    "owner_cancelled": "owner_cancelled",
    "approval_expired": "approval_expired",
}


def lifecycle_root() -> Path:
    """Return the read-only ops lifecycle root."""
    return Path(os.environ.get("REPAIR_STATE_ROOT", _DEFAULT_LIFECYCLE_ROOT))


def _report_identity(record: LifecycleRecord) -> tuple[str, str] | None:
    match record.state:
        case LifecycleState.DONE:
            return "complete", "applied"
        case LifecycleState.REOPENED:
            return "reopen", _REOPEN_REASON_CODES.get(record.reason, "unspecified")
        case (
            LifecycleState.OPEN
            | LifecycleState.DIAGNOSING
            | LifecycleState.SANDBOXED
            | LifecycleState.AWAITING_APPROVAL
            | LifecycleState.APPLIED
        ):
            return None
        case unreachable:
            assert_never(unreachable)


def _reported_marker(ticket_id: str, operation: str, reason_code: str) -> Path:
    payload = f"{ticket_id}|{operation}|{reason_code}"
    return ack_dir() / f"reported-{hashlib.sha256(payload.encode()).hexdigest()}.json"


def reconcile(*, limit: int = 50) -> int:
    """Enqueue at most ``limit`` lifecycle files missing a semantic report request."""
    root = lifecycle_root()
    if limit <= 0 or not root.is_dir():
        return 0
    try:
        lifecycle_files = sorted(root.glob("*.json"))[:limit]
    except OSError:
        print("repair report reconcile skipped: lifecycle unavailable", file=sys.stderr)
        return 0

    enqueued = 0
    store = RepairLifecycleStore(root)
    for lifecycle_file in lifecycle_files:
        try:
            record = store.read(lifecycle_file.stem)
            identity = _report_identity(record)
            if identity is None:
                continue
            operation, reason_code = identity
            capability = read_published(record.ticket_id)
            if capability is None:
                continue
            if _reported_marker(record.ticket_id, operation, reason_code).exists():
                continue
            request = ReportRequest(
                request_id=uuid4().hex,
                operation=operation,
                ticket_id=record.ticket_id,
                reason_code=reason_code,
                occurrence=capability["occurrence"],
                mac=capability["mac"],
                created=datetime.now(tz=UTC).isoformat(),
            )
            if (ack_dir() / f"sem-{semantic_key(request)}.json").exists():
                continue
            if enqueue_if_missing_semantic(request):
                enqueued += 1
        except Exception:  # noqa: BROAD_EXCEPT_OK, BLE001 -- reporting cannot abort repair
            print("repair report reconcile skipped: lifecycle entry unavailable", file=sys.stderr)
    return enqueued


def _main(argv: list[str]) -> int:
    if argv:
        return 2
    print(reconcile())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
