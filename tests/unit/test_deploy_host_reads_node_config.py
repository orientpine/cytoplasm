"""설정이 **있으면 읽는가** — 배포 호스트 해석의 반대 방향.

`test_deploy_node_config.py` 는 `NODE_DEPLOY_SSH_HOST` 를 **쓰는** 스크립트만 본다:
그 변수를 쓰면서 `node_config_sh.py` 를 eval 하지 않으면 `set -u` 아래서 죽기 때문이다.
그런데 그 그물은 **변수를 아예 쓰지 않는** 스크립트를 그대로 통과시킨다.
`host="${DEPLOY_SSH_HOST:-}"` 만 보는 스크립트는 unbound 로 죽지도 않고, 대신
`~/.hermes/node.toml` 에 호스트가 멀쩡히 설정돼 있어도 exit 3 으로 배포를 거부한다 —
2026-08-27 배포에서 mail·todo 가 그렇게 두 번 걸려 변수를 손으로 줘야 했다.

`test_deploy_host_fail_closed_all.py` 는 "**아무 데도 없을 때** 자기 이름으로 거부하는가"
만 검사하므로 이 축(설정이 있으면 읽는가)은 어느 가드에도 없었다. 여기서 고정한다.

**왜 별도 파일인가**: 배포 호스트에는 서로 다른 질문이 셋 있고(unbound 로 죽는가 /
아무 데도 없을 때 거부하는가 / 설정이 있으면 읽는가) 각각을 독립적으로 재생할 수 있어야
한다. 바로 옆 `test_deploy_host_fail_closed.py` 는 FS3 정산 레코드(`task-6`)가 출력 해시로
고정하고 있어 케이스를 더할 수 없었고, 그래서 두 번째 축이 이미
`test_deploy_host_fail_closed_all.py` 로 갈라져 있다(`tests/AGENTS.md`). 세 번째 축도 같은
규칙으로 자기 파일을 갖는다.

목록은 손으로 적지 않고 추적 중인 `deploy.sh` 를 훑어 만든다 — 새 배포 스크립트가 등록을
잊혀 빠지는 것이 이 부채가 처음 생긴 방식이다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]


def _tracked_deploy_scripts() -> tuple[str, ...]:
    listed = subprocess.run(
        ["git", "ls-files", "*deploy.sh"],
        cwd=str(_REPO), capture_output=True, text=True, check=True,
    )
    return tuple(sorted(line for line in listed.stdout.splitlines() if line.strip()))


def test_every_deploy_script_resolving_a_host_reads_the_node_config() -> None:
    broken: list[str] = []
    for relative in _tracked_deploy_scripts():
        text = (_REPO / relative).read_text(encoding="utf-8")
        if "DEPLOY_SSH_HOST" not in text:
            continue  # 다른 노드 변수로 해석한다(예: rag_stack) — 이 축의 대상이 아니다.
        if "node_config_sh.py" in text and "NODE_DEPLOY_SSH_HOST" in text:
            continue
        broken.append(relative)
    assert not broken, (
        "DEPLOY_SSH_HOST 를 해석하면서 노드 설정을 읽지 않는다 — node.toml 이 설정돼 "
        f"있어도 exit 3 으로 거부하고 변수를 손으로 줘야 한다: {broken}"
    )
