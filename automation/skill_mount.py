#!/usr/bin/env python3
"""governed live 스킬 마운트 경로의 **단일 정의** — cron 래퍼는 여기서만 판정한다.

관리자 배포본은 `/srv/autophagy-skills/live/<skill>` 심링크로만 산다. 그 사실을 아는
곳이 여러 벌이면 한 벌이 낡아도 아무 신호가 없다 — 실제로 다섯 no-agent cron 래퍼
(budget·report·coordination·calendar·research-trends)가 각자 경로를 들고 있다가 마운트가
멀쩡한데도 `not mounted`·import 오류를 냈다(`docs/follow-ups.md`, 2026-08-17). 판정을
한 곳으로 모으면 다음에 경로가 바뀔 때 고칠 자리도 한 곳이다.

같은 정의를 쓰는 이웃: `skill_mount_drift.py`(`--live-root` 기본값)·`skill_mount_probe.sh`
(헬스체크 탐지)·`skill_store.py`(`store_root / "live" / <skill>`, root 배포용이라 import
없이 값으로만 같은 모양을 들고 있다). 어긋나면 `tests/unit/test_skill_mount_definition.py`
의 드리프트 가드가 잡는다.

**fail-closed**: 덮어쓰기가 없으면 governed 기본값으로 해결하고, 그 경로가 없으면 호출자는
"마운트 없음"으로 판정한다 — 이 모듈은 절대 다른 루트(에이전트 자가 스킬 루트
`~/.hermes/skills` 등)로 폴백하지 않는다. 그곳은 계정이 **직접 만든** 스킬이 사는 곳이라
배포본을 담을 수 없고, 이름이 겹치면 승인 게이트를 가릴 수 있다(AGENTS.md 자가 스킬 루트 규칙).

덮어쓰기는 둘 다 테스트·운영 주입용이며 기존 계약을 그대로 유지한다:
  * 스킬별 `<SKILL>_SCRIPTS`(예: `BUDGET_SCRIPTS`) — 래퍼가 이미 갖고 있던 구멍.
  * 공용 `AUTOPHAGY_SKILL_LIVE_ROOT` — `skill_mount_probe.sh` 의
    `HEALTHCHECK_SKILL_LIVE_ROOT` 와 같은 목적(=/srv 가 아닌 트리를 가리켜 검증한다).
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

#: 관리자 배포본이 마운트되는 유일한 루트. `skill_mount_drift.py --live-root` 기본값과 같다.
LIVE_ROOT: Final = Path("/srv/autophagy-skills/live")

#: live 루트 주입(테스트·운영 dry-run 전용). 비어 있으면 governed 기본값이다.
LIVE_ROOT_ENV: Final = "AUTOPHAGY_SKILL_LIVE_ROOT"


def live_root(env: Mapping[str, str] | None = None) -> Path:
    """마운트 루트를 돌려준다 — 주입이 있으면 그것, 없으면 governed 기본값."""
    override = (os.environ if env is None else env).get(LIVE_ROOT_ENV, "").strip()
    return Path(override).expanduser() if override else LIVE_ROOT


def skill_scripts(
    skill: str,
    *,
    env_var: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """마운트된 `<skill>` 의 `scripts/` 디렉터리 경로(존재 여부는 호출자가 판정한다).

    해결 순서: 스킬별 덮어쓰기(`env_var`) → live 루트 주입 → governed 기본값.
    경로를 돌려줄 뿐 마운트를 확인하지 않는다 — 판정은 각 래퍼가 자기 진입점
    (`<cli>.py` 존재, import 성공)으로 내리고, 없으면 미마운트로 fail-closed 한다.
    """
    environment = os.environ if env is None else env
    if env_var:
        override = environment.get(env_var, "").strip()
        if override:
            return Path(override).expanduser()
    return live_root(environment) / skill / "scripts"
