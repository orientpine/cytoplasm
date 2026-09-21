"""컨테이너 하네스가 운영자 계정을 인자로 받는다는 계약을 고정한다.

`tests/e2e/install/systemd_container/run.sh` 는 `operator_account='root'` 로만 돌았다.
그래서 **운영자가 root 가 아닌 노드에서만 나타나는 결함**(healthcheck 프로브 자산이
운영자를 root 로 가정)이 하네스를 통과해 v1.6.1 로 나갔고, 실호스트에서야 rc=126 으로
드러났다 — docs/qa/INSTALL-TUI/12-probe-asset-nonroot-operator.txt.

여기서 고정하는 것은 인자 계약 하나다: **하네스는 비-root 운영자로 돌 수 있어야 하고,
쉘·`useradd`·노드 설정이 그대로 삼킬 수 없는 이름은 docker 를 건드리기 전에 거부한다.**
실제 컨테이너 실행의 증명은 하네스 자신의 사후 검증(사용자 sudoers 자산·프로브 소유/모드·
프로브 수렴 판정)이 맡는다 — 그것은 docker 가 필요하므로 이 파일에 둘 수 없다.

`--help` 를 뒤에 붙여 **인자 해석만** 시킨다: 해석이 끝나는 지점에서 종료하므로
이미지 빌드나 컨테이너 부팅이 일어나지 않는다.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_HARNESS = Path(__file__).resolve().parents[2] / "tests/e2e/install/systemd_container/run.sh"

_USAGE_ERROR = 2


def _parse(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", str(_HARNESS), *argv),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_the_harness_accepts_a_non_root_operator_account() -> None:
    # Given: 결함이 나타나는 조건은 운영자가 평범한 계정인 노드다.
    # When: 그 계정 이름을 하네스에 넘긴다.
    result = _parse("--operator", "ops2", "--help")

    # Then: 인자가 받아들여지고, 사용법이 그 자리를 알려 준다.
    assert result.returncode == 0, result.stderr
    assert "--operator" in result.stdout, result.stdout


def test_the_harness_still_takes_root_as_an_explicit_operator() -> None:
    # 기본값은 root 이고, 명시해도 같은 뜻이어야 한다 — 기존 증적이 그 위에 있다.
    result = _parse("--operator", "root", "--help")

    assert result.returncode == 0, result.stderr


def test_the_harness_refuses_an_operator_flag_without_a_name() -> None:
    # 이름이 없으면 컨테이너에서 무엇을 만들지가 정해지지 않는다.
    assert _parse("--operator").returncode == _USAGE_ERROR
    assert _parse("--operator", "", "--help").returncode == _USAGE_ERROR


def test_the_harness_refuses_an_operator_name_the_container_cannot_take() -> None:
    # 이름은 `useradd` 와 노드 설정, 그리고 sudoers 자산에 그대로 들어간다.
    # 리터럴로 받을 수 없는 이름은 docker 를 건드리기 전에 막는다(fail-closed).
    for rejected in ("op s", "../root", "-x", "OPS2", "ops2;id", "ops2\nroot"):
        assert _parse("--operator", rejected, "--help").returncode == _USAGE_ERROR, rejected
