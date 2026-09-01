"""옛 코드로 도는 게이트웨이와 계정별로 갈라진 벤더를 탐지하는가.

2026-08-18 실측: `hermes-update` 가 소스를 교체하고도 게이트웨이를 재시동하지 않아
프로세스가 **옛 코드 + 새 파일** 상태로 20시간 돌았고, 첫 도구 호출에서야 드러났다.
유닛은 내내 active/running 이었으므로 `systemctl is-active` 나 Discord 연결성에 기대는
프로브는 이 장애를 원리적으로 통과시킨다. 같은 날, agent 는 v0.20.1 · peer 는 v0.18.2 로
벤더가 갈라져 있었는데 각자 자기 코드끼리는 일관돼 아무 신호도 나지 않았다.

여기서 고정하는 것은 **탐지**뿐이다. 재적용·재시동은 외부효과라 소유자 원장 소관이며,
이 모듈은 hermes·ssh·systemctl 을 부르지 않는다(patch_state.py 와 같은 계약).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from automation.hermes_compat import gateway_runtime_probe as probe

_REPO = Path(__file__).resolve().parents[2]


def _install(root: Path, *, body: str = "print('gateway')\n", mtime: float | None = None) -> Path:
    source = root / "gateway" / "run.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(body, encoding="utf-8")
    cache = root / "gateway" / "__pycache__" / "run.cpython-312.pyc"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"\x00compiled")
    if mtime is not None:
        os.utime(source, (mtime, mtime))
        os.utime(cache, (mtime + 10_000, mtime + 10_000))
    return root


def test_source_newer_than_start_is_stale(tmp_path: Path) -> None:
    root = _install(tmp_path / "agent", mtime=200.0)

    reading = probe.probe_staleness(root, started_at=100.0)

    assert reading.state == probe.STALE
    assert "200" in reading.detail or "stale" in reading.detail.lower()


def test_start_after_the_newest_source_is_fresh(tmp_path: Path) -> None:
    root = _install(tmp_path / "agent", mtime=100.0)

    assert probe.probe_staleness(root, started_at=300.0).state == probe.FRESH


def test_bytecode_cache_never_decides_staleness(tmp_path: Path) -> None:
    """__pycache__ is written by the running process, so it would always look newer."""
    root = _install(tmp_path / "agent", mtime=100.0)

    assert probe.probe_staleness(root, started_at=300.0).state == probe.FRESH


def test_missing_start_time_is_unknown_not_pass(tmp_path: Path) -> None:
    root = _install(tmp_path / "agent", mtime=100.0)

    assert probe.probe_staleness(root, started_at=None).state == probe.UNKNOWN


def test_unreadable_install_root_is_unknown_not_pass(tmp_path: Path) -> None:
    assert probe.probe_staleness(tmp_path / "absent", started_at=300.0).state == probe.UNKNOWN


def test_accounts_carrying_different_source_are_diverged(tmp_path: Path) -> None:
    agent = _install(tmp_path / "agent", body="print('v0.20.1')\n", mtime=100.0)
    peer = _install(tmp_path / "peer", body="print('v0.18.2')\n", mtime=100.0)

    reading = probe.probe_divergence({"agent": agent, "peer": peer})

    assert reading.state == probe.DIVERGED


def test_accounts_carrying_identical_source_are_converged(tmp_path: Path) -> None:
    agent = _install(tmp_path / "agent", body="print('same')\n", mtime=100.0)
    peer = _install(tmp_path / "peer", body="print('same')\n", mtime=200.0)

    reading = probe.probe_divergence({"agent": agent, "peer": peer})

    assert reading.state == probe.CONVERGED, "mtime differs but shipped source is identical"


def test_unreadable_account_is_unknown_not_converged(tmp_path: Path) -> None:
    agent = _install(tmp_path / "agent", body="print('same')\n", mtime=100.0)

    reading = probe.probe_divergence({"agent": agent, "peer": tmp_path / "absent"})

    assert reading.state == probe.UNKNOWN


def test_unknown_outranks_a_detected_failure() -> None:
    stale = probe.Reading("staleness", probe.STALE, "stale")
    unknown = probe.Reading("divergence", probe.UNKNOWN, "unreadable")

    assert probe.verdict((stale, unknown)) == 2
    assert probe.verdict((stale,)) == 1
    assert probe.verdict(()) == 2


def test_cli_reports_json_and_exits_one_on_stale(tmp_path: Path) -> None:
    agent = _install(tmp_path / "agent", mtime=200.0)
    peer = _install(tmp_path / "peer", mtime=200.0)

    result = subprocess.run(
        (
            sys.executable,
            "-m",
            "automation.hermes_compat.gateway_runtime_probe",
            "--install-root",
            str(agent),
            "--peer-install-root",
            str(peer),
            "--started-at",
            "100",
            "--json",
        ),
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["verdict"] == 1
    assert any(row["state"] == probe.STALE for row in payload["readings"])


def test_cli_exits_zero_when_fresh_and_converged(tmp_path: Path) -> None:
    agent = _install(tmp_path / "agent", body="print('same')\n", mtime=100.0)
    peer = _install(tmp_path / "peer", body="print('same')\n", mtime=100.0)

    result = subprocess.run(
        (
            sys.executable,
            "-m",
            "automation.hermes_compat.gateway_runtime_probe",
            "--install-root",
            str(agent),
            "--peer-install-root",
            str(peer),
            "--started-at",
            "2026-08-18T00:00:00Z",
        ),
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_module_never_shells_out_to_the_gateway() -> None:
    """Detection only: restarting or updating is an owner action, not this module's."""
    source = (_REPO / "automation" / "hermes_compat" / "gateway_runtime_probe.py").read_text(
        encoding="utf-8"
    )

    for forbidden in ("subprocess", "systemctl", "os.system", "hermes update"):
        assert forbidden not in source, f"probe must not reach for {forbidden}"


@pytest.mark.parametrize("value", ["", "not-a-time", "2026-13-45T99:99:99Z"])
def test_cli_refuses_an_unparsable_start_time(tmp_path: Path, value: str) -> None:
    root = _install(tmp_path / "agent", mtime=100.0)

    result = subprocess.run(
        (
            sys.executable,
            "-m",
            "automation.hermes_compat.gateway_runtime_probe",
            "--install-root",
            str(root),
            "--started-at",
            value,
        ),
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2, result.stdout + result.stderr
