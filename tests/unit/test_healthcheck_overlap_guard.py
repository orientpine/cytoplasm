"""겹친 healthcheck 틱은 sweep 을 시작하지 않고 양보한다 — cron 폭주 회귀 고정.

ops crontab 은 `*/5` 로 healthcheck 를 부르는데 최근 400 회 실행의 중앙값은 4048 초
(p90 14760 초, 최대 39020 초)였다. 틱 간격보다 한 자릿수 긴 실행이므로 틱은 겹치고
쌓인다 — 2026-08-31 에 동시 실행 114 개가 관측됐다. 그 폭주 아래에서 SSH 프로브가
간헐적으로 타임아웃하며 **거짓** 수리 티켓을 냈다(`t_2578c8ed` LiteLLM,
`t_2524fe33` peer 게이트웨이 — 둘 다 16:30:02Z 에 시작한 실행이 18:25:23Z 에 보고한
것이고, 같은 프로브는 다른 모든 실행에서 PASS 였다).

그래서 판정 대상은 "겹쳐도 결국 끝난다"가 아니라 **겹친 틱이 sweep 을 아예 시작하지
않는다**이다. 로그 파일이 하나도 생기지 않아야 한다 — 로그가 생겼다는 것은 프로브가
돌기 시작했다는 뜻이고, 그게 곧 폭주의 씨앗이다.

`automation/pipeline_lock.py` 와 같은 양보 규약을 따른다: 잡히지 않으면 rc 0 으로
물러나고 다음 틱이 이어받으며, lock 파일을 **열지도 못하면** 잡힌 것과 구별할 수 없으므로
fail-closed 로 멈춘다.

hermetic: 프로브에 닿기 전에 끝나는 두 경로는 최소 env + 가짜 `ssh` 로 돌리고,
정상 sweep 경로는 `test_healthcheck_checkout_ticket` 의 `_sweep` 을 그대로 재사용한다.
노드도 네트워크도 실제 티켓도 없다.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
from pathlib import Path

import pytest

from tests.unit.test_healthcheck_checkout_ticket import _fake_bin, _mirror_checkout, _sweep

_REPO = Path(__file__).resolve().parents[2]
_HEALTHCHECK = _REPO / "automation" / "healthcheck.sh"
#: 양보한 틱이 남기는 유일한 흔적 — 로그 파일이 없으므로 stderr 한 줄이 증적이다.
_OVERLAP_MARKER = "HEALTHCHECK-OVERLAP-SKIP"
#: lock 을 열 수조차 없을 때의 fail-closed 표식.
_UNAVAILABLE_MARKER = "HEALTHCHECK-LOCK-UNAVAILABLE"


def _sweep_logs(log_dir: Path) -> list[Path]:
    """이 틱이 sweep 을 시작했는지 = 로그 파일을 만들었는지."""
    return sorted(log_dir.glob("healthcheck-*.log"))


def _tick(tmp_path: Path, *, lock_file: Path) -> subprocess.CompletedProcess[str]:
    """cron 틱 한 번. 가드가 있으면 프로브 전에 끝나므로 env 는 최소로 둔다."""
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "isolated-home")
    env["PATH"] = f"{_fake_bin(tmp_path)}{os.pathsep}{env['PATH']}"
    env["HEALTHCHECK_LOG_DIR"] = str(tmp_path / "logs")
    env["HEALTHCHECK_LOCK_FILE"] = str(lock_file)
    env["HEALTHCHECK_SSH_USER"] = ""
    env["HEALTHCHECK_SSH_IDENTITY"] = ""
    # 가드가 없는 동안(RED)에는 이 틱이 실제로 sweep 을 끝까지 돌린다. 그때도 티켓
    # 경로는 타지 않게 막아 둔다 — 회귀 테스트가 거짓 티켓을 내면 본말전도다.
    env["HEALTHCHECK_NO_REPAIR"] = "1"
    return subprocess.run(
        ("bash", str(_HEALTHCHECK)),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_a_tick_yields_when_a_previous_sweep_still_holds_the_lock(tmp_path: Path) -> None:
    """앞선 sweep 이 lock 을 쥔 채 도는 중이면 이번 틱은 조용히 물러난다."""
    lock_file = tmp_path / "healthcheck.lock"
    with lock_file.open("w", encoding="utf-8") as previous_sweep:
        fcntl.flock(previous_sweep, fcntl.LOCK_EX | fcntl.LOCK_NB)
        tick = _tick(tmp_path, lock_file=lock_file)

    # 양보는 실패가 아니다 — cron 이 이 틱을 실패로 보고하면 안 된다.
    assert tick.returncode == 0, tick.stdout + tick.stderr
    assert _OVERLAP_MARKER in tick.stderr, tick.stdout + tick.stderr
    # 그리고 sweep 을 시작조차 하지 않았어야 한다(로그 = 프로브 시작의 증적).
    assert _sweep_logs(tmp_path / "logs") == [], tick.stdout + tick.stderr


def test_a_lone_tick_still_runs_the_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """lock 이 비어 있으면 종전대로 돈다 — 가드가 정상 tick 을 잡아먹으면 안 된다."""
    monkeypatch.setenv("HEALTHCHECK_LOCK_FILE", str(tmp_path / "healthcheck.lock"))

    sweep = _sweep(tmp_path, _mirror_checkout(tmp_path))

    assert sweep.returncode == 0, sweep.output
    assert "ALL_HEALTHY" in sweep.output
    assert _OVERLAP_MARKER not in sweep.output
    assert len(_sweep_logs(tmp_path / "logs")) == 1, sweep.output


def test_an_unopenable_lock_stops_the_tick_fail_closed(tmp_path: Path) -> None:
    """lock 을 열 수 없으면 '아무도 안 돈다'고 가정하지 않는다 — 멈추고 알린다."""
    barrier = tmp_path / "not-a-directory"
    _ = barrier.write_text("", encoding="utf-8")

    tick = _tick(tmp_path, lock_file=barrier / "healthcheck.lock")

    assert tick.returncode != 0, tick.stdout + tick.stderr
    assert _UNAVAILABLE_MARKER in tick.stderr, tick.stdout + tick.stderr
    assert _sweep_logs(tmp_path / "logs") == [], tick.stdout + tick.stderr
