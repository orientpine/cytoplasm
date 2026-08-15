"""Persistence for the reconciler's incident bookkeeping.

MD-2. `ReconcileState` is what makes "exactly one owner notice per incident" true
across ticks and reboots, so this file is read and written on every tick of a timer
whose whole job is to notice that prod is stale.

That inverts the usual priority: **the state file must never be the reason a tick
stops.** A corrupt, truncated or future-versioned file degrades to "nothing is wrong
yet" and the tick proceeds to converge. The cost of that choice is at worst a repeated
notice; the cost of the opposite — aborting on an unparseable bookkeeping file — is a
production runtime that stays behind origin while the thing meant to catch it is dead.
The 2026-07-25 mail-mode precedent is the same shape: runtime state that fails closed
against writing, open against reading.

Location is not a preference. The unit runs with ``ProtectHome=yes``, so ``$HOME`` is
an empty directory at runtime and state written there would silently vanish — the exact
trap the repair push key hit before it moved to ``/srv/autophagy-private`` (0600, ops).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Final

from automation.deploy_reconcile import ReconcileState

DEFAULT_STATE_PATH: Final = Path("/srv/autophagy-private/deploy-reconcile/state.json")


class _Invalid(Exception):
    """A field is present but not the type it claims to be."""


def _require_int(value: object) -> int:
    # bool is an int in Python; a JSON `true` here means the file is not what we think.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Invalid
    return value


def _require_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise _Invalid
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Invalid
    return float(value)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Invalid
    return value


def load_state(path: Path = DEFAULT_STATE_PATH) -> ReconcileState:
    """Read the persisted state, degrading to the default on anything unexpected.

    Unknown keys are ignored on purpose: a newer version writing extra fields must not
    brick an older tick that is mid-rollback.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ReconcileState()
    if not isinstance(raw, dict):
        return ReconcileState()
    try:
        return ReconcileState(
            consecutive_failures=_require_int(raw.get("consecutive_failures", 0)),
            drift_since=_optional_float(raw.get("drift_since")),
            notified_target=_optional_str(raw.get("notified_target")),
            pending_notice=_optional_str(raw.get("pending_notice")),
            incident_open=_require_bool(raw.get("incident_open", False)),
        )
    except _Invalid:
        return ReconcileState()


def save_state(path: Path, state: ReconcileState) -> None:
    """Write the state atomically, owner-only.

    Rename-into-place rather than truncate-and-write: a tick interrupted mid-write would
    otherwise leave a half-file, and the next tick would read it as "nothing is wrong"
    — losing the record of an incident that is still open.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    parent.chmod(0o700)
    payload = json.dumps(
        {
            "consecutive_failures": state.consecutive_failures,
            "drift_since": state.drift_since,
            "notified_target": state.notified_target,
            "pending_notice": state.pending_notice,
            "incident_open": state.incident_open,
        },
        ensure_ascii=False,
        indent=2,
    )
    handle, temporary = tempfile.mkstemp(dir=str(parent), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            _ = stream.write(payload + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
