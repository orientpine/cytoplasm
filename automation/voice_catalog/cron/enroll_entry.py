"""워처 실행 경계 — 단일 인스턴스 점유와 비식별 오류만 담당한다(`capture_entry` 와 같은 모양)."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from automation.interop.approval_lease import FileKeyLease
from automation.rag_ingest.config import ConfigError
from automation.voice_catalog.enroll_watch import run_once
from skills.speechtotext.scripts import stt_catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--once", action="store_true")
    _ = parser.parse_args(argv)
    try:
        root = stt_catalog.catalog_root(os.environ)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with FileKeyLease(root / ".lease").hold("watch") as acquired:
            if not acquired:
                return 0
            return run_once(os.environ, home=Path.home())
    except (OSError, ValueError, RuntimeError, ConfigError) as error:
        detail = str(error).split(" ", 1)[0] if str(error).startswith("CATALOG-") else type(error).__name__
        print(f"VOICE-CATALOG-WATCH-FAIL reason={detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
