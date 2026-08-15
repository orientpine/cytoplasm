#!/usr/bin/env python3
"""Ops-only forced-command receiver for full repair logs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final


PRIVATE_ROOT: Final = Path("/srv/autophagy-private/repair-logs")
TICKET_ID: Final = re.compile(r"^t_[A-Za-z0-9]+$")


@dataclass(frozen=True, slots=True)
class LogRequest:
    ticket_id: str
    occurrence: int
    raw_log: str


def parse_request(raw: str) -> LogRequest:
    """Parse the one JSON request accepted from the restricted SSH key."""
    try:
        payload: dict[str, str | int] = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("invalid repair log request") from error
    if not isinstance(payload, dict):
        raise ValueError("repair log request is not an object")
    ticket_id, occurrence, raw_log = payload.get("ticket_id"), payload.get("occurrence"), payload.get("raw_log")
    if not isinstance(ticket_id, str) or TICKET_ID.fullmatch(ticket_id) is None:
        raise ValueError("invalid repair ticket id")
    if not isinstance(occurrence, int) or occurrence < 1:
        raise ValueError("invalid repair occurrence")
    if not isinstance(raw_log, str):
        raise ValueError("repair log is not text")
    return LogRequest(ticket_id=ticket_id, occurrence=occurrence, raw_log=raw_log)


def persist(request: LogRequest) -> tuple[Path, str]:
    """Write one full log under the ops-only directory and return opaque linkage."""
    os.umask(0o077)
    ticket_dir = PRIVATE_ROOT / request.ticket_id
    ticket_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = ticket_dir.chmod(0o700)
    path = ticket_dir / f"occurrence-{request.occurrence}.log"
    path.write_text(request.raw_log, encoding="utf-8")
    _ = path.chmod(0o600)
    return path, hashlib.sha256(request.raw_log.encode("utf-8")).hexdigest()


def main() -> int:
    """Persist stdin without ever echoing its contents."""
    request = parse_request(sys.stdin.read())
    path, log_hash = persist(request)
    print(json.dumps({"path": str(path), "sha256": log_hash}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"repair-log error: {error.__class__.__name__}", file=sys.stderr)
        raise SystemExit(1)
