"""Consecutive-failure escalation for no-agent cron watchers — one notice per incident.

WHY (2026-08-18): `mail-triage-watch` failed 111 ticks in a row and calendar/coordination
222 each, and cha learned about none of them — those jobs run `--deliver local`, which has
zero delivery targets, so the failure line went nowhere. The obvious fix (`--deliver
discord`) is worse for a high-frequency job: a stuck `*/2` watcher would send 720 DMs a day.

So the delivery target is not the knob. The knob is *how often a watcher speaks*: stay
silent while healthy, speak exactly once when a failure streak reaches the threshold, and
speak exactly once more when it recovers. That is the shape `automation/deploy_reconcile.py`
already uses (`FAILURE_NOTICE_THRESHOLD` + `incident_open`), reused here rather than
reinvented — with the state kept per watcher name so two watchers never share an incident.

Deployed alongside the watchers in ``~/.hermes/scripts/`` and imported from there; it is a
helper, not a cron entrypoint (no ``__main__`` guard), so the deploy-coverage probe skips it.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: A `*/10` watcher reaches this in under an hour — long enough to ride out a transient
#: node hiccup, short enough that the owner hears about a real outage the same morning.
DEFAULT_THRESHOLD: Final = 5

#: Resolved per call, and overridable through ``WATCH_FAILURE_ROOT``, so a test never
#: writes into the live owner state and a second account never shares the first's.
STATE_ROOT_ENV: Final = "WATCH_FAILURE_ROOT"

#: Returned when a state transition could not be saved, so a watcher can keep its
#: failure visible instead of silently treating the tick as recorded.
PERSISTENCE_FAILURE: Final = "failure streak state was not persisted"


def default_root() -> Path:
    override = os.environ.get(STATE_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hermes" / "watch-failure"


@dataclass(frozen=True, slots=True)
class Streak:
    """What the store remembers between ticks."""

    consecutive_failures: int = 0
    incident_open: bool = False


def state_path(name: str, root: Path | None = None) -> Path:
    return (root or default_root()) / f"{name}.json"


def load(name: str, root: Path | None = None) -> Streak:
    """Read the streak; unreadable or corrupt state restarts at zero rather than crashing."""
    try:
        raw = json.loads(state_path(name, root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Streak()
    if not isinstance(raw, dict):
        return Streak()
    failures = raw.get("consecutive_failures")
    return Streak(
        consecutive_failures=failures if isinstance(failures, int) and failures >= 0 else 0,
        incident_open=bool(raw.get("incident_open", False)),
    )


def store(name: str, streak: Streak, root: Path | None = None) -> bool:
    """Persist the streak 0600 under a private directory, returning whether it succeeded."""
    path = state_path(name, root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(
            json.dumps(
                {
                    "consecutive_failures": streak.consecutive_failures,
                    "incident_open": streak.incident_open,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(path, 0o600)
    except OSError:
        return False
    return True


def record(
    name: str,
    *,
    ok: bool,
    detail: str = "",
    threshold: int = DEFAULT_THRESHOLD,
    root: Path | None = None,
) -> str | None:
    """Advance the streak and return a notice, silence, or a persistence-failure marker.

    A successful tick after an open incident closes it and returns the recovery line; a
    failing tick returns the escalation line only on the tick that reaches ``threshold``.
    Every other tick returns ``None``, which is what keeps a stuck `*/2` job from filling
    the owner's DMs while still letting the job run under ``--deliver discord``. A failed
    write returns ``PERSISTENCE_FAILURE`` so callers do not mistake an unrecorded failure
    for a recorded silent tick.
    """
    previous = load(name, root)
    if ok:
        if previous == Streak():
            return None  # healthy steady state writes nothing — a `*/2` job touches no disk
        if not store(name, Streak(), root):
            return PERSISTENCE_FAILURE
        if not previous.incident_open:
            return None
        return f"{name} recovered after {previous.consecutive_failures} consecutive failures"

    failures = previous.consecutive_failures + 1
    reached = failures >= threshold and not previous.incident_open
    if not store(
        name, Streak(consecutive_failures=failures, incident_open=previous.incident_open or reached), root
    ):
        return PERSISTENCE_FAILURE
    if not reached:
        return None
    suffix = f": {detail}" if detail else ""
    return f"{name} failed {failures} ticks in a row{suffix}"
