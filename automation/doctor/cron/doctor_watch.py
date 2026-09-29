"""doctor 알람 워처 — Hermes no-agent cron 이 매시 이 계정의 연결·동작을 점검하고 바뀐 것만 알린다.

no-agent cron 은 os.environ 에 비밀을 넣지 않으므로 `~/.env.secrets` 를 스스로 읽고,
자식에게는 env 를 명시로 넘긴다(watcher-cron 설계규약 (b-2)). 코드는 불변 릴리스 런타임에서 돈다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

ENV_SECRETS: Final = Path.home() / ".env.secrets"
RELEASE_CURRENT: Final = Path("/srv/autophagy-agent-current")
RESIDENT_MIRROR: Final = Path("/srv/autophagy-agents")


def _runtime_root() -> Path:
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return RELEASE_CURRENT if RELEASE_CURRENT.exists() else RESIDENT_MIRROR


def _load_env_secrets(path: Path = ENV_SECRETS) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        key, separator, value = raw_line.strip().partition("=")
        if separator and key and not key.startswith("#") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def run_once(repo_root: Path) -> int:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(repo_root)
    try:
        completed = subprocess.run(  # noqa: S603 - fixed module argv
            [sys.executable, "-m", "automation.doctor", "--notify"],
            cwd=repo_root, env=environment, check=False, timeout=600, capture_output=True, text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"doctor-watch child failed: {type(error).__name__}", file=sys.stderr)
        return 1
    summary = [line for line in completed.stdout.splitlines() if line.startswith(("--- DOCTOR", "DOCTOR-ALARM"))]
    print("\n".join(summary) if summary else f"doctor-watch rc={completed.returncode}")
    if completed.stderr.strip():
        print(completed.stderr.strip()[-400:], file=sys.stderr)
    return 0 if summary else 1


def main(argv: tuple[str, ...] | None = None) -> int:
    arguments = tuple(sys.argv[1:]) if argv is None else argv
    if arguments not in ((), ("--once",)):
        print("usage: doctor_watch.py [--once]", file=sys.stderr)
        return 2
    _load_env_secrets()
    return run_once(_runtime_root())


if __name__ == "__main__":
    raise SystemExit(main())
