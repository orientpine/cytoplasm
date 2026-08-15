"""CLI for dry compaction reports, one-shot reconciliation, and redacted state."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Final

from .apply import apply_curation
from .effects import alert_owner, post_promotion, read_twin
from .model import MemoryKind
from .report import build_report as build_curation_report
from .reporting import preview
from .shadow_cli import main as shadow_main
from .state import CuratorState
from .state_store import load_state
from .watch import CycleResult, run_cycle

_DEFAULT_DIR: Final = "~/.hermes/memories"
_DEFAULT_STATE: Final = "~/.hermes/memory-curator/state.json"


class _CompactArgs(argparse.Namespace):
    memory_dir: str = _DEFAULT_DIR
    kind: str = "both"
    apply: bool = False


class _ReconcileArgs(argparse.Namespace):
    memory_dir: str = _DEFAULT_DIR
    state_path: str = _DEFAULT_STATE
    dry_run: bool = False


class _StateArgs(argparse.Namespace):
    action: str = "show"
    state_path: str = _DEFAULT_STATE


def _existing_memory_dir(raw_path: str) -> Path | None:
    memory_dir = Path(raw_path).expanduser()
    if memory_dir.is_dir():
        return memory_dir
    print(json.dumps({"error": "memory-dir-not-found", "path": str(memory_dir)}))
    return None


def _compact(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="memory_curator",
        description="Keep Hermes MEMORY.md/USER.md tidy and under cap.",
    )
    _ = parser.add_argument(
        "--memory-dir",
        default=os.environ.get("MEMORY_CURATOR_DIR", _DEFAULT_DIR),
    )
    _ = parser.add_argument("--kind", choices=("memory", "user", "both"), default="both")
    _ = parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv, namespace=_CompactArgs())
    memory_dir = _existing_memory_dir(args.memory_dir)
    if memory_dir is None:
        return 2

    match args.kind:
        case "both":
            kinds: tuple[MemoryKind, ...] = ("memory", "user")
        case "memory":
            kinds = ("memory",)
        case "user":
            kinds = ("user",)
        case unreachable:
            parser.error(f"unknown kind: {unreachable}")
    reports = [
        build_curation_report(
            apply_curation(memory_dir, kind, dry_run=not args.apply)
        )
        for kind in kinds
    ]
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0


def _cycle_payload(result: CycleResult, *, dry_run: bool) -> dict[str, object]:
    return {
        "schema": "memory-curator-cycle-v2",
        "dry_run": dry_run,
        "compacted": [build_curation_report(item) for item in result.compacted],
        "promoted": [
            {
                "draft_id": receipt.draft_id,
                "slug": receipt.slug,
                "kind": proposal.twin_kind,
            }
            for proposal, receipt in result.promoted
        ],
        "deleted": [
            {
                "kind": item.source_kind,
                "preview": preview(item.entry_text),
                "freed_chars": item.freed_chars,
                "applied": item.applied,
                "backup": item.backup_path.name if item.backup_path is not None else None,
            }
            for item in result.deleted
        ],
        "blocked": [
            {
                "key": blocked.promotion_key[:16] + "…",
                "reason": blocked.reason,
                "preview": preview(blocked.entry_text) if blocked.entry_text is not None else None,
            }
            for blocked in result.blocked
        ],
        "near_cap_kinds": list(result.near_cap_kinds),
        "alert_decision": result.alert_decision,
        "alerted": result.alerted,
    }


def _reconcile(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="memory_curator reconcile")
    _ = parser.add_argument(
        "--memory-dir",
        default=os.environ.get("MEMORY_CURATOR_DIR", _DEFAULT_DIR),
    )
    _ = parser.add_argument(
        "--state-path",
        default=os.environ.get("MEMORY_CURATOR_STATE", _DEFAULT_STATE),
    )
    _ = parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv, namespace=_ReconcileArgs())
    memory_dir = _existing_memory_dir(args.memory_dir)
    if memory_dir is None:
        return 2

    previous_dry_run = os.environ.get("MEMORY_CURATOR_DRY_RUN")
    requested_dry_run = args.dry_run
    if requested_dry_run:
        os.environ["MEMORY_CURATOR_DRY_RUN"] = "1"
    effective_dry_run = os.environ.get("MEMORY_CURATOR_DRY_RUN") == "1"
    try:
        result = run_cycle(
            memory_dir,
            Path(args.state_path).expanduser(),
            promote=post_promotion,
            alert=alert_owner,
            read_twin=read_twin,
        )
    finally:
        if requested_dry_run:
            if previous_dry_run is None:
                del os.environ["MEMORY_CURATOR_DRY_RUN"]
            else:
                os.environ["MEMORY_CURATOR_DRY_RUN"] = previous_dry_run
    print(json.dumps(_cycle_payload(result, dry_run=effective_dry_run), ensure_ascii=False, indent=2))
    return 0


def _redacted_state(state: CuratorState) -> dict[str, object]:
    promotions = [
        {
            "key": key[:16] + "…",
            "source_kind": record.source_kind,
            "status": record.status,
            "created_at": record.created_at,
            "posted_at": record.posted_at,
            "reconciled_at": record.reconciled_at,
            "has_note_hash": bool(record.note_sha256),
            "has_draft_id": record.draft_id is not None,
            "has_confirm_message_id": record.confirm_message_id is not None,
            "has_backup": record.backup_path is not None,
            "last_block_reason": record.last_block_reason,
        }
        for key, record in sorted(state.promotions.items())
    ]
    return {
        "version": state.version,
        "promotions": promotions,
        "alert": {
            "last_observed": state.alert.last_observed_signature is not None,
            "last_sent": state.alert.last_sent_signature is not None,
            "last_sent_at": state.alert.last_sent_at,
            "pending": state.alert.pending_signature is not None,
        },
    }


def _state(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="memory_curator state")
    _ = parser.add_argument("action", choices=("show",))
    _ = parser.add_argument(
        "--state-path",
        default=os.environ.get("MEMORY_CURATOR_STATE", _DEFAULT_STATE),
    )
    args = parser.parse_args(argv, namespace=_StateArgs())
    state = load_state(Path(args.state_path).expanduser())
    print(json.dumps(_redacted_state(state), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    match arguments:
        case ["reconcile", *rest]:
            return _reconcile(rest)
        case ["state", *rest]:
            return _state(rest)
        case ["shadow", *rest]:
            return shadow_main(rest)
        case _:
            return _compact(arguments)


if __name__ == "__main__":
    sys.exit(main())
