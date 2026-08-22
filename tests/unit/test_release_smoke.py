"""수렴 직후 스모크는 **리컨실러의 권한**으로 통과할 수 있어야 한다.

`apply_release_update` 는 릴리스를 설치하고 게이트웨이 쌍을 재시작한 뒤 스모크를 돌려
"이 릴리스를 유지할지"를 판정한다. 그런데 그 자리에 꽂혀 있던 것은
`automation/deploy-smoke.sh` 였다 — **자체 프로비저너와 자체 일일 타이머를 가진 독립
스크립트**이고(`provision-deploy-smoke.sh`, `autophagy-deploy-smoke.timer`), 하는 일은
`deploy-skill.sh hello-autophagy --sandbox-only`, 즉 **peer 계정에 실제로 staging** 하는
것이다.

리컨실러는 `User=ops` 로 돌고 ops 의 sudo 권한은 정확히 5줄, 전부 `(root) NOPASSWD` 인
릴리스 헬퍼뿐이다 — agent 로도 peer 로도 갈 수 없다. 그래서 이 스모크는 **구조적으로
통과 불가능**했다(실측 2026-08-19: `sudo: a password is required` →
`SELF-SKILL-COLLISION-BLOCK`, 그리고 ops 의 `~/.hermes/deploy-smoke/tick.json` 은 애초에
존재한 적이 없다). 결과는 매 수렴이 설치 직후 롤백되는 것이었다.

두 스모크는 목적도 권한도 다르므로 합치지 않는다. 일일 sandbox 스모크는 그대로 두고,
리컨실러에는 자기 권한으로 실제로 의미 있는 것을 확인하는 스모크를 준다.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
_SMOKE: Final = _REPO / "automation" / "release-smoke.sh"


def _run(release: Path, *, gateway: Path, store_root: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["RELEASE_SMOKE_CURRENT"] = str(release)
    env["RELEASE_SMOKE_GATEWAY_HELPER"] = str(gateway)
    env["RELEASE_SMOKE_STORE_ROOT"] = str(store_root)
    env["RELEASE_SMOKE_SUDO"] = ""  # 테스트는 스스로에게 sudo 를 주지 않는다
    return subprocess.run(
        ("bash", str(_SMOKE)), capture_output=True, text=True, check=False, env=env
    )


def _stub(path: Path, body: str) -> Path:
    _ = path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def node(tmp_path: Path) -> tuple[Path, Path, Path]:
    """릴리스 심링크 + 그 안의 automation 패키지 + 게이트웨이 헬퍼 스텁."""
    sha = "a" * 40
    generation = tmp_path / "releases" / sha
    automation = generation / "automation"
    automation.mkdir(parents=True)
    _ = (automation / "__init__.py").write_text("", encoding="utf-8")
    _ = (automation / "importable.py").write_text("VALUE = 1\n", encoding="utf-8")
    # 스토어 검증은 릴리스 자신의 release_store.py 로 한다 — 설치본이 아니라.
    _ = (automation / "release_store.py").write_text(
        "import sys\nsys.exit(0 if '--verify' in sys.argv else 2)\n", encoding="utf-8"
    )
    current = tmp_path / "autophagy-agent-current"
    current.symlink_to(generation, target_is_directory=True)
    gateway = _stub(tmp_path / "gateway-pair", 'test "$1" = health')
    return current, gateway, tmp_path


def test_a_healthy_release_passes_without_touching_another_account(
    node: tuple[Path, Path, Path],
) -> None:
    current, gateway, store_root = node

    result = _run(current, gateway=gateway, store_root=store_root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RELEASE-SMOKE-PASS" in result.stderr
    # 이것이 이 스크립트의 존재 이유다: 다른 계정으로 넘어가지 않는다. 주석은 옛 스모크가
    # 무엇을 했는지 설명하므로 **실행되는 줄만** 본다(유닛 지시어 검사와 같은 방식).
    executable = "\n".join(
        line
        for line in _SMOKE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert "-u agent" not in executable and "-u peer" not in executable, (
        "ops 는 agent·peer 로 sudo 할 수 없다 — 그 의존이 옛 스모크를 통과 불가능하게 만들었다"
    )
    assert "deploy-skill.sh" not in executable, "스킬 배포는 이 스모크의 일이 아니다"


def test_a_corrupt_release_tree_fails(node: tuple[Path, Path, Path]) -> None:
    """트리가 온전하지 않으면 모든 cron 워처가 조용히 죽는다 — 그 릴리스를 유지하면 안 된다."""
    current, gateway, store_root = node
    _ = (current.resolve() / "automation" / "importable.py").write_text(
        "def truncated(\n", encoding="utf-8"
    )

    result = _run(current, gateway=gateway, store_root=store_root)

    assert result.returncode != 0
    assert "RELEASE-SMOKE-FAIL" in result.stderr


def test_a_gateway_that_did_not_come_back_fails(node: tuple[Path, Path, Path]) -> None:
    """성공 경로는 재시작만 하고 health 를 확인하지 않았다 — 그 공백을 여기서 메운다."""
    current, gateway, store_root = node
    _ = _stub(gateway, "exit 1")

    result = _run(current, gateway=gateway, store_root=store_root)

    assert result.returncode != 0
    assert "RELEASE-SMOKE-FAIL" in result.stderr


def test_a_store_that_disagrees_with_the_pointer_fails(node: tuple[Path, Path, Path]) -> None:
    current, gateway, store_root = node
    _ = (current.resolve() / "automation" / "release_store.py").write_text(
        "import sys\nsys.exit(2)\n", encoding="utf-8"
    )

    result = _run(current, gateway=gateway, store_root=store_root)

    assert result.returncode != 0
    assert "RELEASE-SMOKE-FAIL" in result.stderr


def test_the_reconciler_points_at_this_smoke_not_the_daily_sandbox_one() -> None:
    """배선이 어긋나면 위 테스트가 전부 통과해도 프로덕션은 그대로 롤백된다."""
    cli = (_REPO / "automation" / "deploy_reconcile_cli.py").read_text(encoding="utf-8")
    assert "release-smoke.sh" in cli
    assert "deploy-smoke.sh" not in cli, (
        "일일 sandbox 스모크는 peer staging 을 하므로 ops 권한으로는 영원히 실패한다"
    )
