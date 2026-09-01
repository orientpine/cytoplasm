"""마운트된 스킬이 repo(`automation`)를 찾는 **단일 정의**.

`meeting_cli` 와 `meeting_project` 가 각자 해석하던 것을 합쳤다. 갈라져 있던 동안
`meeting_project` 쪽만 `parents[3]` 깊이 추측을 들고 있었고, 라이브 마운트 실경로가
`/srv/autophagy-skills/releases/meeting/<digest>/scripts` 라 그 자리에 repo 가 없어
`ModuleNotFoundError` 로 죽었다 — 그 실패가 `BOARD-FETCH-FAIL` 로 삼켜져 양식·action item
원장·미처리 전사본 조회가 **전부 조용히 무력**했다(2026-08-28 노드 실측).

`RELEASE_CURRENT`/`MIRROR_CHECKOUT` 은 기본 인자가 아니라 **모듈 상수**다: 기본값으로
굳히면 정의 시점에 박혀 운영·시험에서 다른 릴리스를 끼울 수 없다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

RELEASE_CURRENT: Final = Path("/srv/autophagy-agent-current")
MIRROR_CHECKOUT: Final = Path("/srv/autophagy-agents")


def runtime_root(
    here: Path | None = None,
    *,
    current: Path | None = None,
    mirror: Path | None = None,
) -> Path:
    """The checkout carrying ``automation``, not a depth guess from the mounted skill."""
    override = os.environ.get("AUTOPHAGY_RUNTIME_ROOT") or os.environ.get("AUTOPHAGY_REPO_ROOT")
    if override:
        return Path(override).expanduser()
    release = current if current is not None else RELEASE_CURRENT
    resident = mirror if mirror is not None else MIRROR_CHECKOUT
    origin = (here or Path(__file__)).resolve()
    for candidate in (*origin.parents[2:6], release, resident):
        if (candidate / "automation" / "drive_outputs.py").is_file():
            return candidate
    return release if (release / "automation").is_dir() else resident
