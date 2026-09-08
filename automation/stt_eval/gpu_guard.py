"""GPU 유휴 판정과 기존 파이프라인 lock의 수명을 함께 소유한다."""
from __future__ import annotations

import math
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from automation import pipeline_lock

PROBE_TIMEOUT_SECS = 5.0


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """성공 판정은 with 블록을 나갈 때까지 lock을 유지한다."""

    reason: str = ""
    exit_code: Literal[0, 6] = 0
    lease: pipeline_lock.hold | None = None

    def __enter__(self) -> GuardVerdict:
        return self

    def close(self) -> None:
        if self.lease is not None:
            _ = self.lease.__exit__()

    def __exit__(self, *_exception: object) -> None:
        self.close()


def _probe(argv: list[str], env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, env=dict(env), capture_output=True, text=True,
                          check=False, timeout=PROBE_TIMEOUT_SECS)


def check(env: Mapping[str, str]) -> GuardVerdict:
    """숫자 사용률, 전사 프로세스, 비대기 lock 순으로 판정한다."""
    try:
        utilization = _probe(["nvidia-smi", "--query-gpu=utilization.gpu",
                              "--format=csv,noheader,nounits"], env)
        if utilization.returncode != 0 or not utilization.stdout.strip():
            return GuardVerdict("utilization", 6)
        for line in utilization.stdout.splitlines():
            if line.strip() == "[N/A]":
                continue
            value = float(line.strip())
            if not math.isfinite(value) or not 0 <= value < 5:
                return GuardVerdict("utilization", 6)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return GuardVerdict("utilization", 6)
    # -f는 호출자의 명령줄도 맞힐 수 있으므로 자기 자신과 부모를 제외한다.
    excluded = {os.getpid(), os.getppid()}
    try:
        for mode, name in (("-x", "whisper-cli"),
                           ("-f", "sherpa-onnx-offline-speaker-diarization"),
                           ("-f", "bin/stt-engines")):
            found = _probe(["pgrep", mode, name], env)
            if found.returncode not in (0, 1):
                return GuardVerdict("process", 6)
            pids = {int(line) for line in found.stdout.splitlines()}
            if pids - excluded or (found.returncode == 0 and not pids):
                return GuardVerdict("process", 6)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return GuardVerdict("process", 6)
    lease = pipeline_lock.hold(env)
    if not lease.__enter__():
        return GuardVerdict("lock", 6)
    return GuardVerdict(lease=lease)
