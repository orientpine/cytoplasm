"""mailon 로그인 페이지 열기 시간 초과 회귀 — 폼이 이미 떠 있으면 로그인을 계속한다.

2026-10-02 노드 실측: `agent-browser open https://mailon.kr/` 은 페이지 load 완료까지
기다리는데 그 동작 제한이 25초이고, mailon.kr 은 같은 시각에 5~23초가 걸리다가 자주
그 제한을 넘었다(6회 중 3회 `Operation timed out`). 그때도 브라우저는 이미
`/integrated/login` 에 있었고 `wait --load domcontentloaded` 뒤 로그인 폼이 6회 모두
렌더됐다 — 실패는 페이지가 아니라 `open` 의 완료 판정이었다. 그래서 그 하나의 오류만,
로그인 페이지에 도달한 경우에만 견딘다. 새 검사라 기존 로그인 판정 테스트와 파일을 나눈다.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parents[2] / "skills" / "mail" / "vendor"


def _install_third_party_stubs() -> None:
    if "pyotp" not in sys.modules:
        pyotp = types.ModuleType("pyotp")
        pyotp.TOTP = lambda *_a, **_k: None  # type: ignore[attr-defined]
        sys.modules["pyotp"] = pyotp
    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *_a, **_k: None  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv


_install_third_party_stubs()
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from mailon import login as login_mod  # noqa: E402
from mailon.browser import BrowserError  # noqa: E402

_LOGIN_URL = "https://mailon.kr/"
_TIMEOUT = (
    "agent-browser failed (1): cmd=open https://mailon.kr/ stderr=✗ Operation timed out. "
    "The page may still be loading or the element may not exist."
)


class _Browser:
    def __init__(self, *, open_error: str | None, url: str) -> None:
        self._open_error = open_error
        self._url = url
        self.url_reads = 0

    def open(self, url: str) -> None:
        self.opened = url
        if self._open_error is not None:
            raise BrowserError(self._open_error)

    def current_url(self) -> str:
        self.url_reads += 1
        return self._url


class _Config:
    login_url = _LOGIN_URL


def test_a_timed_out_open_that_reached_the_login_form_continues() -> None:
    browser = _Browser(open_error=_TIMEOUT, url="https://mailon.kr/integrated/login")

    login_mod._open_login_page(browser, _Config())  # type: ignore[arg-type]

    assert browser.opened == _LOGIN_URL
    assert browser.url_reads == 1


def test_a_timed_out_open_that_never_reached_the_login_page_still_fails() -> None:
    browser = _Browser(open_error=_TIMEOUT, url="about:blank")

    with pytest.raises(BrowserError):
        login_mod._open_login_page(browser, _Config())  # type: ignore[arg-type]


def test_any_other_open_failure_still_fails_even_on_the_login_page() -> None:
    browser = _Browser(
        open_error="agent-browser failed (1): cmd=open https://mailon.kr/ stderr=net::ERR_NAME_NOT_RESOLVED",
        url="https://mailon.kr/integrated/login",
    )

    with pytest.raises(BrowserError):
        login_mod._open_login_page(browser, _Config())  # type: ignore[arg-type]
    assert browser.url_reads == 0


def test_a_clean_open_does_not_second_guess_the_url() -> None:
    browser = _Browser(open_error=None, url="https://mailon.kr/integrated/login")

    login_mod._open_login_page(browser, _Config())  # type: ignore[arg-type]

    assert browser.url_reads == 0
