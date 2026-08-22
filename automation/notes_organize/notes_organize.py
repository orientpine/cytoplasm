"""no_agent wrapper for Hermes cron `notes-weekly-organize` (월 08:00 KST).

성공 시 무음(exit 0). 실패 시 자식 stderr 마지막 줄을 마스킹해 한 줄로 남긴다
(mail_digest_watch 선례). 사후 반영 2026-07-21: 07-20 08:00 실패에서 래퍼가
ModuleNotFoundError traceback을 버려 진단이 불가능했던 관측성 결함 수정.
"""
from __future__ import annotations

import os
import re

import subprocess
import sys
from pathlib import Path
from typing import Final

_LIVE_SCRIPTS: Final = "/srv/autophagy-skills/live/report/scripts"
_SCRIPTS = Path(os.environ.get("REPORT_SCRIPTS", _LIVE_SCRIPTS)).expanduser()
CLI = _SCRIPTS / "report_cli.py"
_ENV_SECRETS: Final = Path.home() / ".env.secrets"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")


def _load_env_secrets(path: Path = _ENV_SECRETS) -> None:
    """no-agent cron hands the wrapper no secrets, so the parent loads them itself.

    Measured 2026-08-18 on `budget-watch`: the value was already in ~/.env.secrets and
    the tick still failed as if it were missing, because nothing put it in the
    environment (규약 (b)). The report CLI reaches Obsidian/RAG credentials the same
    way. Inventory check: tests/unit/test_watcher_secret_propagation.py.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _redact(text: str) -> str:
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))


def main() -> int:
    _load_env_secrets()
    if not CLI.exists():
        print("notes-organize error: report skill is not mounted")
        return 1
    try:
        result = subprocess.run(
            [sys.executable, str(CLI), "organize"],
            cwd=Path.home(),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            # 규약 (b-2): the child gets the environment we just self-loaded, explicitly.
            env={**os.environ},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"notes-organize error: {error.__class__.__name__}")
        return 1
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        detail = tail[-1] if tail else ""
        suffix = f": {_redact(detail)[:300]}" if detail else ""
        print(f"notes-organize error rc={result.returncode}{suffix}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
