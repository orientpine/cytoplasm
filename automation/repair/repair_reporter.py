"""Lifecycle failure bridge into the release-runtime repair CLI.

오늘 이 다리를 부르는 게이트웨이 호출자는 없다 — Hermes 가 부르는 훅은 여섯 개뿐이고 그중
"에러를 동반한 라이프사이클 실패" 는 없다(결정과 근거는 automation/interop/AGENTS.md).
그럼에도 남기는 이유는 둘이다: 라이프사이클 에러를 가진 호출자가 생기면 그대로 쓸 수 있고,
`_RELEASE_CLI` 가 **수리 CLI 경로의 단일 파이썬 선언**이라 SKILL.md 의 명령·헬스체크 detect
명령이 그것과 갈라지는지를 tests/unit/test_repair_exec_path.py 가 대조하기 때문이다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

from automation.repair.repair_redaction import redact


#: 실행되는 사본은 불변 릴리스 런타임 하나다. 계정 홈 사본은 배포 선언(deploy-manifest)에도
#: 드리프트 프로브의 홈 패턴(scripts/·plugins/)에도 없어 조용히 낡는다 — 2026-09-09 실측으로
#: 7월 세대 사본이 돌고 있었고, 그 세대에는 "닫힌 카드의 재발은 새 카드를 연다"가 없어 소유자의
#: 수리 요청이 종결 카드에 묻혔다. 명시 override 는 남기되 홈 사본으로 폴백하지는 않는다.
_RELEASE_CLI: Final = "/srv/autophagy-agent-current/automation/repair/repair_cli.py"
REPAIR_CLI: Final = Path(os.environ.get("REPAIR_CLI", _RELEASE_CLI)).expanduser()


def record_lifecycle_failure(event_type: str, context: dict[str, str]) -> None:
    """Hand a Hermes error to repair storage without echoing the raw failure."""
    raw_error = context.get("error")
    if not raw_error:
        return
    location = context.get("task_id", context.get("session_id", "hermes-lifecycle"))
    completed = subprocess.run(
        (
            "python3",
            "-I",
            str(REPAIR_CLI),
            "detect",
            "--source",
            f"agent:{event_type}",
            "--location",
            location,
            "--stdin",
        ),
        capture_output=True,
        check=False,
        input=raw_error,
        text=True,
        timeout=90,
    )
    if completed.returncode != 0:
        print(f"repair reporter failed rc={completed.returncode}: {redact(completed.stderr)[:200]}", file=sys.stderr)
