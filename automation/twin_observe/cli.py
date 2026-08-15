from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.twin_observe.aggregate import aggregate_events
from automation.twin_observe.ledgers import LedgerSource, read_ledgers
from automation.twin_observe.propose import WikiDraftRequest, build_candidates, submit_candidates

_DEFAULT_AUDIT_LOG: Final = Path("~/.hermes/wiki-gate/audit.jsonl")
_DEFAULT_APPROVAL_LOG: Final = Path("/srv/autophagy-agents/logs/approvals.jsonl")


@dataclass(frozen=True, slots=True)
class ObserveRequest:
    sources: tuple[LedgerSource, ...]
    draft_request: WikiDraftRequest
    dry_run: bool


def _default_wiki_cli() -> Path:
    return Path(__file__).resolve().parents[2] / "skills" / "wiki" / "scripts" / "wiki_cli.py"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="On-demand observed decision-twin analysis")
    parser.add_argument("--wiki-audit", type=Path, default=_DEFAULT_AUDIT_LOG)
    parser.add_argument("--approvals", type=Path, default=_DEFAULT_APPROVAL_LOG)
    parser.add_argument("--wiki-cli", type=Path, default=_default_wiki_cli())
    parser.add_argument("--channel-id", default="dm")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _request_from_args(args: argparse.Namespace) -> ObserveRequest:
    sources = (
        LedgerSource("wiki-audit", args.wiki_audit.expanduser()),
        LedgerSource("approvals", args.approvals.expanduser()),
    )
    draft_request = WikiDraftRequest(
        wiki_cli=args.wiki_cli.expanduser(),
        channel_id=args.channel_id,
        environment=dict(os.environ),
    )
    return ObserveRequest(sources, draft_request, args.dry_run)


def run(request: ObserveRequest) -> int:
    read_result = read_ledgers(request.sources)
    candidates = build_candidates(aggregate_events(read_result.events))
    print(
        "OBSERVED-CANDIDATES "
        f"count={len(candidates)} skipped_lines={read_result.skipped_lines} "
        f"unreadable_ledgers={','.join(read_result.unreadable_ledgers) or '-'}"
    )
    for candidate in candidates:
        tally = candidate.tally
        print(
            f"CANDIDATE skill={tally.skill} action={tally.action} "
            f"rejects={tally.rejects} approves={tally.approves} authority={candidate.authority}"
        )
    if not request.dry_run:
        submit_candidates(candidates, request.draft_request)
    return 0


def main() -> int:
    return run(_request_from_args(build_parser().parse_args()))
