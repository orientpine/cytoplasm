"""녹음 → 전사본 → 회의록 파이프라인의 상호배제 계약.

두 no-agent cron 이 같은 파이프라인을 만진다. `speechtotext-drive-watch`(`*/5`)는 녹음을
전사해 전사본을 Drive 에 발행하고 곧바로 meeting 자식을 부르며,
`meeting-pending-transcript-watch`(`0 0`)는 회의록이 없는 전사본을 줍는다. 전사본 발행과
회의록 생성 사이에는 회의록 생성 시간만큼(실측 258.9초) **전사본은 있고 회의록은 없는** 창이
열리고, 그 창이 정확히 야간 워처의 판정 조건이다. `*/5` 는 `:00` 에도 돌므로 자정에 둘이
동시에 시작한다.

겹치면 같은 전사본으로 회의록이 두 개 만들어지고 원장에 같은 일이 두 번호로 들어간다 —
원장의 멱등 가드는 `opened_note` 가 같을 때만 걸리는데 그때는 노트 이름이 다르다.

**flock 은 프로세스 단위라 같은 프로세스에서 두 번 잡으면 둘 다 성공한다.** 그래서 이 파일은
lock 을 별도 프로세스가 쥔 채 워처를 실제로 실행한다 — 같은 프로세스 안에서 확인하면 통과해도
아무것도 증명하지 못한다.

경로가 두 워처에서 우연히 같은 문자열인 것과 정의가 하나인 것은 다르다(선례:
`test_skill_mount_definition.py`). 그래서 문자열을 비교하는 대신 **주입한 경로를 양쪽이
모두 따르는지**를 본다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

REPO: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from automation import pipeline_lock  # noqa: E402

_WATCHERS: Final = {
    "meeting": REPO / "skills/meeting/scripts/meeting_pending_transcript_watch.py",
    "speechtotext": REPO / "skills/speechtotext/scripts/speechtotext_drive_watch.py",
}


def _holder(lock: Path) -> subprocess.Popen[str]:
    """다른 워처가 파이프라인을 쥐고 있는 상태를 만든다 — 별도 프로세스여야 한다."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "import fcntl, sys\n"
        f"handle = open({str(lock)!r}, 'w')\n"
        "fcntl.flock(handle, fcntl.LOCK_EX)\n"
        "print('HELD', flush=True)\n"
        "sys.stdin.readline()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "HELD"
    return process


def _run_watcher(path: Path, home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(path)],
        capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "AUTOPHAGY_RUNTIME_ROOT": str(REPO)},
    )


def test_the_lock_lives_where_both_watchers_are_told_to_look(tmp_path: Path) -> None:
    resolved = pipeline_lock.lock_path({"HOME": str(tmp_path)})

    assert resolved.parent == tmp_path / ".hermes"
    assert resolved.name == pipeline_lock.LOCK_NAME


def test_holding_is_exclusive_across_processes(tmp_path: Path) -> None:
    lock = pipeline_lock.lock_path({"HOME": str(tmp_path)})
    holder = _holder(lock)
    try:
        with pipeline_lock.hold({"HOME": str(tmp_path)}) as acquired:
            assert acquired is False, "다른 프로세스가 쥐고 있으면 잡히면 안 된다"
    finally:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=10)

    with pipeline_lock.hold({"HOME": str(tmp_path)}) as acquired:
        assert acquired is True, "쥔 쪽이 놓으면 다음 틱은 잡을 수 있어야 한다"


@pytest.mark.parametrize("watcher", sorted(_WATCHERS))
def test_a_watcher_yields_while_the_pipeline_is_busy(watcher: str, tmp_path: Path) -> None:
    """양쪽 모두 양보해야 한다 — 한쪽만 지키면 반대 순서에서 그대로 겹친다."""
    holder = _holder(pipeline_lock.lock_path({"HOME": str(tmp_path)}))
    try:
        result = _run_watcher(_WATCHERS[watcher], tmp_path)
    finally:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=10)

    assert result.returncode == 0, f"양보는 실패가 아니다:\n{result.stderr}"
    assert "Traceback" not in result.stderr, result.stderr
