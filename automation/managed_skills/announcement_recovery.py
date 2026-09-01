"""Audited operator recovery for an ambiguous managed-release announcement."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.interop.approval_lease import abandon
from automation.managed_skills.announce_ledger import (
    AnnounceLedger,
    AnnounceLedgerError,
    state_dir,
)

_ACTION_HASH: Final = re.compile(r"sha256:[0-9a-f]{64}\Z")
_KEY_PREFIX: Final = "managed-announce:"
_AUDIT_NAME: Final = "announcement-abandon.audit.jsonl"


class AnnouncementRecoveryError(Exception):
    """Recovery input or live state does not prove the requested reservation binding."""


@dataclass(frozen=True, slots=True)
class RecoveryRequest:
    """A hash-bound request to abandon one confirmed-not-delivered reservation."""

    state_root: Path
    key: str
    action_hash: str
    actor: str
    reason: str

    def __post_init__(self) -> None:
        if not self.key.startswith(_KEY_PREFIX):
            raise AnnouncementRecoveryError("announcement key has an invalid namespace")
        if _ACTION_HASH.fullmatch(self.action_hash) is None:
            raise AnnouncementRecoveryError("announcement action hash is malformed")
        if not self.actor.strip():
            raise AnnouncementRecoveryError("announcement recovery actor is required")
        if not self.reason.strip():
            raise AnnouncementRecoveryError("announcement recovery reason is required")


class _Arguments(argparse.Namespace):
    command: str
    key: str
    action_hash: str
    actor: str
    reason: str
    state_dir: Path | None

    def __init__(self) -> None:
        super().__init__()
        self.command = ""
        self.key = ""
        self.action_hash = ""
        self.actor = ""
        self.reason = ""
        self.state_dir = None


def _audit_request(request: RecoveryRequest, audit_path: Path) -> None:
    """Durably identify the operator's requested reservation abandonment."""
    audit_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = json.dumps(
        {
            "action_hash": request.action_hash,
            "actor": request.actor,
            "event": "announcement-abandon-requested",
            "key": request.key,
            "reason": request.reason,
        },
        sort_keys=True,
    )
    with audit_path.open("a", encoding="utf-8") as handle:
        _ = handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    _ = audit_path.chmod(0o600)


def abandon_announcement(request: RecoveryRequest) -> Path:
    """Audit then clear an exact stale reservation while holding its producer lease."""
    ledger = AnnounceLedger(request.state_root)
    with ledger.lease.hold(request.key) as owned:
        if not owned:
            raise AnnouncementRecoveryError("announcement lease is held")
        try:
            committed = ledger.read(request.key)
        except AnnounceLedgerError as error:
            raise AnnouncementRecoveryError("announcement ledger is unreadable") from error
        if committed is not None:
            raise AnnouncementRecoveryError("announcement already has a committed delivery record")
        reservation = ledger.journal.outstanding(request.key)
        if reservation is None:
            raise AnnouncementRecoveryError("announcement reservation is absent")
        binding = (reservation.get("key"), reservation.get("action_hash"))
        if binding != (request.key, request.action_hash):
            raise AnnouncementRecoveryError("announcement reservation binding does not match")
        audit_path = request.state_root / _AUDIT_NAME
        _audit_request(request, audit_path)
        abandon(request.key, ledger.journal, audit_path)
    return audit_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    abandon_parser = commands.add_parser(
        "abandon",
        help="audit and clear a reservation the owner confirmed was not delivered",
    )
    _ = abandon_parser.add_argument("--key", required=True)
    _ = abandon_parser.add_argument("--action-hash", required=True)
    _ = abandon_parser.add_argument("--actor", required=True)
    _ = abandon_parser.add_argument("--reason", required=True)
    _ = abandon_parser.add_argument("--state-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _Arguments()
    _ = _parser().parse_args(argv, namespace=args)
    root = args.state_dir if args.state_dir is not None else state_dir()
    try:
        audit_path = abandon_announcement(
            RecoveryRequest(root, args.key, args.action_hash, args.actor, args.reason)
        )
    except AnnouncementRecoveryError as error:
        print(f"ANNOUNCEMENT-RECOVERY-BLOCK: {error}", file=sys.stderr)
        return 1
    print(f"ANNOUNCEMENT-ABANDONED key={args.key} audit={audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
