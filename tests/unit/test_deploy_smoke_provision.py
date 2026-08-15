from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TypedDict

_REPO = Path(__file__).resolve().parents[2]
_WRAPPER = _REPO / "automation" / "deploy-smoke.sh"
_PROVISION = _REPO / "automation" / "provision-deploy-smoke.sh"
_UNITS = _REPO / "automation" / "systemd"
_SERVICE = "autophagy-deploy-smoke.service"
_TIMER = "autophagy-deploy-smoke.timer"


class Tick(TypedDict):
    argv: list[str]
    exit_code: int
    outcome: str
    timestamp: str
    version: int


def _fake_runner(tmp_path: Path, exit_code: int) -> tuple[Path, Path]:
    calls = tmp_path / "runner.calls"
    runner = tmp_path / "fake-runner"
    runner.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{calls}"\nexit {exit_code}\n',
        encoding="utf-8",
    )
    runner.chmod(0o755)
    return runner, calls


def _run_wrapper(tmp_path: Path, exit_code: int) -> tuple[subprocess.CompletedProcess[str], Tick, str]:
    runner, calls = _fake_runner(tmp_path, exit_code)
    environment = dict(os.environ)
    environment["HOME"] = str(tmp_path / "home")
    environment["DEPLOY_SMOKE_RUNNER"] = str(runner)
    result = subprocess.run(
        ("bash", str(_WRAPPER)), check=False, capture_output=True, text=True, env=environment
    )
    tick_path = Path(environment["HOME"]) / ".hermes" / "deploy-smoke" / "tick.json"
    tick: Tick = json.loads(tick_path.read_text(encoding="utf-8"))
    return result, tick, calls.read_text(encoding="utf-8")


def _fake_system_commands(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    calls = tmp_path / "system.calls"
    install = fake_bin / "install"
    install.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "install %s\\n" "$*" >> "{calls}"\n'
        "args=()\n"
        "while (( $# )); do\n"
        '  case "$1" in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac\n'
        "done\n"
        '/usr/bin/install "${args[@]}"\n',
        encoding="utf-8",
    )
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        f'#!/usr/bin/env bash\nprintf "systemctl %s\\n" "$*" >> "{calls}"\n',
        encoding="utf-8",
    )
    install.chmod(0o755)
    systemctl.chmod(0o755)
    return fake_bin, calls


def _run_provision(tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], str, Path]:
    fake_bin, calls = _fake_system_commands(tmp_path)
    staging = tmp_path / "units"
    environment = dict(os.environ)
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    environment["DEPLOY_SMOKE_ASSUME_ROOT"] = "1"
    result = subprocess.run(
        ("bash", str(_PROVISION), str(staging)),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    journal = calls.read_text(encoding="utf-8") if calls.exists() else ""
    return result, journal, staging


def test_fake_runner_success_records_tick_and_sandbox_argv(tmp_path: Path) -> None:
    result, tick, calls = _run_wrapper(tmp_path, 0)

    assert result.returncode == 0, result.stderr
    assert calls.splitlines() == ["hello-autophagy", "--sandbox-only"]
    assert tick["outcome"] == "success"
    assert tick["exit_code"] == 0
    assert tick["argv"] == ["hello-autophagy", "--sandbox-only"]


def test_fake_runner_failure_records_tick_and_propagates_exit(tmp_path: Path) -> None:
    result, tick, calls = _run_wrapper(tmp_path, 23)

    assert result.returncode == 23
    assert calls.splitlines() == ["hello-autophagy", "--sandbox-only"]
    assert tick["outcome"] == "failure"
    assert tick["exit_code"] == 23


def test_provision_installs_service_and_daily_timer(tmp_path: Path) -> None:
    result, calls, staging = _run_provision(tmp_path)

    assert result.returncode == 0, result.stderr
    assert (staging / _SERVICE).is_file()
    assert (staging / _TIMER).is_file()
    assert "systemctl daemon-reload" in calls
    assert f"systemctl enable --now {_TIMER}" in calls
    timer = (_UNITS / _TIMER).read_text(encoding="utf-8")
    assert "OnUnitActiveSec=1d" in timer
    assert "Persistent=true" in timer


def test_provision_is_idempotent(tmp_path: Path) -> None:
    first, _, _ = _run_provision(tmp_path)
    second, _, _ = _run_provision(tmp_path)

    assert first.returncode == 0
    assert second.returncode == 0, second.stderr


def test_service_uses_the_injected_runner_capable_wrapper() -> None:
    service = (_UNITS / _SERVICE).read_text(encoding="utf-8")
    wrapper = _WRAPPER.read_text(encoding="utf-8")

    assert "automation/deploy-smoke.sh" in service
    assert "DEPLOY_SMOKE_RUNNER" in wrapper
    assert "automation/deploy-skill.sh" in wrapper
