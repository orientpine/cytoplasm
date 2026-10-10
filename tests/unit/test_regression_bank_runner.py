"""주간 회귀 뱅크 러너는 매 회 하네스를 주 노드의 릴리스 트리로 맞춘 뒤 돌린다.

2026-07-20 사본이 손으로만 갱신되는 하네스에 그대로 남아, 스킬 루트 반전(2026-08-15) 뒤 없어진
`~/.hermes/skills/...` 경로를 부르며 두 달간 매주 실패했다. 이 시험은 러너가 실행 전에 릴리스
트리에서 하네스를 동기화하는지, 동기화가 실패하면 낡은 하네스로 돌지 않는지를 고정한다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

_RUNNER: Final = Path(__file__).resolve().parents[2] / "automation" / "regression_bank" / "remote_bank_runner.sh"

_NODE_CONFIG: Final = """import sys
print("NODE_PRIMARY_NODE_NAME=primary-test")
print("NODE_AGENT_ACCOUNT=agent")
print("NODE_AGENT_HOME=/home/agent")
print("NODE_RELEASE_CURRENT=/srv/release-current")
"""

_RECORDER: Final = """#!/usr/bin/env bash
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$CALLS"
[[ "$(basename "$0")" == rsync ]] && exit "${RSYNC_RC:-0}"
exit 0
"""

_RUN_BANK: Final = """#!/usr/bin/env bash
printf 'run_bank %s\\n' "$*" >> "$CALLS"
exit 0
"""


def _harness(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    harness = tmp_path / "harness"
    (harness / "automation").mkdir(parents=True)
    (harness / "tests" / "e2e").mkdir(parents=True)
    (harness / "automation" / "node_config_sh.py").write_text(_NODE_CONFIG, encoding="utf-8")
    (harness / "tests" / "e2e" / "run_bank.sh").write_text(_RUN_BANK, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("ssh", "rsync"):
        stub = bin_dir / name
        stub.write_text(_RECORDER, encoding="utf-8")
        stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "REGRESSION_BANK_HARNESS": str(harness),
        "CALLS": str(tmp_path / "calls.log"),
    }
    return harness, env


def _calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_the_harness_is_synced_from_the_node_release_before_the_bank_runs(tmp_path: Path) -> None:
    harness, env = _harness(tmp_path)

    result = subprocess.run(["bash", str(_RUNNER)], env=env, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(tmp_path)
    rsync = [index for index, line in enumerate(calls) if line.startswith("rsync ")]
    bank = [index for index, line in enumerate(calls) if line.startswith("run_bank ")]
    assert len(rsync) == 1 and len(bank) == 1 and rsync[0] < bank[0]
    assert f"primary-test:/srv/release-current/ {harness}/" in calls[rsync[0]]
    assert "--delete" in calls[rsync[0]]


def test_a_failed_sync_never_runs_the_stale_harness(tmp_path: Path) -> None:
    _harness_root, env = _harness(tmp_path)

    result = subprocess.run(
        ["bash", str(_RUNNER)], env={**env, "RSYNC_RC": "23"}, capture_output=True, text=True, check=False
    )

    assert result.returncode == 1
    assert "BANK-HARNESS-REFRESH-FAIL" in result.stderr
    assert not any(line.startswith("run_bank ") for line in _calls(tmp_path))


def test_refresh_can_be_turned_off_for_an_unpushed_tree(tmp_path: Path) -> None:
    _harness_root, env = _harness(tmp_path)

    result = subprocess.run(
        ["bash", str(_RUNNER)], env={**env, "REGRESSION_BANK_REFRESH": "0"}, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(tmp_path)
    assert not any(line.startswith("rsync ") for line in calls)
    assert any(line.startswith("run_bank ") for line in calls)
