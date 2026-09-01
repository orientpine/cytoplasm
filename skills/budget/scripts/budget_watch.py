#!/usr/bin/env python3
"""Budget watcher (W4-3) — Hermes cron job, no_agent script mode, every 30 min.

Thin wrapper: runs the governed live budget skill CLI `watch` subcommand (approved
pending drafts are sent through the owner-approval gate, then the balance tab is
snapshotted/diffed). no_agent semantics: success is empty stdout + exit 0;
an expected child failure is RECORDED in the streak and also exits 0 — under
``--deliver discord`` the scheduler posts its own "⚠️ Cron … failed" banner for ANY
non-zero exit regardless of stdout (vendor cron/scheduler.py), so exit 1 is itself
a delivery channel and empty stdout buys no silence (2026-08-24 measured: two
transient ticks, 18:30/20:30 KST, each woke the owner). The streak speaks one
notice when the incident opens and one when it closes; a failure that could NOT
be recorded (helper missing or broken) keeps exit 1 so the scheduler banner stays
as the last line of defence. An exceptional wrapper crash emits one immediate
masked line and exits 1. The 2026-08-23 23:30 Sheets 503 healed on the next tick
and must not wake anybody, but 1.5h of consecutive failures must. Deployed copy lives at ~/.hermes/scripts/budget_watch.py
(Hermes cron sandbox rule); the skill CLI stays the single implementation in the
governed live store — no import of it here, subprocess only
(avoids the W3-2 cron-sandbox PYTHONPATH package-shadowing trap).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

# The streak helper is deployed flat beside this wrapper (~/.hermes/scripts/), so the
# first insert covers the node. In the repo it lives under the mail skill instead — one
# shared copy, no forks (계획 CR-1) — so the second insert covers pytest and a local
# run from a checkout. A partial deploy must not take the watcher down, hence the
# fallback (mail_triage_watch.py precedent).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mail" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import watch_failure_streak
except ImportError:  # pragma: no cover — only reachable on a half-deployed node
    watch_failure_streak = None

WATCH_NAME: Final = "budget-watch"
#: `*/30` — three consecutive failures ≈ 1.5h. The 2026-08-23 23:30 Sheets 503 healed on
#: the next tick and must stay silent; a revoked permission or a deleted tab must not.
FAILURE_NOTICE_THRESHOLD: Final = 3

# 마운트 판정은 governed live 정의 하나(automation/skill_mount.py)에서만 온다. 노드에서
# 이 래퍼는 ~/.hermes/scripts/ 에 평평하게 배포되므로 코드 루트를 값으로 되짚는다 —
# 체크아웃 → 릴리스 current → 미러 (test_skill_runtime_root_fallback.py 와 같은 관용구).
for _root in (
    *Path(__file__).resolve().parents,
    Path(os.environ.get("AUTOPHAGY_RUNTIME_ROOT") or "/srv/autophagy-agent-current"),
    Path("/srv/autophagy-agents"),
):
    if (_root / "automation" / "skill_mount.py").is_file():
        sys.path.insert(0, str(_root))
        break
try:
    from automation.skill_mount import skill_scripts
except ImportError:  # pragma: no cover — 릴리스가 아직 이 정의에 수렴하지 않은 노드
    _SCRIPTS = None  # 판정할 수 없으면 마운트 없음으로 fail-closed 한다
else:
    _SCRIPTS = skill_scripts("budget", env_var="BUDGET_SCRIPTS")
_ENV_SECRETS: Final = Path.home() / ".env.secrets"
CLI = None if _SCRIPTS is None else _SCRIPTS / "budget_cli.py"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")


def _redact(text: str) -> str:
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))


def _load_env_secrets(path: Path = _ENV_SECRETS) -> None:
    """no-agent cron gets no secrets in os.environ, so the parent loads them itself.

    Measured 2026-08-18: with `BUDGET_SHEET_ID` present in ~/.env.secrets the tick
    still died on `GATE-REFUSED BUDGET_SHEET_ID가 없습니다 (fail-closed)`, while the
    same wrapper run after `set -a; . ~/.env.secrets` exited 0 — the configuration
    was fine and simply never reached the child. Same shape as
    todo_confirm_reaction_watch._load_env_secrets.

    budget was only the watcher whose missing configuration happened to be *visible*
    (a sheet id the gate names out loud). Since 2026-08-20 the same contract is checked
    across every watcher a deploy script pushes to ~/.hermes/scripts/, by
    tests/unit/test_watcher_secret_propagation.py — five more were failing it.
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


def _announce(*, ok: bool, detail: str = "") -> bool:
    """Speak only when a failure streak opens or closes — see watch_failure_streak.

    Returns True when the streak RECORDED the tick: only then may a failing tick
    exit 0 (silence must be earned by a recorded failure, never by breakage).
    A broken notice path (unwritable state root, half-deployed helper, closed
    stdout) must never change a successful tick's verdict — same rule as
    budget_cli's ``NOTIFY-FAIL``.
    """
    try:
        if watch_failure_streak is None:
            if not ok:
                print(f"{WATCH_NAME} error: {detail}"[:300])
            return False
        notice = watch_failure_streak.record(
            WATCH_NAME, ok=ok, detail=detail, threshold=FAILURE_NOTICE_THRESHOLD
        )
    except Exception:  # noqa: BLE001 — notification must never break the tick
        return False
    try:
        if notice is not None:
            print(_redact(notice)[:300])
    except Exception:  # noqa: BLE001 — a closed sink loses the line, not the record
        pass
    return True


def main() -> int:
    _load_env_secrets()
    if CLI is None or not CLI.exists():
        return 0 if _announce(ok=False, detail="budget skill is not mounted") else 1
    result = subprocess.run(  # noqa: S603 — fixed argv, agent-owned script
        [sys.executable, str(CLI), "watch"],
        capture_output=True, text=True, timeout=600, check=False,
        cwd=str(Path.home()),
        # Rule (b-2): state the child's environment explicitly rather than letting
        # it fall back — the credentials only exist because we just self-loaded them.
        env={**os.environ},
    )
    if result.returncode == 0:
        _announce(ok=True)  # silent tick unless it closes an open incident
        return 0  # stdout of the child intentionally dropped
    tail = (result.stderr or result.stdout).strip().splitlines()
    detail = tail[-1] if tail else f"rc={result.returncode}"
    recorded = _announce(ok=False, detail=f"rc={result.returncode}: {_redact(detail)[:200]}")
    return 0 if recorded else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — cron crash path: immediate masked line
        try:
            print(f"budget-watch error: {_redact(str(error))}"[:300])
        except BrokenPipeError:
            pass
        sys.exit(1)
