"""On-demand entrypoint. Nothing here schedules itself and nothing here writes a note."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from automation.wiki_curate.run import run_curation
from automation.wiki_curate.sources import (
    read_obsidian_notes,
    read_wiki_digests,
    read_wiki_origins,
)
from automation.wiki_curate.state import DEFAULT_STATE_PATH

DEFAULT_WORKSPACE = Path("~/.hermes/wiki-curate/drafts")
DEFAULT_WIKI_SCRIPTS = Path("/srv/autophagy-skills/live/wiki/scripts")
DEFAULT_CAP = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m automation.wiki_curate",
        description="Obsidian 원천에서 위키 초안 후보를 제안한다 (저장은 소유자 ✅ 게이트).",
    )
    parser.add_argument("--obsidian-root", type=Path, required=True)
    parser.add_argument("--wiki-root", type=Path, default=Path("~/wiki"))
    parser.add_argument("--wiki-scripts", type=Path, default=DEFAULT_WIKI_SCRIPTS)
    parser.add_argument("--sensitivity-rules", type=Path,
                        default=Path("~/.hermes/rag-ingest/sensitivity-rules.yaml"))
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--cap", type=int, default=DEFAULT_CAP, help="주당 제안 상한")
    parser.add_argument("--emit", action="store_true",
                        help="실제로 draft 게이트에 넘긴다 (기본은 계획만 출력)")
    return parser


def _classifier(rules_path: Path):
    from automation.rag_ingest.sensitivity import classify, load_rules

    rules = load_rules(rules_path)
    return lambda text: frozenset(classify(text, rules))


def _runner(argv: list[str]) -> int:
    return subprocess.run([sys.executable, *argv], check=False).returncode


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from automation.twin_distill.llm import CodexLlmClient, LlmConfigurationError

    try:
        classifier = _classifier(args.sensitivity_rules.expanduser())
    except Exception as error:  # noqa: BLE001 - fail closed: cannot tell sensitive from not
        print(f"CURATE-BLOCK: 민감도 규칙을 읽을 수 없어 중단한다: {error}", file=sys.stderr)
        return 4
    cli_path = args.wiki_scripts.expanduser() / "wiki_cli.py"
    if not cli_path.is_file():
        print(f"CURATE-BLOCK: 위키 draft 게이트가 없다: {cli_path}", file=sys.stderr)
        return 4
    try:
        client = CodexLlmClient.from_environment(os.environ)
    except LlmConfigurationError as error:
        print(f"CURATE-BLOCK: {error}", file=sys.stderr)
        return 4

    plan = run_curation(
        sources=read_obsidian_notes(args.obsidian_root.expanduser(), classifier=classifier),
        existing_digests=read_wiki_digests(args.wiki_root.expanduser()),
        existing_origins=read_wiki_origins(args.wiki_root.expanduser()),
        state_path=args.state.expanduser(),
        cli_path=cli_path,
        workspace=args.workspace.expanduser(),
        client=client,
        runner=_runner,
        clock=lambda: datetime.now(timezone.utc),
        cap=args.cap,
        emit=args.emit,
    )
    print(f"후보 {len(plan.candidates)}건 · 제안 {plan.emitted}건 · 남은 주간 quota {plan.quota_remaining}")
    for candidate in plan.candidates:
        print(f"  - {candidate.source_ref} (event_date={candidate.event_date or '미상'})")
    if plan.failures:
        print(f"  실패 {len(plan.failures)}건: {', '.join(plan.failures)}", file=sys.stderr)
    return 1 if plan.failures else 0
