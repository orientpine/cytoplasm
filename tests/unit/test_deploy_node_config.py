"""노드 호스트를 쓰는 배포 스크립트는 노드 설정을 스스로 불러와야 한다.

`host="${DEPLOY_SSH_HOST:-$NODE_DEPLOY_SSH_HOST}"` 는 `node_config_sh.py --print-env` 를
eval 한 뒤에만 성립한다. 그 줄을 빠뜨리면 `set -u` 아래서 **unbound variable** 로 즉사하는데,
배포 스크립트는 평소에 실행되지 않으므로 그 사실이 첫 배포까지 숨는다 — 2026-08-26
speechtotext 워처 배포가 정확히 거기서 멈췄다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]


def test_every_deploy_script_using_the_node_host_loads_the_node_config() -> None:
    broken = [
        script.relative_to(_REPO).as_posix()
        for script in (
            *sorted(_REPO.glob("skills/*/deploy.sh")),
            *sorted(_REPO.glob("automation/*/deploy.sh")),
        )
        if "NODE_DEPLOY_SSH_HOST" in (text := script.read_text(encoding="utf-8"))
        and "node_config_sh.py" not in text
    ]
    assert not broken, (
        "NODE_DEPLOY_SSH_HOST 를 쓰면서 node_config_sh.py 를 eval 하지 않는다 — "
        f"set -u 아래서 unbound variable 로 죽는다: {broken}"
    )
