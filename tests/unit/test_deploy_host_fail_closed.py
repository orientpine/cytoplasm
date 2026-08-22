"""배포 스크립트가 호스트 미지정을 자기 이름으로 거부하는가.

`skills/mail/deploy.sh` 의 폴백은 리터럴 플레이스홀더 `<primary-node>` 였다. 환경변수를
빠뜨리면 ssh 가 해석되지 않는 이름으로 접속을 시도하고, 나오는 오류는 DNS 를 가리킨다 —
실제 원인("변수를 안 줬다")은 어디에도 없다. 엉뚱한 노드로 배포되지는 않으므로 안전 문제는
아니지만, 배포 중에 한 번 더 막히는 마찰이고 원인 추적이 길어진다.

검증은 스크립트를 **실행**해서 한다 — 텍스트 grep 은 fail-closed 를 증명하지 못한다.
`DEPLOY_SSH_HOST` 가 비어 있으면 ssh 이전 단계에서 멈춰야 하므로 외부효과는 없다.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_FAIL_CLOSED = ("skills/mail/deploy.sh", "skills/wiki/deploy.sh")


@pytest.mark.parametrize("relative", _FAIL_CLOSED)
def test_missing_host_is_refused_before_any_ssh(relative: str) -> None:
    # Given: the deploy script with no DEPLOY_SSH_HOST in the environment.
    environment = {key: value for key, value in os.environ.items() if key != "DEPLOY_SSH_HOST"}
    environment["PATH"] = "/usr/bin:/bin"

    # When: it is run.
    result = subprocess.run(
        ["bash", str(_REPO / relative)],
        capture_output=True, text=True, check=False, timeout=60,
        cwd=str(_REPO), env=environment,
    )

    # Then: it stops with its own diagnosis rather than handing an unresolvable
    # placeholder to ssh, and it names the fix.
    assert result.returncode == 3, result.stdout + result.stderr
    assert "DEPLOY-BLOCK" in result.stderr
    assert "DEPLOY_SSH_HOST" in result.stderr


@pytest.mark.parametrize("relative", _FAIL_CLOSED)
def test_the_placeholder_hostname_is_gone(relative: str) -> None:
    # Then: no path through the script can still reach ssh with the literal placeholder.
    assert "<primary-node>" not in (_REPO / relative).read_text(encoding="utf-8")
