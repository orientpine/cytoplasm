"""FileKeyLease must leave a frozen refusal as that refusal, then drop the flock."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from automation.interop.approval_lease import FileKeyLease, slug


@dataclass(frozen=True, slots=True)
class Boom(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


def _peer_can_lock(path: Path) -> bool:
    """True when another process can take LOCK_EX|LOCK_NB on ``path``."""
    script = (
        "import fcntl, sys\n"
        f"handle = open({str(path)!r}, 'a')\n"
        "try:\n"
        "    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except OSError:\n"
        "    raise SystemExit(2)\n"
        "raise SystemExit(0)\n"
    )
    completed = subprocess.run((sys.executable, "-c", script), check=False, timeout=10)
    return completed.returncode == 0


def test_hold_when_frozen_slots_exception_is_raised_then_propagates_and_releases(
    tmp_path: Path,
) -> None:
    # Given: a real per-key flock, the same kind producers and watchers share.
    lease = FileKeyLease(tmp_path)
    key = "drive:project/a"
    path = tmp_path / f"{slug(key)}.lease"

    # When: a frozen slots exception is raised inside the owned span.
    with pytest.raises(Boom, match="x"):
        with lease.hold(key) as owned:
            assert owned is True
            assert _peer_can_lock(path) is False
            raise Boom("x")

    # Then: callers see that exception, not TypeError, and the next process can lock.
    assert _peer_can_lock(path) is True
