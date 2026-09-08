"""로컬 CLI 자식 그룹의 시간 상한과 검증된 평가 산출물을 소유한다."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import TypeAlias

from automation.stt_eval.model import EvalRecordError, load_record
from automation.stt_eval.runner_inputs import Recording

Cli: TypeAlias = Path | Sequence[str]


@dataclass(frozen=True, slots=True)
class Outcome:
    reason: str
    rc: int | None = None
    config_sha256: str | None = None
    artifact: Path | None = None


def _interrupt(_signum: int, _frame: FrameType | None) -> None:
    # 정리 중 반복 신호가 finally를 다시 끊지 못하게 한다.
    for sig in (signal.SIGINT, signal.SIGTERM):
        _ = signal.signal(sig, signal.SIG_IGN)
    raise KeyboardInterrupt


@contextmanager
def cancellation() -> Iterator[None]:
    """주 스레드에서는 종료 신호도 예외로 바꾸어 자식·tmp를 회수한다."""
    previous = {}
    if threading.current_thread() is threading.main_thread():
        previous = {sig: signal.signal(sig, _interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            _ = signal.signal(sig, handler)


def _kill(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        # 이미 끝난 그룹은 회수할 자식이 없다.
        pass
    _ = process.wait()


def execute(cli: Cli, env: Mapping[str, str], work: Path, audio: Path,
            recording: Recording, timeout: float) -> Outcome:
    command = [sys.executable, str(cli)] if isinstance(cli, Path) else list(cli)
    argv = [*command, "transcribe", "--file", str(audio),
            "--label", f"eval-{recording.audio_sha256[:8]}"]
    child = {**env, "SPEECHTOTEXT_BACKEND": "local", "DRIVE_PUBLISH_ENABLED": "0",
             "SPEECHTOTEXT_TRANSCRIPT_DIR": str(work),
             "SPEECHTOTEXT_WINDOW_CACHE": str(work / "windows"), "TMPDIR": str(work)}
    try:
        process = subprocess.Popen(argv, env=child, cwd=work, start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return Outcome("cli-unavailable")
    try:
        rc = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(process)
        return Outcome("timeout", process.returncode)
    except BaseException:
        _kill(process)
        raise
    artifacts = list(work.glob("*.eval.json"))
    if not artifacts:
        return Outcome("no-artifact", rc)
    if len(artifacts) != 1 or artifacts[0].is_symlink():
        return Outcome("invalid-artifact", rc)
    artifact = artifacts[0]
    try:
        record = load_record(artifact)
    except EvalRecordError:
        return Outcome("invalid-artifact", rc)
    if record.audio_sha256 != recording.audio_sha256:
        return Outcome("artifact-mismatch", rc)
    if record.config_sha256 is None:
        return Outcome("invalid-artifact", rc)
    if rc != 0 or record.status == "failed":
        return Outcome("cli-failed", rc, record.config_sha256)
    return Outcome("", rc, record.config_sha256, artifact)
