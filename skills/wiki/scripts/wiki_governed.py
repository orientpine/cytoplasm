"""Runtime governed-copy guard for mutating wiki commands.

배포된 스킬 사본과 실행 중인 사본을 혼동하지 않도록 automation 판정을 지연 import한다.
"""
from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

GOVERNED_LIVE_ROOT: Final = Path("/srv/autophagy-skills/live")
LIVE_ROOT_ENV: Final = "AUTOPHAGY_SKILL_LIVE_ROOT"
SKILL_NAME: Final = "wiki"
STALE_COPY_MARKER: Final = "STALE-SKILL-COPY-BLOCK"


def _runtime_root(script: Path) -> Path:
    """automation을 가진 릴리스 런타임 루트를 찾아 lazy import를 준비한다."""
    candidates = [Path("/srv/autophagy-agent-current"), *script.resolve().parents]
    for candidate in candidates:
        if (candidate / "automation" / "skill_mount.py").is_file():
            return candidate
    return Path("/srv/autophagy-agent-current")


def refusal(script: Path, *, env: Mapping[str, str] | None = None) -> str | None:
    """변경 명령이 관리자 governed 사본 밖에서 실행되는지 판정한다.

    automation을 import할 수 없는 배포 노드에서도 live wiki 사본이 있으면 fail-closed한다.
    """
    root = _runtime_root(script)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        canonical = importlib.import_module("automation.skill_mount").governed_copy_refusal
    except ImportError:
        environment = os.environ if env is None else env
        live = Path(environment.get(LIVE_ROOT_ENV, "").strip()).expanduser() if environment.get(LIVE_ROOT_ENV, "").strip() else GOVERNED_LIVE_ROOT
        governed = live / SKILL_NAME / "scripts"
        if not governed.is_dir():
            return None
        if governed.resolve() == script.resolve().parent:
            return None
        return f"{STALE_COPY_MARKER}: wiki 은 관리자 배포본 {governed} 에서만 실행한다"
    return canonical(SKILL_NAME, script, env=env)
