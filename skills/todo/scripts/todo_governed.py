"""todo 변경 명령의 배포 사본을 판정한다.

스킬 스크립트는 로딩 시점에 automation을 가져올 수 없으므로, 실제 변경 직전에
승인 런타임의 resolver를 통해 canonical 판정을 지연 호출한다.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from todo_approval_runtime import _repo_module

GOVERNED_LIVE_ROOT: Final = Path("/srv/autophagy-skills/live")
LIVE_ROOT_ENV: Final = "AUTOPHAGY_SKILL_LIVE_ROOT"
SKILL_NAME: Final = "todo"
STALE_COPY_MARKER: Final = "STALE-SKILL-COPY-BLOCK"


def refusal(script: Path, *, env: Mapping[str, str] | None = None) -> str | None:
    """변경 명령이 관리자 배포본 밖의 낡은 사본에서 실행되지 않게 한다."""
    try:
        mount = _repo_module("skill_mount")
    except Exception:
        environment = os.environ if env is None else env
        override = environment.get(LIVE_ROOT_ENV, "").strip()
        live_root = Path(override).expanduser() if override else GOVERNED_LIVE_ROOT
        governed = live_root / SKILL_NAME / "scripts"
        if not governed.is_dir():
            return None
        if governed.resolve() == script.resolve().parent:
            return None
        return f"{STALE_COPY_MARKER}: todo 은 관리자 배포본 {governed} 에서만 실행한다"
    return mount.governed_copy_refusal(SKILL_NAME, script, env=env)
