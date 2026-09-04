"""Shared mirror probing and safe fast-forward behavior for the reconciler.

관측 미러의 판정과 이동을 CLI 배선에서 분리해, 통지용 상태 번역과 실제 ff-pull 이
서로 다른 안전 규칙을 만들지 않게 한다.
"""
from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeAlias

from automation.deploy_update_channel import with_update_channel

Run: TypeAlias = Callable[[Sequence[str], float], subprocess.CompletedProcess[str] | None]
MirrorVerdict: TypeAlias = Callable[[Path, Path, str | None], str]
CurrentReleaseSha: TypeAlias = Callable[[Path], str]

_MIRROR_STATES = {
    "mirror-dirty": "dirty",
    "mirror-ahead": "ahead",
    "mirror-behind": "behind",
    "mirror-clean": "clean",
}


def probe_mirror_verdict(
    mirror: Path,
    probe: Path,
    *,
    update_channel: str | None,
    run: Run,
    timeout: float,
) -> str:
    """공유 shell probe 결과만 써서 다른 경로와 미러 판단이 갈라지지 않게 한다."""
    source, target = shlex.quote(str(probe)), shlex.quote(str(mirror))
    completed = run(
        with_update_channel(
            ("bash", "-c", f"source {source} && checkout_mirror_verdict {target}"),
            update_channel,
        ),
        timeout,
    )
    return "" if completed is None else completed.stdout.strip()


def mirror_state_from_verdict(verdict: str) -> str:
    """관측 실패는 사고 시계를 흔들지 않는 unknown으로만 낮춘다."""
    return _MIRROR_STATES.get(verdict, "unknown")


def sync_mirror(
    origin_sha: str,
    *,
    mirror: Path,
    pointer: Path,
    probe: Path,
    update_channel: str | None,
    current_release_sha: CurrentReleaseSha,
    run: Run,
    mirror_verdict: MirrorVerdict,
    behind: str,
    in_sync: str,
    pulled: str,
    pull_failed: str,
    prod_stale: str,
    git_timeout: float,
    ff_pull_timeout: float,
) -> str:
    """미러는 안전한 behind일 때만, prod가 도달한 뒤에만 fast-forward한다.

    dirty/ahead 작업은 미러 밖에 없을 수 있고, prod가 뒤처진 동안의 behind는 장애 증거다.
    어느 경우도 타이머가 지우거나 앞질러서는 안 된다.
    """
    if current_release_sha(pointer) != origin_sha:
        return f"{prod_stale}: prod has not reached origin/main yet"
    head = run(("git", "-C", str(mirror), "rev-parse", "HEAD"), git_timeout)
    if head is not None and head.returncode == 0 and head.stdout.strip() == origin_sha:
        return in_sync
    verdict = mirror_verdict(mirror, probe, update_channel)
    if verdict != behind:
        return f"untouched: {verdict or 'verdict unavailable'}"
    pulled_result = run(
        with_update_channel(
            ("git", "-C", str(mirror), "pull", "--ff-only"),
            update_channel,
        ),
        ff_pull_timeout,
    )
    if pulled_result is None or pulled_result.returncode != 0:
        return pull_failed
    return pulled
