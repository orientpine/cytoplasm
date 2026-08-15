"""⑦ 공급망 워처 타이머 프로비저너 — 켤 수 없는 타이머는 켜지 않는다.

이 워처의 실패 양식은 재조정 타이머와 같되 더 조용하다. 재조정은 sudo 거부로
최소한 실패 로그라도 남기지만, 이쪽의 대표적 오설정은 **에러조차 내지 않는다**:

* `ProtectHome=yes` 로 '하드닝'하면 /home 이 빈 디렉터리로 보여 레코드 열거가
  매 tick 0건을 돌려준다. 유닛은 성공(exit 0)하고, 타이머는 건강해 보이고,
  소유자의 ✅ 는 영원히 읽히지 않는다.
* 게이트 디렉터리가 없어도 정확히 같은 그림이 된다 — 0건, 성공, 침묵.

그래서 프로비저너는 유닛을 깔기 전에 이 조건들을 **실제로** 확인하고, 하나라도
어긋나면 타이머를 시작하지 않는다. 여기서 고정하는 것이 그 거부다.

계정·시크릿 경로·런타임은 전부 유닛 파일에서 읽는다. 두 번째 사본을 만들면
계정을 바꿨을 때 따라오지 않는 쪽이 생기고, 그것이 재조정 타이머에서 실제로
배포된 결함이었다(2026-08-01, User=ops 인데 grant 는 deploy-runner).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from automation.node_asset_renderer import render_asset
from automation.node_config import default_node_config

_REPO = Path(__file__).resolve().parents[2]
_PROVISION = _REPO / "automation" / "provision-supply-chain-watch.sh"
_UNIT_SRC = _REPO / "automation" / "systemd"
_SERVICE_NAME = "autophagy-supply-chain-watch.service"
_TIMER_NAME = "autophagy-supply-chain-watch.timer"


def _fake_bin(tmp_path: Path, *, import_ok: bool = True, grant_effective: bool = True) -> Path:
    """실제 root 가 필요한 명령의 대역 — 무엇을 물었는지 기록한다."""
    fake = tmp_path / "bin"
    fake.mkdir(parents=True, exist_ok=True)
    journal = tmp_path / "calls.log"

    # install 은 실제로 만들어야 하지만 일반 사용자는 chown 할 수 없다.
    (fake / "install").write_text(
        "#!/usr/bin/env bash\n"
        f'printf "install %s\\n" "$*" >> "{journal}"\n'
        "args=()\n"
        "while (( $# )); do\n"
        '  case "$1" in\n'
        "    -o|-g) shift 2 ;;\n"
        '    *) args+=("$1"); shift ;;\n'
        "  esac\n"
        "done\n"
        '/usr/bin/install "${args[@]}"\n',
        encoding="utf-8",
    )
    (fake / "systemctl").write_text(
        f'#!/usr/bin/env bash\nprintf "systemctl %s\\n" "$*" >> "{journal}"\nexit 0\n',
        encoding="utf-8",
    )
    # 서비스 계정으로의 import 스모크. 실패 seam 이 곧 '켜지 않는다'의 증거다.
    # sudo 는 두 역할을 한다: 권한 열거(-l)와 서비스 계정 import 스모크.
    listed = "    (root) NOPASSWD: ${HELPER_PATH}" if grant_effective else "    (none)"
    (fake / "sudo").write_text(
        "#!/usr/bin/env bash\n"
        f'printf "sudo %s\\n" "$*" >> "{journal}"\n'
        'if [[ "$*" == *" -l "* || "$1" == "-n" && "$2" == "-l" ]]; then\n'
        f'  printf "%s\\n" "{listed}"\n'
        "  exit 0\n"
        "fi\n"
        f"exit {0 if import_ok else 1}\n",
        encoding="utf-8",
    )
    (fake / "visudo").write_text(
        f'#!/usr/bin/env bash\nprintf "visudo %s\\n" "$*" >> "{journal}"\nexit 0\n',
        encoding="utf-8",
    )
    # 계정과 홈은 노드에만 있다. 존재 확인 자체는 올바른 검사이므로 여기서 준다.
    (fake / "id").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for stub in fake.iterdir():
        stub.chmod(0o755)
    return fake


def _unit_dir(tmp_path: Path, home: Path, *, protect_home: str | None = None) -> Path:
    """테스트용 유닛 사본.

    유닛의 ``EnvironmentFile`` 은 노드 절대경로라 그대로 쓰면 hermetic 테스트가
    항상 '홈 불일치'로 거부된다. 그래서 사본은 그 경로를 테스트 홈으로 맞췄다 —
    불일치 감지 자체는 원본 유닛을 그대로 쓰는 별도 테스트가 맡는다.
    """
    doctored = tmp_path / "unit-src"
    doctored.mkdir(parents=True, exist_ok=True)
    for name in (_SERVICE_NAME, _TIMER_NAME):
        text = (_UNIT_SRC / name).read_text(encoding="utf-8")
        if name == _SERVICE_NAME:
            text = text.replace("$NODE_AGENT_HOME", str(home))
            if protect_home is not None:
                text = text.replace("ProtectHome=no", f"ProtectHome={protect_home}")
        _ = (doctored / name).write_text(text, encoding="utf-8")
    return doctored


def _home(tmp_path: Path, *, secrets: bool = True, gate: bool = True, interop: bool = True) -> Path:
    home = tmp_path / "home" / "agent"
    home.mkdir(parents=True, exist_ok=True)
    if secrets:
        _ = (home / ".env.secrets").write_text("DISCORD_BOT_TOKEN=x\n", encoding="utf-8")
    if gate:
        (home / ".hermes" / "skill-gate" / "pending").mkdir(parents=True, exist_ok=True)
    if interop:
        config = home / ".hermes" / "interop"
        config.mkdir(parents=True, exist_ok=True)
        _ = (config / "config.json").write_text('{"owner_id": "1"}', encoding="utf-8")
    return home


def _runtime(tmp_path: Path, *, present: bool = True) -> Path:
    runtime = tmp_path / "release"
    if present:
        (runtime / "automation").mkdir(parents=True, exist_ok=True)
        _ = (runtime / "automation" / "supply_chain_watch_cli.py").write_text("", encoding="utf-8")
    return runtime


def _run(
    tmp_path: Path,
    *,
    secrets: bool = True,
    gate: bool = True,
    interop: bool = True,
    runtime: bool = True,
    import_ok: bool = True,
    grant_effective: bool = True,
    protect_home: str | None = None,
    no_enable: bool = False,
) -> tuple[subprocess.CompletedProcess[str], str]:
    env = dict(os.environ)
    env["PATH"] = (
        f"{_fake_bin(tmp_path, import_ok=import_ok, grant_effective=grant_effective)}"
        f"{os.pathsep}{env['PATH']}"
    )
    env["SUPPLY_CHAIN_WATCH_ASSUME_ROOT"] = "1"
    env["UNIT_DIR"] = str(tmp_path / "units")
    home = _home(tmp_path, secrets=secrets, gate=gate, interop=interop)
    env["UNIT_SRC_DIR"] = str(_unit_dir(tmp_path, home, protect_home=protect_home))
    env["SERVICE_HOME"] = str(home)
    env["RUNTIME_ROOT"] = str(_runtime(tmp_path, present=runtime))
    env["HELPER_PATH"] = str(tmp_path / "libexec" / "autophagy-resume-deploy")
    env["SUDOERS_PATH"] = str(tmp_path / "sudoers.d" / "autophagy-supply-chain-resume")
    if no_enable:
        env["SUPPLY_CHAIN_WATCH_NO_ENABLE"] = "1"
    result = subprocess.run(
        ("bash", str(_PROVISION)), capture_output=True, text=True, check=False, env=env
    )
    journal = tmp_path / "calls.log"
    return result, journal.read_text(encoding="utf-8") if journal.exists() else ""


def test_a_hardened_protect_home_is_a_hard_stop(tmp_path: Path) -> None:
    """0건을 성공으로 보고하는 침묵 — 이 워처의 가장 위험한 오설정이다."""
    result, calls = _run(tmp_path, protect_home="yes")
    assert result.returncode != 0
    assert "ProtectHome" in result.stderr
    assert "enable" not in calls


def test_a_missing_gate_directory_refuses_to_start_the_timer(tmp_path: Path) -> None:
    """레코드 디렉터리가 없으면 매 tick 0건이다 — 역시 에러 없이 조용하다."""
    result, calls = _run(tmp_path, gate=False)
    assert result.returncode != 0
    assert "skill-gate" in result.stderr
    assert "enable" not in calls


def test_a_missing_interop_config_refuses_to_start_the_timer(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, interop=False)
    assert result.returncode != 0
    assert "interop" in result.stderr
    assert "enable" not in calls


def test_a_missing_secrets_file_refuses_to_start_the_timer(tmp_path: Path) -> None:
    """EnvironmentFile 이 없으면 systemd 가 유닛 자체를 실패시킨다."""
    result, calls = _run(tmp_path, secrets=False)
    assert result.returncode != 0
    assert ".env.secrets" in result.stderr
    assert "enable" not in calls


def test_a_missing_runtime_release_refuses_to_start_the_timer(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, runtime=False)
    assert result.returncode != 0
    assert "enable" not in calls


def test_an_import_that_fails_refuses_to_start_the_timer(tmp_path: Path) -> None:
    """켜기 전에 '이 계정이 이 코드를 실제로 불러올 수 있는가'를 묻는다."""
    result, calls = _run(tmp_path, import_ok=False)
    assert result.returncode != 0
    assert "enable" not in calls


def test_the_happy_path_installs_verifies_and_starts(tmp_path: Path) -> None:
    result, calls = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "systemctl daemon-reload" in calls
    assert f"systemctl enable --now {_TIMER_NAME}" in calls
    assert (tmp_path / "units" / _SERVICE_NAME).is_file()
    assert (tmp_path / "units" / _TIMER_NAME).is_file()


def test_no_enable_installs_everything_but_leaves_the_timer_stopped(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, no_enable=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "units" / _TIMER_NAME).is_file()
    assert "systemctl enable" not in calls


def test_it_is_idempotent(tmp_path: Path) -> None:
    first, _ = _run(tmp_path)
    second, _ = _run(tmp_path)
    assert first.returncode == 0 and second.returncode == 0, second.stderr


def test_the_account_and_paths_come_from_the_unit_not_a_second_copy(tmp_path: Path) -> None:
    """계정을 바꿔도 검사가 따라오려면 유닛이 단일 진실이어야 한다."""
    body = _PROVISION.read_text(encoding="utf-8")
    assert 'SERVICE_USER="agent"' not in body
    assert "EnvironmentFile" in body, "시크릿 경로도 유닛에서 읽어야 한다"
    result, calls = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    unit_user = next(
        line.split("=", 1)[1]
        for line in render_asset(_UNIT_SRC / _SERVICE_NAME, default_node_config()).splitlines()
        if line.startswith("User=")
    )
    assert f"sudo -n -u {unit_user}" in calls


def test_the_environment_file_must_match_the_accounts_real_home(tmp_path: Path) -> None:
    """유닛의 하드코딩 경로와 계정의 실제 홈이 어긋나는 순간을 잡는다.

    둘 다 완전히 갖춰져 있고 오직 경로만 다르다 — 그래야 앞선 검사(홈 부재 등)가
    먼저 걸리지 않고 EnvironmentFile 일치 검사 그 자체를 검증하게 된다.
    """
    populated = _home(tmp_path)
    elsewhere = tmp_path / "home" / "someone-else"
    shutil.copytree(populated, elsewhere)
    env = dict(os.environ)
    env["PATH"] = f"{_fake_bin(tmp_path)}{os.pathsep}{env['PATH']}"
    env["SUPPLY_CHAIN_WATCH_ASSUME_ROOT"] = "1"
    env["UNIT_DIR"] = str(tmp_path / "units")
    env["UNIT_SRC_DIR"] = str(_unit_dir(tmp_path, populated))
    env["SERVICE_HOME"] = str(elsewhere)
    env["RUNTIME_ROOT"] = str(_runtime(tmp_path))
    result = subprocess.run(
        ("bash", str(_PROVISION)), capture_output=True, text=True, check=False, env=env
    )
    assert result.returncode != 0
    assert "EnvironmentFile" in result.stderr


def test_an_ineffective_grant_refuses_to_start_the_timer(tmp_path: Path) -> None:
    """sudoers 는 멀쩡한데 계정이 헬퍼에 닿지 못하는 상태 — 이웃 타이머의 실제 결함이었다."""
    result, calls = _run(tmp_path, grant_effective=False)
    assert result.returncode != 0
    assert "may not run" in result.stderr
    assert "enable" not in calls


def test_the_one_escalation_and_its_grant_are_installed(tmp_path: Path) -> None:
    result, calls = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "libexec" / "autophagy-resume-deploy").is_file()
    assert (tmp_path / "sudoers.d" / "autophagy-supply-chain-resume").is_file()
    assert "visudo -cf" in calls, "신뢰 전에 문법을 검증해야 한다"


def test_the_grant_is_validated_before_it_is_probed(tmp_path: Path) -> None:
    """순서가 뒤집히면 깨진 sudoers 파일을 신뢰한 채 probe 하게 된다."""
    _, calls = _run(tmp_path)
    lines = calls.splitlines()
    visudo_at = next(i for i, line in enumerate(lines) if line.startswith("visudo "))
    probe_at = next(i for i, line in enumerate(lines) if line.startswith("sudo -n -l"))
    assert visudo_at < probe_at
