"""CLI entry point (also used by the Hermes cron ``no_agent`` script).

Exit codes: 0 = ok (including "RAG down, jobs queued" — retried next tick),
1 = fatal (config/auth/protocol error needing operator action).

stdout policy for cron: silent when everything delivered; one short line when
jobs remain queued (RAG node down) so the notice is delivered without spam.
Detailed logs go to ``{state_dir}/logs/ingest-YYYYMMDD.log`` (agent-only).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from .mcp_client import McpFatalError
from .pipeline import run_pipeline

ALL_SOURCES = {"wiki", "notes", "meetings", "conversations", "discord", "obsidian"}


def _write_log(log_dir: Path, lines: list[str]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
    stamp = datetime.now(tz=timezone.utc)
    log_path = log_dir / f"ingest-{stamp.strftime('%Y%m%d')}.log"
    prefix = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    with log_path.open("a", encoding="utf-8") as handle:
        for line in lines:
            _ = handle.write(f"{prefix} {line}\n")
    os.chmod(log_path, 0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rag-ingest")
    parser.add_argument("command", choices=["run"], help="run one ingest pass")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--sources",
        default="wiki,notes,meetings,conversations,discord,obsidian",
        help="comma-separated subset of: wiki,notes,meetings,conversations,discord,obsidian",
    )
    parser.add_argument("--force", action="store_true", help="ignore client-side fingerprints")
    parser.add_argument("--verbose", action="store_true", help="echo log lines to stdout")
    arguments = parser.parse_args(argv)

    sources = {name.strip() for name in arguments.sources.split(",") if name.strip()}
    unknown = sources - ALL_SOURCES
    if unknown:
        print(f"rag-ingest: unknown sources {sorted(unknown)}", file=sys.stderr)
        return 1

    try:
        config = load_config(arguments.config)
        pending, log_lines = run_pipeline(config, sources, force=arguments.force)
    except (ConfigError, McpFatalError) as error:
        print(f"rag-ingest: FATAL {error}", file=sys.stderr)
        return 1

    _write_log(config.log_dir, log_lines)
    if arguments.verbose:
        for line in log_lines:
            print(line)
    elif pending:
        print(f"rag-ingest: RAG unreachable — {pending} job(s) queued, will retry next tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
