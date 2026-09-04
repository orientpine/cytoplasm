"""report 스킬의 변경 명령을 관리자 배포본으로 한정하는 지연 게이트.

배포된 사본과 실행되는 사본은 다를 수 있으므로, live 마운트가 존재할 때
checkout 사본의 변경 작업을 막는다. automation은 스킬 import 시점에 요구하지 않는다.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

GOVERNED_LIVE_ROOT: Final = Path("/srv/autophagy-skills/live")
LIVE_ROOT_ENV: Final = "AUTOPHAGY_SKILL_LIVE_ROOT"
SKILL_NAME: Final = "report"
STALE_COPY_MARKER: Final = "STALE-SKILL-COPY-BLOCK"


def _repo_root() -> Path:
    """automation을 담은 런타임 루트를 기존 report 스킬 방식으로 찾는다."""
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    here = Path(__file__).resolve()
    for candidate in (*here.parents[2:6], Path("/srv/autophagy-agent-current")):
        if (candidate / "automation" / "skill_mount.py").is_file():
            return candidate
    for parent in here.parents:
        if (parent / "automation" / "skill_mount.py").is_file():
            return parent
    return Path("/srv/autophagy-agent-current")


def refusal(script: Path, *, env: Mapping[str, str] | None = None) -> str | None:
    """live root를 해석하고 governed copy 판정을 지연 수행한다."""
    environment = os.environ if env is None else env
    root = Path(environment.get(LIVE_ROOT_ENV, "").strip()).expanduser() if environment.get(LIVE_ROOT_ENV, "").strip() else GOVERNED_LIVE_ROOT
    try:
        repo = _repo_root()
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from automation.skill_mount import governed_copy_refusal
    except ImportError:
        governed = root / SKILL_NAME / "scripts"
        if governed.is_dir():
            return f"{STALE_COPY_MARKER}: 관리자 배포본 {governed} 에서만 실행합니다"
        return None
    return governed_copy_refusal(SKILL_NAME, script, env=environment)
