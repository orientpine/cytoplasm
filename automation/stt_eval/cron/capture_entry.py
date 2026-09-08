"""수집 전용 실행 경계: 단일 인스턴스 점유와 비식별 오류만 담당한다."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import cast

from automation.interop.approval_lease import FileKeyLease
from automation.plaud_sync.audio_manifest import manifest_path, outside_checkout
from automation.rag_ingest.config import ConfigError

from automation.stt_eval.cron.capture import run_once


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--once", action="store_true")
    _ = parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    try:
        _ = manifest_path(os.environ)
        home = Path.home()
        lease = FileKeyLease(outside_checkout(home / ".hermes/stt-eval-capture"))
        with lease.hold("watch") as acquired:
            if not acquired:
                return 0
            return run_once(os.environ, verbose=cast(bool, args.verbose))
    except (OSError, ValueError, RuntimeError, ConfigError) as error:
        signal = "STT-EVAL-ROOT-REFUSED" if str(error) == "STT-EVAL-ROOT-REFUSED" else f"STT-EVAL-CAPTURE-FAIL reason={type(error).__name__}"
        print(signal, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
