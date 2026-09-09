"""설치기가 판정하지 못한 것과, 그래서 소유자에게 넘기는 것.

`automation/install/discord_check.py` 의 ``main`` 은 서로 다른 두 가지를 rc 로 구분한다 —
rc=1 은 "검사했고 전제가 충족되지 않았다"이고, rc=2 는 "``DISCORD_BOT_TOKEN`` 이 환경에
없어 아무것도 검사하지 못했다"이다. 뒤엣것을 FAIL 로 접으면
`automation/install/apply.py` 의 ``apply_plan`` 이 거기서 break 해 계획의 나머지 전부가
실행되지 않는데, `docs/guide/install.md` §6 은 바로 그 설치(``sudo`` 가 환경변수를 지운
설치)를 허용한다 — "Discord 전제는 설치 자체의 전제가 아니다".

판정과 그 뒷일이 한 모듈에 있는 이유는 둘이 같은 문장의 두 조각이기 때문이다. WARN 은
"이 항목을 아무도 닫지 않았다"는 뜻이고, 그 상태를 만드는 쪽과 사람에게 넘기는 문구가
갈라지면 한쪽만 고쳐진 채로 남는다. 순수 로직이라 I/O 도 시계도 없다 — 경로는 인자로 받는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

from automation.install.checks import CheckResult, Status

DISCORD_READINESS: Final = "discord-readiness"

_TOKEN_ABSENT: Final = 2
_TOKEN_ABSENT_DETAIL: Final = (
    "TOKEN-ABSENT: DISCORD_BOT_TOKEN이 설치기 환경에 없어 판정하지 못했다 "
    "(discord_check.py rc=2). sudo가 환경변수를 지우면 그렇게 되며, Discord 전제는 설치 "
    "자체의 전제가 아니므로 설치는 계속한다 — 아래 「소유자 확인 절차」로 직접 확인한다"
)
FOLLOW_UP_HEADING: Final = "소유자 확인 절차 — 설치기가 판정하지 못한 항목이 있다:"


def discord_readiness(code: int) -> CheckResult:
    """rc를 판정으로 옮긴다 — 판정 불가는 실패가 아니라 경고다."""
    if code == _TOKEN_ABSENT:
        return CheckResult(DISCORD_READINESS, Status.WARN, _TOKEN_ABSENT_DETAIL)
    # 문구는 그대로 둔다 — docs/guide/install.md §8의 증상 표가 이 문자열을 인용한다.
    status = Status.PASS if code == 0 else Status.FAIL
    return CheckResult(DISCORD_READINESS, status, f"discord_check.py rc={code}")


def discord_usage_refused(code: object) -> CheckResult:
    """argv 거부는 판정이 아니라 부름 자체의 실패 — 경고로 낮추지 않는다."""
    # SystemExit(2) 와 토큰 부재의 rc=2 는 **같은 숫자**다. 그 값을 판정표에 넣으면
    # "설치기가 만든 명령이 틀렸다" 가 "소유자의 토큰이 없다" 로 둔갑하고, 설치는 거짓
    # 이야기 위에서 계속 간다.
    return CheckResult(
        DISCORD_READINESS,
        Status.FAIL,
        f"USAGE-REFUSED: discord_check.py가 argv를 거부했다 (SystemExit {code}) — "
        "설치기가 넘긴 --config 값을 확인한다",
    )


def follow_up(results: Sequence[CheckResult], *, discord_config: Path) -> str:
    """WARN으로 남은 항목을 닫는 절차를 렌더한다; 남은 것이 없으면 빈 문자열."""
    warned = [result for result in results if result.status is Status.WARN]
    if not warned:
        return ""
    lines = [FOLLOW_UP_HEADING]
    for result in warned:
        # 절차를 적어 두지 않은 경고도 이름과 사유는 낸다 — 제목만 있고 항목이 없는 블록은
        # "남은 일이 없다"로 읽힌다.
        lines.extend(
            _discord_steps(discord_config)
            if result.name == DISCORD_READINESS
            else (f"- {result.name}: {result.detail}",)
        )
    return "\n".join(lines)


def _discord_steps(config: Path) -> tuple[str, ...]:
    return (
        f"- {DISCORD_READINESS}: 이 노드에서 직접 확인한다.",
        "    set -a; . ~/.env.secrets; set +a",
        f"    python3 automation/install/discord_check.py --config {config}",
        "  rc=0이면 전제가 충족된 것이다. 승인 카드가 실제로 도착하는지는 봇과의 DM에서",
        "  확인한다 — DM 표면은 그 검사도 확인하지 못한다(UNVERIFIED-SURFACE).",
    )
