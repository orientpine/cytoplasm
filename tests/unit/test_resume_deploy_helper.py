"""워처가 escalate 할 수 있는 유일한 명령 — 무엇을 거부하는지가 그 가치다.

⑦ 워처는 `agent` 로 돌고 그 계정에는 sudo 가 없다. 반면 배포 파이프라인은 단계마다
escalate 한다(샌드박스=peer, 릴리스 수렴=ops). 그래서 승인을 읽고도 끝내지 못했다
(2026-08-02 실측: `sudo: a password is required`).

`agent` 에 ops/peer 쉘을 주는 것은 답이 아니다 — 이 리포는 CI 러너에 대해 이미 같은
질문에 답했다: *"질문은 워크플로가 안전한가가 아니라 그것이 실행되는 계정이 어디까지
닿는가다."* `agent` 는 LLM 이 지시하는 코드를 실행하는 계정이고, `ops` 는 수리 push 키와
릴리스 설치 권한에 닿는다.

그래서 절대경로 하나, 인자 모양 하나. 여기서 고정하는 것은 그 좁음이다.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from automation.node_asset_renderer import render_asset
from automation.node_config import default_node_config

_HELPER = Path(__file__).resolve().parents[2] / "automation" / "libexec" / "autophagy-resume-deploy"


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["NODE_RELEASE_CURRENT"] = str(default_node_config().release_current)
    if env:
        environment.update(env)
    return subprocess.run(
        ("bash", str(_HELPER), *args), capture_output=True, text=True, check=False, env=environment
    )


def test_it_refuses_anything_that_is_not_a_skill_name() -> None:
    """이름 자리에 들어온 것이 이름이 아니면 파이프라인을 시작조차 하지 않는다."""
    for hostile in (
        "demo; rm -rf /",
        "../../etc/passwd",
        "demo --fresh",
        "--provenance-off",
        "DEMO",
        "",
        "x",
    ):
        result = _run("--skill", hostile)
        assert result.returncode != 0, hostile
        assert "refusing" in result.stderr or "usage" in result.stderr, hostile


def test_it_refuses_the_reserved_publish_prefix() -> None:
    """deploy 레코드와 publish 레코드가 같은 pending 경로를 다투는 이름이다."""
    result = _run("--skill", "publish-demo")
    assert result.returncode != 0
    assert "reserved" in result.stderr


def test_it_refuses_extra_or_missing_arguments() -> None:
    for argv in ((), ("--skill",), ("--skill", "demo", "--fresh"), ("demo",), ("--force", "demo")):
        result = _run(*argv)
        assert result.returncode != 0, argv


def test_the_argument_shape_matches_the_sudoers_grant() -> None:
    """grant 는 `--skill *` 하나만 연다 — 헬퍼가 다른 모양을 받으면 둘이 어긋난다."""
    grant_path = (
        Path(__file__).resolve().parents[2]
        / "automation" / "sudoers.d" / "autophagy-supply-chain-resume"
    )
    grant = render_asset(grant_path, default_node_config())
    assert "/usr/local/libexec/autophagy-resume-deploy --skill *" in grant
    assert "(root)" in grant and "NOPASSWD" in grant
    assert "ops" not in grant.split("agent ALL=", 1)[1], "agent 에게 ops 를 열어주면 안 된다"


def test_it_rebuilds_the_environment_rather_than_passing_it_through() -> None:
    """DEPLOY_ALLOW_UNPUSHED 는 provenance 가드를 끈다 — 이 경로로 넘어가면 안 된다."""
    body = _HELPER.read_text(encoding="utf-8")
    assert "env -i" in body, "환경을 새로 세우지 않으면 우회 변수가 그대로 넘어간다"
    for bypass in ("DEPLOY_ALLOW_UNPUSHED", "SKILL_SRC_DIR", "--sandbox-only", "--fresh"):
        assert f"${bypass}" not in body and f"{bypass}=" not in body.replace("DEPLOY_ALLOW_UNPUSHED,", ""), bypass


def test_it_sources_the_pipeline_from_the_sealed_release_only() -> None:
    """소스가 릴리스가 아니면 origin/main 바이트 일치 보장이 통째로 사라진다."""
    body = _HELPER.read_text(encoding="utf-8")
    assert 'RELEASE_CURRENT="$NODE_RELEASE_CURRENT"' in body
    rendered = render_asset(_HELPER, default_node_config())
    assert 'RELEASE_CURRENT="/srv/autophagy-agent-current"' in rendered
    assert 'PIPELINE="$RELEASE_CURRENT/automation/deploy-skill.sh"' in body


def test_it_execs_so_the_exit_code_survives_verbatim() -> None:
    """8(lease)과 9(owner stop)는 정반대 뜻이라 뭉개면 승인이 사라지거나 되살아난다."""
    body = _HELPER.read_text(encoding="utf-8")
    assert "exec /usr/bin/env -i" in body


def test_a_missing_release_is_reported_not_guessed(tmp_path: Path) -> None:
    """릴리스가 없는 개발 머신에서 돌리면 조용히 다른 것을 실행하지 않고 멈춘다."""
    result = _run("--skill", "demo")
    if Path("/srv/autophagy-agent-current/automation/deploy-skill.sh").exists():
        return  # 노드에서 실행 중 — 이 경로는 개발 머신 전용 단언이다
    assert result.returncode != 0
    assert "release pipeline is absent" in result.stderr


def test_it_never_lets_python_write_bytecode_into_the_sealed_release() -> None:
    """root 는 권한 검사를 우회한다 — 파이프라인이 릴리스를 스스로 오염시켰다.

    릴리스 트리는 `dr-xr-xr-x root:root` 로 봉인돼 있어 agent 도 ops 도 쓸 수 없다.
    그런데 이 헬퍼는 파이프라인을 root 로 띄우고, CPython 은 import 한 모듈 옆에
    `__pycache__/*.pyc` 를 남긴다. root 에게는 읽기 전용 디렉터리도 막히지 않는다.

    그 결과가 release-provenance 의 자기 차단이다 — 트리가 커밋과 정확히 같아야
    하는데 커밋에 없는 .pyc 가 생기므로, **첫 실행이 두 번째 실행을 영구히 막는다**.
    2026-08-03 실측: `DEPLOY-BLOCK: release has files absent from the commit:
    automation/__pycache__/__init__.cpython-312.pyc, …` (root:root, 파이프라인 실행 시각).

    환경을 새로 세우는 바로 그 자리에서 바이트코드 기록을 끈다.
    """
    body = _HELPER.read_text(encoding="utf-8")
    assert "PYTHONDONTWRITEBYTECODE=1" in body, (
        "root 로 도는 파이프라인이 봉인된 릴리스에 .pyc 를 남기면 다음 provenance 검사가 막힌다"
    )
