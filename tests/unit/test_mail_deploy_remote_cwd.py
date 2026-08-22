"""mail 배포의 원격 실행은 ssh 가 물려준 cwd 에 기대면 안 된다 — 회귀 고정.

2026-08-18 실측 배경: `run_agent` 가 `sudo -n -u agent -H bash -lc …` 로 원격
명령을 돌리는데, `-H` 는 HOME 만 바꾸고 **cwd 는 ssh 가 놓은 자리 그대로**다.
운영자 계정 홈(`/home/<operator>`)은 `agent` 가 읽을 수 없어서, 초기 디렉터리로
복귀하려는 도구가 거기서 죽는다:

    find: Failed to restore initial working directory: /home/<operator>: Permission denied

`mailon_runtime_release.sh` 의 digest 계산 `find` 가 이걸로 실패했고
`set -euo pipefail` 이 배포 전체를 끌고 내려갔다. 선례는
`automation/memory_curator/deploy.sh` 로, 원격 명령 첫머리에 `cd "$HOME";` 를 둔다.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DEPLOY = _REPO / "skills" / "mail" / "deploy.sh"


def _run_agent_body() -> str:
    source = _DEPLOY.read_text(encoding="utf-8")
    match = re.search(r"^run_agent\(\) \{\n(.*?)^\}", source, re.MULTILINE | re.DOTALL)
    assert match, "run_agent() 정의를 찾지 못했다 — 스캔이 깨졌다"
    return match.group(1)


def test_remote_helper_moves_to_a_readable_directory_first() -> None:
    # Given: 원격 실행 헬퍼의 본문.
    body = _run_agent_body()

    # When: 실제로 ssh 로 넘기는 줄을 본다.
    ssh_lines = [line for line in body.splitlines() if "ssh " in line and "bash -lc" in line]
    assert ssh_lines, "run_agent 가 더 이상 ssh 로 원격 실행하지 않는다 — 계약을 다시 보라"

    # Then: 페이로드보다 먼저 agent 가 읽을 수 있는 디렉터리로 이동한다.
    for line in ssh_lines:
        assert 'cd \\"\\$HOME\\";' in line, (
            "ssh 는 운영자 홈에 cwd 를 남기고 -H 는 HOME 만 바꾼다. "
            "agent 가 읽을 수 없는 cwd 에서 find 같은 도구가 죽으므로 "
            f'페이로드 앞에 cd "$HOME"; 이 있어야 한다: {line.strip()}'
        )


def test_cd_precedes_the_payload() -> None:
    # Given: ssh 로 넘어가는 명령 문자열.
    body = _run_agent_body()

    # Then: cd 가 `$script` 앞에 온다 — 뒤에 붙으면 아무 것도 막지 못한다.
    match = re.search(r'cd \\"\\\$HOME\\";\s*\$script', body)
    assert match, 'cd "$HOME"; 는 $script 바로 앞에 있어야 한다'
