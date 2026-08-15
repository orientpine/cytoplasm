"""Persist the fail-closed regression-bank state consumed by W6-2 repairs."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final


class BankStatus(StrEnum):
    """Recorded health of the latest complete regression-bank execution."""

    PASSING = "passing"
    FAILING = "failing"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BankState:
    """Minimal durable bank result; an unknown state is deliberately fail-closed."""

    status: BankStatus
    returncode: int | None
    finished_at: str | None


DEFAULT_STATE_PATH: Final = Path("/srv/autophagy-agents/logs/regression-bank-state.json")


class _RecordArguments(argparse.Namespace):
    def __init__(self) -> None:
        super().__init__()
        self.returncode: int = 0
        self.state_file: Path = DEFAULT_STATE_PATH


def read_state(path: Path) -> BankState:
    """Read one atomically-written state file, treating invalid or absent state as unknown."""
    if not path.is_file():
        return BankState(BankStatus.UNKNOWN, None, None)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return BankState(BankStatus.UNKNOWN, None, None)
    if not isinstance(raw, dict):
        return BankState(BankStatus.UNKNOWN, None, None)
    status_text = raw.get("status")
    returncode = raw.get("returncode")
    finished_at = raw.get("finished_at")
    if not isinstance(status_text, str) or not isinstance(returncode, int):
        return BankState(BankStatus.UNKNOWN, None, None)
    if finished_at is not None and not isinstance(finished_at, str):
        return BankState(BankStatus.UNKNOWN, None, None)
    try:
        status = BankStatus(status_text)
    except ValueError:
        return BankState(BankStatus.UNKNOWN, None, None)
    return BankState(status, returncode, finished_at)


def record_result(path: Path, returncode: int, finished_at: datetime | None = None) -> BankState:
    """Atomically replace the bank result so repair processes never observe a partial state."""
    state = BankState(
        BankStatus.PASSING if returncode == 0 else BankStatus.FAILING,
        returncode,
        (finished_at or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(
        {"status": state.status, "returncode": state.returncode, "finished_at": state.finished_at},
        separators=(",", ":"),
    )
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        _ = handle.write(payload + "\n")
        temporary = Path(handle.name)
    temporary.chmod(0o644)
    os.replace(temporary, path)
    return state


def allows_patch_application(path: Path = DEFAULT_STATE_PATH) -> bool:
    """Allow a repair mutation only after the most recently recorded bank run passed."""
    return read_state(path).status is BankStatus.PASSING


def main(argv: list[str]) -> int:
    """Record one completed bank result for the fail-closed repair gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("command", choices=("record",))
    _ = parser.add_argument("--returncode", type=int, required=True)
    _ = parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_PATH)
    arguments = _RecordArguments()
    _ = parser.parse_args(argv, namespace=arguments)
    state = record_result(arguments.state_file, arguments.returncode)
    print(f"bank-state {state.status} rc={state.returncode} at={state.finished_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
