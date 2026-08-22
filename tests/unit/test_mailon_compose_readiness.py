"""compose 는 성공할 때까지 재시도된다 — 회귀 고정.

2026-08-18 실측 배경: 로그인이 `https://mailon.kr/mail` 에 도달해도 SPA 는 아직
빌드 중이고, **서로 다른 깨진 상태 여러 개**를 차례로 통과한다. 로그인 직후
compose 를 반복 호출해 측정한 것:

    t+0.1s  TypeError: Cannot read properties of undefined (reading 'compose')
    t+0.7s  TypeError: tabPanel._getMenuById is not a function
    t+1.3s  TypeError: tabPanel._getMenuById is not a function
    t+1.9s  compose OK

그래서 준비 상태를 *술어*로 판정할 수 없다. 앞선 시도는 `tabPanel` 이 정의됐는지
물었는데, 그 객체가 아직 `_getMenuById` 를 서비스하지 못하는 동안에도 참이라
compose 가 통과된 뒤 그대로 터졌다. 이제는 compose 호출 자체를 재시도하고 그
성공을 준비 신호로 삼는다 — compose 열기는 읽기 전용이라 재시도가 안전하다.

mailon 은 이 throw 를 BrowserError → exit 2(`auth_or_browser_error`)로 접기
때문에, 순전한 기동 경쟁이 "기관메일 인증 실패"로 보고됐다.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parents[2] / "skills" / "mail" / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from mailon import resolve as resolve_mod, send_trigger  # noqa: E402
from mailon.browser import BrowserError  # noqa: E402
from mailon.send import ComposeSender, SendRequest  # noqa: E402

# 대기 예산을 실제 벽시계로 태우지 않는다 — `wait_ms` 가 이 시계를 민다.
_REAL_MONOTONIC = time.monotonic
_CLOCK = {"now": 0.0}

# 실측된 실패 문자열. 서로 *다른* 실패라는 점이 이 설계의 근거다.
_FAILURES = (
    "Cannot read properties of undefined (reading 'compose')",
    "tabPanel._getMenuById is not a function",
    "tabPanel._getMenuById is not a function",
)


class _FakeBrowser:
    """`resolve_name` 이 실제로 쓰는 표면만 구현하고 호출 순서를 기록한다."""

    def __init__(self, fail_times: int = 0, *, never_ready: bool = False) -> None:
        self._fail_times = fail_times
        self._never_ready = never_ready
        self._compose_calls = 0
        self.calls: list[str] = []
        self.last_error = ""

    # --- scripts ---------------------------------------------------------
    def eval_js(self, script: str) -> str:
        if "_tbar.compose" in script:
            self.calls.append("compose")
            self._compose_calls += 1
            if self._never_ready or self._compose_calls <= self._fail_times:
                index = (self._compose_calls - 1) % len(_FAILURES)
                self.last_error = f"TypeError: {_FAILURES[index]}"
                raise BrowserError(self.last_error)
            return '"compose-opened"'
        self.calls.append("eval_js")
        return '""'

    def eval_json(self, _script: str):
        self.calls.append("eval_json")
        return []

    # --- interaction -----------------------------------------------------
    def focus(self, _ref: str) -> None:
        self.calls.append("focus")

    def type_text(self, _ref: str, _value: str) -> None:
        self.calls.append("type_text")

    def wait_ms(self, milliseconds: int) -> None:
        _CLOCK["now"] += milliseconds / 1000.0

    def clear_network_requests(self) -> None:
        self.calls.append("clear_network_requests")

    def network_post_count(self) -> int:
        return 0


class _SendBrowser(_FakeBrowser):
    def eval_json(self, _script: str):
        self.calls.append("eval_json")
        return {"csrf": {"name": "t", "value": "v"}, "file_input": None}

    def fill(self, _selector: str, _value: str) -> None:
        return None

    def upload(self, _selector: str, _paths: tuple[Path, ...]) -> None:
        return None

    def network_requests(self) -> str:
        return "[]"


@pytest.fixture(autouse=True)
def _fake_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    _CLOCK["now"] = 0.0
    monkeypatch.setattr(time, "monotonic", lambda: _CLOCK["now"])


def test_compose_is_retried_through_the_measured_broken_states() -> None:
    # Given: 실측대로 서로 다른 실패 3개를 거친 뒤 열리는 페이지.
    browser = _FakeBrowser(fail_times=3)

    # When / Then: 예외 없이 통과한다 — 네 번째 호출이 성공한 그 호출이다.
    send_trigger.open_compose_when_ready(browser, clock=lambda: _CLOCK["now"])
    assert browser.calls.count("compose") == 4
    assert _CLOCK["now"] < 30.0


def test_compose_failure_is_not_swallowed_when_it_never_opens() -> None:
    # Given: 끝내 열리지 않는 페이지.
    browser = _FakeBrowser(never_ready=True)

    # When / Then: 예산을 소진하고, 마지막 실패 이유를 실은 채 BrowserError.
    with pytest.raises(BrowserError) as excinfo:
        send_trigger.open_compose_when_ready(browser, clock=lambda: _CLOCK["now"])

    message = str(excinfo.value)
    assert "compose did not become callable" in message
    assert browser.last_error in message  # 마지막 시도의 원인이 보존된다
    assert _CLOCK["now"] >= 30.0


def test_default_clock_uses_the_monkeypatched_time_seam() -> None:
    browser = _FakeBrowser(never_ready=True)
    started = _REAL_MONOTONIC()

    with pytest.raises(BrowserError, match="compose did not become callable"):
        send_trigger.open_compose_when_ready(browser, timeout_s=0.1)

    assert _CLOCK["now"] == 0.5
    assert _REAL_MONOTONIC() - started < 0.05


def test_compose_opens_before_the_to_field_is_touched() -> None:
    # Given: 한 번 실패 뒤 열리는 페이지.
    browser = _FakeBrowser(fail_times=1)

    # When: 수신자를 조회한다.
    resolve_mod.resolve_name(browser, "홍길동")

    # Then: 성공한 compose 가 To 필드 조작보다 먼저다 — 이 순서가 결함의 핵심이다.
    assert "focus" in browser.calls
    last_compose = len(browser.calls) - 1 - browser.calls[::-1].index("compose")
    assert last_compose < browser.calls.index("focus")


def test_send_survives_the_measured_compose_startup_race() -> None:
    browser = _SendBrowser(fail_times=3)
    request = SendRequest(
        recipients=("owner@example.invalid",),
        cc=(),
        subject="s",
        body="b",
        attachments=(),
    )

    result = ComposeSender(browser, clock=lambda: _CLOCK["now"]).send(
        request,
        dry_run=True,
    )

    assert result.status == "dry_run"
    assert browser.calls.count("compose") == 4


def test_send_fails_closed_when_compose_never_opens() -> None:
    browser = _SendBrowser(never_ready=True)
    request = SendRequest(
        recipients=("owner@example.invalid",),
        cc=(),
        subject="s",
        body="b",
        attachments=(),
    )

    with pytest.raises(BrowserError) as excinfo:
        ComposeSender(browser, clock=lambda: _CLOCK["now"]).send(
            request,
            dry_run=True,
        )

    assert "compose did not become callable" in str(excinfo.value)
