"""원격 e2e 드라이버는 시나리오의 자리표시자 호스트를 노드 설정으로 푼다.

공개 비식별화(2026-08-16) 뒤 시나리오의 `remote_host` 는 `<primary-node>` 자리표시자다. 드라이버가
그것을 그대로 ssh 에 넘겨 w2·w3-calendar·w3-report-hub 가 매번 `hostname contains invalid characters`
로 실패했다(2026-10-10 수동 회귀 뱅크 실행). 실제 호스트는 노드 설정 `deploy_ssh_host` 에 있다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_DRIVERS = ("w2_personal_memory.sh", "w3_calendar.sh", "w3_report_hub.sh")

_NODE_CONFIG = """print("NODE_DEPLOY_SSH_HOST=configured-host")
"""

_SSH = """#!/usr/bin/env bash
printf '%s\\n' "$1" >> "$SSH_CALLS"
exit 1
"""


def _layout(tmp_path: Path, driver: str, remote_host: str) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "root"
    for part in ("drivers", "fixtures"):
        shutil.copytree(_REPO / "tests" / "e2e" / part, root / "tests" / "e2e" / part)
    (root / "tests" / "e2e" / "scenarios").mkdir(parents=True)
    (root / "automation").mkdir()
    (root / "automation" / "node_config_sh.py").write_text(_NODE_CONFIG, encoding="utf-8")
    scenario = root / "tests" / "e2e" / "scenarios" / "case.yaml"
    scenario.write_text(f"scenario: case\nremote_host: {remote_host}\nremote_account: agent\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(_SSH, encoding="utf-8")
    (bin_dir / "ssh").chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SSH_CALLS": str(tmp_path / "ssh.log"),
    }
    return scenario, env


def _first_ssh_host(tmp_path: Path, driver: str, scenario: Path, env: dict[str, str]) -> str:
    _ = subprocess.run(
        ["bash", str(scenario.parents[1] / "drivers" / driver), str(scenario), str(tmp_path / "report")],
        env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    return (tmp_path / "ssh.log").read_text(encoding="utf-8").splitlines()[0]


@pytest.mark.parametrize("driver", _DRIVERS)
def test_a_placeholder_host_is_resolved_from_the_node_config(tmp_path: Path, driver: str) -> None:
    scenario, env = _layout(tmp_path, driver, "<primary-node>")

    assert _first_ssh_host(tmp_path, driver, scenario, env) == "configured-host"


@pytest.mark.parametrize("driver", _DRIVERS)
def test_a_concrete_host_in_the_scenario_is_kept(tmp_path: Path, driver: str) -> None:
    scenario, env = _layout(tmp_path, driver, "lab-node")

    assert _first_ssh_host(tmp_path, driver, scenario, env) == "lab-node"
