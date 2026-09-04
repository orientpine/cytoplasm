#!/usr/bin/env python3
"""No-agent cron: discover Plaud recordings and drive owner-gated lifelog notes.

Watcher contract: only Discord REACTIONS are polled (규약 a) — the Plaud cloud
poll is a proposal source like the relocate classifier, and every proposal still
needs cha's ✅ before a byte is pushed. `~/.env.secrets` is self-loaded (b), the
repo import goes through the runtime-root resolver (c), the deployed file name
is skill-unique (e), and records advance only after the effect succeeded (f).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from collections.abc import Sequence
from typing import IO, Final, Protocol, TypeAlias
from zoneinfo import ZoneInfo


def _runtime_root() -> Path:
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    current = Path("/srv/autophagy-agent-current")
    return current if current.exists() else Path("/srv/autophagy-agents")


_REPO_ROOT = _runtime_root()
if (_REPO_ROOT / "automation").is_dir() and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from automation.interop.approval_surface import POLICY_VERSION  # noqa: E402
from automation.plaud_sync.lifelog_extract_live import build_extractor  # noqa: E402
from automation.plaud_sync.lifelog_fields import note_timezone  # noqa: E402
from automation.plaud_sync.model import PlaudSyncState  # noqa: E402
from automation.plaud_sync.store import (  # noqa: E402
    PlaudSyncStore,
    load_state,
    save_note_body,
    save_state,
)
from automation.plaud_sync.sync import plan_new_records, poll_due  # noqa: E402
from automation.plaud_sync import transcribe_live  # noqa: E402
from automation.plaud_sync.watch_step import ResolveResult, resolve_tick  # noqa: E402

ENV_SECRETS: Final = Path.home() / ".env.secrets"
INTEROP_CONFIG: Final = Path.home() / ".hermes" / "interop" / "config.json"
STATE_DIR: Final = Path.home() / ".hermes" / "plaud-sync"
STATE_PATH: Final = STATE_DIR / "state.json"
LOCK_PATH: Final = STATE_DIR / "watch.lock"
JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)

_LONG_DIGITS = re.compile(r"\d{5,}")
_SECRET_VALUE = re.compile(r"(?i)(token|secret|password|key)=[^\s]+")


class JsonLoader(Protocol):
    def __call__(self, s: str) -> JsonValue: ...


_JSON_LOADS: JsonLoader = json.loads


class WatchError(RuntimeError):
    """Node configuration is insufficient for a fail-closed sync tick."""


def _load_env_secrets(path: Path = ENV_SECRETS) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        key, separator, value = raw_line.strip().partition("=")
        if separator and key and not key.startswith("#") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _owner_id(path: Path = INTEROP_CONFIG) -> str:
    try:
        payload = _JSON_LOADS(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WatchError("interop owner configuration is unavailable") from error
    if not isinstance(payload, dict):
        raise WatchError("interop owner configuration is malformed")
    owner_id = payload.get("owner_id")
    if not isinstance(owner_id, str) or not owner_id:
        raise WatchError("interop owner configuration has no owner id")
    return owner_id


def acquire_single_instance_lock(lock_path: Path = LOCK_PATH) -> IO[str] | None:
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def _note_timezone() -> ZoneInfo:
    """PLAUD_SYNC_TIMEZONE (default Asia/Seoul) — a bad name falls back loudly, not silently."""
    zone, warning = note_timezone(os.environ)
    if warning:
        print(f"plaud-sync: {warning}", file=sys.stderr)
    return zone


def _discover(state: PlaudSyncState, now: datetime) -> PlaudSyncState:
    """Fetch new recordings when the poll is due; a bad tick must not block resolve."""
    if not poll_due(state, now, _env_int("PLAUD_SYNC_POLL_SECONDS", 1800)):
        return state
    try:
        from automation.plaud_sync.fetch import fetch_recordings
        from automation.plaud_sync.mcp_client import PlaudMcpClient

        lookback = _env_int("PLAUD_SYNC_LOOKBACK_DAYS", 14)
        date_from = (now - timedelta(days=lookback)).date().isoformat()
        with PlaudMcpClient() as client:
            recordings = fetch_recordings(client, date_from=date_from)
        result = plan_new_records(
            state,
            recordings,
            now=now,
            policy_version=POLICY_VERSION,
            extractor=build_extractor(os.environ, repo_root=_REPO_ROOT),
            tz=_note_timezone(),
            initial_status="transcribing" if transcribe_live.enabled(os.environ) else "planned",
        )
        for recording_id, body in result.bodies.items():
            save_note_body(STATE_DIR, recording_id, body)
        for recording_id in result.skipped:
            print(
                f"plaud-sync: unplannable recording skipped: {recording_id}",
                file=sys.stderr,
            )
        for recording_id in result.deferred:
            print(
                f"plaud-sync: field extraction failed; retry next poll: {recording_id}",
                file=sys.stderr,
            )
    except Exception as error:  # noqa: BLE001 - discovery is best-effort; resolve must still run
        print(f"plaud-sync discovery error: {_masked_error(error)}", file=sys.stderr)
        return state
    return result.state


def _merge_effect_bindings(
    before: PlaudSyncState,
    result: ResolveResult,
    persisted: PlaudSyncState,
) -> ResolveResult:
    """Adopt bindings the store committed mid-tick so the final save keeps them."""
    records = dict(result.state.records)
    for key, resolved in records.items():
        initial = before.records.get(key)
        current = persisted.records.get(key)
        if initial is None or current is None:
            continue
        if (
            initial.action_hash != current.action_hash
            or resolved.action_hash != current.action_hash
        ):
            continue
        merged = resolved
        if (current.message_id, current.channel_id) != (
            initial.message_id,
            initial.channel_id,
        ):
            merged = replace(
                merged, message_id=current.message_id, channel_id=current.channel_id
            )
        if current.approval_thread_id != initial.approval_thread_id:
            merged = replace(merged, approval_thread_id=current.approval_thread_id)
        if current.last_block_reason != initial.last_block_reason:
            merged = replace(merged, last_block_reason=current.last_block_reason)
        records[key] = merged
    return replace(
        result,
        state=PlaudSyncState(result.state.version, result.state.last_poll_at, records),
    )


def run_once(now: datetime) -> ResolveResult:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        raise WatchError("Discord token is unavailable")
    from automation.plaud_sync.effects_live import build_effects

    state = _discover(load_state(STATE_PATH), now)
    save_state(STATE_PATH, state)
    result = resolve_tick(
        state,
        effects=build_effects(
            state_path=STATE_PATH, token=token, owner_id=_owner_id(), now=now
        ),
        max_posts=_env_int("PLAUD_SYNC_MAX_POSTS", 3),
    )
    result = _merge_effect_bindings(state, result, load_state(STATE_PATH))
    save_state(STATE_PATH, result.state)
    return result


def _transcribe() -> transcribe_live.StepSummary | None:
    try:
        return transcribe_live.run_transcribe_step(
            state_dir=STATE_DIR, lock_path=LOCK_PATH, env=os.environ
        )
    except Exception as error:  # noqa: BLE001 - best-effort like discovery; this tick's approvals already ran
        print(f"plaud-sync transcribe error: {_masked_error(error)}", file=sys.stderr)
        return None


def _summary(result: ResolveResult) -> str | None:
    if not (result.posted or result.written or result.abandoned):
        return None
    return (
        f"plaud-sync: posted={len(result.posted)} "
        f"written={len(result.written)} abandoned={len(result.abandoned)}"
    )


def _masked_error(error: Exception) -> str:
    secret_safe = _SECRET_VALUE.sub(r"\1=[MASKED]", str(error))
    return _LONG_DIGITS.sub("[MASKED-NUM]", secret_safe)[:300]


def _repost_posted() -> tuple[str, ...]:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        raise WatchError("Discord token is unavailable")
    from automation.plaud_sync.reaction_transport import DiscordTransport
    from automation.plaud_sync.repost import repost_posted

    return repost_posted(PlaudSyncStore(STATE_PATH), DiscordTransport(token, _owner_id()))


def _run(argv: Sequence[str]) -> list[str]:
    """One tick; ``--repost-posted`` consumes reactions first, re-cards, then posts."""
    unknown = sorted(set(argv) - {"--repost-posted"})
    if unknown:
        raise WatchError(f"unknown argument: {unknown}")
    lines = [_summary(run_once(datetime.now(UTC)))]
    if "--repost-posted" in argv:
        lines.append(f"plaud-sync: reposted={len(_repost_posted())}")
        lines.append(_summary(run_once(datetime.now(UTC))))
    return [line for line in lines if line is not None]


def main(argv: Sequence[str] | None = None) -> int:
    try:
        _load_env_secrets()
        lock = acquire_single_instance_lock(LOCK_PATH)
        if lock is None:
            return 0
        with lock:
            lines = _run(sys.argv[1:] if argv is None else argv)
        # watch.lock is released here on purpose: local transcription runs for tens of
        # minutes and must not hold the next tick's ✅ hostage (transcribe_live docstring).
        step = _transcribe()
        if step is not None:
            lines.extend(filter(None, [step.line]))
            if step.promoted and (lock := acquire_single_instance_lock(LOCK_PATH)) is not None:
                with lock:
                    lines.extend(filter(None, [_summary(run_once(datetime.now(UTC)))]))
        for line in lines:
            print(line)
    except Exception as error:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK - final cron alert boundary
        print(f"plaud-sync-watch error: {_masked_error(error)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
