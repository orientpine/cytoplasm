"""재조정 타이머가 도는 계정은 수렴 helper를 실행할 수 있어야 한다.

MD-2의 서비스는 `User=ops`로 돌면서 `sudo -n <helper>`로 수렴한다. 그런데 helper를
허용하는 grant는 `deploy-runner`(MD-3, 러너 전용 계정)에만 있었다. 두 파일이 각자
정확한데 **서로 맞물리지 않는** 종류의 결함이라, 어느 쪽 단위 테스트에도 걸리지 않았다.

증상은 조용하다: 타이머가 2분마다 깨어나 sudo 거부로 실패하고, 알림 경로는 아직
미배선(`unconfigured_notifier`)이라 아무 데도 도달하지 않는다. 겉보기에는 건강한 노드가
영원히 수렴하지 않는다 — 이 기능이 없애려던 바로 그 침묵이다.

그래서 여기서 고정하는 것은 "grant가 있다"가 아니라 **"서비스가 실제로 도는 계정에
grant가 있다"**이다. 유닛의 `User=`에서 계정을 읽어 대조하므로, 나중에 계정을 바꾸면
이 테스트가 함께 따라 움직인다.
"""
from __future__ import annotations

import re
from pathlib import Path

from automation.node_asset_renderer import render_asset
from automation.node_config import default_node_config

_REPO = Path(__file__).resolve().parents[2]
_SERVICE = _REPO / "automation" / "systemd" / "autophagy-deploy-reconcile.service"
_SUDOERS_DIR = _REPO / "automation" / "sudoers.d"
_CONVERGE_PROVISION = _REPO / "automation" / "provision-deploy-converge.sh"

_HELPER = "/usr/local/libexec/autophagy-converge-origin-main"


def _service_user() -> str:
    match = re.search(r"^User=(\S+)$", render_asset(_SERVICE, default_node_config()), re.MULTILINE)
    assert match is not None, "the reconcile service must name the account it runs as"
    return match.group(1)


def _grant_lines() -> list[str]:
    lines: list[str] = []
    for path in sorted(_SUDOERS_DIR.iterdir()):
        if not path.is_file():
            continue
        lines.extend(
            line.strip()
            for line in render_asset(path, default_node_config()).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    return lines


def test_the_account_the_timer_runs_as_may_run_the_helper() -> None:
    # 이것이 빠져 있었다. 유닛도 sudoers 도 각자 정확했지만 둘이 만나지 않았다.
    user = _service_user()
    expected = f"{user} ALL=(root) NOPASSWD: {_HELPER}"
    assert expected in _grant_lines(), f"{user} 에게 helper grant 가 없다: {expected}"


def test_the_grant_names_one_fixed_command_with_no_wildcard() -> None:
    """와일드카드는 고정 명령 하나를 명령 '군'으로 되돌린다."""
    user = _service_user()
    granted = [line for line in _grant_lines() if line.startswith(f"{user} ") and _HELPER in line]
    assert len(granted) == 1, granted
    assert "*" not in granted[0]
    assert not granted[0].endswith("ALL")


def test_the_grant_file_holds_only_that_one_line() -> None:
    # 한 파일에 여러 grant 를 모으면 나중에 하나를 지울 때 다른 하나가 함께 사라진다.
    path = _SUDOERS_DIR / "autophagy-deploy-reconcile"
    assert path.is_file(), f"{path} 가 없다"
    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert len(lines) == 1, lines


def test_the_granted_path_is_the_one_the_provisioner_installs() -> None:
    """두 파일이 같은 특권 경로를 가리켜야 한다 — 어긋나면 grant 가 무의미해진다."""
    assert _HELPER in _CONVERGE_PROVISION.read_text(encoding="utf-8")


def test_the_service_still_does_not_set_no_new_privileges() -> None:
    """이 grant 는 NoNewPrivileges 가 없을 때만 쓸모가 있다 — 함께 고정한다."""
    body = _SERVICE.read_text(encoding="utf-8")
    assert "NoNewPrivileges=yes" not in body
    assert "NoNewPrivileges=true" not in body
