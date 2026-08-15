"""발신 계정 라우팅 — mail 스킬이 어느 계정으로 보낼지 결정하는 단일 진실.

triage가 MailOn(KIMM) 단독을 가정하던 시절에는 Gmail 발신이 로컬 임시 초안과
별도 gws 호출로 흩어졌다. 이 모듈은 그 선택을 한 곳으로 모은다:

* 소유자가 명시한 계정을 그대로 쓴다.
* 답장은 원 스레드의 계정을 상속한다(같은 스레드에서 계정이 바뀌지 않는다).
* 둘 다 없으면 **거부한다** — 발신 계정을 대신 골라주는 것은 되돌릴 수 없는
  외부효과이므로 조용한 기본값을 두지 않는다(fail-closed).

순수 함수: I/O·subprocess·네트워크·환경변수 읽기 없음. stdlib 전용.
"""
from __future__ import annotations

from typing import Final, Literal, TypeAlias, get_args

Account: TypeAlias = Literal["gmail", "kimm"]

ACCOUNTS: Final[tuple[Account, ...]] = get_args(Account)

_ALLOWED: Final = ", ".join(ACCOUNTS)

_MISSING_MESSAGE: Final = (
    "발신 계정을 확정할 수 없습니다 — account를 명시하거나 답장 스레드의 계정이 필요합니다"
    + f" (허용: {_ALLOWED}). 기본 계정을 임의로 고르지 않습니다."
)


class AccountSelectionError(ValueError):
    """발신 계정을 결정론적으로 확정할 수 없을 때 발생(fail-closed)."""


def select_account(explicit: str | None, *, reply_to_account: str | None = None) -> Account:
    """발신 계정 하나를 확정하거나 ``AccountSelectionError``로 거부한다.

    Args:
        explicit: 소유자가 명시한 계정. 우선순위가 가장 높다.
        reply_to_account: 답장 대상 스레드가 사용 중인 계정. ``explicit``이
            없을 때만 상속된다.
    """
    if explicit is not None:
        return _parse_account(explicit, field="account")
    if reply_to_account is not None:
        return _parse_account(reply_to_account, field="reply_to_account")
    raise AccountSelectionError(_MISSING_MESSAGE)


def _parse_account(raw: str, *, field: str) -> Account:
    """앞뒤 공백만 제거하고 정확한 소문자 계정명만 통과시킨다."""
    match raw.strip():
        case "gmail":
            return "gmail"
        case "kimm":
            return "kimm"
        case rejected:
            message = f"{field} 값 {rejected!r}은(는) 허용되지 않습니다"
            raise AccountSelectionError(f"{message} (허용: {_ALLOWED} — 정확한 소문자만).")
