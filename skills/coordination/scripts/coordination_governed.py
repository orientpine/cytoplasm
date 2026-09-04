"""Governed-copy guard for coordination mutations.

배포된 스킬과 실행 중인 사본을 혼동하지 않도록 automation 판정을 지연한다.
스킬 스크립트는 import 시점에 automation을 보장할 수 없기 때문이다.
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
SKILL_NAME: Final = "coordination"
STALE_COPY_MARKER: Final = "STALE-SKILL-COPY-BLOCK"


def _repo_root() -> Path:
    """자동화 모듈을 가진 릴리스 런타임 루트를 찾는다."""
    override = os.environ.get("AUTOPHAGY_REPO_ROOT")
    if override:
        return Path(override).expanduser()
    candidates = [Path("/srv/autophagy-agent-current")]
    here = Path(__file__).resolve()
    candidates.extend(candidate for candidate in here.parents if (candidate / "automation" / "skill_mount.py").is_file())
    for candidate in candidates:
        if (candidate / "automation" / "skill_mount.py").is_file():
            return candidate
    return candidates[0]


def refusal(script: Path, *, env: Mapping[str, str] | None = None) -> str | None:
    """정식 live 사본이 아닌 변경 실행을 거부한다."""
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        mount = importlib.import_module("automation.skill_mount")
    except ImportError:
        environment = os.environ if env is None else env
        live_root = Path(environment.get(LIVE_ROOT_ENV, "") or GOVERNED_LIVE_ROOT).expanduser()
        governed = live_root / SKILL_NAME / "scripts"
        if not governed.is_dir():
            return None
        return (
            f"{STALE_COPY_MARKER}: coordination 은 관리자 배포본 {governed} 에서만 실행한다"
            f" — 이 사본 {script} 은 배포 루트 밖이다"
        ) if governed.resolve() != script.resolve().parent else None
    return mount.governed_copy_refusal(SKILL_NAME, script, env=env)
