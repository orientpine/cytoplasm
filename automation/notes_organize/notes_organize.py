"""no_agent wrapper for Hermes cron `notes-weekly-organize` (월 08:00 KST).

성공 시 무음(exit 0). 실패 시 자식 stderr 마지막 줄을 마스킹해 한 줄로 남긴다
(mail_digest_watch 선례). 사후 반영 2026-07-21: 07-20 08:00 실패에서 래퍼가
ModuleNotFoundError traceback을 버려 진단이 불가능했던 관측성 결함 수정.
"""
from __future__ import annotations

import re

import subprocess
import sys
from pathlib import Path

CLI = Path.home() / ".hermes" / "skills" / "report" / "scripts" / "report_cli.py"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")


def _redact(text: str) -> str:
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))


def main() -> int:
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
