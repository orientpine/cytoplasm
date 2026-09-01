"""Read-only unsigned-head observation and durable incident recording."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from automation.deploy_reconcile import Deliver, reconcile_skip, reconcile_unsigned_head
from automation.deploy_reconcile_state import load_state, save_state
from automation.git_tag_signature import GitRunner, HEAD_REF, OBJECT_ID

Clock = Callable[[], float]

_LS_REMOTE_TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class IncidentRecorder:
    """Persist one reconciler incident transition through the shared state file."""

    state_path: Path
    deliver: Deliver
    clock: Clock = time.time

    def skip(self, reason: str) -> None:
        state = load_state(self.state_path)
        updated = reconcile_skip(
            state,
            reason=reason,
            now=self.clock(),
            deliver=self.deliver,
        )
        save_state(self.state_path, updated)

    def unsigned(
        self, remote_head: str, current_sha: str, commit_count: int | None = None
    ) -> None:
        state = load_state(self.state_path)
        updated = reconcile_unsigned_head(
            state,
            remote_head=remote_head,
            current_sha=current_sha,
            now=self.clock(),
            deliver=self.deliver,
            commit_count=commit_count,
        )
        save_state(self.state_path, updated)


def raw_remote_main_sha(
    mirror: Path,
    update_channel: str | None = None,
    runner: GitRunner = subprocess.run,
) -> str:
    """Observe raw origin/main for owner guidance, never as an install target."""
    remote = update_channel if update_channel is not None else "origin"
    args = ["git", "-C", str(mirror), "ls-remote", remote, HEAD_REF]
    try:
        completed = runner(
            args,
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=_LS_REMOTE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    fields = completed.stdout.split()
    if (
        len(fields) != 2
        or OBJECT_ID.fullmatch(fields[0]) is None
        or fields[1] != HEAD_REF
    ):
        return ""
    return fields[0]


def unreleased_commit_count(
    mirror: Path,
    current_sha: str,
    remote_head: str,
    runner: GitRunner = subprocess.run,
) -> int | None:
    """Count origin commits past the running release; ``None`` when unobservable.

    The mirror is an observation post and may legitimately trail origin — then the
    objects are simply absent and the backlog digest says "수 미상" instead of guessing.
    Read-only by construction: ``rev-list --count`` touches no ref and no worktree.
    """
    if (
        OBJECT_ID.fullmatch(current_sha or "") is None
        or OBJECT_ID.fullmatch(remote_head or "") is None
    ):
        return None
    args = ["git", "-C", str(mirror), "rev-list", "--count", f"{current_sha}..{remote_head}"]
    try:
        completed = runner(
            args,
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=_LS_REMOTE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    text = completed.stdout.strip()
    return int(text) if text.isdigit() else None
