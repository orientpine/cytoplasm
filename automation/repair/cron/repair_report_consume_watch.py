"""No-agent cron entry point for the agent-owned repair report consumer."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final


SECRETS_PATH: Final = Path.home() / ".env.secrets"
RUNTIME_ROOT: Final = Path(
    os.environ.get("REPAIR_REPORT_RUNTIME", "~/.hermes/repair-report-runtime")
).expanduser()


def _load_secrets(path: Path = SECRETS_PATH) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        _ = os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    try:
        _load_secrets()
        sys.path.insert(0, str(RUNTIME_ROOT))
        from automation.repair.repair_report_consumer import consume_once

        print(f"repair report consume watch: {consume_once()}")
    except Exception as error:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        print(f"repair report consume watch failed: {error.__class__.__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
