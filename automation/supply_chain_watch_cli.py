"""The watcher's node wiring: assemble the tick from the gate's own identity, then run it.

FA-3. Everything this decides is already assembled and tested elsewhere; the job here is
to hand `watch_tick` the three things it cannot invent — how to ask the gate what the owner
said, how to actually run a resume, and how to tell an approval that already mounted from
one that never did — and to do that without introducing a second source of truth for any.

The identity is the gate's. ``skill_gate._identity()`` is the canonical factory and the
directory it returns is the only resolver of a surface, so this module never builds one,
never reads a token, and never names a channel. It borrows.

What it does own is the subprocess boundary, and the one rule there is that the exit code
survives verbatim: the outcome table distinguishes lease contention from an owner
cancellation by number alone, and swallowing or remapping that number would erase the
distinction the last fix existed to create. A command that cannot even start reports 127
rather than raising, because a tick that dies mid-directory starves every approval behind
it — silently, which is the failure this whole feature removes.

Deployment note the unit file repeats: this must NOT run with ``ProtectHome=yes``. The
records, the interop config and the bot token all live under the record owner's ``$HOME``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation import skill_gate
from automation.skill_gate_approval import SkillApprovalGate
from automation.skill_store import STORE_ROOT
from automation.supply_chain_plan import PendingRequest
from automation.supply_chain_reconcile import Reconciled, reconcile
from automation.supply_chain_watch import (
    FailureAttempt,
    TickResult,
    retry_due,
    update_failure,
    watch_tick,
)

#: The account whose ``$HOME`` holds the gate records; the unit runs as it.
RECORD_OWNER: Final = "agent"

#: Nothing started. Distinct from any code the pipeline itself returns, so the outcome
#: table reads it as a plain failure rather than as contention or a cancellation.
COMMAND_UNAVAILABLE: Final = 127

_TIMEOUT: Final = 3600.0
_STATE_DEFAULT: Final = "~/.hermes/supply-chain-watch/tick.json"


def state_path() -> Path:
    return Path(os.environ.get("SUPPLY_CHAIN_WATCH_STATE", _STATE_DEFAULT)).expanduser()


def write_tick_summary(
    path: Path,
    results: tuple[TickResult, ...],
    *,
    release_sha: str,
    timestamp: str,
    failures: Mapping[str, FailureAttempt] | None = None,
) -> None:
    payload = {
        "release_sha": release_sha,
        "results": [
            {"key": result.request.key, "outcome": result.outcome, "reason": result.reason}
            for result in results
        ],
        "timestamp": timestamp,
        "version": 2,
        "failures": {
            key: {
                "attempts": failure.attempts,
                "fingerprint": failure.fingerprint,
                "next_attempt_at": failure.next_attempt_at,
            }
            for key, failure in sorted((failures or {}).items())
        },
    }
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        _ = json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        _ = handle.write("\n")
        temporary = Path(handle.name)
    temporary.chmod(0o600)
    os.replace(temporary, path)


def load_failures(path: Path) -> dict[str, FailureAttempt]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    rows = decoded.get("failures") if isinstance(decoded, dict) else None
    if not isinstance(rows, dict):
        return {}
    failures: dict[str, FailureAttempt] = {}
    for key, row in rows.items():
        if not isinstance(key, str) or not isinstance(row, dict):
            continue
        try:
            failures[key] = FailureAttempt(
                str(row["fingerprint"]), int(row["attempts"]), float(row["next_attempt_at"])
            )
        except (KeyError, TypeError, ValueError):
            continue
    return failures


def run_command(command: tuple[str, ...]) -> int:
    """Run a resume and return its exit code, unchanged.

    Unchanged is the whole contract: 8 and 9 mean opposite things to the caller, and a
    remapped or swallowed code collapses them.
    """
    try:
        return subprocess.run(command, check=False, timeout=_TIMEOUT).returncode
    except (OSError, subprocess.SubprocessError):
        return COMMAND_UNAVAILABLE


def resume_helper() -> Path:
    """The one command this account may escalate.

    Deliberately NOT resolved from the runtime root: the helper is the privileged
    boundary and lives outside the tree it launches, so a release cannot nominate its
    own escalation path. It is installed from the repo by provision-supply-chain-watch.
    """
    return Path("/usr/local/libexec/autophagy-resume-deploy")


def gate_for(skill: str, digest: str) -> SkillApprovalGate:
    """The gate for one skill at one digest — the deploy path's own factory, borrowed.

    Assembling a second one here would be a second copy of the spec that decides what the
    owner's ✅ authorized, and the second copy is always the one that rots when the first
    changes. The digest is supplied by the caller because a retirement is a compare-and-
    swap against the digest the RECORD stores, not against whatever is live right now.
    """
    return skill_gate._deploy_gate(  # noqa: SLF001 - the gate owns this, deliberately
        argparse.Namespace(skill=skill, hash=digest)
    )


def reconcile_request(request: PendingRequest) -> Reconciled:
    """Whether this approval's mount already happened, measured against the real store."""
    return reconcile(
        request,
        gate_dir=skill_gate.GATE_DIR,
        store_root=STORE_ROOT,
        gate_for=gate_for,
    )


def main() -> int:
    identity = skill_gate._identity()  # noqa: SLF001 - the gate owns this, deliberately
    owner_id = skill_gate._owner_id()  # noqa: SLF001
    channel_id = identity.directory().skill_approvals()

    def decision_of(message_id: str) -> str:
        args = argparse.Namespace(message_id=message_id)
        return skill_gate._owner_decision(args, owner_id, channel_id)  # noqa: SLF001

    path = state_path()
    failures = load_failures(path)
    now = time.time()

    origin_sha = Path(".origin-sha")
    release_sha = origin_sha.read_text(encoding="utf-8").strip() if origin_sha.is_file() else "unknown"

    def eligible(request: PendingRequest) -> bool:
        return retry_due(failures.get(request.key), release_sha=release_sha, now=now)

    tick = watch_tick(
        skill_gate.GATE_DIR,
        resume_helper(),
        decide=decision_of,
        run=run_command,
        reconcile=reconcile_request,
        eligible=eligible,
    )
    results = tick.requests
    next_failures = dict(failures)
    if tick.succeeded:
        live_keys = {result.request.key for result in results}
        for stale_key in next_failures.keys() - live_keys:
            next_failures.pop(stale_key)
    alerted = False
    for result in results:
        if result.outcome == "backoff":
            # Say it every tick. Silence in the journal reads as "the request is gone",
            # not as "the request is waiting" (2026-08-04: eight ticks, zero mentions).
            # The `continue` is load-bearing: falling through reaches the `else` below,
            # whose pop() would drop the suppression record and turn every tick into a
            # fresh retry. `alerted` stays untouched — waiting is not a new alert, so the
            # tick still exits 0 and systemd does not mark a waiting request as a failure.
            waiting = failures.get(result.request.key)
            attempts = waiting.attempts if waiting else 0
            remaining = max(0, int(waiting.next_attempt_at - now)) if waiting else 0
            print(
                f"[supply-chain-watch] {result.request.key} backoff "
                f"(attempt {attempts}, retry in {remaining}s)",
                file=sys.stderr,
            )
            continue
        if result.outcome in {"failed", "retry"}:
            decision = update_failure(
                failures.get(result.request.key), result, release_sha=release_sha, now=now
            )
            next_failures[result.request.key] = decision.state
            if not decision.alert:
                continue
            alerted = True
        else:
            next_failures.pop(result.request.key, None)
        print(
            f"[supply-chain-watch] {result.request.key} {result.outcome} ({result.reason})",
            file=sys.stderr,
        )
    write_tick_summary(
        path,
        results,
        release_sha=release_sha,
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        failures=next_failures,
    )
    return 1 if alerted else 0


if __name__ == "__main__":
    raise SystemExit(main())
