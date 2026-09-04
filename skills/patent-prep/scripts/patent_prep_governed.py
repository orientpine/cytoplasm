"""특허 준비 스킬의 변경 명령을 관리자 배포본으로 한정하는 지연 게이트."""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

GOVERNED_LIVE_ROOT: Final = Path("/srv/autophagy-skills/live")
LIVE_ROOT_ENV: Final = "AUTOPHAGY_SKILL_LIVE_ROOT"
SKILL_NAME: Final = "patent-prep"
STALE_COPY_MARKER: Final = "STALE-SKILL-COPY-BLOCK"


def _repo_root() -> Path:
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
    environment = os.environ if env is None else env
    value = environment.get(LIVE_ROOT_ENV, "").strip()
    root = Path(value).expanduser() if value else GOVERNED_LIVE_ROOT
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
