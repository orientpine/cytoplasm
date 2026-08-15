"""재조정 타이머 프로비저너 — 켤 수 없는 타이머는 켜지 않는다.

이 기능의 실패 양식은 **침묵**이다. 알림은 아직 `unconfigured_notifier`라, 2분마다
깨어나 sudo 거부로 실패하는 타이머는 아무 데도 신호를 내지 않는다. 겉보기에 건강한
노드가 영원히 수렴하지 않는다.

실제로 그 틈이 한 번 배포됐다(2026-08-01): 서비스는 `User=ops`인데 helper grant는
`deploy-runner`에만 있었다. 두 파일이 각자 정확했고 어느 단위 테스트도 둘을 함께 보지
않았다. 그래서 프로비저너는 sudoers를 깐 뒤 **`sudo -l`로 그 권한이 실제로 먹었는지**
확인하고, 아니면 타이머를 시작하지 않는다. 여기서 고정하는 것이 그 거부다.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from automation.node_asset_renderer import render_asset
from automation.node_config import default_node_config

_REPO = Path(__file__).resolve().parents[2]
_PROVISION = _REPO / "automation" / "provision-deploy-reconcile.sh"
_SERVICE = _REPO / "automation" / "systemd" / "autophagy-deploy-reconcile.service"

_HELPER_NAME = "autophagy-converge-origin-main"


def _fake_bin(tmp_path: Path, *, grant_effective: bool = True) -> Path:
    """Stand-ins for the commands that need a real root, recording what was asked."""
    fake = tmp_path / "bin"
    fake.mkdir(parents=True, exist_ok=True)
    journal = tmp_path / "calls.log"

    # `install` must really create things, but cannot chown as a normal user.
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
    (fake / "visudo").write_text(
        f'#!/usr/bin/env bash\nprintf "visudo %s\\n" "$*" >> "{journal}"\nexit 0\n', encoding="utf-8"
    )
    # `sudo -l -U <user>` is the effectiveness probe the script relies on.
    # 실제 sudo 는 노드의 경로를 찍는다. 테스트는 tmp 경로를 쓰므로 런타임에
    # $HELPER_PATH 를 펼쳐 스크립트가 보는 것과 같은 경로를 돌려준다.
    listed = '    (root) NOPASSWD: ${HELPER_PATH}' if grant_effective else "    (none)"
    (fake / "sudo").write_text(
        "#!/usr/bin/env bash\n"
        f'printf "sudo %s\\n" "$*" >> "{journal}"\n'
        f'printf "%s\\n" "{listed}"\n',
        encoding="utf-8",
    )
    (fake / "systemctl").write_text(
        f'#!/usr/bin/env bash\nprintf "systemctl %s\\n" "$*" >> "{journal}"\nexit 0\n',
        encoding="utf-8",
    )
    # 서비스 계정은 노드에만 있다. 계정 존재 확인 자체는 올바른 검사이므로
    # 스크립트를 느슨하지 않고 여기서 준다.
    (fake / "id").write_text(
        '#!/usr/bin/env bash\nexit 0\n', encoding="utf-8"
    )
    for stub in fake.iterdir():
        stub.chmod(0o755)
    return fake


def _run(tmp_path: Path, *, with_helper: bool = True, grant_effective: bool = True,
         no_enable: bool = False) -> tuple[subprocess.CompletedProcess[str], str]:
    helper = tmp_path / "libexec" / _HELPER_NAME
    if with_helper:
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        helper.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{_fake_bin(tmp_path, grant_effective=grant_effective)}{os.pathsep}{env['PATH']}"
    env["DEPLOY_RECONCILE_ASSUME_ROOT"] = "1"
    env["SUDOERS_PATH"] = str(tmp_path / "sudoers.d" / "autophagy-deploy-reconcile")
    env["UNIT_DIR"] = str(tmp_path / "units")
    env["STATE_DIR"] = str(tmp_path / "state")
    env["HELPER_PATH"] = str(helper)
    if no_enable:
        env["DEPLOY_RECONCILE_NO_ENABLE"] = "1"
    result = subprocess.run(
        ("bash", str(_PROVISION)), capture_output=True, text=True, check=False, env=env
    )
    journal = tmp_path / "calls.log"
    return result, journal.read_text(encoding="utf-8") if journal.exists() else ""


def test_a_missing_helper_is_a_hard_stop(tmp_path: Path) -> None:
    # 타이머만 켜 두면 2분마다 조용히 실패한다 — 경고가 아니라 정지여야 한다
    result, calls = _run(tmp_path, with_helper=False)
    assert result.returncode != 0
    assert "provision-deploy-converge.sh" in result.stderr
    assert "enable" not in calls


def test_an_ineffective_grant_refuses_to_start_the_timer(tmp_path: Path) -> None:
    """오늘의 결함 그 자체: sudoers 는 멀쩡한데 계정이 helper 에 닿지 못하는 상태."""
    result, calls = _run(tmp_path, grant_effective=False)
    assert result.returncode != 0
    assert "may not run" in result.stderr
    assert "enable" not in calls


def test_the_happy_path_installs_validates_and_starts(tmp_path: Path) -> None:
    result, calls = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "visudo -cf" in calls                      # 신뢰 전 검증
    assert "systemctl daemon-reload" in calls
    assert "systemctl enable --now autophagy-deploy-reconcile.timer" in calls
    assert (tmp_path / "sudoers.d" / "autophagy-deploy-reconcile").is_file()
    assert (tmp_path / "units" / "autophagy-deploy-reconcile.service").is_file()
    assert (tmp_path / "units" / "autophagy-deploy-reconcile.timer").is_file()
    assert (tmp_path / "state").is_dir()


def test_sudoers_is_validated_before_the_grant_is_probed(tmp_path: Path) -> None:
    """순서가 뒤집히면 깨진 sudoers 파일을 신뢰한 채 probe 하게 된다."""
    _, calls = _run(tmp_path)
    lines = calls.splitlines()
    visudo_at = next(i for i, line in enumerate(lines) if line.startswith("visudo "))
    probe_at = next(i for i, line in enumerate(lines) if line.startswith("sudo -n -l"))
    assert visudo_at < probe_at


def test_no_enable_installs_everything_but_leaves_the_timer_stopped(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, no_enable=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "units" / "autophagy-deploy-reconcile.timer").is_file()
    assert "systemctl enable" not in calls


def test_it_is_idempotent(tmp_path: Path) -> None:
    first, _ = _run(tmp_path)
    second, _ = _run(tmp_path)
    assert first.returncode == 0 and second.returncode == 0, second.stderr


def test_the_account_comes_from_the_unit_not_a_second_copy(tmp_path: Path) -> None:
    """유닛의 User= 가 단일 진실이어야 계정을 바꿔도 grant 가 따라간다."""
    body = _PROVISION.read_text(encoding="utf-8")
    assert "sed -n 's/^User=" in body
    assert "SERVICE_USER=\"ops\"" not in body        # 하드코딩된 두 번째 사본 금지
    result, calls = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    unit_user = next(
        line.split("=", 1)[1]
        for line in render_asset(_SERVICE, default_node_config()).splitlines()
        if line.startswith("User=")
    )
    assert f"sudo -n -l -U {unit_user}" in calls
