"""Split mailon's exit 2 into the two failures it actually folds together.

WHY (2026-08-13~18): the owner's paper mail was blocked for five days and the cause was
reported the whole time as 「기관메일 인증 실패」. It was not — the credentials were fine;
the real causes were a login-completion misjudgement (PR #148) and a mailbox SPA startup
race (PR #152). The mechanism of the misdiagnosis was this one line: mailon's exit 2 means
``auth_or_browser_error``, and the wrapper folded it unconditionally into ``auth_error``
with guidance whose first sentence *asserts* an authentication failure. That sent the
repair in the opposite direction, as far as recommending a password rotation.

The signal to tell them apart already existed (``classify_stderr`` reads mailon's stderr
into a ``failure_signature``); it simply was not consulted. The process exit code stays 2
for every branch, so callers that switch on it see no change — only the machine-readable
``error_code`` and the human guidance get more precise.
"""
from __future__ import annotations

from typing import Final

from mailon_interface import REAUTH_GUIDANCE

#: Signatures that really do mean the credential/login flow failed.
AUTH_SIGNATURES: Final = frozenset({"login_error", "login_dom_ipt_id"})
#: Signatures that mean the browser or the page never got far enough to try.
BROWSER_SIGNATURES: Final = frozenset(
    {"browser_error", "timeout", "inbox_folder_uid_selector"}
)

BROWSER_GUIDANCE: Final = (
    "브라우저·페이지 단계 실패(mailon exit 2 = auth_or_browser_error, stderr 시그니처는 "
    "브라우저 계열). 자격증명 문제가 아니므로 재인증·비밀번호 교체로 가지 말 것 — "
    "실측 선례: 2026-08 compose 기동 경쟁이 5일간 인증 실패로 오진됐다. 확인 순서: "
    "① 잔여 chrome 정리(pkill -u agent -f chrome) 후 재시도 "
    "② agent-browser 실행 가능 여부와 HEADLESS 설정 "
    "③ 그래도 재현되면 stderr 시그니처를 그대로 증적에 남기고 selector·대기시간을 본다."
)

AMBIGUOUS_GUIDANCE: Final = (
    "mailon exit 2 는 인증 실패와 브라우저 실패를 한 코드로 접는데, 이번 stderr 에는 "
    "어느 쪽인지 가릴 시그니처가 없다 — 둘 중 하나로 단정하지 말 것. 먼저 stderr 전문을 "
    "증적에 남기고, 브라우저 계열(잔여 chrome·agent-browser·타임아웃)을 배제한 뒤에만 "
    "재인증 절차로 넘어간다. 단정이 수리 방향을 반대로 돌린 실측 선례가 있다(5일)."
)


def classify_exit_two(failure_signature: str) -> tuple[str, str]:
    """Return the ``(error_code, guidance)`` for a mailon exit 2 with this signature.

    An unrecognised signature deliberately resolves to the undecided code rather than to
    ``auth_error``: fail-closed here means refusing to name a cause, not guessing the
    likelier one.
    """
    if failure_signature in AUTH_SIGNATURES:
        return "auth_error", REAUTH_GUIDANCE
    if failure_signature in BROWSER_SIGNATURES:
        return "browser_error", BROWSER_GUIDANCE
    return "auth_or_browser_error", AMBIGUOUS_GUIDANCE
