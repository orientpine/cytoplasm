"""Private node-local history for self-skill audits and overlap decisions.

Owner notices are intentionally transient, so these files retain the facts needed to
review a quiet audit or make an explicit archive-versus-promotion decision later.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation.selfskill_audit.delta import Action, Delta
from automation.selfskill_audit.overlap import OverlapHit

_STATE_ROOT_ENV: Final = "HERMES_STATE_ROOT"


def _state_root(home: Path | None) -> Path:
    configured = os.environ.get(_STATE_ROOT_ENV, "").strip()
    return Path(configured).expanduser() if configured else (Path.home() if home is None else home) / ".hermes"


def _timestamp(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def _fail(error: Exception) -> None:
    print(f"LOCAL-LOG-FAIL {error}", file=sys.stderr)


def append_run(
    *,
    now: datetime,
    account: str,
    deltas: tuple[Delta, ...],
    shadowed: tuple[str, ...],
    overlaps: tuple[OverlapHit, ...],
    notified: bool,
    home: Path | None = None,
) -> None:
    """Append every outcome, including quiet runs, because absent notices are ambiguous.

    Local storage failure is advisory: it must not alter notification delivery or the
    audit watermark, which continue to provide the existing at-least-once guarantee.
    """
    timestamp = _timestamp(now)
    counts = Counter(delta.action for delta in deltas)
    record = {
        "ts": timestamp,
        "account": account,
        "delta_counts": {action.value: counts[action] for action in Action},
        "shadowed": list(shadowed),
        "overlaps": [
            {"self": hit.self_name, "governed": hit.governed_name, "score": hit.score}
            for hit in overlaps
        ],
        "notified": notified,
    }
    path = _state_root(home) / "logs" / "selfskill-audit" / f"{timestamp[:7]}.jsonl"
    try:
        _private_directory(path.parent)
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            _ = handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o600)
    except (OSError, TypeError, ValueError) as error:
        _fail(error)


def _read_pending(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("pending overlaps must be a JSON object")
    return document


def _atomic_private_json(path: Path, document: str) -> None:
    _private_directory(path.parent)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            _ = handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        temporary = None
        path.chmod(0o600)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def update_pending_overlaps(
    *, now: datetime,
    overlaps: tuple[OverlapHit, ...],
    home: Path | None = None,
) -> int:
    """Refresh the current overlap decision ledger and drop observations now resolved.

    A hit persists until it disappears instead of until a DM happens, so the owner can
    decide whether the self skill should be archived or promoted on a later day.
    """
    timestamp = _timestamp(now)
    path = _state_root(home) / "selfskill-audit" / "pending-overlaps.json"
    try:
        previous = _read_pending(path)
        pending: dict[str, object] = {}
        for hit in overlaps:
            key = f"{hit.self_name}→{hit.governed_name}"
            old = previous.get(key)
            first_seen = old.get("first_seen") if isinstance(old, dict) else None
            pending[key] = {
                "first_seen": first_seen if isinstance(first_seen, str) else timestamp,
                "last_seen": timestamp,
                "score": hit.score,
                "shared": list(hit.shared),
            }
        _atomic_private_json(path, json.dumps(pending, ensure_ascii=False, sort_keys=True) + "\n")
        return len(pending)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        _fail(error)
        return len(overlaps)
