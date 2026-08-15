"""Hermes cron watcher (no_agent, LLM-free) for the memory curator.

Deployed to ``~agent/.hermes/scripts/memory_curator_watch.py`` and registered:

    hermes cron create "every 30m" --name memory-curator-watch \
        --no-agent --script memory_curator_watch.py --deliver local

The package is deployed to ``~agent/.hermes/memory_curator_runtime/``.  Each
tick applies the autonomous, lossless compaction to MEMORY.md/USER.md,
proposes each durable-judgment entry to the decision twin for cha's owner-DM
✅ (idempotent, one live confirm per entry), removes the source only after the
persisted twin binding verifies, and DMs cha on actionable changes.  Empty
stdout = silent tick; only promotion/deletion/alert activity or a fatal error
prints (watchdog pattern, see W1-7 daily-cost-report).

Single-instance flock guard: a long run must not race a second pipeline, so
an overlapping tick exits 0 silently.  The kernel releases the flock when the
holder exits — even on crash.

No-agent cron env (per the watcher-cron 설계규약): this wrapper self-loads
``~/.env.secrets`` (``DISCORD_BOT_TOKEN``) and puts the curator runtime plus the
wiki gate code — from ``AUTOPHAGY_REPO_ROOT/skills/wiki/scripts``, exactly like
``wiki_confirm_reaction_watch.py`` so both use the SAME gate — on ``sys.path``.
The wiki gate reads its own defaults (``WIKI_ROOT=~/wiki``,
``WIKI_GATE_DIR=~/.hermes/wiki-gate``, ``INTEROP_CONFIG``).
"""

from __future__ import annotations

import fcntl
import importlib
import os
import sys
from collections.abc import Callable, Iterable, Sized
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Protocol, runtime_checkable

ENV_SECRETS = Path.home() / ".env.secrets"
RUNTIME_DIR = Path.home() / ".hermes" / "memory_curator_runtime"
MEMORY_DIR = Path.home() / ".hermes" / "memories"
STATE_PATH = Path.home() / ".hermes" / "memory-curator" / "state.json"
GATE_DIR = Path(os.environ.get("WIKI_GATE_DIR", "~/.hermes/wiki-gate")).expanduser()
#: 알림 이력이라 승격 원장(state.json)이 아니라 옆에 둔다.
REMINDER_MARKER = STATE_PATH.parent / "last-approval-reminder"
LOCK_PATH = Path.home() / ".hermes" / "memory-curator" / "watch.lock"


class _CycleResult(Protocol):
    promoted: Sized
    deleted: Sized
    blocked: Sized
    near_cap_kinds: Iterable[str]
    alert_decision: str
    alerted: bool


@runtime_checkable
class _EffectsModule(Protocol):
    post_promotion: Callable[..., _Receipt | None]
    alert_owner: Callable[[str], bool]
    read_twin: Callable[[str], bytes | None]
    draft_present: Callable[[str], bool]
    send_pending_reminder: Callable[..., bool]


class _Receipt(Protocol):
    draft_id: str


@runtime_checkable
class _WatchModule(Protocol):
    run_cycle: Callable[..., _CycleResult]


def _load_env_secrets(path: Path = ENV_SECRETS) -> None:
    """Self-load ~/.env.secrets — a no-agent cron inherits no secrets."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _install_paths() -> None:
    """Curator runtime + the wiki gate code the promote effect reuses (same
    source as the wiki confirm watcher: AUTOPHAGY_REPO_ROOT/skills/wiki/scripts)."""
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        repo = Path(override).expanduser()
    else:
        current = Path("/srv/autophagy-agent-current")  # release runtime (DG-4)
        repo = current if current.exists() else Path("/srv/autophagy-agents")
    wiki_scripts = Path(
        os.environ.get("WIKI_SCRIPTS", str(repo / "skills" / "wiki" / "scripts"))
    ).expanduser()
    for path in (RUNTIME_DIR, wiki_scripts, repo):
        if path.exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


def acquire_single_instance_lock(lock_path: Path = LOCK_PATH) -> IO[str] | None:
    """Non-blocking flock; ``None`` = another tick is still running (skip)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


if __name__ == "__main__":
    _lock = acquire_single_instance_lock()
    if _lock is None:
        sys.exit(0)
    _load_env_secrets()
    _install_paths()
    effects_module = importlib.import_module("memory_curator.effects")
    watch_module = importlib.import_module("memory_curator.watch")
    if not isinstance(effects_module, _EffectsModule) or not isinstance(
        watch_module, _WatchModule
    ):
        print("memory-curator: runtime API mismatch")
        sys.exit(1)

    result = watch_module.run_cycle(
        MEMORY_DIR,
        STATE_PATH,
        promote=effects_module.post_promotion,
        alert=effects_module.alert_owner,
        read_twin=effects_module.read_twin,
        proposal_alive=effects_module.draft_present,
    )
    closure_count = 0
    closure_unbound = 0
    closure_orphans = 0
    closure_failed = False
    try:
        state_module = importlib.import_module("memory_curator.state_store")
        closure_module = importlib.import_module("memory_curator.closure")
        closure_effects = importlib.import_module("automation.memory_curator_closure_effects")
        closure_result = closure_module.close_terminal_promotions(
            closure_module.ClosureRequest(
                state_module.load_state(STATE_PATH),
                GATE_DIR,
                closure_effects.build_surface(),
                False,
            )
        )
        closure_count = closure_result.closable
        closure_unbound = closure_result.unbound
        closure_orphans = len(closure_result.orphans)
    except Exception:  # noqa: BLE001 - 종결은 다음 tick에서 receipt 기반으로 재개한다.
        closure_failed = True
    # 대기 중인 승인을 다시 가리킨다 — 승인 메시지는 건드리지 않고 알림만 보낸다.
    # 소유자가 처리한 것은 전부 보이는 위치였고 안 보이는 것만 남아 있었다(2026-08-03).
    reminded = False
    try:
        state_module = importlib.import_module("memory_curator.state_store")
        reminded = effects_module.send_pending_reminder(
            state_module.load_state(STATE_PATH),
            gate_dir=GATE_DIR,
            marker_path=REMINDER_MARKER,
            now=datetime.now(UTC),
            alert=effects_module.alert_owner,
        )
    except Exception:  # noqa: BLE001 - 리마인더는 부가물이다. 실패가 tick 을 죽이면 안 된다.
        reminded = False

    if (
        result.promoted
        or result.deleted
        or result.alerted
        or reminded
        or closure_count
        or closure_unbound
        or closure_orphans
        or closure_failed
    ):
        near = ",".join(result.near_cap_kinds) or "-"
        print(
            f"memory-curator: promoted={len(result.promoted)} "
            + f"deleted={len(result.deleted)} blocked={len(result.blocked)} "
            + f"alert={result.alert_decision} alerted={result.alerted} near_cap={near} "
            + f"reminded={reminded} closure={closure_count} "
            + f"unbound={closure_unbound} orphans={closure_orphans} "
            + f"closure_failed={closure_failed}"
        )
    sys.exit(0)
