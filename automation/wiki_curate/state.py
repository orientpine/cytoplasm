"""Weekly proposal quota, persisted outside every git checkout."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TypeAlias

Clock: TypeAlias = Callable[[], datetime]

DEFAULT_STATE_PATH = Path("~/.hermes/wiki-curate/state.json")


class StateRefused(RuntimeError):
    """The quota file would land somewhere it must never be written."""


def _assert_outside_checkout(path: Path) -> None:
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            raise StateRefused(
                f"runtime state must not live inside a git checkout: {path} (found {parent}/.git)"
            )


def _iso_week(clock: Clock) -> str:
    year, week, _ = clock().date().isocalendar()
    return f"{year}-W{week:02d}"


def _read(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _proposed_this_week(path: Path, week: str) -> int:
    data = _read(path)
    if data.get("iso_week") != week:
        return 0
    try:
        return max(0, int(data.get("proposed", 0)))
    except (TypeError, ValueError):
        return 0


def remaining_quota(path: Path, *, cap: int, clock: Clock) -> int:
    _assert_outside_checkout(path)
    return max(0, cap - _proposed_this_week(path, _iso_week(clock)))


def record_proposals(path: Path, count: int, *, clock: Clock) -> None:
    _assert_outside_checkout(path)
    week = _iso_week(clock)
    _write(path, {"iso_week": week, "proposed": _proposed_this_week(path, week) + count})
