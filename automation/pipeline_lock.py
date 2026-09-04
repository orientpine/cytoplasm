"""녹음 → 전사본 → 회의록 파이프라인의 **단일 lock**.

두 no-agent cron 이 이 파이프라인을 만진다 — `speechtotext_drive_watch` 는 녹음을 전사해
전사본을 발행하고 곧바로 meeting 자식을 부르고, `meeting_pending_transcript_watch` 는
회의록이 없는 전사본을 줍는다. 전사본 발행과 회의록 생성 사이에는 회의록 생성 시간만큼
**전사본은 있고 회의록은 없는** 창이 열리는데, 그 창이 정확히 야간 워처의 판정 조건이다.
그래서 두 워처는 다른 자원이 아니라 **같은 자원**을 만진다 — lock 도 하나여야 한다.

lock 이 어느 스킬 디렉터리에도 없는 이유가 그것이다. 파이프라인은 둘 중 누구의 것도 아니다.

겹치면 나중에 온 쪽이 **양보**한다(잡히지 않으면 그대로 종료). 양보는 실패가 아니므로 rc 0
이고, 다음 틱이 이어받는다 — speechtotext 는 5분 뒤, meeting 은 다음 밤이다.

plaud_sync 의 로컬 전사 스텝(`plaud_sync.transcribe_live.run_transcribe_step`)도 같은 lock 을
잡는다(2026-09-04). 전사본·회의록 파일은 만지지 않지만 whisper.cpp·sherpa 라는 **같은 노드
자원**을 쓴다 — 두 워처가 동시에 전사하면 둘 다 느려지고 메모리를 다툰다. 못 잡으면 busy 한
줄을 내고 다음 틱(10분)에 다시 온다.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final, TextIO

LOCK_NAME: Final = "transcript-pipeline.lock"
STATE_ROOT_ENV: Final = "HERMES_STATE_ROOT"


def lock_path(env: Mapping[str, str] | None = None) -> Path:
    """The one file both watchers contend on."""
    environment = os.environ if env is None else env
    root = environment.get(STATE_ROOT_ENV, "").strip()
    if root:
        return Path(root).expanduser() / LOCK_NAME
    return Path(environment.get("HOME", "/tmp")).expanduser() / ".hermes" / LOCK_NAME


class hold:
    """Context manager yielding whether this process may touch the pipeline.

    A watcher that cannot even open the lock file must not proceed on the assumption that
    nobody else is running — an unreadable lock is indistinguishable from a held one, and
    guessing wrong duplicates a meeting's minutes.
    """

    __slots__: tuple[str, ...] = ("_env", "_handle")

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env: Mapping[str, str] | None = env
        self._handle: TextIO | None = None

    def __enter__(self) -> bool:
        path = lock_path(self._env)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("w", encoding="utf-8")
        except OSError:
            return False
        self._handle = handle
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            self._handle = None
            return False
        return True

    def __exit__(self, *_exception: object) -> bool:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        return False
