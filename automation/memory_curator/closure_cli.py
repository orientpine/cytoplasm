"""Dry-run inventory for terminal memory-promotion closure."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from automation.memory_curator_closure_effects import build_surface

from .closure import ClosureRequest, close_terminal_promotions
from .state_store import load_state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memory-curator-closure")
    parser.add_argument("--dry-run", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    state_path = Path(
        os.environ.get(
            "MEMORY_CURATOR_STATE",
            str(Path.home() / ".hermes" / "memory-curator" / "state.json"),
        )
    ).expanduser()
    gate_dir = Path(os.environ.get("WIKI_GATE_DIR", "~/.hermes/wiki-gate")).expanduser()
    result = close_terminal_promotions(
        ClosureRequest(load_state(state_path), gate_dir, build_surface(), args.dry_run)
    )
    for line in result.lines:
        print(line)
    print(
        f"CLOSURE-DRYRUN closable={result.closable} "
        f"unbound={result.unbound} orphans={len(result.orphans)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
