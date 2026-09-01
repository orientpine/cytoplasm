"""배포 가능한 모든 스킬은 deploy-skill.sh 가 요구하는 샌드박스 시나리오를 실어야 한다.

`deploy-skill.sh` stage 1 은 `scripts/scenario.sh` 가 없으면 `SANDBOX-BLOCK` 으로 멈추는데,
그 사실은 **소유자 대상 배포가 이미 시작된 뒤에야** 들린다. 2026-08-25 speechtotext 가
그 파일 없이 착지해 배포 사이클 하나를 그렇게 썼다. 같은 질문을 여기서 먼저 한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]


def test_every_skill_ships_a_sandbox_scenario() -> None:
    missing = sorted(
        skill.name
        for skill in (_REPO / "skills").iterdir()
        if skill.is_dir()
        and (skill / "SKILL.md").is_file()
        and not (skill / "scripts" / "scenario.sh").is_file()
    )
    assert not missing, f"deploy-skill.sh 가 SANDBOX-BLOCK 할 스킬(scripts/scenario.sh 없음): {missing}"
