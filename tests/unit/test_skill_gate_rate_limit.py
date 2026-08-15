"""스킬 게이트의 Discord 호출은 429 를 존중해야 한다 — 최고 권한 경로가 유일한 예외였다.

2026-08-02 실측. ⑦ 워처를 켜자 소유자가 ✅ 를 눌렀는데도 배포가 재개되지 않았다.
원인은 승인 판정이 아니라 전송이었다:

    HTTP 429 .../reactions/%E2%9B%94  {"retry_after": 1.932}

`_owner_decision` 은 레코드당 반응 조회를 2회(⛔ 먼저, ✅ 다음) 한다. 워처가 6건을
한 tick 에 연속 조회하면 per-route 버킷이 소진되고, **열거 순서 뒤쪽이 항상** 429 를
맞는다. `wiki` 는 알파벳 마지막이라 매번 걸려 영원히 `undecidable` 이었다.

이 리포는 이미 429 를 올바르게 다룬다(`automation/interop/discord_transport.py` 가
`Retry-After` 를 존중하며 재시도). 하필 스킬 게이트만 맨 `urlopen` 이었다 — 시스템에서
가장 권한이 높은 경로가 유일하게 백오프 없는 호출자였고, 사람이 한 번씩 부를 때는
드러나지 않다가 2분 주기 워처가 생기자 드러났다.

여기서 고정하는 것은 세 가지다: 429 는 재시도한다, 429 가 아닌 실패는 재시도하지
않는다(오류를 숨기면 안 된다), 그리고 재시도는 유한하다(tick 이 영원히 매달리면
그것대로 침묵하는 고장이다).
"""
from __future__ import annotations

import io
import json
from email.message import Message
from typing import Any
from urllib.error import HTTPError

import pytest

from automation import skill_gate


class _Response:
    """urlopen 컨텍스트 매니저 흉내 — 본문 하나만 돌려준다."""

    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _rate_limited(retry_after: str | None = "1.5") -> HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError(
        "https://discord.com/api/v10/x", 429, "Too Many Requests", headers, io.BytesIO(b"{}")
    )


def _install(monkeypatch: pytest.MonkeyPatch, responses: list[object]) -> list[float]:
    """호출마다 responses 를 하나씩 소비한다. 예외면 raise, 아니면 본문."""
    slept: list[float] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setattr(skill_gate.time, "sleep", lambda seconds: slept.append(seconds))

    def fake_urlopen(_request: Any, timeout: int = 0) -> _Response:  # noqa: ARG001
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _Response(item)

    monkeypatch.setattr(skill_gate, "urlopen", fake_urlopen)
    return slept


def test_a_rate_limited_call_is_retried_instead_of_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """소유자의 ✅ 를 못 읽어 배포가 멈추던 그 지점."""
    slept = _install(monkeypatch, [_rate_limited(), {"ok": True}])
    assert skill_gate._api("GET", "/x") == {"ok": True}
    assert slept, "Retry-After 를 기다리지 않았다"


def test_the_retry_honours_the_servers_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    """더 빨리 다시 두드리면 버킷을 더 오래 닫아둘 뿐이다."""
    slept = _install(monkeypatch, [_rate_limited("2.5"), {"ok": True}])
    _ = skill_gate._api("GET", "/x")
    assert slept[0] >= 2.5


def test_a_missing_retry_after_still_backs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    slept = _install(monkeypatch, [_rate_limited(None), {"ok": True}])
    _ = skill_gate._api("GET", "/x")
    assert slept and slept[0] > 0


def test_a_non_rate_limit_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """404 를 재시도로 숨기면 삭제된 승인 메시지가 정상처럼 보인다."""
    headers = Message()
    not_found = HTTPError("https://discord.com/api/v10/x", 404, "Not Found", headers, None)
    responses: list[object] = [not_found, {"ok": True}]
    _ = _install(monkeypatch, responses)
    with pytest.raises(HTTPError) as raised:
        _ = skill_gate._api("GET", "/x")
    assert raised.value.code == 404
    assert len(responses) == 1, "404 인데도 다시 호출했다"


def test_a_permanent_rate_limit_gives_up_rather_than_hanging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """무한 재시도는 tick 을 영원히 매달아 그것대로 침묵하는 고장이 된다."""
    _ = _install(monkeypatch, [_rate_limited() for _ in range(50)])
    with pytest.raises(HTTPError) as raised:
        _ = skill_gate._api("GET", "/x")
    assert raised.value.code == 429


def test_a_successful_call_never_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    slept = _install(monkeypatch, [{"ok": True}])
    assert skill_gate._api("GET", "/x") == {"ok": True}
    assert slept == []
